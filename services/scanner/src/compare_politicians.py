"""Politician-level delta detection (Phase 6).

Mirrors the scan-delta pattern in ``compare.py`` but operates on the
politicians table rather than infrastructure_scans.

Change types (must match the ``politician_changes_change_type_check``
constraint in the DB):
    party_switch, office_change, retired, newly_elected,
    social_added, social_removed, social_dead,
    constituency_change, name_change
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Optional

import orjson

if TYPE_CHECKING:
    import asyncpg

    from .opennorth import OpenNorthSet

from .db import Database

log = logging.getLogger(__name__)


def _norm(v: Any) -> Optional[str]:
    """Normalize for comparison: treat empty string as None, strip whitespace."""
    if v is None:
        return None
    s = str(v).strip()
    return s or None


async def diff_and_record(
    db: Database,
    existing: "Optional[asyncpg.Record]",
    incoming: dict,
    set_def: "OpenNorthSet",
) -> list[dict]:
    """Produce a list of change dicts (not yet written to DB) from comparing
    the current politicians row with the newly-fetched Open North rep.

    If ``existing`` is None this represents a newly-elected politician: a
    single ``newly_elected`` change is returned.
    """
    # Import here so we don't create a circular import at module load time.
    from .opennorth import _constituency_id  # local import

    # Fields we extract from the incoming rep to compare against existing.
    incoming_name = _norm(incoming.get("name")) or "Unknown"
    incoming_party = _norm(incoming.get("party_name"))
    incoming_office = _norm(incoming.get("elected_office") or set_def.office)
    incoming_cid = _norm(_constituency_id(incoming, set_def))

    # Newly elected (no prior row): emit one change.
    if existing is None:
        return [
            {
                "change_type": "newly_elected",
                "old_value": None,
                "new_value": {
                    "name": incoming_name,
                    "party": incoming_party,
                    "office": incoming_office,
                    "level": set_def.level,
                    "province_territory": set_def.province,
                    "constituency_id": incoming_cid,
                },
                "severity": "notable",
            }
        ]

    changes: list[dict] = []

    old_party = _norm(existing.get("party"))
    if old_party != incoming_party and (old_party or incoming_party):
        changes.append(
            {
                "change_type": "party_switch",
                "old_value": {"party": old_party},
                "new_value": {"party": incoming_party},
                "severity": "notable",
            }
        )

    old_office = _norm(existing.get("elected_office"))
    if old_office != incoming_office and (old_office or incoming_office):
        changes.append(
            {
                "change_type": "office_change",
                "old_value": {"elected_office": old_office},
                "new_value": {"elected_office": incoming_office},
                "severity": "notable",
            }
        )

    old_cid = _norm(existing.get("constituency_id"))
    if old_cid != incoming_cid and (old_cid or incoming_cid):
        changes.append(
            {
                "change_type": "constituency_change",
                "old_value": {"constituency_id": old_cid},
                "new_value": {"constituency_id": incoming_cid},
                "severity": "info",
            }
        )

    old_name = _norm(existing.get("name"))
    if old_name != incoming_name and (old_name or incoming_name):
        changes.append(
            {
                "change_type": "name_change",
                "old_value": {"name": old_name},
                "new_value": {"name": incoming_name},
                "severity": "info",
            }
        )

    return changes


# Change types that represent a material term boundary — detecting any of
# these closes the current term and opens a new one.
_TERM_BOUNDARY_TYPES = frozenset(
    {"party_switch", "office_change", "constituency_change"}
)


async def apply_changes(
    db: Database,
    politician_id: str,
    changes: list[dict],
    *,
    set_def: "Optional[OpenNorthSet]" = None,
    incoming: Optional[dict] = None,
) -> None:
    """Persist each change row in ``politician_changes`` and, when a change
    crosses a term boundary, close the current open term and open a new one
    reflecting the incoming values.
    """
    if not changes:
        return

    for ch in changes:
        await db.execute(
            """
            INSERT INTO politician_changes
              (politician_id, change_type, old_value, new_value, severity)
            VALUES ($1, $2, $3, $4, $5)
            """,
            politician_id,
            ch["change_type"],
            orjson.dumps(ch.get("old_value")).decode() if ch.get("old_value") is not None else None,
            orjson.dumps(ch.get("new_value")).decode() if ch.get("new_value") is not None else None,
            ch.get("severity", "info"),
        )

    # If any change is a term boundary, close open terms and start a new one.
    has_boundary = any(c["change_type"] in _TERM_BOUNDARY_TYPES for c in changes)
    if has_boundary and incoming is not None and set_def is not None:
        await db.execute(
            """
            UPDATE politician_terms
               SET ended_at = now()
             WHERE politician_id = $1
               AND ended_at IS NULL
            """,
            politician_id,
        )
        await _insert_term_from_rep(db, politician_id, incoming, set_def)


async def _insert_term_from_rep(
    db: Database,
    politician_id: str,
    rep: dict,
    set_def: "OpenNorthSet",
) -> None:
    """Insert a fresh ``politician_terms`` row from an Open North rep dict."""
    from .opennorth import _constituency_id  # local import

    office = _norm(rep.get("elected_office") or set_def.office) or set_def.office
    party = _norm(rep.get("party_name"))
    cid = _norm(_constituency_id(rep, set_def))
    province = set_def.province or _norm(rep.get("extra", {}).get("province"))
    source = f"opennorth:{set_def.path.rstrip('/').split('/')[-1]}"

    await db.execute(
        """
        INSERT INTO politician_terms
          (politician_id, office, party, level, province_territory,
           constituency_id, started_at, ended_at, source)
        VALUES ($1, $2, $3, $4, $5, $6, now(), NULL, $7)
        """,
        politician_id,
        office,
        party,
        set_def.level,
        province,
        cid,
        source,
    )


async def open_initial_term(
    db: Database,
    politician_id: str,
    rep_data: dict,
    set_def: "OpenNorthSet",
) -> None:
    """Open the first term row for a newly-elected politician."""
    await _insert_term_from_rep(db, politician_id, rep_data, set_def)


async def detect_retirements(
    db: Database,
    set_def: "OpenNorthSet",
    seen_source_ids: set[str],
) -> None:
    """Mark politicians as retired when they were previously active in this
    set but weren't seen in the current ingestion run.

    Matches politicians whose source_id is shaped
    ``opennorth:{set_slug}:...`` and compares against ``seen_source_ids``.
    """
    set_slug = set_def.path.rstrip("/").split("/")[-1]
    prefix = f"opennorth:{set_slug}:"

    rows = await db.fetch(
        """
        SELECT id, source_id, name, party, elected_office, level,
               province_territory, constituency_id
          FROM politicians
         WHERE source_id LIKE $1
           AND is_active = true
        """,
        prefix + "%",
    )

    retired_rows = [r for r in rows if r["source_id"] not in seen_source_ids]
    if not retired_rows:
        return

    log.info(
        "detect_retirements: marking %d politician(s) retired from set %s",
        len(retired_rows),
        set_slug,
    )

    for row in retired_rows:
        pid = str(row["id"])
        try:
            # Record the change first.
            old_value = {
                "name": row.get("name"),
                "party": row.get("party"),
                "office": row.get("elected_office"),
                "level": row.get("level"),
                "province_territory": row.get("province_territory"),
                "constituency_id": row.get("constituency_id"),
            }
            await db.execute(
                """
                INSERT INTO politician_changes
                  (politician_id, change_type, old_value, new_value, severity)
                VALUES ($1, 'retired', $2, NULL, 'notable')
                """,
                pid,
                orjson.dumps(old_value).decode(),
            )

            # Close any open term.
            await db.execute(
                """
                UPDATE politician_terms
                   SET ended_at = now()
                 WHERE politician_id = $1
                   AND ended_at IS NULL
                """,
                pid,
            )

            # Mark politician inactive.
            await db.execute(
                """
                UPDATE politicians
                   SET is_active = false,
                       updated_at = now()
                 WHERE id = $1
                """,
                pid,
            )
        except Exception as exc:  # pragma: no cover - defensive
            log.exception(
                "detect_retirements: failed to retire politician %s: %s",
                pid,
                exc,
            )


async def backfill_initial_terms(db: Database) -> dict:
    """One-time backfill: for every active politician that has no open term,
    insert an initial term based on the current politicians row.

    Returns a small stats dict suitable for logging.
    """
    rows = await db.fetch(
        """
        SELECT p.id, p.name, p.party, p.elected_office, p.level,
               p.province_territory, p.constituency_id, p.created_at,
               p.source_id
          FROM politicians p
         WHERE p.is_active = true
           AND NOT EXISTS (
                SELECT 1 FROM politician_terms t
                 WHERE t.politician_id = p.id
                   AND t.ended_at IS NULL
           )
        """
    )
    inserted = 0
    skipped = 0
    for row in rows:
        office = _norm(row["elected_office"]) or "Unknown"
        source = None
        sid = row["source_id"]
        if sid and ":" in sid:
            # e.g. 'opennorth:house-of-commons:joe-blow' -> 'opennorth:house-of-commons'
            parts = sid.split(":")
            if len(parts) >= 2:
                source = ":".join(parts[:2])
        try:
            await db.execute(
                """
                INSERT INTO politician_terms
                  (politician_id, office, party, level, province_territory,
                   constituency_id, started_at, ended_at, source)
                VALUES ($1, $2, $3, $4, $5, $6, $7, NULL, $8)
                """,
                row["id"],
                office,
                row["party"],
                row["level"],
                row["province_territory"],
                row["constituency_id"],
                row["created_at"],
                source,
            )
            inserted += 1
        except Exception as exc:
            skipped += 1
            log.warning(
                "backfill_initial_terms: failed to open term for %s: %s",
                row["id"],
                exc,
            )

    stats = {"inserted": inserted, "skipped": skipped, "candidates": len(rows)}
    log.info("backfill_initial_terms: %s", stats)
    return stats
