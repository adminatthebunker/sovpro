"""Tier-2 pattern probe for missing politician socials.

Pipeline (zero LLM tokens):

  1. Read v_socials_missing — one row per (politician, missing_platform)
  2. For each row, build a small set of candidate handles from name tokens
  3. HEAD/GET each candidate; extract a display name from og:title / meta
  4. Score name-match + constituency/party signal vs. the politician
  5. conf >= 0.70 → auto-insert (source='pattern_probe')
     0.40 <= conf < 0.70 → insert with flagged_low_confidence=true
     conf < 0.40 → reject (log, don't insert)

Per-platform verification:

  bluesky   — clean JSON API at public.api.bsky.app; strong signal
  twitter   — unauth GET of x.com, og:title match. Many profiles render
              JS-only; rejects are silent.
  facebook  — unauth GET of facebook.com, og:title match. Login-walled
              accounts may return a stub page — we only accept on title hit.
  instagram — unauth GET of instagram.com, og:title match.
  youtube   — unauth GET of youtube.com/@handle, og:title match.
  threads   — threads.net/@{handle}; piggybacks on an instagram hit
              (threads handle == instagram handle).
  linkedin  — *skipped* here; anti-bot aggressive. Leave for Tier 3.

Callers supply --platform to narrow scope. Defaults to 'bluesky' because
that's where the gap is biggest (1,700+ missing) and the API is cleanest.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Optional
from urllib.parse import quote

import httpx
from rich.console import Console
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn

from .db import Database
from .socials import upsert_social

log = logging.getLogger(__name__)
console = Console()


PLATFORMS_SUPPORTED: tuple[str, ...] = (
    "bluesky", "twitter", "facebook", "instagram", "youtube", "threads",
)
# linkedin deliberately omitted — anti-bot makes pattern probing unreliable.

USER_AGENT = (
    "Mozilla/5.0 (compatible; SovereignWatch/1.0; +https://canadianpoliticaldata.ca) "
    "polite bot, contact admin@thebunkerops.ca"
)

BSKY_API = "https://public.api.bsky.app/xrpc/app.bsky.actor.getProfile"

# Rate-limit config. Per-host min-gap with global concurrency cap.
# Most social hosts get a polite 1s/host gap; well-documented public APIs
# (currently just Bluesky's app-view) can handle higher QPS — we override
# them in _HOST_GAP_OVERRIDES.
_MIN_HOST_GAP_S = 1.0
_HOST_GAP_OVERRIDES: dict[str, float] = {
    "public.api.bsky.app": 0.10,  # 10 QPS; documented cap is ~300 QPS/IP
}
_GLOBAL_CONC = 8
_PER_POLITICIAN_MAX_CANDIDATES = 4


# ── Scoring constants ────────────────────────────────────────────────

PROMOTE_THRESHOLD = 0.70   # conf >= this → not flagged
FLAG_THRESHOLD    = 0.40   # conf < this → reject outright


# ── Data model ───────────────────────────────────────────────────────

@dataclass
class MissingRow:
    politician_id: str
    name: str
    level: str
    province_territory: Optional[str]
    constituency_name: Optional[str]
    party: Optional[str]
    platform: str  # the MISSING platform


@dataclass
class Candidate:
    url: str
    handle: str          # bare handle for the platform
    # Signals extracted from the probe response:
    profile_title: Optional[str] = None   # og:title / display_name / <title>
    profile_bio: Optional[str] = None     # meta description / account.description
    confidence: float = 0.0
    reject_reason: Optional[str] = None


# ── Name utilities ───────────────────────────────────────────────────

def _strip_accents(s: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFKD", s)
        if not unicodedata.combining(c)
    )


_STOP_TOKENS: frozenset[str] = frozenset({
    "the", "of", "de", "la", "le", "mla", "mpp", "mp", "mna", "mha",
    "hon", "honourable", "hon.", "dr", "mr", "mrs", "ms", "sir",
})


def _name_tokens(name: str) -> list[str]:
    """Return lowercase ASCII tokens from a politician name, stop words dropped."""
    s = _strip_accents(name or "").lower()
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return [t for t in s.split() if t and t not in _STOP_TOKENS and len(t) >= 2]


def _first_last(name: str) -> tuple[Optional[str], Optional[str]]:
    toks = _name_tokens(name)
    if not toks:
        return None, None
    if len(toks) == 1:
        return toks[0], None
    return toks[0], toks[-1]


def _name_overlap(politician_name: str, profile_text: str) -> float:
    """Token-overlap ratio: fraction of politician tokens appearing in profile."""
    pol = set(_name_tokens(politician_name))
    pro = set(_name_tokens(profile_text))
    if not pol:
        return 0.0
    return len(pol & pro) / len(pol)


# ── Candidate generators ─────────────────────────────────────────────

# For platforms whose handles are ASCII-only, we generate a compact set of
# name-based variants. Order matters — we try the most likely first and
# stop at the first high-confidence hit per politician.

def _candidates_bluesky(name: str, twitter_handle: Optional[str]) -> list[tuple[str, str]]:
    """Return (handle, url) pairs. `handle` is the bsky DID/handle string."""
    first, last = _first_last(name)
    cands: list[str] = []
    if first and last:
        cands.append(f"{first}{last}")
        cands.append(f"{first}-{last}")
        cands.append(f"{first}.{last}")
    if twitter_handle:
        # A large share of politicians simply reuse their twitter handle.
        th = re.sub(r"[^a-z0-9]", "", twitter_handle.lower())
        if th:
            cands.append(th)
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for c in cands:
        bh = f"{c}.bsky.social"
        if bh in seen:
            continue
        seen.add(bh)
        out.append((bh, f"https://bsky.app/profile/{bh}"))
    return out[:_PER_POLITICIAN_MAX_CANDIDATES]


def _candidates_twitter(name: str) -> list[tuple[str, str]]:
    first, last = _first_last(name)
    if not first or not last:
        return []
    cands = [
        f"{first}{last}",
        f"{first}_{last}",
        f"{first}.{last}",
        f"{first[0]}{last}" if len(first) > 0 else None,
    ]
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for h in cands:
        if not h or h in seen:
            continue
        seen.add(h)
        out.append((h, f"https://x.com/{quote(h)}"))
    return out[:_PER_POLITICIAN_MAX_CANDIDATES]


def _candidates_facebook(name: str) -> list[tuple[str, str]]:
    first, last = _first_last(name)
    if not first or not last:
        return []
    cands = [f"{first}.{last}", f"{first}{last}", f"{first}-{last}"]
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for h in cands:
        if h in seen:
            continue
        seen.add(h)
        out.append((h, f"https://www.facebook.com/{quote(h)}"))
    return out[:_PER_POLITICIAN_MAX_CANDIDATES]


def _candidates_instagram(name: str) -> list[tuple[str, str]]:
    first, last = _first_last(name)
    if not first or not last:
        return []
    cands = [f"{first}{last}", f"{first}.{last}", f"{first}_{last}"]
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for h in cands:
        if h in seen:
            continue
        seen.add(h)
        out.append((h, f"https://www.instagram.com/{quote(h)}/"))
    return out[:_PER_POLITICIAN_MAX_CANDIDATES]


def _candidates_youtube(name: str) -> list[tuple[str, str]]:
    first, last = _first_last(name)
    if not first or not last:
        return []
    cands = [f"{first}{last}", f"{first}-{last}"]
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for h in cands:
        if h in seen:
            continue
        seen.add(h)
        out.append((h, f"https://www.youtube.com/@{quote(h)}"))
    return out[:_PER_POLITICIAN_MAX_CANDIDATES]


def _candidates_threads(name: str, instagram_handle: Optional[str]) -> list[tuple[str, str]]:
    # threads.net handles mirror the instagram handle. If we just learned
    # a valid IG handle for this politician, prefer that.
    cands: list[str] = []
    if instagram_handle:
        cands.append(instagram_handle)
    first, last = _first_last(name)
    if first and last:
        cands.extend([f"{first}{last}", f"{first}.{last}", f"{first}_{last}"])
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for h in cands:
        if not h or h in seen:
            continue
        seen.add(h)
        out.append((h, f"https://www.threads.net/@{quote(h)}"))
    return out[:_PER_POLITICIAN_MAX_CANDIDATES]


# ── Verifiers ────────────────────────────────────────────────────────

_OG_TITLE = re.compile(
    r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)["\']',
    re.IGNORECASE,
)
_OG_DESC = re.compile(
    r'<meta[^>]+property=["\']og:description["\'][^>]+content=["\']([^"\']+)["\']',
    re.IGNORECASE,
)
_PAGE_TITLE = re.compile(r"<title[^>]*>([^<]+)</title>", re.IGNORECASE)


def _extract_profile_text(html: str) -> tuple[Optional[str], Optional[str]]:
    """Return (title-ish, description-ish) from a profile HTML page."""
    title = None
    desc = None
    m = _OG_TITLE.search(html)
    if m:
        title = m.group(1)
    else:
        m = _PAGE_TITLE.search(html)
        if m:
            title = m.group(1)
    m = _OG_DESC.search(html)
    if m:
        desc = m.group(1)
    return title, desc


async def _verify_bluesky(client: httpx.AsyncClient, handle: str) -> tuple[Optional[str], Optional[str]]:
    """Return (display_name, description) from the public profile API, or (None, None)."""
    try:
        r = await client.get(BSKY_API, params={"actor": handle}, timeout=15.0)
    except httpx.HTTPError:
        return None, None
    if r.status_code != 200:
        return None, None
    try:
        js = r.json()
    except Exception:
        return None, None
    return js.get("displayName") or js.get("handle"), js.get("description")


async def _verify_html(client: httpx.AsyncClient, url: str) -> tuple[Optional[str], Optional[str]]:
    """Fetch a profile page and pull (title-ish, description-ish) from og/meta."""
    try:
        r = await client.get(url, timeout=15.0, follow_redirects=True)
    except httpx.HTTPError:
        return None, None
    if r.status_code not in (200, 301, 302):
        return None, None
    return _extract_profile_text(r.text)


# ── Scoring ──────────────────────────────────────────────────────────

# Keywords that signal "this profile is about Canadian politics". Used as
# a *gate* for high-confidence promotion — a perfect name match alone is
# not enough (the Bluesky universe has many John Smiths who are not MPs).
# The gate is intentionally broad; false-negatives (politicians who don't
# put "MP" in their bio) downgrade to the flagged queue, which Tier-3
# review can clear.
_POLITICAL_KEYWORDS: tuple[str, ...] = (
    "mp ", " mp", "m.p.", "member of parliament",
    "mla", "m.l.a.", "mpp", "m.p.p.", "mna", "m.n.a.", "mha", "m.h.a.",
    "senator", "senate", "sénatrice", "sénateur",
    "mayor", "councillor", "councilor", "deputy mayor",
    "liberal", "conservative", "ndp", "bloc", "green party",
    "parti liberal", "parti conservateur", "parti quebecois",
    "progressive conservative", "independent senators",
    "house of commons", "parliament", "parlement",
    "legislative assembly", "assemblee nationale",
    "constituency", "riding", "caucus", "minister",
    "parl.gc.ca", "ourcommons.ca", "sencanada.ca",
    "leg.bc.ca", "ola.org", "assemblee", "assemblee-nationale",
    "proudly representing", "elected", "re-elected", "reelected",
    # French equivalents for Quebec politicians
    "depute", "deputee", "ministre",
)

# Maximum confidence achievable from a name match alone (no political
# context signal). This caps the theatre-director-named-like-a-senator
# problem at the flagged-review threshold instead of auto-inserting.
_NAME_ONLY_CAP = 0.55


def _has_political_context(text: str) -> bool:
    """True if text contains any Canadian-politics signal."""
    for kw in _POLITICAL_KEYWORDS:
        if kw in text:
            return True
    return False


def _score(
    politician: MissingRow,
    title: Optional[str],
    bio: Optional[str],
) -> tuple[float, Optional[str]]:
    """Return (confidence, reject_reason_or_None).

    Design goal: false-positives are much worse than false-negatives here,
    because high-confidence rows skip human review. We require either a
    political-context signal or a strong local identifier (constituency +
    province match) to promote above the name-only cap.

    Confidence components:
      +0.60 * name_overlap(title)            base signal (capped at 0.60)
      +0.20 if political-context keyword present in title+bio
      +0.15 if constituency token present in title+bio
      +0.10 if province name/abbrev present in title+bio
      +0.05 if party acronym present in title+bio
      -0.30 if 'parody' / 'unofficial' / 'fan' / 'not affiliated' appear
      -0.40 if province abbrev/name for a DIFFERENT province appears

    Without a political-context signal, the total is capped at
    _NAME_ONLY_CAP (= 0.55), which keeps it below the PROMOTE_THRESHOLD
    (= 0.70) so the row always lands in the flagged review queue.
    """
    if not title:
        return 0.0, "no_title"
    t = _strip_accents((title + " " + (bio or "")).lower())
    nm = _name_overlap(politician.name, title)
    if nm == 0.0:
        return 0.0, "zero_name_overlap"

    score = 0.60 * nm
    political = _has_political_context(t)
    if political:
        score += 0.20

    party = (politician.party or "").lower()
    constituency = _strip_accents((politician.constituency_name or "").lower())
    pt = (politician.province_territory or "").lower()

    if constituency:
        cons_tokens = _name_tokens(constituency)
        if cons_tokens and any(tok in t for tok in cons_tokens):
            score += 0.15

    if party:
        for tok in party.split():
            if len(tok) <= 4 and tok in t:
                score += 0.05
                break

    province_names = {
        "ab": ["alberta", "ab"],
        "bc": ["british columbia", "bc"],
        "mb": ["manitoba", "mb"],
        "nb": ["new brunswick", "nb"],
        "nl": ["newfoundland", "labrador", "nl"],
        "ns": ["nova scotia", "ns"],
        "nt": ["northwest territories", "nt"],
        "nu": ["nunavut", "nu"],
        "on": ["ontario", "on"],
        "pe": ["pei", "prince edward island", "pe"],
        "qc": ["quebec", "qc"],
        "sk": ["saskatchewan", "sk"],
        "yt": ["yukon", "yt"],
    }
    if pt:
        own = province_names.get(pt, [])
        if any(w in t for w in own):
            score += 0.10
        for code, words in province_names.items():
            if code == pt:
                continue
            if any(w in t for w in words):
                score -= 0.40
                break

    if any(w in t for w in ("parody", "unofficial", "fan account", "not affiliated")):
        score -= 0.30

    if not political:
        score = min(score, _NAME_ONLY_CAP)

    score = max(0.0, min(1.0, score))

    if score < FLAG_THRESHOLD:
        return score, "below_flag_threshold"
    return score, None


# ── Driver ──────────────────────────────────────────────────────────

async def _fetch_missing_rows(
    db: Database,
    *,
    platform: str,
    limit: int,
) -> list[MissingRow]:
    rows = await db.fetch(
        """
        SELECT politician_id, name, level, province_territory,
               constituency_name, party, platform
          FROM v_socials_missing
         WHERE platform = $1
         ORDER BY level, province_territory, name
         LIMIT $2
        """,
        platform, int(limit),
    )
    return [
        MissingRow(
            politician_id=str(r["politician_id"]),
            name=r["name"] or "",
            level=r["level"],
            province_territory=r["province_territory"],
            constituency_name=r["constituency_name"],
            party=r["party"],
            platform=r["platform"],
        )
        for r in rows
    ]


async def _fetch_existing_handles(
    db: Database,
    politician_ids: list[str],
) -> dict[str, dict[str, str]]:
    """Return {politician_id: {platform: handle, ...}} for lookups during probing."""
    if not politician_ids:
        return {}
    rows = await db.fetch(
        """
        SELECT politician_id, platform, handle
          FROM politician_socials
         WHERE politician_id = ANY($1)
        """,
        politician_ids,
    )
    out: dict[str, dict[str, str]] = defaultdict(dict)
    for r in rows:
        if r["handle"]:
            out[str(r["politician_id"])][r["platform"]] = r["handle"]
    return dict(out)


class _HostPacer:
    """Enforces a minimum gap between requests to the same host.

    Default gap is polite (_MIN_HOST_GAP_S = 1s). Hosts listed in
    _HOST_GAP_OVERRIDES get a smaller gap — used for well-documented
    public APIs where 1-QPS would be needlessly slow.
    """
    def __init__(self, default_gap_s: float = _MIN_HOST_GAP_S) -> None:
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._last: dict[str, float] = defaultdict(float)
        self._default = default_gap_s

    def _gap_for(self, host: str) -> float:
        return _HOST_GAP_OVERRIDES.get(host, self._default)

    async def wait(self, host: str) -> None:
        lock = self._locks[host]
        async with lock:
            now = time.monotonic()
            elapsed = now - self._last[host]
            gap = self._gap_for(host)
            if elapsed < gap:
                await asyncio.sleep(gap - elapsed)
            self._last[host] = time.monotonic()


def _candidates_for(
    platform: str,
    row: MissingRow,
    existing: dict[str, str],
) -> list[tuple[str, str]]:
    if platform == "bluesky":
        return _candidates_bluesky(row.name, existing.get("twitter"))
    if platform == "twitter":
        return _candidates_twitter(row.name)
    if platform == "facebook":
        return _candidates_facebook(row.name)
    if platform == "instagram":
        return _candidates_instagram(row.name)
    if platform == "youtube":
        return _candidates_youtube(row.name)
    if platform == "threads":
        return _candidates_threads(row.name, existing.get("instagram"))
    return []


async def probe_missing_socials(
    db: Database,
    *,
    platform: str = "bluesky",
    limit: int = 500,
    dry_run: bool = False,
) -> None:
    """Run Tier-2 discovery for the given platform.

    `limit` caps the number of v_socials_missing rows processed in one
    invocation — useful while tuning thresholds. `dry_run` prints what
    would be inserted without writing.
    """
    if platform not in PLATFORMS_SUPPORTED:
        console.print(f"[red]unsupported platform: {platform}. use one of {PLATFORMS_SUPPORTED}[/red]")
        return

    missing = await _fetch_missing_rows(db, platform=platform, limit=limit)
    if not missing:
        console.print(f"[yellow]nothing to probe for platform={platform}[/yellow]")
        return
    console.print(
        f"[cyan]probe-missing-socials:[/cyan] platform={platform} "
        f"rows={len(missing)} dry_run={dry_run}"
    )
    pids = [r.politician_id for r in missing]
    existing = await _fetch_existing_handles(db, pids)

    pacer = _HostPacer()
    sem = asyncio.Semaphore(_GLOBAL_CONC)
    stats = Counter()
    inserted_high: list[str] = []
    inserted_flag: list[str] = []
    rejected: list[str] = []

    progress = Progress(
        SpinnerColumn(),
        TextColumn("{task.description}"),
        BarColumn(),
        TextColumn("{task.completed}/{task.total}"),
        TimeElapsedColumn(),
    )

    async with httpx.AsyncClient(
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-CA,en;q=0.9",
        },
    ) as client:

        async def probe_one(row: MissingRow) -> None:
            cands = _candidates_for(platform, row, existing.get(row.politician_id, {}))
            if not cands:
                stats["no_candidates"] += 1
                return
            best: Optional[Candidate] = None
            for handle, url in cands:
                stats["candidates_probed"] += 1
                host = "public.api.bsky.app" if platform == "bluesky" else _host_of(url)
                async with sem:
                    await pacer.wait(host)
                    if platform == "bluesky":
                        title, bio = await _verify_bluesky(client, handle)
                    else:
                        title, bio = await _verify_html(client, url)
                if title is None:
                    stats["no_profile_response"] += 1
                    continue
                conf, reject = _score(row, title, bio)
                cand = Candidate(
                    url=url, handle=handle,
                    profile_title=title, profile_bio=bio,
                    confidence=conf, reject_reason=reject,
                )
                if best is None or cand.confidence > best.confidence:
                    best = cand
                # Short-circuit: if we already have a great match, stop probing.
                if conf >= 0.90:
                    break

            if best is None:
                stats["no_hit"] += 1
                return
            if best.confidence < FLAG_THRESHOLD:
                rejected.append(
                    f"{row.name}:{platform}:{best.handle} "
                    f"conf={best.confidence:.2f} reason={best.reject_reason}"
                )
                stats["rejected"] += 1
                return

            # Auto-insert (possibly flagged).
            flagged = best.confidence < PROMOTE_THRESHOLD
            if dry_run:
                (inserted_flag if flagged else inserted_high).append(
                    f"{row.name}:{platform}:{best.handle} conf={best.confidence:.2f}"
                )
                stats["dry_run_would_insert"] += 1
                return

            try:
                canon = await upsert_social(
                    db, row.politician_id, platform, best.url,
                    source="pattern_probe",
                    confidence=best.confidence,
                    evidence_url=best.url,
                )
            except Exception as exc:
                log.warning("probe upsert failed for %s %s: %s", row.politician_id, best.url, exc)
                stats["insert_error"] += 1
                return
            if canon is None:
                stats["upsert_rejected"] += 1
                return
            if flagged:
                inserted_flag.append(
                    f"{row.name}:{platform}:{best.handle} conf={best.confidence:.2f}"
                )
                stats["flagged_inserted"] += 1
            else:
                inserted_high.append(
                    f"{row.name}:{platform}:{best.handle} conf={best.confidence:.2f}"
                )
                stats["high_inserted"] += 1

        with progress:
            task = progress.add_task(f"probing {platform}", total=len(missing))
            # bounded concurrency — don't gather everything at once.
            batch_size = _GLOBAL_CONC * 4
            for i in range(0, len(missing), batch_size):
                batch = missing[i:i + batch_size]
                await asyncio.gather(*(probe_one(r) for r in batch))
                progress.update(task, advance=len(batch))

    console.print()
    console.print(
        f"[green]✓ probe done:[/green] candidates={stats['candidates_probed']} "
        f"high={stats['high_inserted']} flagged={stats['flagged_inserted']} "
        f"rejected={stats['rejected']} no_hit={stats['no_hit']} "
        f"no_candidates={stats['no_candidates']} "
        f"no_profile={stats['no_profile_response']}"
    )
    if inserted_high:
        console.print(f"[green]High-confidence inserts ({len(inserted_high)}):[/green]")
        for line in inserted_high[:25]:
            console.print(f"  {line}")
        if len(inserted_high) > 25:
            console.print(f"  … and {len(inserted_high) - 25} more")
    if inserted_flag:
        console.print(f"[yellow]Flagged inserts ({len(inserted_flag)}):[/yellow]")
        for line in inserted_flag[:25]:
            console.print(f"  {line}")
        if len(inserted_flag) > 25:
            console.print(f"  … and {len(inserted_flag) - 25} more")


def _host_of(url: str) -> str:
    from urllib.parse import urlparse
    h = urlparse(url).hostname or ""
    return h.lower()
