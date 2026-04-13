"""Pretty-print sovereignty stats to the console."""
from __future__ import annotations

from rich.console import Console
from rich.table import Table

from .classify import tier_emoji, tier_label
from .db import Database


async def print_stats(db: Database, console: Console) -> None:
    # ── Overall sovereignty breakdown ────────────────────────────
    rows = await db.fetch(
        """
        SELECT sovereignty_tier AS tier, COUNT(*) AS n
        FROM (
            SELECT DISTINCT ON (website_id) sovereignty_tier
            FROM infrastructure_scans
            ORDER BY website_id, scanned_at DESC
        ) t
        GROUP BY sovereignty_tier
        ORDER BY sovereignty_tier
        """
    )
    total = sum(r["n"] for r in rows)
    table = Table(title=f"Sovereignty Tiers ({total} websites)")
    table.add_column("Tier")
    table.add_column("Label")
    table.add_column("Count", justify="right")
    table.add_column("%", justify="right")
    for r in rows:
        pct = 100 * r["n"] / total if total else 0
        table.add_row(f"{tier_emoji(r['tier'])} {r['tier']}",
                      tier_label(r["tier"]),
                      str(r["n"]),
                      f"{pct:5.1f}%")
    console.print(table)

    # ── Top hosting providers ────────────────────────────────────
    top_providers = await db.fetch(
        """
        SELECT hosting_provider, COUNT(*) AS n
        FROM (
            SELECT DISTINCT ON (website_id) hosting_provider
            FROM infrastructure_scans
            ORDER BY website_id, scanned_at DESC
        ) t
        WHERE hosting_provider IS NOT NULL
        GROUP BY hosting_provider
        ORDER BY n DESC LIMIT 10
        """
    )
    tbl = Table(title="Top Hosting Providers")
    tbl.add_column("Provider")
    tbl.add_column("Sites", justify="right")
    for r in top_providers:
        tbl.add_row(r["hosting_provider"] or "—", str(r["n"]))
    console.print(tbl)

    # ── Referendum breakdown ─────────────────────────────────────
    ref = await db.fetch(
        """
        SELECT o.side, COUNT(DISTINCT w.id) AS websites,
               COUNT(*) FILTER (WHERE s.ip_country = 'CA') AS ca,
               COUNT(*) FILTER (WHERE s.ip_country = 'US') AS us
        FROM organizations o
        JOIN websites w ON w.owner_type='organization' AND w.owner_id=o.id AND w.is_active=true
        LEFT JOIN LATERAL (
            SELECT * FROM infrastructure_scans
            WHERE website_id = w.id ORDER BY scanned_at DESC LIMIT 1
        ) s ON true
        WHERE o.type IN ('referendum_leave','referendum_stay')
        GROUP BY o.side
        """
    )
    rt = Table(title="Referendum Organizations")
    rt.add_column("Side")
    rt.add_column("Websites", justify="right")
    rt.add_column("🇨🇦 CA", justify="right")
    rt.add_column("🇺🇸 US", justify="right")
    for r in ref:
        rt.add_row(r["side"] or "—", str(r["websites"]), str(r["ca"]), str(r["us"]))
    console.print(rt)
