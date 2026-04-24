"""Nova Scotia MLA slug-stamp ingester.

At project entry only 10 / 55 seated NS MLAs have
``politicians.nslegislature_slug`` populated — those are the members
who happened to sponsor one of the 25 bills whose HTML made it through
the NS WAF budget. The remaining 45 MLAs have no canonical slug,
which breaks Hansard speaker resolution (which keys on the
``/members/profiles/<slug>`` href embedded in every speech anchor).

This module closes that gap by harvesting ``(slug, displayed_name)``
pairs directly from the current-session Hansard sittings — the same
pages ``ingest-ns-hansard`` consumes. Each speech turn contains

    <a href="/members/profiles/<slug>" class="hsd_mla" ...>NAME</a>

so a small sample of sittings (default: 5 newest) is enough to cover
every MLA who has spoken this session. For each harvested pair we
normalise the displayed NAME and match it against existing
``politicians`` rows filtered to NS / provincial / active; on a unique
match we stamp ``nslegislature_slug``. No new politician rows are
inserted — they already exist via Open North's ``nova_scotia_mlas``
set.

This is a one-shot refresher intended to run before
``ingest-ns-hansard``. Re-running is idempotent — already-matching
slugs stay put; new matches get stamped.
"""
from __future__ import annotations

import asyncio
import logging
import re
import unicodedata
from dataclasses import dataclass
from typing import Optional

import httpx

from ..db import Database

log = logging.getLogger(__name__)

SESSION_INDEX_URL = (
    "https://nslegislature.ca/legislative-business/hansard-debates/{parliament}-{session}"
)
REQUEST_TIMEOUT = 60
REQUEST_DELAY_SECONDS = 1.5
HEADERS = {
    "User-Agent": "CanadianPoliticalDataBot/1.0 (+https://canadianpoliticaldata.ca)",
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "en-CA,en;q=0.9",
}

# Anchor pattern for a speech-turn speaker link (inside a <p>).
# Mirrors ns_hansard_parse._TURN_OPENER_RE but narrower — we only want
# profile slugs (speaker-role anchors point at /members/speaker/).
_PROFILE_ANCHOR_RE = re.compile(
    r"<p\b[^>]*>"
    # Session 65-1 self-closes the name anchor; 63-3 and earlier leave
    # it open (``<a name="x">`` with no ``</a>`` before the href).
    r"\s*(?:<a\s+name=\"[^\"]+\"[^>]*>\s*(?:</a>\s*)?)?"
    r"<a\b[^>]*\bhref=\"/members/profiles/(?P<slug>[^\"]+)\""
    r"[^>]*>(?P<name>[^<]+)</a>",
    re.IGNORECASE,
)

# Sitting links on the session index page.
# e.g. href="/legislative-business/hansard-debates/assembly-65-session-1/house_26apr09"
_SITTING_HREF_RE = re.compile(
    r"href=\"(?P<href>/legislative-business/hansard-debates/"
    r"assembly-\d+-session-\d+/house_\d{2}[a-z]{3}\d{2})\"",
    re.IGNORECASE,
)

# Honorific stripper for the harvested name ("HON. DEREK MOMBOURQUETTE"
# → "DEREK MOMBOURQUETTE").
_HONORIFIC_RE = re.compile(
    r"^(?:hon\.|hon|honourable|mr\.|mrs\.|ms\.|miss\.?|dr\.?|madam|sir)\s+",
    re.IGNORECASE,
)

_WS_RE = re.compile(r"\s+")


def _norm_name(name: str) -> str:
    """Lowercase + accent-fold + strip honorific + collapse whitespace.

    ``"HON. DEREK MOMBOURQUETTE"`` → ``"derek mombourquette"``.
    ``"Élise LEBLANC"`` → ``"elise leblanc"``.
    """
    if not name:
        return ""
    text = _HONORIFIC_RE.sub("", name.strip())
    text = unicodedata.normalize("NFKD", text.replace(" ", " "))
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.lower()
    text = re.sub(r"[^\w\s-]", " ", text)
    return _WS_RE.sub(" ", text).strip()


# ── Discovery ──────────────────────────────────────────────────────

