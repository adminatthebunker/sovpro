"""Resolve bill_sponsors.politician_id from sponsor slug + name.

Pure offline. Operates entirely on data already in the DB:
  - ``bill_sponsors`` rows (sponsor_slug, sponsor_name_raw)
  - ``bills`` (province_territory)
  - ``politicians`` (name, level, province_territory, {ola,nslegislature}_slug)

Resolution order for each unresolved row:
  1. **Slug join** — if the target province's slug column already has
     our sponsor_slug indexed, that's an exact match, done.
  2. **Name match** — normalize sponsor_name_raw ("Jill Balser",
     "Flack, Hon. Rob" post-normalization is "Rob Flack") and look for
     a unique politician in the right province with that name. On
     unique hit, link the bill_sponsors row AND write the slug back to
     politicians.<col>_slug so future bills with that sponsor short-
     circuit to step 1.
  3. **Defer** — if the sponsor is pre-2024 (no longer sitting) or
     the name doesn't match a current politician, leave
     politician_id NULL. Historical roster ingestion is a separate
     future concern.

Jurisdiction-aware: each source_system maps to the politician slug
column it owns, so adding BC / MB / etc. later is one-line additions
to SOURCE_SYSTEM_TO_SLUG_COL.
"""
from __future__ import annotations

import logging
import re
import unicodedata
from typing import Optional

from ..db import Database

log = logging.getLogger(__name__)

# Map each bill-pipeline source_system to the politicians column that
# stores its profile slug. Adding a province = one line here.
SOURCE_SYSTEM_TO_SLUG_COL: dict[str, str] = {
    "nslegislature-html": "nslegislature_slug",
    "ola-on":             "ola_slug",
    # BC self-resolves at ingestion time because LIMS hands us an
    # integer memberId that joins directly against politicians.
    # lims_member_id. No slug fuzz needed. See bc_bills._persist_sponsor.
}


def _norm(name: str) -> str:
    """Normalize a human name for comparison.

    - Strip honorifics (Hon., Mr., Ms., Dr., Premier, etc.)
    - Lowercase, strip diacritics
    - Collapse whitespace, drop punctuation
    """
    if not name:
        return ""
    s = unicodedata.normalize("NFKD", name)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.lower()
    s = re.sub(r"\b(hon\.?|mr\.?|mrs\.?|ms\.?|dr\.?|premier|minister)\b", " ", s)
    s = re.sub(r"[^\w\s-]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


async def resolve_sponsors(
    db: Database, *, limit: Optional[int] = None
) -> dict[str, int]:
    """Walk unresolved bill_sponsors rows and link them to politicians."""
    stats = {
        "scanned": 0,
        "linked_by_slug": 0,
        "linked_by_name": 0,
        "slugs_backfilled": 0,
        "unmatched": 0,
        "skipped_no_slug_col": 0,
    }

    sql = """
        SELECT s.id, s.bill_id, s.sponsor_slug, s.sponsor_name_raw,
               s.source_system,
               b.level, b.province_territory
          FROM bill_sponsors s
          JOIN bills b ON b.id = s.bill_id
         WHERE s.politician_id IS NULL
         ORDER BY s.created_at
    """
    if limit is not None:
        sql += f" LIMIT {int(limit)}"
    rows = await db.fetch(sql)

    for row in rows:
        stats["scanned"] += 1
        sponsor_id = row["id"]
        slug = row["sponsor_slug"]
        name_raw = row["sponsor_name_raw"]
        source_system = row["source_system"]
        level = row["level"]
        prov = row["province_territory"]

        slug_col = SOURCE_SYSTEM_TO_SLUG_COL.get(source_system)
        if slug_col is None:
            stats["skipped_no_slug_col"] += 1
            continue

        # Step 1: slug join. The slug column is indexed, so this is a
        # single index lookup per sponsor.
        pol_id: Optional[str] = None
        if slug:
            pol_id = await db.fetchval(
                f"SELECT id FROM politicians "
                f"WHERE {slug_col} = $1 AND level = $2 "
                f"  AND ($3::text IS NULL OR province_territory = $3)",
                slug, level, prov,
            )
            if pol_id:
                await _link(db, sponsor_id, pol_id)
                stats["linked_by_slug"] += 1
                continue

        # Step 2: name match. Must be unique within
        # (level, province_territory) to avoid false positives when
        # two current MLAs share a first name.
        if name_raw:
            target = _norm(name_raw)
            if not target:
                stats["unmatched"] += 1
                continue
            candidates = await db.fetch(
                """
                SELECT id, name, first_name, last_name
                  FROM politicians
                 WHERE level = $1
                   AND ($2::text IS NULL OR province_territory = $2)
                   AND is_active = true
                """,
                level, prov,
            )
            matches = [
                c for c in candidates
                if _norm(c["name"]) == target
                or _norm(f"{c['first_name'] or ''} {c['last_name'] or ''}") == target
            ]
            if len(matches) == 1:
                pol_id = str(matches[0]["id"])
                await _link(db, sponsor_id, pol_id)
                stats["linked_by_name"] += 1
                # Backfill the slug column so the next bill with this
                # slug resolves in step 1 without re-running name fuzz.
                if slug:
                    await db.execute(
                        f"UPDATE politicians SET {slug_col} = $2, updated_at = now() "
                        f"WHERE id = $1 AND ({slug_col} IS NULL OR {slug_col} = $2)",
                        pol_id, slug,
                    )
                    stats["slugs_backfilled"] += 1
                continue

        stats["unmatched"] += 1

    log.info(
        "resolve_sponsors: scanned=%d by_slug=%d by_name=%d "
        "slugs_backfilled=%d unmatched=%d",
        stats["scanned"], stats["linked_by_slug"], stats["linked_by_name"],
        stats["slugs_backfilled"], stats["unmatched"],
    )
    return stats


async def _link(db: Database, bill_sponsor_id: str, politician_id: str) -> None:
    await db.execute(
        "UPDATE bill_sponsors SET politician_id = $2 WHERE id = $1",
        bill_sponsor_id, politician_id,
    )
