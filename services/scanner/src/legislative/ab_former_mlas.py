"""AB historical MLA roster backfill.

The AB Legislative Assembly member-listing page serves every MLA
who's ever served, per-legislature, via a ``?legl=N`` query parameter:

    https://www.assembly.ab.ca/members/members-of-the-legislative-assembly?legl=1
      → 29 MLAs of the 1st Legislature (1906-1909)
    ...?legl=31
      → 91 MLAs of the current 31st Legislature (2023-)

We iterate ``legl=1..31``, collect every ``(mid, name)`` pair plus
the header-advertised year range for that legislature, and:

1. INSERT or UPDATE politicians keyed on ``ab_assembly_mid`` (the
   zero-padded integer the Assembly assigns once and never reuses).
   First-seen historical MLAs land with ``is_active = false``.
2. INSERT ``politician_terms`` rows per (politician, legislature)
   so downstream resolvers can filter by speech date rather than
   treating the full 120-year roster as a flat lookup.

Why this matters: without historical MLAs, ``load_speaker_lookup``
in ab_hansard.py only knows 91 people for a 439k-speech corpus that
stretches back to 2000, so the resolver can only anchor ~42 % of
speeches to a politician. The other 58 % are speakers who are real
MLAs but retired before the current roster was captured. Adding the
historical roster unblocks them without any re-fetch of Hansard.

Idempotency: politicians is upserted on ``ab_assembly_mid``;
politician_terms is upserted on ``(politician_id, office,
started_at)``. A full re-run produces no net row change.
"""
from __future__ import annotations

import asyncio
import logging
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Optional

import httpx

from ..db import Database

log = logging.getLogger(__name__)

ROSTER_URL_TMPL = (
    "https://www.assembly.ab.ca/members/members-of-the-legislative-assembly?legl={legl}"
)
HEADERS = {
    "User-Agent": "CanadianPoliticalDataBot/1.0 (+https://canadianpoliticaldata.ca)",
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "en-CA,en;q=0.9",
}
REQUEST_TIMEOUT = 60
REQUEST_DELAY_SECONDS = 1.0  # Be polite to assembly.ab.ca

# "31st Legislature (2023 - ...)" or "30th Legislature (2019 - 2023)"
_LEGL_HEADER_RE = re.compile(
    r"(?P<n>\d+)(?:st|nd|rd|th)\s+Legislature\s*\(\s*"
    r"(?P<start>\d{4})\s*-\s*(?P<end>\d{4}|\.\.\.)\s*\)",
    re.IGNORECASE,
)

# Same pattern as ab_mlas._MLA_LINK_RE. Captures mid + anchor text.
_MLA_LINK_RE = re.compile(
    r'href="[^"]*/member-information\?mid=(?P<mid>\d+)[^"]*"[^>]*>'
    r"\s*(?P<name>[^<]+?)\s*</a>",
    re.IGNORECASE | re.DOTALL,
)

_HONORIFICS_RE = re.compile(
    r"\b(?:member|honourable|honorable|hon\.?|mr\.?|mrs\.?|ms\.?|"
    r"miss\.?|dr\.?|prof\.?|premier|minister|speaker|deputy|"
    r"kc|qc)\b",
    re.IGNORECASE,
)


@dataclass
class Stats:
    legls_scanned: int = 0
    mid_legl_pairs_seen: int = 0
    politicians_inserted: int = 0
    politicians_updated: int = 0
    terms_inserted: int = 0
    terms_skipped: int = 0
    missing_legl_dates: list[int] = field(default_factory=list)


def _strip_titles(s: str) -> str:
    cleaned = _HONORIFICS_RE.sub(" ", s or "")
    parts = [p.strip() for p in cleaned.split(",")]
    parts = [p for p in parts if p]
    return ", ".join(parts)


def _split_last_first(name: str) -> tuple[str, str, str]:
    """Return (full_display, first_name, last_name).

    Roster names come as ``"Smith, John"`` or occasionally
    ``"Smith, KC, Honourable John"`` where the post-nominal wedges
    between surname and forename. Strip titles first, then split on
    the first comma.
    """
    cleaned = _strip_titles(name)
    if "," in cleaned:
        last, first = [p.strip() for p in cleaned.split(",", 1)]
        display = f"{first} {last}".strip()
        return display, first, last
    # No comma — treat the whole thing as given name and leave
    # last_name empty. Rare edge case; log-worthy if it happens.
    return cleaned, cleaned, ""


