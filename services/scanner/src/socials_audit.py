"""Audit the current state of politician_socials coverage.

Produces three outputs:

1. A jurisdiction × platform coverage matrix printed to stdout (Rich table).
2. A per-politician missing-platforms CSV at POLITICIAN_SOCIALS_AUDIT_CSV
   (default /tmp/politician_socials_audit.csv).
3. A `v_socials_missing` view that Tier 2 / Tier 3 commands query to pick
   work.

Schema of v_socials_missing (one row per (politician, missing_platform)):
    politician_id  uuid
    name           text
    level          text
    province_territory text
    constituency_name  text
    party          text
    platform       text        -- the platform that is MISSING
    official_url   text
    personal_url   text
    openparliament_slug text
    ola_slug       text
    nslegislature_slug text
    lims_member_id int
    qc_assnat_id   int
    ab_assembly_mid text

The view is refreshable-cheap — it's a plain view, not materialized, so
every audit run sees the current state.
"""
from __future__ import annotations

import csv
import logging
import os
from collections import defaultdict
from typing import Optional

from rich.console import Console
from rich.table import Table

from .db import Database
from .socials import ALLOWED_PLATFORMS

log = logging.getLogger(__name__)
console = Console()

# Platforms we *try* to cover for every active politician. 'other' is the
# canonicalisation catch-all so we exclude it from gap analysis.
AUDIT_PLATFORMS: tuple[str, ...] = tuple(
    p for p in sorted(ALLOWED_PLATFORMS) if p != "other"
)


DEFAULT_CSV = "/tmp/politician_socials_audit.csv"


_CREATE_VIEW_SQL = """
CREATE OR REPLACE VIEW v_socials_missing AS
WITH active AS (
    SELECT id, name, level, province_territory, constituency_name, party,
           official_url, personal_url,
           openparliament_slug, ola_slug, nslegislature_slug,
           lims_member_id, qc_assnat_id, ab_assembly_mid
      FROM politicians
     WHERE is_active = true
),
platforms(platform) AS (
    VALUES %s
),
existing AS (
    SELECT DISTINCT politician_id, platform FROM politician_socials
)
SELECT a.id               AS politician_id,
       a.name,
       a.level,
       a.province_territory,
       a.constituency_name,
       a.party,
       p.platform,
       a.official_url,
       a.personal_url,
       a.openparliament_slug,
       a.ola_slug,
       a.nslegislature_slug,
       a.lims_member_id,
       a.qc_assnat_id,
       a.ab_assembly_mid
  FROM active a
  CROSS JOIN platforms p
  LEFT JOIN existing e
         ON e.politician_id = a.id AND e.platform = p.platform
 WHERE e.politician_id IS NULL
"""


async def _create_view(db: Database) -> None:
    values_clause = ", ".join(f"('{p}')" for p in AUDIT_PLATFORMS)
    sql = _CREATE_VIEW_SQL % values_clause
    await db.execute(sql)


