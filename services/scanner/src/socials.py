"""Social handle normalization, discovery, and liveness.

Phase 5 of the Canadian political dataset expansion.

Three public entry points:

  normalize_socials(db)
    Read every populated politicians.social_urls JSONB column and explode
    each (platform, url) pair into a normalized politician_socials row.
    Idempotent — safe to re-run.

  verify_liveness(db, *, limit, stale_hours)
    HEAD/GET each politician_socials.url that has never been verified or
    is older than `stale_hours`. Update is_live + last_verified_at.
    When a handle flips live -> dead, write a politician_changes row.

  upsert_social(db, politician_id, platform_hint, url, *, source, ...)
    Low-level helper shared with the discovery pass in enrich.py.
    Canonicalises handle, upserts the row, and writes a `social_added`
    politician_changes row on first insert.
    `source` is required — every caller must declare where the URL came
    from ('wikidata', 'openparliament', 'personal_site', 'pattern_probe',
    'agent_sonnet', etc.). Used for provenance in migration 0026.

Canonicalisation map (plan spec):
  twitter / x.com  -> platform='twitter', handle = last path segment w/o '@'
  facebook         -> handle = last segment, or 'id:N' for profile.php?id=N
  instagram        -> handle = last segment w/o '@'
  youtube          -> handle = last segment (channel ID or @handle)
  tiktok           -> handle = last segment w/o '@'
  linkedin         -> handle = segment after /in/ or /company/
  mastodon         -> handle = '@user@instance'
  bluesky          -> handle = segment after /profile/
  threads          -> handle = segment after /@
  anything else    -> platform='other', handle=None
"""
from __future__ import annotations

import asyncio
import logging
import re
from collections import Counter
from dataclasses import dataclass
from typing import Optional
from urllib.parse import parse_qs, urlparse

import httpx
import orjson
from rich.console import Console
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn

from .db import Database

log = logging.getLogger(__name__)
console = Console()


ALLOWED_PLATFORMS = frozenset({
    "twitter", "facebook", "instagram", "youtube", "tiktok",
    "linkedin", "mastodon", "bluesky", "threads", "other",
})

# (host substring, platform_name)
_HOST_TO_PLATFORM: tuple[tuple[str, str], ...] = (
    ("twitter.com", "twitter"),
    ("x.com", "twitter"),
    ("facebook.com", "facebook"),
    ("fb.com", "facebook"),
    ("instagram.com", "instagram"),
    ("youtube.com", "youtube"),
    ("youtu.be", "youtube"),
    ("tiktok.com", "tiktok"),
    ("linkedin.com", "linkedin"),
    ("bsky.app", "bluesky"),
    ("threads.net", "threads"),
)

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)


@dataclass
class CanonicalSocial:
    platform: str          # one of ALLOWED_PLATFORMS
    handle: Optional[str]  # None => platform='other'
    url: str               # normalized URL (scheme preserved where present)