async def _get_with_retry(client: httpx.AsyncClient, url: str) -> httpx.Response:
    last_exc: Optional[BaseException] = None
    for attempt, delay in enumerate((0, 2, 4, 8)):
        if delay:
            await asyncio.sleep(delay)
        try:
            r = await client.get(url, timeout=REQUEST_TIMEOUT)
            if r.status_code >= 500:
                log.warning("AB former-MLAs: %s returned %d, retrying", url, r.status_code)
                continue
            r.raise_for_status()
            return r
        except (httpx.ReadTimeout, httpx.ConnectError, httpx.ReadError) as e:
            last_exc = e
            log.warning("AB former-MLAs: transient error on %s: %s", url, e)
    raise RuntimeError(f"unreachable: {url} (last_exc={last_exc})")


@dataclass
class _LeglPage:
    legl: int
    start_year: int
    end_year: Optional[int]  # None for ongoing
    members: list[tuple[str, str]]  # (mid, raw_name)


def _parse_legl_page(html: str, legl: int) -> _LeglPage:
    start_year: Optional[int] = None
    end_year: Optional[int] = None
    for m in _LEGL_HEADER_RE.finditer(html):
        if int(m.group("n")) == legl:
            start_year = int(m.group("start"))
            end_raw = m.group("end")
            end_year = None if end_raw == "..." else int(end_raw)
            break

    # Fall back to *any* header if the self-referencing one isn't on
    # the page — shouldn't happen, but avoids losing data on format
    # drift. Log it so we notice.
    if start_year is None:
        m = _LEGL_HEADER_RE.search(html)
        if m and int(m.group("n")) == legl:
            start_year = int(m.group("start"))
            end_raw = m.group("end")
            end_year = None if end_raw == "..." else int(end_raw)

    # Collect (mid, name) pairs unique by mid. Same mid repeats for
    # the photo link + the name link on the same card.
    seen: dict[str, str] = {}
    for m in _MLA_LINK_RE.finditer(html):
        mid = m.group("mid")
        name = re.sub(r"\s+", " ", m.group("name") or "").strip()
        if not name:
            continue
        prev = seen.get(mid)
        if prev is None or len(name) > len(prev):
            seen[mid] = name

    members = list(seen.items())
    return _LeglPage(
        legl=legl,
        start_year=start_year or 0,
        end_year=end_year,
        members=members,
    )


def _year_to_dt_start(year: int) -> datetime:
    return datetime(year, 1, 1, tzinfo=timezone.utc)


def _year_to_dt_end(year: int) -> datetime:
    # End-of-year UTC. Paired with approximate start-of-year; gives
    # the resolver a clean yearly window per legislature.
    return datetime(year, 12, 31, 23, 59, 59, tzinfo=timezone.utc)