@dataclass
class SittingRef:
    href: str                # e.g. /legislative-business/.../house_26apr09
    url: str                 # absolute URL


async def _get(client: httpx.AsyncClient, url: str) -> httpx.Response:
    r = await client.get(url, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    return r


async def discover_sitting_urls(
    client: httpx.AsyncClient, *, parliament: int, session: int,
) -> list[SittingRef]:
    """Walk the session index and return sitting URLs, newest-first."""
    index_url = SESSION_INDEX_URL.format(
        parliament=parliament, session=session,
    )
    r = await _get(client, index_url)
    html = r.text
    base = "https://nslegislature.ca"
    seen: set[str] = set()
    refs: list[SittingRef] = []
    for m in _SITTING_HREF_RE.finditer(html):
        href = m.group("href")
        if href in seen:
            continue
        seen.add(href)
        refs.append(SittingRef(href=href, url=base + href))
    return refs


# ── Harvest ────────────────────────────────────────────────────────

@dataclass
class SlugObservation:
    slug: str
    displayed_names: set[str]    # every observed anchor text for this slug


def harvest_slugs(html: str, seen: dict[str, SlugObservation]) -> None:
    """Populate ``seen`` with every (slug, anchor_text) pair found.

    Called once per sitting HTML. Duplicates within a page are
    harmless — we keep a set of observed names per slug.
    """
    for m in _PROFILE_ANCHOR_RE.finditer(html):
        slug = m.group("slug").strip().lower()
        name = _WS_RE.sub(" ", m.group("name")).strip()
        if not slug or not name:
            continue
        obs = seen.setdefault(slug, SlugObservation(slug=slug, displayed_names=set()))
        obs.displayed_names.add(name)


# ── Match + stamp ──────────────────────────────────────────────────

@dataclass
class IngestStats:
    sittings_scanned: int = 0
    slugs_harvested: int = 0
    already_correct: int = 0       # slug on politicians row already matched
    stamped: int = 0               # slug newly written
    conflict: int = 0              # politicians row already has a DIFFERENT slug
    no_match: int = 0              # no NS politician matches the harvested name
    ambiguous: int = 0             # multiple NS politicians match


async def _load_ns_politicians(db: Database) -> list[dict]:
    """Load NS provincial politicians (seated + departed MLAs).

    Historical backfill sessions contain speaker anchors for MLAs who
    have since left office; Open North marks those rows
    ``is_active=false`` but they're still valid slug-join targets. The
    old current-seat-only filter would drop those matches and leave
    historical slugs unstamped.
    """
    rows = await db.fetch(
        """
        SELECT id::text         AS id,
               name,
               first_name,
               last_name,
               nslegislature_slug,
               constituency_name,
               is_active
          FROM politicians
         WHERE province_territory = 'NS'
           AND level              = 'provincial'
        """
    )
    return [dict(r) for r in rows]


def _build_name_index(rows: list[dict]) -> dict[str, list[dict]]:
    """Index politicians by every useful normalised form of their name.

    Returns ``{normalised_key: [row, …]}``. Keys include full name,
    first+last, last-name-only. Multi-hit keys are legitimate when two
    MLAs share a surname; the caller's match logic treats those as
    ambiguous unless a more specific key disambiguates.
    """
    idx: dict[str, list[dict]] = {}
    for r in rows:
        full = _norm_name(r["name"] or "")
        if full:
            idx.setdefault(full, []).append(r)
        fl = _norm_name(f"{r['first_name'] or ''} {r['last_name'] or ''}")
        if fl and fl != full:
            idx.setdefault(fl, []).append(r)
        last = _norm_name(r["last_name"] or "")
        if last:
            idx.setdefault(last, []).append(r)
    return idx


def _match(
    index: dict[str, list[dict]], displayed_name: str,
) -> tuple[Optional[dict], str]:
    """Resolve a harvested displayed name to a unique NS politician row.

    Tries full-name, then last-name-only. Returns (row, status) where
    status is one of: 'matched', 'ambiguous', 'no_match'.
    """
    key_full = _norm_name(displayed_name)
    if key_full:
        hits = index.get(key_full, [])
        if len(hits) == 1:
            return hits[0], "matched"
        if len(hits) > 1:
            return None, "ambiguous"
    # Fall back to last-token-only match.
    tokens = key_full.split()
    if tokens:
        last = tokens[-1]
        hits = index.get(last, [])
        if len(hits) == 1:
            return hits[0], "matched"
        if len(hits) > 1:
            return None, "ambiguous"
    return None, "no_match"


async def ingest(
    db: Database,
    *,
    parliament: int = 65,
    session: int = 1,
    sample_sittings: int = 5,
) -> IngestStats:
    """Harvest slugs from the newest ``sample_sittings`` sittings, match
    to existing NS politicians, stamp ``nslegislature_slug``.

    Defaults are tuned for session 65-1 (current). Pass higher
    ``sample_sittings`` for historical sessions where each sitting
    covers fewer distinct speakers.
    """
    stats = IngestStats()
    seen: dict[str, SlugObservation] = {}

    async with httpx.AsyncClient(
        headers=HEADERS, follow_redirects=True,
    ) as client:
        refs = await discover_sitting_urls(
            client, parliament=parliament, session=session,
        )
        # Newest sittings listed first on the index; trim to the sample.
        refs = refs[:sample_sittings]
        log.info(
            "ns_mlas: harvesting slugs from %d sittings (parliament=%d session=%d)",
            len(refs), parliament, session,
        )

        for ref in refs:
            try:
                r = await _get(client, ref.url)
                harvest_slugs(r.text, seen)
                stats.sittings_scanned += 1
            except Exception as exc:
                log.warning("ns_mlas: failed to fetch %s: %s", ref.url, exc)
                continue
            await asyncio.sleep(REQUEST_DELAY_SECONDS)

    stats.slugs_harvested = len(seen)
    log.info("ns_mlas: harvested %d distinct slugs", stats.slugs_harvested)

    # Match + stamp.
    pols = await _load_ns_politicians(db)
    index = _build_name_index(pols)
    log.info("ns_mlas: loaded %d seated NS MLAs for matching", len(pols))

    for obs in seen.values():
        # For each slug, try matching using every observed displayed
        # name — the speech-turn spellings (ALL CAPS with honorific)
        # resolve best, so we prefer a longer name if multiple are
        # observed.
        candidates = sorted(obs.displayed_names, key=lambda s: -len(s))
        matched_row: Optional[dict] = None
        outcome = "no_match"
        for name in candidates:
            row, status = _match(index, name)
            if status == "matched":
                matched_row = row
                outcome = "matched"
                break
            if status == "ambiguous":
                outcome = "ambiguous"
        if outcome == "ambiguous" and matched_row is None:
            stats.ambiguous += 1
            log.info("ns_mlas: slug=%s ambiguous (names: %s)", obs.slug, candidates)
            continue
        if outcome == "no_match":
            stats.no_match += 1
            log.info("ns_mlas: slug=%s no_match (names: %s)", obs.slug, candidates)
            continue
        assert matched_row is not None
        existing = matched_row["nslegislature_slug"]
        if existing == obs.slug:
            stats.already_correct += 1
            continue
        if existing and existing != obs.slug:
            stats.conflict += 1
            log.warning(
                "ns_mlas: slug conflict on %s — existing=%r, harvested=%r",
                matched_row["name"], existing, obs.slug,
            )
            continue
        await db.execute(
            """
            UPDATE politicians
               SET nslegislature_slug = $1,
                   updated_at         = now()
             WHERE id = $2::uuid
            """,
            obs.slug, matched_row["id"],
        )
        stats.stamped += 1
        log.info(
            "ns_mlas: stamped slug=%s onto %s (id=%s)",
            obs.slug, matched_row["name"], matched_row["id"],
        )

    log.info(
        "ns_mlas done: sittings=%d harvested=%d stamped=%d already=%d "
        "conflict=%d no_match=%d ambiguous=%d",
        stats.sittings_scanned, stats.slugs_harvested, stats.stamped,
        stats.already_correct, stats.conflict, stats.no_match, stats.ambiguous,
    )
    return stats
