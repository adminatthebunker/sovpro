"""CLI entry point for the scanner service.

Usage:
    python -m src --help
    python -m src ingest-mps
    python -m src ingest-mlas
    python -m src ingest-councils
    python -m src seed-orgs
    python -m src scan [--limit N] [--stale-hours N]
    python -m src refresh-views
    python -m src stats
"""
from __future__ import annotations

import asyncio
import os
import sys
from typing import Optional

import click
from rich.console import Console
from rich.table import Table

from .db import Database, get_dsn
from .enrich import enrich_alberta_mlas, enrich_federal_mps
from .opennorth import ingest_alberta_extras, ingest_councils, ingest_mlas, ingest_mps
from .scanner import scan_all
from .seed_orgs import seed_organizations
from .stats import print_stats

console = Console()


@click.group()
@click.option("--database-url", envvar="DATABASE_URL", default=None, help="Postgres DSN")
@click.pass_context
def cli(ctx: click.Context, database_url: Optional[str]) -> None:
    """SovereignWatch scanner — ingest, scan, and classify political websites."""
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


@cli.command("seed-orgs")
@click.pass_context
def cmd_seed_orgs(ctx: click.Context) -> None:
    """Seed referendum organizations (idempotent)."""
    asyncio.run(_run(seed_organizations, ctx.obj["dsn"]))


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