async def audit_socials(
    db: Database,
    *,
    csv_path: Optional[str] = None,
    no_csv: bool = False,
) -> None:
    """Print a coverage summary, (re)create v_socials_missing, and write CSV."""
    await _create_view(db)

    # Per-jurisdiction coverage summary.
    coverage_rows = await db.fetch(
        """
        SELECT level,
               COALESCE(province_territory, '-') AS pt,
               COUNT(DISTINCT p.id)                        AS total,
               COUNT(DISTINCT s.politician_id)             AS with_any,
               ROUND(100.0 * COUNT(DISTINCT s.politician_id)
                     / NULLIF(COUNT(DISTINCT p.id), 0), 1) AS pct
          FROM politicians p
          LEFT JOIN politician_socials s ON s.politician_id = p.id
         WHERE p.is_active = true
         GROUP BY level, province_territory
         ORDER BY level, pt
        """
    )

    platform_rows = await db.fetch(
        """
        SELECT platform,
               COUNT(*) AS n,
               COUNT(*) FILTER (WHERE is_live IS NULL) AS unverified,
               COUNT(*) FILTER (WHERE is_live = true)  AS live,
               COUNT(*) FILTER (WHERE is_live = false) AS dead,
               COUNT(*) FILTER (WHERE flagged_low_confidence = true) AS flagged
          FROM politician_socials
         GROUP BY platform
         ORDER BY n DESC
        """
    )

    source_rows = await db.fetch(
        """
        SELECT COALESCE(source, '<null>') AS source,
               COUNT(*) AS n,
               AVG(confidence)::numeric(4,3) AS avg_conf,
               COUNT(*) FILTER (WHERE flagged_low_confidence = true) AS flagged
          FROM politician_socials
         GROUP BY source
         ORDER BY n DESC
        """
    )

    missing_total = await db.fetchval(
        "SELECT COUNT(*) FROM v_socials_missing"
    )

    zero_socials = await db.fetchval(
        """
        SELECT COUNT(*)
          FROM politicians p
         WHERE p.is_active = true
           AND NOT EXISTS (
               SELECT 1 FROM politician_socials s
                WHERE s.politician_id = p.id
           )
        """
    )

    total_active = await db.fetchval(
        "SELECT COUNT(*) FROM politicians WHERE is_active = true"
    )

    console.print()
    console.print(
        f"[bold cyan]Politicians (is_active):[/bold cyan] {total_active} — "
        f"[bold]{total_active - zero_socials}[/bold] have at least one social "
        f"([green]{100.0 * (total_active - zero_socials) / max(1, total_active):.1f}%[/green]), "
        f"[red]{zero_socials}[/red] have zero"
    )
    console.print(
        f"[bold cyan]Missing-matrix rows:[/bold cyan] {missing_total} "
        f"(politician × missing platform)"
    )

    # Platform depth.
    tbl_p = Table(title="Platform depth", show_lines=False)
    tbl_p.add_column("platform", style="cyan")
    tbl_p.add_column("rows", justify="right")
    tbl_p.add_column("unverified", justify="right")
    tbl_p.add_column("live", justify="right", style="green")
    tbl_p.add_column("dead", justify="right", style="red")
    tbl_p.add_column("flagged", justify="right", style="yellow")
    for r in platform_rows:
        tbl_p.add_row(
            r["platform"], str(r["n"]), str(r["unverified"]),
            str(r["live"]), str(r["dead"]), str(r["flagged"]),
        )
    console.print(tbl_p)

    # Source distribution.
    tbl_s = Table(title="Rows by source", show_lines=False)
    tbl_s.add_column("source", style="cyan")
    tbl_s.add_column("rows", justify="right")
    tbl_s.add_column("avg_conf", justify="right")
    tbl_s.add_column("flagged", justify="right", style="yellow")
    for r in source_rows:
        tbl_s.add_row(
            r["source"], str(r["n"]),
            "" if r["avg_conf"] is None else f"{r['avg_conf']:.3f}",
            str(r["flagged"]),
        )
    console.print(tbl_s)

    # Jurisdiction coverage.
    tbl_j = Table(title="Coverage by jurisdiction (politicians with ≥1 social / total)")
    tbl_j.add_column("level", style="cyan")
    tbl_j.add_column("pt")
    tbl_j.add_column("total", justify="right")
    tbl_j.add_column("with_any", justify="right")
    tbl_j.add_column("%", justify="right", style="green")
    for r in coverage_rows:
        pct = r["pct"]
        pct_cell = "" if pct is None else f"{pct:.1f}"
        style_pct = None
        if pct is not None and pct < 80:
            style_pct = "red"
        tbl_j.add_row(
            r["level"], r["pt"], str(r["total"]), str(r["with_any"]),
            f"[{style_pct}]{pct_cell}[/{style_pct}]" if style_pct else pct_cell,
        )
    console.print(tbl_j)

    # Missing matrix by platform — which platforms are the thinnest?
    missing_by_platform = await db.fetch(
        """
        SELECT platform, COUNT(*) AS n
          FROM v_socials_missing
         GROUP BY platform
         ORDER BY n DESC
        """
    )
    tbl_m = Table(title="Missing rows by platform (active politicians)")
    tbl_m.add_column("platform", style="cyan")
    tbl_m.add_column("missing", justify="right", style="red")
    for r in missing_by_platform:
        tbl_m.add_row(r["platform"], str(r["n"]))
    console.print(tbl_m)

    if no_csv:
        return

    # CSV export — one row per (politician, missing_platform) with enough
    # context that Tier-2/Tier-3 commands can drive discovery without
    # re-querying.
    out = csv_path or os.environ.get("POLITICIAN_SOCIALS_AUDIT_CSV") or DEFAULT_CSV
    rows = await db.fetch(
        """
        SELECT politician_id, name, level, province_territory,
               constituency_name, party, platform,
               official_url, personal_url,
               openparliament_slug, ola_slug, nslegislature_slug,
               lims_member_id, qc_assnat_id, ab_assembly_mid
          FROM v_socials_missing
         ORDER BY level, province_territory, name, platform
        """
    )
    fields = [
        "politician_id", "name", "level", "province_territory",
        "constituency_name", "party", "platform",
        "official_url", "personal_url",
        "openparliament_slug", "ola_slug", "nslegislature_slug",
        "lims_member_id", "qc_assnat_id", "ab_assembly_mid",
    ]
    with open(out, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow({k: ("" if r[k] is None else str(r[k])) for k in fields})
    console.print(f"[green]✓ wrote {len(rows)} missing-row entries to {out}[/green]")