def canonicalize(platform_hint: Optional[str], url: str) -> Optional[CanonicalSocial]:
    """Return a CanonicalSocial or None if url is unusable.

    `platform_hint` may be a key observed in social_urls JSONB (e.g. 'twitter',
    'x.com', 'facebook') or None when we only have the URL (discovery path).
    """
    if not url:
        return None
    url = url.strip()
    if not url:
        return None
    # Prepend scheme if the URL starts with bare host (Open North sometimes
    # stores 'twitter.com/foo' without the scheme).
    if not url.startswith(("http://", "https://")):
        url = "https://" + url.lstrip("/")

    try:
        parsed = urlparse(url)
    except Exception:
        return None
    host = (parsed.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if host.startswith("m."):
        host = host[2:]

    platform = _platform_from(host, platform_hint)
    path = parsed.path or ""

    handle: Optional[str] = None
    if platform == "twitter":
        handle = _last_segment(path)
        handle = _strip_at(handle)
    elif platform == "facebook":
        # /profile.php?id=123 is the old Facebook numeric form.
        if path.rstrip("/").endswith("profile.php"):
            qs = parse_qs(parsed.query or "")
            fid = (qs.get("id") or [None])[0]
            if fid:
                handle = f"id:{fid}"
        if not handle:
            handle = _last_segment(path)
            handle = _strip_at(handle)
    elif platform == "instagram":
        handle = _last_segment(path)
        handle = _strip_at(handle)
    elif platform == "youtube":
        # /@handle, /channel/UCxxxx, /user/name, /c/name, /<name>
        segs = [s for s in path.split("/") if s]
        if segs:
            if segs[0] in ("channel", "user", "c") and len(segs) >= 2:
                handle = segs[1]
            else:
                handle = segs[-1]
    elif platform == "tiktok":
        handle = _last_segment(path)
        handle = _strip_at(handle)
    elif platform == "linkedin":
        segs = [s for s in path.split("/") if s]
        for i, s in enumerate(segs):
            if s in ("in", "company", "pub") and i + 1 < len(segs):
                handle = segs[i + 1]
                break
        if not handle:
            handle = _last_segment(path)
    elif platform == "mastodon":
        # Expected form @user@instance or /@user on a given host.
        segs = [s for s in path.split("/") if s]
        user = None
        if segs and segs[0].startswith("@"):
            user = segs[0][1:]
        if user and host:
            handle = f"@{user}@{host}"
        elif user:
            handle = f"@{user}"
    elif platform == "bluesky":
        segs = [s for s in path.split("/") if s]
        for i, s in enumerate(segs):
            if s == "profile" and i + 1 < len(segs):
                handle = segs[i + 1]
                break
    elif platform == "threads":
        segs = [s for s in path.split("/") if s]
        if segs and segs[0].startswith("@"):
            handle = segs[0][1:]
        elif segs:
            handle = segs[-1]
    else:
        platform = "other"
        handle = None

    if handle is not None:
        handle = handle.strip()
        if not handle or handle.lower() in _IGNORED_HANDLES:
            # Link pointed at the platform root or a system page; classify
            # as 'other' so it doesn't collide with real handles.
            return CanonicalSocial(platform="other", handle=None, url=url)
    if platform not in ALLOWED_PLATFORMS:
        platform = "other"
        handle = None
    return CanonicalSocial(platform=platform, handle=handle, url=url)


def _platform_from(host: str, hint: Optional[str]) -> str:
    for needle, plat in _HOST_TO_PLATFORM:
        if needle in host:
            return plat
    # Mastodon is host-agnostic; detect via hint only.
    hh = (hint or "").lower()
    if hh in ALLOWED_PLATFORMS:
        return hh
    if hh == "x.com":
        return "twitter"
    if "mastodon" in hh or "mastodon" in host:
        return "mastodon"
    return "other"


def _last_segment(path: str) -> Optional[str]:
    segs = [s for s in path.split("/") if s]
    return segs[-1] if segs else None


def _strip_at(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    return value[1:] if value.startswith("@") else value


# Platform root pages / common system endpoints that aren't real handles.
_IGNORED_HANDLES: frozenset[str] = frozenset({
    "home", "explore", "login", "signup", "signin", "about",
    "share", "sharer.php", "intent", "tr", "i",
})


# Sources trusted enough to never flag. Tier-2 and Tier-3 discoveries use
# confidence thresholds documented on the politician_socials table.
_TRUSTED_SOURCES: frozenset[str] = frozenset({
    "legacy", "legacy_jsonb", "wikidata", "openparliament",
    "masto_host", "personal_site", "muni_scrape", "html_regex",
    "gap_filler", "admin_manual", "agent_batch",
})

_PROBE_FLAG_THRESHOLD = 0.70
_AGENT_FLAG_THRESHOLD = 0.85


def _should_flag(source: str, confidence: float) -> bool:
    """Return True if this row should land in the review queue."""
    if source == "pattern_probe":
        return confidence < _PROBE_FLAG_THRESHOLD
    if source == "agent_sonnet":
        return confidence < _AGENT_FLAG_THRESHOLD
    # Everything else is upstream-trusted; don't flag.
    return False


# ── SQL helpers ───────────────────────────────────────────────────

async def upsert_social(
    db: Database,
    politician_id: str,
    platform_hint: Optional[str],
    url: str,
    *,
    source: str,
    confidence: float = 1.0,
    evidence_url: Optional[str] = None,
) -> Optional[CanonicalSocial]:
    """Canonicalise `url` and upsert a politician_socials row.

    Returns the CanonicalSocial on success, None if the URL couldn't be
    canonicalised or had no handle (platform='other').

    Writes a `social_added` politician_changes row the first time this
    (politician_id, platform, handle) tuple is seen.

    Args:
      source: where this URL came from. See politician_socials.source column
        comment (migration 0026) for allowed values.
      confidence: 0.0-1.0. Upstream feeds pass 1.0; Tier-2 probe passes the
        name-match score; Tier-3 agent passes the agent's self-reported
        confidence.
      evidence_url: page the discovery process verified against (Wikipedia
        article, bsky profile, og:title source). Null for upstream feeds
        where the feed is itself the evidence.

    The row's flagged_low_confidence flag is derived from confidence + source:
      pattern_probe:  flagged below 0.70
      agent_sonnet:   flagged below 0.85
      everything else: never flagged (upstream feeds are trusted)
    """
    canon = canonicalize(platform_hint, url)
    if canon is None or canon.platform == "other" or not canon.handle:
        return None

    # Clamp confidence just in case a caller hands us something wild.
    conf = max(0.0, min(1.0, float(confidence)))
    flagged = _should_flag(source, conf)

    # Check existence first so we only log 'social_added' once per handle.
    existing = await db.fetchrow(
        """
        SELECT id, confidence FROM politician_socials
        WHERE politician_id = $1 AND platform = $2 AND LOWER(handle) = LOWER($3)
        """,
        politician_id, canon.platform, canon.handle,
    )

    # On conflict: only overwrite provenance if the new confidence beats
    # the existing one. Protects Tier-1 legacy rows from being clobbered
    # by a lower-confidence probe or agent hit.
    await db.execute(
        """
        INSERT INTO politician_socials
            (politician_id, platform, handle, url,
             source, confidence, evidence_url, flagged_low_confidence, discovered_at)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, now())
        ON CONFLICT (politician_id, platform, LOWER(handle)) DO UPDATE SET
            url = EXCLUDED.url,
            source = CASE
                WHEN EXCLUDED.confidence > COALESCE(politician_socials.confidence, 0)
                THEN EXCLUDED.source
                ELSE politician_socials.source
            END,
            confidence = GREATEST(COALESCE(politician_socials.confidence, 0), EXCLUDED.confidence),
            evidence_url = CASE
                WHEN EXCLUDED.confidence > COALESCE(politician_socials.confidence, 0)
                THEN EXCLUDED.evidence_url
                ELSE politician_socials.evidence_url
            END,
            flagged_low_confidence = CASE
                WHEN EXCLUDED.confidence > COALESCE(politician_socials.confidence, 0)
                THEN EXCLUDED.flagged_low_confidence
                ELSE politician_socials.flagged_low_confidence
            END,
            updated_at = now()
        """,
        politician_id, canon.platform, canon.handle, canon.url,
        source, conf, evidence_url, flagged,
    )

    if existing is None:
        try:
            await db.execute(
                """
                INSERT INTO politician_changes
                    (politician_id, change_type, new_value, severity)
                VALUES ($1, 'social_added', $2, 'info')
                """,
                politician_id,
                orjson.dumps({
                    "platform": canon.platform,
                    "handle": canon.handle,
                    "url": canon.url,
                }).decode(),
            )
        except Exception as exc:
            # Logging the change is best-effort; don't let it break ingestion.
            log.warning("failed to log social_added change: %s", exc)

    return canon


# ── Bulk import from agent findings ──────────────────────────────

async def bulk_import_socials(
    db: Database,
    *,
    input_path: str,
    source: str = "agent_batch",
) -> None:
    """Import agent-discovered social URLs from a JSONL file.

    Each line must be a JSON object of the form:
        {"politician_id": "<uuid>", "urls": ["https://twitter.com/...", ...]}

    Optional keys (ignored by the importer, carried for audit):
        {"name": "...", "source": "agent_batch_NNN"}

    Every URL flows through `upsert_social`, which enforces the canonical-
    isation map and writes a `social_added` change row on first insert.
    Unknown platforms (YouTube handle that 404s, random campaign domain)
    silently no-op rather than polluting the table.

    `source` is applied to every row inserted. Defaults to 'agent_batch'
    which is trusted (no confidence flag). Callers that want to land
    low-confidence rows should prefer the Tier-3 agent driver which sets
    source='agent_sonnet' with per-URL confidence.
    """
    path = str(input_path)
    batch_source = source
    processed = 0
    pols_seen: set[str] = set()
    added: Counter[str] = Counter()
    duplicates = 0
    rejected = 0
    errors = 0

    try:
        with open(path, "r", encoding="utf-8") as fh:
            lines = [ln for ln in fh.readlines() if ln.strip()]
    except FileNotFoundError:
        console.print(f"[red]bulk-import: file not found: {path}[/red]")
        return

    console.print(f"[cyan]bulk-import: reading {len(lines)} lines from {path}[/cyan]")

    for raw in lines:
        try:
            obj = orjson.loads(raw)
        except Exception as exc:
            errors += 1
            log.warning("skip malformed line: %s", exc)
            continue

        pid = obj.get("politician_id")
        urls = obj.get("urls") or []
        if not pid or not isinstance(urls, list):
            rejected += 1
            continue
        pols_seen.add(pid)

        for url in urls:
            if not isinstance(url, str):
                continue
            processed += 1
            # Pre-check existence so we can distinguish net-new adds from
            # duplicates in the summary. `upsert_social` is idempotent so
            # this is safe/cheap.
            canon = canonicalize(None, url)
            if canon is None or canon.platform == "other" or not canon.handle:
                rejected += 1
                continue
            pre = await db.fetchrow(
                """
                SELECT 1 FROM politician_socials
                WHERE politician_id = $1 AND platform = $2 AND LOWER(handle) = LOWER($3)
                """,
                pid, canon.platform, canon.handle,
            )
            try:
                result = await upsert_social(
                    db, pid, None, url,
                    source=batch_source,
                )
            except Exception as exc:
                errors += 1
                log.warning("upsert failed for %s %s: %s", pid, url, exc)
                continue
            if result is None:
                rejected += 1
            elif pre is None:
                added[result.platform] += 1
            else:
                duplicates += 1

    console.print(
        f"[green]✓ bulk-import done:[/green] {processed} urls, "
        f"{len(pols_seen)} politicians touched, "
        f"{sum(added.values())} new, {duplicates} dup, "
        f"{rejected} rejected, {errors} errors"
    )
    if added:
        for plat, n in added.most_common():
            console.print(f"  {plat:<10} +{n}")


# ── Step 1: normalizer ────────────────────────────────────────────

async def normalize_socials(db: Database) -> None:
    """Explode politicians.social_urls JSONB into politician_socials rows."""
    rows = await db.fetch(
        """
        SELECT id, social_urls
        FROM politicians
        WHERE social_urls IS NOT NULL AND social_urls::text <> '{}'
        """
    )
    console.print(f"[cyan]Normalising {len(rows)} politicians with social_urls[/cyan]")

    counts: Counter[str] = Counter()
    skipped = 0
    for row in rows:
        pid = str(row["id"])
        payload = row["social_urls"]
        if isinstance(payload, (bytes, bytearray)):
            payload = payload.decode()
        if isinstance(payload, str):
            try:
                payload = orjson.loads(payload)
            except Exception:
                skipped += 1
                continue
        if not isinstance(payload, dict):
            skipped += 1
            continue
        for platform_hint, url in payload.items():
            if not isinstance(url, str) or not url:
                continue
            try:
                canon = await upsert_social(
                    db, pid, platform_hint, url,
                    source="legacy_jsonb",
                )
            except Exception as exc:
                log.warning("upsert failed for %s %s: %s", pid, url, exc)
                continue
            if canon is not None:
                counts[canon.platform] += 1
            else:
                counts["other"] += 1

    if not counts and not rows:
        console.print("[yellow]No social_urls to normalize yet[/yellow]")
    else:
        console.print("[green]Normalised counts by platform:[/green]")
        for plat, n in counts.most_common():
            console.print(f"  {plat:<10} {n}")
        if skipped:
            console.print(f"[yellow]Skipped {skipped} malformed JSONB rows[/yellow]")


# ── Step 3: liveness worker ───────────────────────────────────────

_LIVE_CODES = {200, 301, 302, 303, 307, 308}
_DEAD_CODES = {404, 410}


async def verify_liveness(
    db: Database,
    *,
    limit: int = 500,
    stale_hours: int = 168,
) -> None:
    """HEAD/GET each social URL; update is_live / last_verified_at."""
    rows = await db.fetch(
        f"""
        SELECT id, politician_id, platform, handle, url, is_live
        FROM politician_socials
        WHERE last_verified_at IS NULL
           OR last_verified_at < now() - interval '{int(stale_hours)} hours'
        ORDER BY last_verified_at NULLS FIRST, updated_at ASC
        LIMIT $1
        """,
        int(limit),
    )

    if not rows:
        console.print("[yellow]No social rows due for liveness check[/yellow]")
        return

    console.print(f"[cyan]Verifying liveness for {len(rows)} social rows[/cyan]")

    sem = asyncio.Semaphore(8)
    stats = Counter()
    flips_to_dead: list[dict] = []

    progress = Progress(
        SpinnerColumn(),
        TextColumn("{task.description}"),
        BarColumn(),
        TextColumn("{task.completed}/{task.total}"),
        TimeElapsedColumn(),
    )

    async with httpx.AsyncClient(
        timeout=httpx.Timeout(10.0, connect=5.0),
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        },
        follow_redirects=True,
    ) as client:
        with progress:
            task = progress.add_task("Checking", total=len(rows))

            async def handle(row) -> None:
                async with sem:
                    prior_live = row["is_live"]
                    if row["platform"] == "bluesky":
                        classification, status = await _classify_bluesky(
                            client, row["handle"], row["url"],
                        )
                    else:
                        classification, status = await _classify(client, row["url"])
                    try:
                        if classification == "live":
                            await db.execute(
                                """
                                UPDATE politician_socials
                                   SET is_live = true,
                                       last_verified_at = now(),
                                       updated_at = now()
                                 WHERE id = $1
                                """,
                                row["id"],
                            )
                            stats["live"] += 1
                        elif classification == "dead":
                            await db.execute(
                                """
                                UPDATE politician_socials
                                   SET is_live = false,
                                       last_verified_at = now(),
                                       updated_at = now()
                                 WHERE id = $1
                                """,
                                row["id"],
                            )
                            stats["dead"] += 1
                            if prior_live is True:
                                flips_to_dead.append({
                                    "politician_id": str(row["politician_id"]),
                                    "platform": row["platform"],
                                    "handle": row["handle"],
                                    "url": row["url"],
                                    "status": status,
                                })
                        else:  # transient
                            await db.execute(
                                """
                                UPDATE politician_socials
                                   SET last_verified_at = now(),
                                       updated_at = now()
                                 WHERE id = $1
                                """,
                                row["id"],
                            )
                            stats["transient"] += 1
                    except Exception as exc:
                        log.warning("update failed for %s: %s", row["id"], exc)
                    finally:
                        progress.update(task, advance=1)

            await asyncio.gather(*(handle(r) for r in rows))

    # Write social_dead change rows for live->dead flips.
    for ev in flips_to_dead:
        try:
            await db.execute(
                """
                INSERT INTO politician_changes
                    (politician_id, change_type, old_value, severity)
                VALUES ($1, 'social_dead', $2, 'warning')
                """,
                ev["politician_id"],
                orjson.dumps({
                    "platform": ev["platform"],
                    "handle": ev["handle"],
                    "url": ev["url"],
                    "status": ev["status"],
                }).decode(),
            )
        except Exception as exc:
            log.warning("failed to log social_dead change: %s", exc)

    console.print(
        f"[green]✓ live={stats['live']} dead={stats['dead']} "
        f"transient={stats['transient']} flips_to_dead={len(flips_to_dead)}[/green]"
    )


_BSKY_PROFILE_API = "https://public.api.bsky.app/xrpc/app.bsky.actor.getProfile"


async def _classify_bluesky(
    client: httpx.AsyncClient,
    handle: Optional[str],
    url: str,
) -> tuple[str, Optional[int]]:
    """Bluesky-specific liveness check.

    bsky.app/profile/<handle> is a JS-rendered SPA route that returns HTTP
    404 to unauthenticated HEAD/GET from bot User-Agents, even for live
    accounts. The authoritative signal is the App View API
    (public.api.bsky.app/xrpc/app.bsky.actor.getProfile) which returns a
    JSON profile for any live account and 400/404 for missing ones.

    Fall back to the generic `_classify` if we have no handle to look up.
    """
    h = (handle or "").strip()
    if not h:
        return await _classify(client, url)
    try:
        r = await client.get(_BSKY_PROFILE_API, params={"actor": h})
    except httpx.HTTPError:
        return "transient", None
    if r.status_code == 200:
        return "live", 200
    if r.status_code in (400, 404):
        # App View returns 400 "Profile not found" for missing handles.
        return "dead", r.status_code
    return "transient", r.status_code


async def _classify(client: httpx.AsyncClient, url: str) -> tuple[str, Optional[int]]:
    """Return ('live'|'dead'|'transient', final_status_or_None).

    Strategy: try HEAD first; if the server rejects HEAD (many do) or returns
    anything outside live/dead buckets, fall back to GET.
    """
    status: Optional[int] = None
    try:
        r = await client.head(url)
        status = r.status_code
        if status in _LIVE_CODES:
            return "live", status
        if status in _DEAD_CODES:
            return "dead", status
        # Many sites return 403/405/429 for HEAD — try GET.
    except httpx.HTTPError:
        status = None
    try:
        r = await client.get(url)
        status = r.status_code
        if status in _LIVE_CODES:
            return "live", status
        if status in _DEAD_CODES:
            return "dead", status
        return "transient", status
    except httpx.HTTPError:
        return "transient", status
