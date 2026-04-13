"""Idempotently ensure the referendum organization data is present.

The canonical seed runs from db/seed.sql on first container start. This script
exists so the scanner CLI (`python -m src seed-orgs`) can re-apply the data
from a running container without needing a volume recreate.
"""
from __future__ import annotations

import json
import logging
import os

from rich.console import Console

from .db import Database

console = Console()
log = logging.getLogger(__name__)


SEED_SQL_PATH = os.environ.get("SEED_SQL_PATH", "/app/db/seed.sql")


async def seed_organizations(db: Database) -> None:
    # Prefer executing the canonical seed.sql if mounted; otherwise embed a
    # minimal inline fallback so scanner is useful without the db/ volume.
    if os.path.isfile(SEED_SQL_PATH):
        with open(SEED_SQL_PATH, "r", encoding="utf-8") as f:
            sql = f.read()
        await db.pool.execute(sql)
        console.print(f"[green]Seeded organizations from {SEED_SQL_PATH}[/green]")
        return

    console.print(f"[yellow]{SEED_SQL_PATH} not found; using embedded minimal seed[/yellow]")
    await _embedded_seed(db)


EMBEDDED_ORGS = [
    {
        "slug": "alberta-prosperity-project",
        "name": "Alberta Prosperity Project",
        "type": "referendum_leave",
        "side": "leave",
        "description": "Primary Alberta separatist organization.",
        "province_territory": "AB",
        "websites": [
            ("https://albertaprosperityproject.com/", "primary"),
            ("https://nb.albertaprosperity.com/", "pledge"),
        ],
    },
    {
        "slug": "stay-free-alberta",
        "name": "Stay Free Alberta",
        "type": "referendum_leave",
        "side": "leave",
        "description": "Petition vehicle for APP.",
        "province_territory": "AB",
        "websites": [
            ("https://stayfreealberta.com/", "primary"),
            ("https://stayfreealberta.com/sign/", "petition"),
        ],
    },
    {
        "slug": "forever-canadian",
        "name": "Forever Canadian / Alberta Forever Canada",
        "type": "referendum_stay",
        "side": "stay",
        "description": "Anti-separatist citizen initiative.",
        "province_territory": "AB",
        "websites": [("https://www.forever-canadian.ca/en", "primary")],
    },
    {
        "slug": "ucp",
        "name": "United Conservative Party (UCP)",
        "type": "political_party",
        "side": "neutral",
        "description": "Alberta's governing party.",
        "province_territory": "AB",
        "websites": [("https://www.unitedconservative.ca/", "party")],
    },
    {
        "slug": "alberta-ndp",
        "name": "Alberta NDP",
        "type": "political_party",
        "side": "stay",
        "description": "Official Alberta opposition.",
        "province_territory": "AB",
        "websites": [("https://www.albertandp.ca/", "party")],
    },
]


async def _embedded_seed(db: Database) -> None:
    for org in EMBEDDED_ORGS:
        row = await db.fetchrow(
            """
            INSERT INTO organizations (slug, name, type, side, description, province_territory)
            VALUES ($1, $2, $3, $4, $5, $6)
            ON CONFLICT (slug) DO UPDATE SET
              name = EXCLUDED.name,
              type = EXCLUDED.type,
              side = EXCLUDED.side,
              description = EXCLUDED.description,
              province_territory = EXCLUDED.province_territory,
              updated_at = now()
            RETURNING id
            """,
            org["slug"], org["name"], org["type"], org["side"],
            org["description"], org["province_territory"],
        )
        oid = row["id"]
        for url, label in org["websites"]:
            await db.execute(
                """
                INSERT INTO websites (owner_type, owner_id, url, label)
                VALUES ('organization', $1, $2, $3)
                ON CONFLICT (owner_type, owner_id, url) DO NOTHING
                """,
                oid, url, label,
            )
    console.print(f"[green]Seeded {len(EMBEDDED_ORGS)} organizations from embedded list[/green]")