async def ingest_ab_former_mlas(
    db: Database,
    *,
    from_legl: int = 1,
    until_legl: int = 31,
    delay: float = REQUEST_DELAY_SECONDS,
) -> Stats:
    """Enumerate legl=N pages, upsert politicians + politician_terms.

    Parameters
    ----------
    from_legl, until_legl
        Inclusive legislature range. 1..31 covers all of AB history
        1906-present; most runs will just use defaults.
    delay
        Seconds between page fetches.
    """
    stats = Stats()

    # Pass 1: fetch all pages.
    pages: list[_LeglPage] = []
    async with httpx.AsyncClient(headers=HEADERS, follow_redirects=True) as client:
        for legl in range(from_legl, until_legl + 1):
            url = ROSTER_URL_TMPL.format(legl=legl)
            r = await _get_with_retry(client, url)
            page = _parse_legl_page(r.text, legl)
            if page.start_year == 0:
                log.warning("AB former-MLAs: no date range on legl=%d page, skipping", legl)
                stats.missing_legl_dates.append(legl)
                continue
            pages.append(page)
            stats.legls_scanned += 1
            stats.mid_legl_pairs_seen += len(page.members)
            log.info(
                "AB former-MLAs: legl=%d (%d-%s) members=%d",
                legl, page.start_year, page.end_year or "present", len(page.members),
            )
            if legl < until_legl:
                await asyncio.sleep(delay)

    if not pages:
        log.warning("AB former-MLAs: no pages ingested (missing_legl_dates=%s)", stats.missing_legl_dates)
        return stats

    # Pass 2: upsert politicians (one row per unique mid).
    # Keep the longest-seen display name to tolerate template drift
    # like occasional "Smith, Honourable John Q" on some pages.
    mid_to_name: dict[str, str] = {}
    for page in pages:
        for mid, name in page.members:
            prev = mid_to_name.get(mid)
            if prev is None or len(name) > len(prev):
                mid_to_name[mid] = name

    # is_active=TRUE only for members of the *actual* current legislature
    # — the one whose end-year is None ("..." on the index page), not
    # whichever legl happens to be --until-legl. Partial scans of
    # historical ranges should not flip is_active.
    current_page = next((p for p in pages if p.end_year is None), None)
    current_mids: set[str] = (
        {mid for mid, _ in current_page.members} if current_page else set()
    )

    for mid, raw_name in mid_to_name.items():
        display, first, last = _split_last_first(raw_name)
        if not display:
            continue
        is_active = mid in current_mids
        row = await db.fetchrow(
            """
            INSERT INTO politicians
                (name, first_name, last_name, level, province_territory,
                 ab_assembly_mid, is_active, source_id)
            VALUES
                ($1, $2, $3, 'provincial', 'AB', $4, $5,
                 'assembly.ab.ca:former-mlas:mid=' || $4)
            ON CONFLICT (ab_assembly_mid) WHERE ab_assembly_mid IS NOT NULL
            DO UPDATE SET
                -- Only enrich; never overwrite a richer name or
                -- flip an already-active row to inactive.
                name       = COALESCE(NULLIF(politicians.name, ''),       EXCLUDED.name),
                first_name = COALESCE(NULLIF(politicians.first_name, ''), EXCLUDED.first_name),
                last_name  = COALESCE(NULLIF(politicians.last_name, ''),  EXCLUDED.last_name),
                updated_at = now()
            RETURNING id, (xmax = 0) AS inserted
            """,
            display, first, last, mid, is_active,
        )
        if row["inserted"]:
            stats.politicians_inserted += 1
        else:
            stats.politicians_updated += 1

    # Pass 3: upsert terms — one per (mid, legl) pair.
    # Idempotency: if a term row already exists at the same
    # started_at for this politician + 'MLA', skip. We don't try to
    # merge overlapping historical terms (a politician who served
    # in consecutive legislatures gets one term per legislature);
    # downstream queries union/group as needed.
    for page in pages:
        start_dt = _year_to_dt_start(page.start_year)
        end_dt = _year_to_dt_end(page.end_year) if page.end_year else None
        for mid, _ in page.members:
            pol_row = await db.fetchrow(
                "SELECT id FROM politicians WHERE ab_assembly_mid = $1",
                mid,
            )
            if pol_row is None:
                continue
            existing = await db.fetchrow(
                """
                SELECT 1 FROM politician_terms
                 WHERE politician_id = $1 AND office = 'MLA'
                   AND started_at = $2
                """,
                pol_row["id"], start_dt,
            )
            if existing is not None:
                stats.terms_skipped += 1
                continue
            await db.execute(
                """
                INSERT INTO politician_terms
                    (politician_id, office, level, province_territory,
                     started_at, ended_at, source)
                VALUES
                    ($1, 'MLA', 'provincial', 'AB', $2, $3,
                     'assembly.ab.ca:legl-' || $4)
                """,
                pol_row["id"], start_dt, end_dt, str(page.legl),
            )
            stats.terms_inserted += 1

    log.info(
        "AB former-MLAs: legls=%d mid_legl_pairs=%d politicians_inserted=%d "
        "politicians_updated=%d terms_inserted=%d terms_skipped=%d missing_dates=%s",
        stats.legls_scanned, stats.mid_legl_pairs_seen,
        stats.politicians_inserted, stats.politicians_updated,
        stats.terms_inserted, stats.terms_skipped,
        stats.missing_legl_dates,
    )
    return stats


