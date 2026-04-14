"""CLI entry point for the scanner service.

Usage:
    python -m src --help
    python -m src ingest-mps
    python -m src ingest-mlas
    python -m src ingest-councils
    python -m src backfill-terms
    python -m src seed-orgs
    python -m src scan [--limit N] [--stale-hours N]
    python -m src refresh-views
    python -m src stats
    python -m src normalize-socials
    python -m src verify-socials [--limit N] [--stale-hours N]
"""
from __future__ import annotations

import asyncio
import os
import sys
from typing import Optional

import click
from rich.console import Console
from rich.table import Table

from .compare_politicians import backfill_initial_terms
from .db import Database, get_dsn
from .enrich import (
    enrich_alberta_mlas,
    enrich_all_legislatures,
    enrich_bc_mlas,
    enrich_federal_mps,
    enrich_manitoba_mlas,
    enrich_new_brunswick_mlas,
    enrich_nl_mhas,
    enrich_nova_scotia_mlas,
    enrich_nunavut_mlas,
    enrich_nwt_mlas,
    enrich_ontario_mpps,
    enrich_pei_mlas,
    enrich_quebec_mnas,
    enrich_saskatchewan_mlas,
    enrich_yukon_mlas,
)
from .opennorth import (
    ingest_alberta_extras,
    ingest_all_councils,
    ingest_all_legislatures,
    ingest_bc_mlas,
    ingest_councils,
    ingest_manitoba_mlas,
    ingest_mlas,
    ingest_mps,
    ingest_new_brunswick_mlas,
    ingest_nl_mhas,
    ingest_nova_scotia_mlas,
    ingest_nunavut_mlas,
    ingest_nwt_mlas,
    ingest_ontario_mpps,
    ingest_pei_mlas,
    ingest_quebec_mnas,
    ingest_saskatchewan_mlas,
    ingest_yukon_mlas,
)
from .scanner import scan_all
from .seed_orgs import seed_organizations
from .socials import normalize_socials, verify_liveness
from .stats import print_stats

console = Console()


@click.group()
@click.option("--database-url", envvar="DATABASE_URL", default=None, help="Postgres DSN")
@click.pass_context
def cli(ctx: click.Context, database_url: Optional[str]) -> None:
    """Canadian Political Data scanner — ingest, scan, and classify political websites."""
    ctx.ensure_object(dict)
    ctx.obj["dsn"] = database_url or get_dsn()


@cli.command("ingest-mps")
@click.option("--limit", type=int, default=500)
@click.pass_context
def cmd_ingest_mps(ctx: click.Context, limit: int) -> None:
    """Fetch federal MPs from Open North."""
    asyncio.run(_run(ingest_mps, ctx.obj["dsn"], limit=limit))


@cli.command("ingest-mlas")
@click.option("--limit", type=int, default=100)
@click.pass_context
def cmd_ingest_mlas(ctx: click.Context, limit: int) -> None:
    """Fetch Alberta MLAs from Open North."""
    asyncio.run(_run(ingest_mlas, ctx.obj["dsn"], limit=limit))


@cli.command("ingest-councils")
@click.pass_context
def cmd_ingest_councils(ctx: click.Context) -> None:
    """Fetch Edmonton + Calgary councils from Open North."""
    asyncio.run(_run(ingest_councils, ctx.obj["dsn"]))


@cli.command("ingest-ab-extras")
@click.pass_context
def cmd_ingest_ab_extras(ctx: click.Context) -> None:
    """Fetch additional Alberta municipal councils (Strathcona, Wood Buffalo, Lethbridge, Grande Prairie)."""
    asyncio.run(_run(ingest_alberta_extras, ctx.obj["dsn"]))


# ─────────────────────────────────────────────────────────────────────
# Provincial / territorial legislature ingestion (Phase 2)
# ─────────────────────────────────────────────────────────────────────


@cli.command("ingest-legislatures")
@click.option("--limit", type=int, default=200,
              help="Max reps to fetch per legislature (default 200 — larger than any province).")
@click.pass_context
def cmd_ingest_legislatures(ctx: click.Context, limit: int) -> None:
    """Fetch MLAs/MPPs/MNAs/MHAs for every province + territory."""
    asyncio.run(_run(ingest_all_legislatures, ctx.obj["dsn"], limit=limit))


@cli.command("ingest-bc-mlas")
@click.option("--limit", type=int, default=200)
@click.pass_context
def cmd_ingest_bc_mlas(ctx: click.Context, limit: int) -> None:
    """Fetch British Columbia MLAs from Open North."""
    asyncio.run(_run(ingest_bc_mlas, ctx.obj["dsn"], limit=limit))


