"""Resolve speeches.politician_id for acting-Speaker / Deputy-Speaker turns.

Context: federal Hansard speaker attributions of the form

    "The Acting Speaker (Mr. McClelland)"
    "The Deputy Speaker (Hon. Jean Augustine)"
    "The Chairman (Ms. Bakopanos)"

carry a real MP's name in the parens, but openparliament's /speeches/
API does not populate `politician_url` for these rows — the attribution
points at the ROLE, not the person. At ingest, the attribution regex in
`federal_hansard.py` (ATTRIB_RE) doesn't match this shape either (no
"Name (Constituency, PartyAbbrev)" pattern), so politician_id is left
NULL.

This module runs after ingest, walks unresolved federal speeches whose
speaker_name_raw matches a presiding-officer pattern, extracts the
parenthesised name, and matches it against the `politicians` table by
normalised first+last or last-only.

Limitations:
  - Only matches against politicians we currently have in the DB
    (approximately current + recently-retired MPs). Historical MPs from
    the 1990s who aren't in politicians can't be resolved; they stay
    NULL. Expected hit rate therefore concentrates on P41-P44 era.
  - Surname-only attributions ("Mr. McClelland") resolve to the unique
    federal politician whose last_name matches — if two Federal MPs
    share a surname at different periods, we skip rather than guess.
"""
from __future__ import annotations

import logging
import re
from typing import Optional

from ..db import Database
from .sponsor_resolver import _norm

log = logging.getLogger(__name__)


# Matches presiding-officer attributions. Captures the parenthesised
# name block verbatim; the resolver does the honorific-stripping.
#
#   "The Acting Speaker (Mr. McClelland)"              → "Mr. McClelland"
#   "The Deputy Speaker (Hon. Jean Augustine)"         → "Hon. Jean Augustine"
#   "The Chairman (Ms. Bakopanos)"                     → "Ms. Bakopanos"
#   "The Assistant Deputy Chair (Mrs. Carol Hughes)"   → "Mrs. Carol Hughes"
#
# Note: plain "The Speaker" / "The Deputy Speaker" (no parens) do not
# match — those don't carry a name.
PRESIDING_OFFICER_RE = re.compile(
    r"^\s*(?:Mr\.|Mrs\.|Ms\.|Dr\.|Hon\.\s*)?"
    r"The\s+"
    r"(?:Acting\s+|Deputy\s+|Assistant\s+Deputy\s+)?"
    r"(?:Speaker|Chair(?:man|person)?|Chairman|Vice-Chair)"
    r"\s*\(([^)]+)\)\s*$",
    re.IGNORECASE,
)


def extract_presiding_officer_name(raw: str) -> Optional[str]:
    """Return the parenthesised name from a presiding-officer attribution,
    or None if this isn't such an attribution.
    """
    if not raw:
        return None
    m = PRESIDING_OFFICER_RE.match(raw)
    return m.group(1).strip() if m else None


async def resolve_acting_speakers(
    db: Database, *, limit: Optional[int] = None
) -> dict[str, int]:
    """Walk unresolved federal speeches with presiding-officer attribution
    and link them to politicians where a unique name match exists.

    Updates are done in bulk UPDATE batches keyed by politician_id so we
    don't touch the HNSW indexes (we're only changing speeches, not
    speech_chunks — the chunks' politician_id is a separate denorm that
    the retrieval path can refresh later).
    """
    stats = {
        "scanned": 0,
        "no_parens": 0,
        "resolved": 0,
        "ambiguous": 0,
        "no_politician_found": 0,
    }

    # Load all federal politicians ONCE — only ~1,815 rows, cheaper than
    # per-speech lookups.
    politicians = await db.fetch(
        """
        SELECT id::text AS id, name, first_name, last_name
          FROM politicians
         WHERE level = 'federal'
        """
    )
    # Build a normalised-name index: key → list of politician ids.
    by_full: dict[str, list[str]] = {}
    by_last: dict[str, list[str]] = {}
    for p in politicians:
        norm_full = _norm(p["name"] or "")
        norm_fl = _norm(f"{p['first_name'] or ''} {p['last_name'] or ''}".strip())
        norm_last = _norm(p["last_name"] or "")
        for key in {norm_full, norm_fl}:
            if key:
                by_full.setdefault(key, []).append(p["id"])
        if norm_last:
            by_last.setdefault(norm_last, []).append(p["id"])
    log.info(
        "loaded %d federal politicians (unique full-names=%d, unique surnames=%d)",
        len(politicians), len(by_full), len(by_last),
    )

    # Walk candidate speeches. Filter to the narrowest pattern so we only
    # touch rows that can possibly match.
    sql = """
        SELECT id::text AS id, speaker_name_raw, spoken_at
          FROM speeches
         WHERE level = 'federal'
           AND politician_id IS NULL
           AND speaker_name_raw ~ '[(][A-Za-z]'
           AND speaker_name_raw ILIKE 'The %'
    """
    if limit is not None:
        sql += f" LIMIT {int(limit)}"
    rows = await db.fetch(sql)

    # Collect updates as (politician_id, speech_id) pairs, then flush in
    # one UPDATE per politician to amortise DB round-trips.
    to_update: dict[str, list[str]] = {}

    for row in rows:
        stats["scanned"] += 1
        parens = extract_presiding_officer_name(row["speaker_name_raw"])
        if not parens:
            stats["no_parens"] += 1
            continue

        target = _norm(parens)
        if not target:
            stats["no_politician_found"] += 1
            continue

        # Prefer full-name match; fall back to surname-only.
        candidates = by_full.get(target)
        if candidates is None:
            candidates = by_last.get(target)

        if not candidates:
            stats["no_politician_found"] += 1
            continue
        if len(candidates) > 1:
            stats["ambiguous"] += 1
            continue

        to_update.setdefault(candidates[0], []).append(row["id"])
        stats["resolved"] += 1

    if to_update:
        for pol_id, speech_ids in to_update.items():
            await db.execute(
                """
                UPDATE speeches
                   SET politician_id = $1,
                       updated_at = now()
                 WHERE id = ANY($2::uuid[])
                   AND politician_id IS NULL
                """,
                pol_id, speech_ids,
            )
        log.info(
            "updated %d speeches across %d politicians",
            sum(len(v) for v in to_update.values()), len(to_update),
        )

    log.info(
        "resolve-acting-speakers: scanned=%d resolved=%d ambiguous=%d "
        "no_politician_found=%d no_parens=%d",
        stats["scanned"], stats["resolved"], stats["ambiguous"],
        stats["no_politician_found"], stats["no_parens"],
    )
    return stats
