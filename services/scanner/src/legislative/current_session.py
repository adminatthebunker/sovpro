"""Current legislative session resolver — DB-backed.

When a Hansard ingester (or any session-scoped command) is invoked without
explicit --parliament/--session, this resolver looks up the latest session
for that jurisdiction in `legislative_sessions`. The table is populated by
the corresponding bills ingester (ingest-*-bills), which has its own
upstream-driven current-session detection.

Operational pattern: in any daily-ingest chain, schedule the bills
ingester before the Hansard ingester. Every running schedule already does
this (NS, MB). When this resolver is called for a brand-new jurisdiction
with an empty legislative_sessions table, it raises ValueError telling the
operator to run the bills command first.

Why DB-backed and not upstream:
  - Bills ingesters already do upstream current-session detection.
  - One source of truth (the DB) is simpler than ten upstream-specific
    HTTP probes that would each need their own error handling.
  - Hansard ingest is always preceded by bills ingest in scheduled chains;
    by the time Hansard runs, legislative_sessions reflects whatever
    upstream considers current.
"""
from __future__ import annotations

from typing import Optional, Tuple

from ..db import Database


async def current_session(
    db: Database,
    *,
    level: str,
    province_territory: Optional[str] = None,
) -> Tuple[int, int]:
    """Return (parliament_number, session_number) for the jurisdiction's latest session.

    Args:
        level: 'federal' or 'provincial'.
        province_territory: 2-letter code (AB/BC/QC/...). None for federal.

    Raises:
        ValueError when no session exists yet for the jurisdiction.
    """
    if province_territory is None:
        row = await db.fetchrow(
            """
            SELECT parliament_number, session_number
              FROM legislative_sessions
             WHERE level = $1 AND province_territory IS NULL
             ORDER BY parliament_number DESC, session_number DESC
             LIMIT 1
            """,
            level,
        )
    else:
        row = await db.fetchrow(
            """
            SELECT parliament_number, session_number
              FROM legislative_sessions
             WHERE level = $1 AND province_territory = $2
             ORDER BY parliament_number DESC, session_number DESC
             LIMIT 1
            """,
            level, province_territory,
        )
    if row is None:
        scope = f"{level}/{province_territory}" if province_territory else level
        raise ValueError(
            f"No legislative_sessions row for {scope}. "
            f"Run the bills ingester for this jurisdiction first to populate "
            f"the current session, then re-run this command."
        )
    return int(row["parliament_number"]), int(row["session_number"])