@cli.command("ingest-ontario-mpps")
@click.option("--limit", type=int, default=200)
@click.pass_context
def cmd_ingest_ontario_mpps(ctx: click.Context, limit: int) -> None:
    """Fetch Ontario MPPs from Open North."""
    asyncio.run(_run(ingest_ontario_mpps, ctx.obj["dsn"], limit=limit))


@cli.command("ingest-quebec-mnas")
@click.option("--limit", type=int, default=200)
@click.pass_context
def cmd_ingest_quebec_mnas(ctx: click.Context, limit: int) -> None:
    """Fetch Québec MNAs (Assemblée nationale) from Open North."""
    asyncio.run(_run(ingest_quebec_mnas, ctx.obj["dsn"], limit=limit))


@cli.command("ingest-manitoba-mlas")
@click.option("--limit", type=int, default=200)
@click.pass_context
def cmd_ingest_manitoba_mlas(ctx: click.Context, limit: int) -> None:
    """Fetch Manitoba MLAs from Open North."""
    asyncio.run(_run(ingest_manitoba_mlas, ctx.obj["dsn"], limit=limit))


@cli.command("ingest-saskatchewan-mlas")
@click.option("--limit", type=int, default=200)
@click.pass_context
def cmd_ingest_saskatchewan_mlas(ctx: click.Context, limit: int) -> None:
    """Fetch Saskatchewan MLAs from Open North."""
    asyncio.run(_run(ingest_saskatchewan_mlas, ctx.obj["dsn"], limit=limit))


@cli.command("ingest-nova-scotia-mlas")
@click.option("--limit", type=int, default=200)
@click.pass_context
def cmd_ingest_nova_scotia_mlas(ctx: click.Context, limit: int) -> None:
    """Fetch Nova Scotia MLAs from Open North."""
    asyncio.run(_run(ingest_nova_scotia_mlas, ctx.obj["dsn"], limit=limit))


@cli.command("ingest-new-brunswick-mlas")
@click.option("--limit", type=int, default=200)
@click.pass_context
def cmd_ingest_new_brunswick_mlas(ctx: click.Context, limit: int) -> None:
    """Fetch New Brunswick MLAs from Open North."""
    asyncio.run(_run(ingest_new_brunswick_mlas, ctx.obj["dsn"], limit=limit))


@cli.command("ingest-pei-mlas")
@click.option("--limit", type=int, default=200)
@click.pass_context
def cmd_ingest_pei_mlas(ctx: click.Context, limit: int) -> None:
    """Fetch Prince Edward Island MLAs from Open North."""
    asyncio.run(_run(ingest_pei_mlas, ctx.obj["dsn"], limit=limit))


@cli.command("ingest-nl-mhas")
@click.option("--limit", type=int, default=200)
@click.pass_context
def cmd_ingest_nl_mhas(ctx: click.Context, limit: int) -> None:
    """Fetch Newfoundland & Labrador MHAs from Open North."""
    asyncio.run(_run(ingest_nl_mhas, ctx.obj["dsn"], limit=limit))


@cli.command("ingest-yukon-mlas")
@click.option("--limit", type=int, default=200)
@click.pass_context
def cmd_ingest_yukon_mlas(ctx: click.Context, limit: int) -> None:
    """Fetch Yukon MLAs from Open North."""
    asyncio.run(_run(ingest_yukon_mlas, ctx.obj["dsn"], limit=limit))


@cli.command("ingest-nwt-mlas")
@click.option("--limit", type=int, default=200)
@click.pass_context
def cmd_ingest_nwt_mlas(ctx: click.Context, limit: int) -> None:
    """Fetch Northwest Territories MLAs from Open North."""
    asyncio.run(_run(ingest_nwt_mlas, ctx.obj["dsn"], limit=limit))


@cli.command("ingest-nunavut-mlas")
@click.option("--limit", type=int, default=200)
@click.pass_context
def cmd_ingest_nunavut_mlas(ctx: click.Context, limit: int) -> None:
    """Fetch Nunavut MLAs (currently 0 rows — see opennorth.py TODO)."""
    asyncio.run(_run(ingest_nunavut_mlas, ctx.obj["dsn"], limit=limit))


# ─────────────────────────────────────────────────────────────────────
# Municipal ingestion (Phase 4)
# ─────────────────────────────────────────────────────────────────────


@cli.command("ingest-all-councils")
@click.option("--limit", "limit_per_set", type=int, default=200,
              help="Max councillors to ingest per municipal set")
@click.pass_context
def cmd_ingest_all_councils(ctx: click.Context, limit_per_set: int) -> None:
    """Fetch every municipal council Open North indexes (Phase 4)."""
    asyncio.run(_run(ingest_all_councils, ctx.obj["dsn"],
                     limit_per_set=limit_per_set))


