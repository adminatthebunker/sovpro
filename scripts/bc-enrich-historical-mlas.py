"""Bulk-insert historical BC MLAs into politicians so Hansard backfill
can resolve them.

LIMS GraphQL `allMembers` returns ~376 BC members (active + retired).
The bills-ingest pipeline's `enrich_bc_member_ids` skips `active=False`
members. For Hansard backfill (pre-43rd Parliament), we need the retired
ones too, so this script inserts them with minimal fields — `name`,
`first_name`, `last_name`, `lims_member_id`, `is_active=false`,
`level='provincial'`, `province_territory='BC'`.

Safe to re-run. Uses lims_member_id as the dedup key (won't insert a row
if one already exists for that LIMS id).

Run once per scanner image:
  docker cp scripts/bc-enrich-historical-mlas.py sw-scanner-jobs:/tmp/
  docker exec sw-scanner-jobs python /tmp/bc-enrich-historical-mlas.py
"""
from __future__ import annotations

import asyncio
import os
import sys

import httpx

sys.path.insert(0, "/app")
from src.db import Database  # noqa: E402


LIMS_URL = "https://lims.leg.bc.ca/graphql"
QUERY = "{ allMembers { nodes { id firstName lastName active } } }"


async def main() -> None:
    db = Database(os.environ["DATABASE_URL"])
    await db.connect()
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.post(LIMS_URL, json={"query": QUERY})
            r.raise_for_status()
            members = r.json()["data"]["allMembers"]["nodes"]

        existing_lims = {
            row["lims_member_id"]
            for row in await db.fetch(
                "SELECT lims_member_id FROM politicians "
                "WHERE level='provincial' AND province_territory='BC' "
                "AND lims_member_id IS NOT NULL"
            )
        }
        print(f"LIMS members={len(members)}, existing linked={len(existing_lims)}")

        inserted = skipped = 0
        for m in members:
            lims_id = int(m["id"])
            if lims_id in existing_lims:
                skipped += 1
                continue
            first = (m.get("firstName") or "").strip()
            last = (m.get("lastName") or "").strip()
            if not last:
                continue
            name = f"{first} {last}".strip()
            await db.execute(
                """
                INSERT INTO politicians (
                    name, first_name, last_name,
                    level, province_territory,
                    is_active, lims_member_id,
                    social_urls, extras, source_id
                )
                VALUES ($1, $2, $3, 'provincial', 'BC',
                        $4, $5, '{}'::jsonb, '{}'::jsonb, $6)
                """,
                name, first or None, last,
                bool(m.get("active")),
                lims_id,
                f"lims-bc:member-{lims_id}",
            )
            inserted += 1

        print(f"inserted={inserted} skipped={skipped}")
    finally:
        await db.close()


if __name__ == "__main__":
    asyncio.run(main())
