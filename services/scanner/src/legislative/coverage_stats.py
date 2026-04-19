"""Refresh the per-jurisdiction stats that drive /coverage.

`jurisdiction_sources` (migration 0019) was seeded with hardcoded status
flags and zero counts. The public coverage page reads those rows as-is,
so flipping Hansard from "partial" to "live" after a real ingest
requires this refresher to run. Keeping it here — offline, SQL-only —
lets the admin re-trigger it after any ingest job.

The refresh is purely derivative:
  - `speeches_count` = rows in `speeches` for this level+prov.
  - `politicians_count` = rows in `politicians` for this level+prov.
  - `bills_count` = rows in `bills` for this level+prov.
  - `hansard_status` flips to 'live' if we have ≥ 50 k speeches in that
    jurisdiction, 'partial' if 1-49k, else left alone.
  - `last_verified_at` = now().

Status flags for bills/votes/committees are NOT touched — those are
editorial judgements (e.g. "PE is blocked by a WAF") that don't flow
from row counts. Edit them directly in SQL or via a future migration.
"""
from __future__ import annotations

import logging
from typing import Optional

from ..db import Database

log = logging.getLogger(__name__)

# Mapping from jurisdiction_sources.jurisdiction code → (level, province_territory)
# filter pair used against speeches / politicians / bills.
JURISDICTION_FILTER: dict[str, tuple[str, Optional[str]]] = {
    "federal": ("federal", None),
    "AB": ("provincial", "AB"),
    "BC": ("provincial", "BC"),
    "MB": ("provincial", "MB"),
    "NB": ("provincial", "NB"),
    "NL": ("provincial", "NL"),
    "NS": ("provincial", "NS"),
    "NT": ("provincial", "NT"),
    "NU": ("provincial", "NU"),
    "ON": ("provincial", "ON"),
    "PE": ("provincial", "PE"),
    "QC": ("provincial", "QC"),
    "SK": ("provincial", "SK"),
    "YT": ("provincial", "YT"),
}


def _hansard_status(speech_count: int) -> Optional[str]:
    """Derive hansard_status from speech count. Returns None if we
    shouldn't touch the existing value (e.g. blocked jurisdictions)."""
    if speech_count >= 50_000:
        return "live"
    if speech_count >= 1_000:
        return "partial"
    return None


async def refresh_coverage_stats(db: Database) -> dict[str, dict[str, int]]:
    """Recompute jurisdiction_sources counts from live tables.

    Returns a per-jurisdiction report keyed by jurisdiction code, each
    value being a dict of before/after deltas.
    """
    report: dict[str, dict[str, int]] = {}

    for code, (level, prov) in JURISDICTION_FILTER.items():
        # Count live rows. prov=NULL means no province filter (federal).
        if prov is None:
            speeches = await db.fetchval(
                "SELECT count(*) FROM speeches WHERE level = $1", level,
            )
            pols = await db.fetchval(
                "SELECT count(*) FROM politicians WHERE level = $1", level,
            )
            bills_ct = await db.fetchval(
                "SELECT count(*) FROM bills WHERE level = $1", level,
            )
        else:
            speeches = await db.fetchval(
                "SELECT count(*) FROM speeches WHERE level = $1 AND province_territory = $2",
                level, prov,
            )
            pols = await db.fetchval(
                "SELECT count(*) FROM politicians WHERE level = $1 AND province_territory = $2",
                level, prov,
            )
            bills_ct = await db.fetchval(
                "SELECT count(*) FROM bills WHERE level = $1 AND province_territory = $2",
                level, prov,
            )

        current = await db.fetchrow(
            """
            SELECT speeches_count, politicians_count, bills_count, hansard_status
              FROM jurisdiction_sources
             WHERE jurisdiction = $1
            """,
            code,
        )
        if current is None:
            log.warning("jurisdiction %s not in jurisdiction_sources; skipping", code)
            continue

        new_hansard = _hansard_status(speeches)
        # Don't downgrade from 'blocked' — that's editorial.
        if current["hansard_status"] == "blocked":
            new_hansard = "blocked"
        # Don't touch if no signal.
        if new_hansard is None:
            new_hansard = current["hansard_status"]

        await db.execute(
            """
            UPDATE jurisdiction_sources
               SET speeches_count    = $2,
                   politicians_count = $3,
                   bills_count       = $4,
                   hansard_status    = $5,
                   last_verified_at  = now(),
                   updated_at        = now()
             WHERE jurisdiction = $1
            """,
            code, speeches, pols, bills_ct, new_hansard,
        )

        report[code] = {
            "speeches": speeches,
            "politicians": pols,
            "bills": bills_ct,
            "hansard_status": new_hansard,
            "prev_speeches": current["speeches_count"] or 0,
            "prev_hansard_status": current["hansard_status"],
        }
        log.info(
            "coverage %s: speeches %d→%d politicians→%d bills→%d hansard %s→%s",
            code,
            current["speeches_count"] or 0, speeches,
            pols, bills_ct,
            current["hansard_status"], new_hansard,
        )

    return report