@cli.command("seed-orgs")
@click.pass_context
def cmd_seed_orgs(ctx: click.Context) -> None:
    """Seed referendum organizations (idempotent)."""
    asyncio.run(_run(seed_organizations, ctx.obj["dsn"]))


@cli.command("backfill-terms")
@click.pass_context
def cmd_backfill_terms(ctx: click.Context) -> None:
    """One-time: open an initial politician_terms row for every active
    politician without an existing open term."""
    async def _wrap(db: Database) -> None:
        stats = await backfill_initial_terms(db)
        console.print(
            f"[green]backfill-terms[/green]: inserted={stats['inserted']} "
            f"skipped={stats['skipped']} candidates={stats['candidates']}"
        )
    asyncio.run(_run(_wrap, ctx.obj["dsn"]))


@cli.command("enrich-mps")
@click.option("--limit", type=int, default=None)
@click.option("--force", is_flag=True, help="Re-discover even if personal_url is set")
@click.pass_context
def cmd_enrich_mps(ctx: click.Context, limit, force) -> None:
    """Discover personal/campaign websites for federal MPs (via ourcommons.ca)."""
    asyncio.run(_run(enrich_federal_mps, ctx.obj["dsn"], limit=limit, force=force))


@cli.command("enrich-mlas")
@click.option("--limit", type=int, default=None)
@click.pass_context
def cmd_enrich_mlas(ctx: click.Context, limit) -> None:
    """Discover personal websites for Alberta MLAs (via assembly.ab.ca)."""
    asyncio.run(_run(enrich_alberta_mlas, ctx.obj["dsn"], limit=limit))


# ─────────────────────────────────────────────────────────────────────
# Per-legislature enrichment (Phase 3)
# ─────────────────────────────────────────────────────────────────────


@cli.command("enrich-legislatures")
@click.option("--limit", type=int, default=None,
              help="Max rows per province (default: all without personal_url).")
@click.pass_context
def cmd_enrich_legislatures(ctx: click.Context, limit) -> None:
    """Run every provincial/territorial enricher in sequence."""
    asyncio.run(_run(enrich_all_legislatures, ctx.obj["dsn"], limit=limit))


@cli.command("enrich-bc-mlas")
@click.option("--limit", type=int, default=None)
@click.pass_context
def cmd_enrich_bc_mlas(ctx: click.Context, limit) -> None:
    """Discover personal sites for BC MLAs (via leg.bc.ca)."""
    asyncio.run(_run(enrich_bc_mlas, ctx.obj["dsn"], limit=limit))


@cli.command("enrich-ontario-mpps")
@click.option("--limit", type=int, default=None)
@click.pass_context
def cmd_enrich_ontario_mpps(ctx: click.Context, limit) -> None:
    """Discover personal sites for Ontario MPPs (via ola.org)."""
    asyncio.run(_run(enrich_ontario_mpps, ctx.obj["dsn"], limit=limit))


@cli.command("enrich-quebec-mnas")
@click.option("--limit", type=int, default=None)
@click.pass_context
def cmd_enrich_quebec_mnas(ctx: click.Context, limit) -> None:
    """Discover personal sites for Québec MNAs (via assnat.qc.ca)."""
    asyncio.run(_run(enrich_quebec_mnas, ctx.obj["dsn"], limit=limit))


@cli.command("enrich-manitoba-mlas")
@click.option("--limit", type=int, default=None)
@click.pass_context
def cmd_enrich_manitoba_mlas(ctx: click.Context, limit) -> None:
    """Discover personal sites for Manitoba MLAs (via gov.mb.ca/legislature)."""
    asyncio.run(_run(enrich_manitoba_mlas, ctx.obj["dsn"], limit=limit))


@cli.command("enrich-saskatchewan-mlas")
@click.option("--limit", type=int, default=None)
@click.pass_context
def cmd_enrich_saskatchewan_mlas(ctx: click.Context, limit) -> None:
    """Discover personal sites for Saskatchewan MLAs (via legassembly.sk.ca)."""
    asyncio.run(_run(enrich_saskatchewan_mlas, ctx.obj["dsn"], limit=limit))


@cli.command("enrich-nova-scotia-mlas")
@click.option("--limit", type=int, default=None)
@click.pass_context
def cmd_enrich_nova_scotia_mlas(ctx: click.Context, limit) -> None:
    """Discover personal sites for Nova Scotia MLAs (via nslegislature.ca)."""
    asyncio.run(_run(enrich_nova_scotia_mlas, ctx.obj["dsn"], limit=limit))


@cli.command("enrich-new-brunswick-mlas")
@click.option("--limit", type=int, default=None)
@click.pass_context
def cmd_enrich_new_brunswick_mlas(ctx: click.Context, limit) -> None:
    """Discover personal sites for NB MLAs (via legnb.ca)."""
    asyncio.run(_run(enrich_new_brunswick_mlas, ctx.obj["dsn"], limit=limit))


