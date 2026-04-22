"""Manitoba bill sponsor resolution — slug-first with name-fuzz fallback.

``ingest-mb-bills`` resolves sponsors inline via an exact join on
``politicians.mb_assembly_slug``. That path covers 100% of current-
session sponsors whose MLA is in our roster. This module handles the
residual cases:

  * Historical bills ingested before a given MLA's slug was stamped.
  * Bills whose sponsor cell uses a compound surname the
    inline parser mis-tokenised.
  * Bills whose sponsor text is missing from the MLA roster (e.g. a
    recent by-election winner not yet in Open North).

Resolution order per unresolved ``bill_sponsors`` row:

  1. Direct FK lookup on ``mb_assembly_slug`` using the stored
     ``sponsor_slug`` column.
  2. Normalized-name match (``sponsor_resolver._norm``) on
     ``sponsor_name_raw`` against current MB politicians.
  3. Defer (leave ``politician_id`` NULL).

On a name-match hit that also carries a plausible surname slug, we
stamp the matched politician's ``mb_assembly_slug`` so subsequent
runs short-circuit to step 1. This mirrors the slug-backfill
behaviour in ``sponsor_resolver.resolve_sponsors``.
"""
from __future__ import annotations

import logging
from typing import Optional

from ..db import Database
from .sponsor_resolver import _norm

log = logging.getLogger(__name__)

SOURCE_SYSTEM = "manitoba-bills"


async def resolve(
    db: Database, *, limit: Optional[int] = None,
) -> dict[str, int]:
    stats = {
        "scanned": 0,
        "linked_by_slug": 0,
        "linked_by_name": 0,
        "slugs_backfilled": 0,
        "unmatched": 0,
    }

    sql = """
        SELECT bs.id, bs.sponsor_slug, bs.sponsor_name_raw
          FROM bill_sponsors bs
          JOIN bills b ON b.id = bs.bill_id
         WHERE bs.source_system = $1
           AND bs.politician_id IS NULL
         ORDER BY bs.created_at
    """
    if limit is not None:
        sql += f" LIMIT {int(limit)}"
    rows = await db.fetch(sql, SOURCE_SYSTEM)

    # Preload the MB roster once; name-fuzz is an in-memory scan.
    roster = await db.fetch(
        """
        SELECT id, name, first_name, last_name, mb_assembly_slug
          FROM politicians
         WHERE level='provincial' AND province_territory='MB'
        """
    )

    for row in rows:
        stats["scanned"] += 1
        sponsor_id = row["id"]
        slug = row["sponsor_slug"]
        name_raw = row["sponsor_name_raw"]

        # Step 1: slug join.
        slug_hit = next(
            (p for p in roster if slug and p["mb_assembly_slug"] == slug),
            None,
        )
        if slug_hit is not None:
            await _link(db, sponsor_id, str(slug_hit["id"]))
            stats["linked_by_slug"] += 1
            continue

        # Step 2: normalized-name match.
        linked = False
        if name_raw:
            target = _norm(name_raw)
            if target:
                matches = [
                    p for p in roster
                    if _norm(p["name"] or "") == target
                    or _norm(f"{p['first_name'] or ''} {p['last_name'] or ''}") == target
                ]
                if len(matches) == 1:
                    pol_id = str(matches[0]["id"])
                    await _link(db, sponsor_id, pol_id)
                    stats["linked_by_name"] += 1
                    linked = True
                    # Stamp slug back onto politicians if it was missing,
                    # so future bills with this slug short-circuit.
                    if slug and not matches[0]["mb_assembly_slug"]:
                        await db.execute(
                            """
                            UPDATE politicians
                               SET mb_assembly_slug = $2, updated_at = now()
                             WHERE id = $1 AND mb_assembly_slug IS NULL
                            """,
                            pol_id, slug,
                        )
                        stats["slugs_backfilled"] += 1
        if not linked:
            stats["unmatched"] += 1

    log.info(
        "resolve_mb_bill_sponsors: scanned=%d by_slug=%d by_name=%d "
        "slugs_backfilled=%d unmatched=%d",
        stats["scanned"], stats["linked_by_slug"], stats["linked_by_name"],
        stats["slugs_backfilled"], stats["unmatched"],
    )
    return stats


async def _link(db: Database, sponsor_id: str, politician_id: str) -> None:
    await db.execute(
        "UPDATE bill_sponsors SET politician_id = $2 WHERE id = $1",
        sponsor_id, politician_id,
    )