# ── Post-pass speaker resolution ──────────────────────────────────


@dataclass
class ResolveStats:
    scanned: int = 0
    updated: int = 0
    still_unresolved: int = 0  # sum of "no candidate" + "ambiguous"


async def resolve_ab_speakers(
    db: Database, *, limit: Optional[int] = None,
) -> ResolveStats:
    """Re-resolve politician_id on AB speeches with NULL politician_id,
    using the new historical-MLA roster.

    The match is keyed on (normalized_surname, legislature_number).
    AB Hansard already parses both fields out of the PDF at ingest
    time and stores them in ``speeches.raw->'ab_hansard'``. The
    historical-roster ingest stamps politician_terms with
    ``source = 'assembly.ab.ca:legl-N'``, so a pure-SQL join
    resolves each speech against the MLAs who served in that
    legislature — no cross-legislature bleed, no surname ambiguity
    from historical same-surname figures.

    Any speech whose surname+legl match hits >1 MLA (rare but real
    for common surnames within a single legislature, e.g. two
    "Smith"s sitting at once) gets left NULL and counted as
    ``still_ambiguous`` — the parser doesn't carry riding info into
    ``raw`` yet, so there's no safe automatic disambiguation.

    Affects both ``speeches.politician_id`` and the derived
    ``speech_chunks.politician_id`` (rebuilt by the next refresh
    pass). Idempotent: re-running is a no-op once all resolvable
    speeches have landed.

    Returns a stats dict; the batch-update approach avoids 439k
    individual round-trips.
    """
    stats = ResolveStats()

    # Count total scannable work, pre-update
    scanned_row = await db.fetchrow(
        """
        SELECT COUNT(*) AS n
          FROM speeches s
         WHERE s.source_system = 'assembly.ab.ca'
           AND s.politician_id IS NULL
           AND s.raw->'ab_hansard'->>'surname' IS NOT NULL
           AND s.raw->'ab_hansard'->>'legislature' IS NOT NULL
        """
    )
    stats.scanned = int(scanned_row["n"])

    # Enumerate legislatures present in the unresolved set — one
    # batched UPDATE per legl avoids the 184k-row cartesian-ish
    # pressure that timed the server out on a single all-at-once
    # statement. Each per-legl update is ~1s on the current corpus.
    legl_rows = await db.fetch(
        """
        SELECT DISTINCT (s.raw->'ab_hansard'->>'legislature')::int AS legl
          FROM speeches s
         WHERE s.source_system = 'assembly.ab.ca'
           AND s.politician_id IS NULL
           AND s.raw->'ab_hansard'->>'surname' IS NOT NULL
           AND s.raw->'ab_hansard'->>'legislature' IS NOT NULL
         ORDER BY 1
        """
    )
    legls = [int(r["legl"]) for r in legl_rows]
    log.info("resolve_ab_speakers: legls with unresolved speeches = %s", legls)

    budget_left = int(limit) if limit else None
    for legl in legls:
        if budget_left is not None and budget_left <= 0:
            break

        # Per-legl limit if caller set an overall --limit
        per_legl_limit = f"LIMIT {budget_left}" if budget_left is not None else ""

        # cand_count=1 gate = "exactly one MLA with that surname in
        # this legl." The split_part(last_name, ' ', -1) branch
        # handles compound surnames like "Calahoo Stonehouse" →
        # "stonehouse"; the lower(unaccent(last_name)) branch covers
        # the full-surname form in case the parser emitted the
        # whole compound.
        update_sql = f"""
        WITH target_speeches AS (
          SELECT s.id,
                 lower(unaccent(s.raw->'ab_hansard'->>'surname')) AS norm_surname
            FROM speeches s
           WHERE s.source_system = 'assembly.ab.ca'
             AND s.politician_id IS NULL
             AND (s.raw->'ab_hansard'->>'legislature')::int = $1
             AND s.raw->'ab_hansard'->>'surname' IS NOT NULL
           {per_legl_limit}
        ),
        candidates AS (
          SELECT ts.id AS speech_id,
                 p.id  AS politician_id,
                 COUNT(*) OVER (PARTITION BY ts.id) AS cand_count
            FROM target_speeches ts
            JOIN politician_terms pt
              ON pt.source = 'assembly.ab.ca:legl-' || $1::text
            JOIN politicians p
              ON p.id = pt.politician_id
             AND p.province_territory = 'AB'
             AND p.level = 'provincial'
             AND p.ab_assembly_mid IS NOT NULL
             AND (
               lower(unaccent(split_part(p.last_name, ' ', -1))) = ts.norm_surname
               OR lower(unaccent(p.last_name))                    = ts.norm_surname
             )
        ),
        updated AS (
          UPDATE speeches s
             SET politician_id = c.politician_id,
                 confidence    = GREATEST(s.confidence, 0.9),
                 updated_at    = now()
            FROM candidates c
           WHERE s.id = c.speech_id
             AND c.cand_count = 1
          RETURNING s.id
        )
        SELECT COUNT(*) AS n FROM updated
        """
        # 10-minute per-legl budget — largest legl (27) has ~48k
        # unresolved rows which take a couple of minutes to crunch
        # under the existing indexes, well past asyncpg's default
        # 60s command timeout.
        upd_row = await db.pool.fetchrow(update_sql, legl, timeout=600)
        n = int(upd_row["n"])
        stats.updated += n
        if budget_left is not None:
            budget_left -= n
        log.info("resolve_ab_speakers: legl=%d updated=%d", legl, n)

    # Step 3: propagate to speech_chunks, batched per legl. A one-shot
    # UPDATE over the ~230k-row delta contends with autovacuum on
    # speech_chunks (the table has ~500k AB rows inside a 2.7M-row
    # heap) and was cancelled by statement_timeout after 30 minutes
    # pre-reboot. Per-legl batches each complete in <60s on a quiet
    # DB. Enumerate from the stale set so re-runs are self-targeting
    # — Step 2's `legls` list can be empty when all speeches are
    # already resolved (e.g. after a pre-reboot run), which would
    # silently skip chunk propagation.
    chunk_legl_rows = await db.fetch(
        """
        SELECT DISTINCT (s.raw->'ab_hansard'->>'legislature')::int AS legl
          FROM speech_chunks sc
          JOIN speeches s ON s.id = sc.speech_id
         WHERE s.source_system = 'assembly.ab.ca'
           AND s.politician_id IS NOT NULL
           AND sc.politician_id IS DISTINCT FROM s.politician_id
         ORDER BY 1
        """
    )
    chunk_legls = [int(r["legl"]) for r in chunk_legl_rows]
    log.info("resolve_ab_speakers: legls with stale chunks = %s", chunk_legls)
    for legl in chunk_legls:
        n_row = await db.pool.fetchrow(
            """
            WITH updated AS (
              UPDATE speech_chunks sc
                 SET politician_id = s.politician_id
                FROM speeches s
               WHERE sc.speech_id = s.id
                 AND s.source_system = 'assembly.ab.ca'
                 AND s.politician_id IS NOT NULL
                 AND sc.politician_id IS DISTINCT FROM s.politician_id
                 AND (s.raw->'ab_hansard'->>'legislature')::int = $1
              RETURNING sc.id
            )
            SELECT COUNT(*) AS n FROM updated
            """,
            legl, timeout=600,
        )
        log.info(
            "resolve_ab_speakers: chunk propagation legl=%d updated=%d",
            legl, int(n_row["n"]),
        )

    # Step 4: tally the still-unresolved-or-ambiguous post-update count
    tail_row = await db.fetchrow(
        """
        SELECT COUNT(*) AS n
          FROM speeches s
         WHERE s.source_system = 'assembly.ab.ca'
           AND s.politician_id IS NULL
           AND s.raw->'ab_hansard'->>'surname' IS NOT NULL
           AND s.raw->'ab_hansard'->>'legislature' IS NOT NULL
        """
    )
    stats.still_unresolved = int(tail_row["n"])

    log.info(
        "resolve_ab_speakers: scanned=%d updated=%d still_unresolved=%d",
        stats.scanned, stats.updated, stats.still_unresolved,
    )
    return stats