@cli.command("enrich-pei-mlas")
@click.option("--limit", type=int, default=None)
@click.pass_context
def cmd_enrich_pei_mlas(ctx: click.Context, limit) -> None:
    """Discover personal sites for PEI MLAs (via assembly.pe.ca)."""
    asyncio.run(_run(enrich_pei_mlas, ctx.obj["dsn"], limit=limit))


@cli.command("enrich-nl-mhas")
@click.option("--limit", type=int, default=None)
@click.pass_context
def cmd_enrich_nl_mhas(ctx: click.Context, limit) -> None:
    """Discover personal sites for NL MHAs (via assembly.nl.ca)."""
    asyncio.run(_run(enrich_nl_mhas, ctx.obj["dsn"], limit=limit))


@cli.command("enrich-yukon-mlas")
@click.option("--limit", type=int, default=None)
@click.pass_context
def cmd_enrich_yukon_mlas(ctx: click.Context, limit) -> None:
    """Discover personal sites for Yukon MLAs (via yukonassembly.ca)."""
    asyncio.run(_run(enrich_yukon_mlas, ctx.obj["dsn"], limit=limit))


@cli.command("enrich-nwt-mlas")
@click.option("--limit", type=int, default=None)
@click.pass_context
def cmd_enrich_nwt_mlas(ctx: click.Context, limit) -> None:
    """Discover personal sites for NWT MLAs (via ntlegislativeassembly.ca)."""
    asyncio.run(_run(enrich_nwt_mlas, ctx.obj["dsn"], limit=limit))


@cli.command("enrich-nunavut-mlas")
@click.pass_context
def cmd_enrich_nunavut_mlas(ctx: click.Context) -> None:
    """Stub — Nunavut has no ingested politicians yet (see opennorth.py TODO)."""
    asyncio.run(_run(enrich_nunavut_mlas, ctx.obj["dsn"]))


# ─────────────────────────────────────────────────────────────────────
# Socials (Phase 5)
# ─────────────────────────────────────────────────────────────────────


@cli.command("normalize-socials")
@click.pass_context
def cmd_normalize_socials(ctx: click.Context) -> None:
    """Explode politicians.social_urls JSONB into politician_socials rows."""
    asyncio.run(_run(normalize_socials, ctx.obj["dsn"]))


@cli.command("verify-socials")
@click.option("--limit", type=int, default=500, help="Max rows to verify per run")
@click.option("--stale-hours", type=int, default=168,
              help="Re-verify rows whose last_verified_at is older than this")
@click.pass_context
def cmd_verify_socials(ctx: click.Context, limit: int, stale_hours: int) -> None:
    """Issue liveness checks against each politician_socials URL."""
    asyncio.run(_run(verify_liveness, ctx.obj["dsn"],
                     limit=limit, stale_hours=stale_hours))


@cli.command("scan")
@click.option("--limit", type=int, default=None, help="Max websites to scan")
@click.option("--stale-hours", type=int, default=24,
              help="Skip sites scanned within this many hours (0 = scan all)")
@click.option("--concurrency", type=int, default=None, help="Override SCANNER_CONCURRENCY")
@click.option("--only", type=click.Choice(["politician", "organization"]), default=None)
@click.pass_context
def cmd_scan(ctx: click.Context, limit, stale_hours, concurrency, only) -> None:
    """Scan websites (DNS, GeoIP, TLS, HTTP)."""
    asyncio.run(_run(scan_all, ctx.obj["dsn"],
                     limit=limit, stale_hours=stale_hours,
                     concurrency=concurrency, owner_type=only))


@cli.command("refresh-views")
@click.pass_context
def cmd_refresh(ctx: click.Context) -> None:
    """Refresh map materialized views."""
    async def _run_refresh(dsn: str) -> None:
        db = Database(dsn)
        await db.connect()
        try:
            await db.pool.execute("SELECT refresh_map_views();")
            console.print("[green]Materialized views refreshed[/green]")
        finally:
            await db.close()

    asyncio.run(_run_refresh(ctx.obj["dsn"]))


@cli.command("stats")
@click.pass_context
def cmd_stats(ctx: click.Context) -> None:
    """Print sovereignty summary."""
    asyncio.run(_stats(ctx.obj["dsn"]))


async def _run(func, dsn: str, **kwargs) -> None:
    db = Database(dsn)
    await db.connect()
    try:
        await func(db, **kwargs)
    finally:
        await db.close()


async def _stats(dsn: str) -> None:
    db = Database(dsn)
    await db.connect()
    try:
        await print_stats(db, console)
    finally:
        await db.close()


if __name__ == "__main__":
    try:
        cli(obj={})
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted[/yellow]")
        sys.exit(130)
