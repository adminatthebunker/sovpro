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

from .committees import (
    ingest_ab_committees,
    ingest_all_committees,
    ingest_federal_committees,
)
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
from .socials import bulk_import_socials, normalize_socials, verify_liveness
from .socials_audit import audit_socials
from .socials_probe import PLATFORMS_SUPPORTED, probe_missing_socials
from .socials_agent import (
    DEFAULT_BATCH_SIZE as AGENT_DEFAULT_BATCH_SIZE,
    DEFAULT_MODEL as AGENT_DEFAULT_MODEL,
    agent_find_socials,
)
from .resolve_openparliament import resolve_slugs
from .socials_enrichment import (
    enrich_all_socials,
    enrich_from_openparl,
    enrich_from_wikidata,
    enrich_mastodon_candidates,
)
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


@cli.command("audit-socials")
@click.option("--csv", "csv_path", default=None,
              help="Where to write the missing-rows CSV (default $POLITICIAN_SOCIALS_AUDIT_CSV or /tmp/politician_socials_audit.csv)")
@click.option("--no-csv", is_flag=True, help="Skip CSV export; just print tables")
@click.pass_context
def cmd_audit_socials(ctx: click.Context, csv_path, no_csv) -> None:
    """Snapshot social coverage and refresh v_socials_missing view."""
    asyncio.run(_run(audit_socials, ctx.obj["dsn"],
                     csv_path=csv_path, no_csv=no_csv))


@cli.command("probe-missing-socials")
@click.option("--platform", type=click.Choice(list(PLATFORMS_SUPPORTED)),
              default="bluesky",
              help="Which missing platform to probe (default: bluesky)")
@click.option("--limit", type=int, default=500,
              help="Max v_socials_missing rows to process this run")
@click.option("--dry-run", is_flag=True,
              help="Print would-be inserts without writing")
@click.pass_context
def cmd_probe_missing_socials(ctx: click.Context, platform: str,
                              limit: int, dry_run: bool) -> None:
    """Tier-2: pattern-probe URL candidates and upsert scored hits."""
    asyncio.run(_run(probe_missing_socials, ctx.obj["dsn"],
                     platform=platform, limit=limit, dry_run=dry_run))


@cli.command("agent-missing-socials")
@click.option("--platform", type=str, default=None,
              help="Focus on a single platform (e.g. twitter). Default: all missing platforms per politician.")
@click.option("--batch-size", type=int, default=AGENT_DEFAULT_BATCH_SIZE,
              help="Politicians per agent call (capped at 25)")
@click.option("--max-batches", type=int, default=20,
              help="Hard cap on agent calls per invocation")
@click.option("--model", type=str, default=AGENT_DEFAULT_MODEL)
@click.option("--dry-run", is_flag=True,
              help="Print candidate hits without inserting")
@click.pass_context
def cmd_agent_missing_socials(ctx: click.Context, platform, batch_size,
                              max_batches, model, dry_run) -> None:
    """Tier-3: Sonnet agent + web_search for residual missing socials."""
    asyncio.run(_run(agent_find_socials, ctx.obj["dsn"],
                     platform=platform, batch_size=batch_size,
                     max_batches=max_batches, model=model,
                     dry_run=dry_run))


@cli.command("verify-socials")
@click.option("--limit", type=int, default=500, help="Max rows to verify per run")
@click.option("--stale-hours", type=int, default=168,
              help="Re-verify rows whose last_verified_at is older than this")
@click.pass_context
def cmd_verify_socials(ctx: click.Context, limit: int, stale_hours: int) -> None:
    """Issue liveness checks against each politician_socials URL."""
    asyncio.run(_run(verify_liveness, ctx.obj["dsn"],
                     limit=limit, stale_hours=stale_hours))


@cli.command("bulk-import-socials")
@click.option("--input", "input_path", required=True,
              help="Path to JSONL (one {politician_id, urls:[...]} per line)")
@click.pass_context
def cmd_bulk_import_socials(ctx: click.Context, input_path: str) -> None:
    """Import agent-discovered social URLs via the canonical upserter."""
    asyncio.run(_run(bulk_import_socials, ctx.obj["dsn"], input_path=input_path))


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


@cli.command("backfill-politician-photos")
@click.option("--limit", type=int, default=None,
              help="Cap the number of politicians processed this run.")
@click.option("--stale-days", type=int, default=30,
              help="Re-fetch photos whose last fetch is older than N days.")
@click.option("--politician-id", type=str, default=None,
              help="Process a single politician by UUID (overrides limit/stale filters).")
@click.option("--concurrency", type=int, default=4,
              help="Parallel fetches. Per-host rate limiting still applies.")
@click.pass_context
def cmd_backfill_photos(
    ctx: click.Context,
    limit: Optional[int],
    stale_days: int,
    politician_id: Optional[str],
    concurrency: int,
) -> None:
    """Mirror upstream politician portraits onto the local `assets` volume.

    Writes to /assets/politicians/<uuid>.<ext> and updates politicians.photo_path
    + photo_bytes_hash + photo_fetched_at + photo_source_url. The original
    photo_url is left untouched for attribution and re-fetch.
    """
    from .photos import backfill_politician_photos

    async def _wrap(db: Database) -> None:
        stats = await backfill_politician_photos(
            db,
            limit=limit,
            stale_days=stale_days,
            politician_id=politician_id,
            concurrency=concurrency,
        )
        console.print(f"[green]backfill-politician-photos[/green]: {stats.summary()}")
        for sample in stats.fail_samples:
            console.print(f"  [yellow]fail[/yellow] {sample}")

    asyncio.run(_run(_wrap, ctx.obj["dsn"]))


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


# ─────────────────────────────────────────────────────────────────────
# Committee ingestion (Team C)
# ─────────────────────────────────────────────────────────────────────


@cli.command("ingest-committees-federal")
@click.pass_context
def cmd_ingest_committees_federal(ctx: click.Context) -> None:
    """Scrape parl.ca / ourcommons.ca committee members into politician_committees."""
    asyncio.run(_run(ingest_federal_committees, ctx.obj["dsn"]))


@cli.command("ingest-committees-ab")
@click.pass_context
def cmd_ingest_committees_ab(ctx: click.Context) -> None:
    """Scrape assembly.ab.ca committee membership into politician_committees."""
    asyncio.run(_run(ingest_ab_committees, ctx.obj["dsn"]))


@cli.command("ingest-committees-all")
@click.pass_context
def cmd_ingest_committees_all(ctx: click.Context) -> None:
    """Run every available committee ingester (federal + implemented provinces)."""
    asyncio.run(_run(ingest_all_committees, ctx.obj["dsn"]))


# ─────────────────────────────────────────────────────────────────────
# Socials enrichment from external sources (Team B)
# ─────────────────────────────────────────────────────────────────────


@cli.command("enrich-socials-wikidata")
@click.option("--level", type=click.Choice(["federal", "provincial"]),
              default=None, help="Restrict to one level; default covers all.")
@click.pass_context
def cmd_enrich_socials_wikidata(ctx: click.Context, level) -> None:
    """Pull handles for Canadian legislators via Wikidata SPARQL."""
    async def _wrap(db: Database) -> None:
        n = await enrich_from_wikidata(db, level=level)
        console.print(f"[green]wikidata enrichment inserted {n} rows[/green]")
    asyncio.run(_run(_wrap, ctx.obj["dsn"]))


@cli.command("enrich-socials-openparl")
@click.pass_context
def cmd_enrich_socials_openparl(ctx: click.Context) -> None:
    """Backfill federal-MP handles from openparliament.ca detail pages."""
    async def _wrap(db: Database) -> None:
        n = await enrich_from_openparl(db)
        console.print(f"[green]openparl enrichment inserted {n} rows[/green]")
    asyncio.run(_run(_wrap, ctx.obj["dsn"]))


@cli.command("resolve-openparliament-slugs")
@click.pass_context
def cmd_resolve_openparliament_slugs(ctx: click.Context) -> None:
    """Match our federal MPs to their openparliament.ca URL slugs.

    Populates politicians.openparliament_slug via name-matching against
    openparliament.ca's public list. Re-entrant: skips MPs that already
    have a slug. Run after each federal ingest to pick up by-election
    winners.
    """
    async def _wrap(db: Database) -> None:
        await resolve_slugs(db)
    asyncio.run(_run(_wrap, ctx.obj["dsn"]))


@cli.command("enrich-socials-mastodon")
@click.pass_context
def cmd_enrich_socials_mastodon(ctx: click.Context) -> None:
    """Probe canada.masto.host for plausible politician handles."""
    async def _wrap(db: Database) -> None:
        n = await enrich_mastodon_candidates(db)
        console.print(f"[green]mastodon enrichment inserted {n} rows[/green]")
    asyncio.run(_run(_wrap, ctx.obj["dsn"]))


@cli.command("enrich-socials-all")
@click.pass_context
def cmd_enrich_socials_all(ctx: click.Context) -> None:
    """Run wikidata → openparl → mastodon enrichers in order."""
    asyncio.run(_run(enrich_all_socials, ctx.obj["dsn"]))


# ─────────────────────────────────────────────────────────────────────
# Municipal enrichment (Team D)
# ─────────────────────────────────────────────────────────────────────


@cli.command("enrich-municipal")
@click.option("--limit", type=int, default=None,
              help="Max councillors to enrich (default: all without personal_url).")
@click.option("--concurrency", type=int, default=6,
              help="Max parallel HTTP connections across hosts.")
@click.pass_context
def cmd_enrich_municipal(ctx: click.Context, limit, concurrency: int) -> None:
    """Discover per-councillor personal/campaign sites across 108 councils.

    Covers every municipal politician ingested via Open North (Phase 4).
    Uses a handful of CMS-specific scrapers for the large platforms
    (Drupal-Ottawa, WordPress-Mississauga) plus a name-aware generic scorer
    that works against any municipal site. Respects robots.txt per host.
    """
    from .muni_enrich import enrich_municipal
    asyncio.run(_run(enrich_municipal, ctx.obj["dsn"],
                     limit=limit, concurrency=concurrency))


# ─────────────────────────────────────────────────────────────────────
# Gap fillers (Team A — web-research-driven)
# ─────────────────────────────────────────────────────────────────────
# Direct scrapers for legislatures Open North either doesn't cover
# (Nunavut) or leaves with unusable data (NB/NL empty url field,
# BC mostly-missing roster, Yukon Cloudflare-blocked). Each command is
# a thin wrapper around the corresponding gap_fillers submodule.
from .legislative.ns_bills import ingest_ns_bills  # noqa: E402
from .legislative.ns_bill_pages import fetch_ns_bill_pages  # noqa: E402
from .legislative.ns_bill_parse import parse_ns_bill_pages  # noqa: E402
from .legislative.on_bills import (  # noqa: E402
    discover_ola_bills, fetch_ola_bill_pages, parse_ola_bill_pages,
)
from .legislative.sponsor_resolver import resolve_sponsors  # noqa: E402
from .legislative.bc_bills import (  # noqa: E402
    enrich_bc_member_ids, ingest_bc_bills,
)
from .legislative.ns_rss import ingest_ns_rss  # noqa: E402
from .legislative.qc_mnas import enrich_qc_mna_ids  # noqa: E402
from .legislative.qc_bills import (  # noqa: E402
    fetch_qc_bill_sponsors, ingest_qc_bills_csv, ingest_qc_bills_rss,
)
from .legislative.ab_mlas import enrich_ab_mla_ids  # noqa: E402
from .legislative.ab_bills import ingest_ab_bills  # noqa: E402
from .legislative.nb_bills import ingest_nb_bills  # noqa: E402
from .legislative.nl_bills import ingest_nl_bills  # noqa: E402
from .legislative.nt_bills import ingest_nt_bills  # noqa: E402
from .legislative.nu_bills import ingest_nu_bills  # noqa: E402
from .gap_fillers import bc as _gf_bc  # noqa: E402
from .gap_fillers import nb as _gf_nb  # noqa: E402
from .gap_fillers import nl as _gf_nl  # noqa: E402
from .gap_fillers import nunavut as _gf_nunavut  # noqa: E402
from .gap_fillers import ontario as _gf_ontario  # noqa: E402
from .gap_fillers import yukon as _gf_yukon  # noqa: E402
from .gap_fillers.runner import run_all as _gf_run_all  # noqa: E402


@cli.command("fill-gaps")
@click.pass_context
def cmd_fill_gaps(ctx: click.Context) -> None:
    """Run every gap-filler (NU/YT/NB/NL/BC/ON) in sequence."""
    asyncio.run(_run(_gf_run_all, ctx.obj["dsn"]))


@cli.command("fill-nunavut")
@click.pass_context
def cmd_fill_nunavut(ctx: click.Context) -> None:
    """Scrape assembly.nu.ca for the 22 Nunavut MLAs (consensus government)."""
    asyncio.run(_run(_gf_nunavut.run, ctx.obj["dsn"]))


@cli.command("fill-yukon")
@click.pass_context
def cmd_fill_yukon(ctx: click.Context) -> None:
    """Bootstrap Yukon (21 MLAs) from Wikipedia — yukonassembly.ca is Cloudflare-blocked."""
    asyncio.run(_run(_gf_yukon.run, ctx.obj["dsn"]))


@cli.command("fill-nb")
@click.pass_context
def cmd_fill_nb(ctx: click.Context) -> None:
    """Scrape legnb.ca for the 49 NB MLA roster (Open North returns empty URLs)."""
    asyncio.run(_run(_gf_nb.run, ctx.obj["dsn"]))


@cli.command("fill-nl")
@click.pass_context
def cmd_fill_nl(ctx: click.Context) -> None:
    """Scrape assembly.nl.ca for the 40 NL MHA roster (Open North returns empty URLs)."""
    asyncio.run(_run(_gf_nl.run, ctx.obj["dsn"]))


@cli.command("fill-bc")
@click.pass_context
def cmd_fill_bc(ctx: click.Context) -> None:
    """Seed BC (93 MLAs) from Wikipedia + leg.bc.ca email table (Open North has only 5)."""
    asyncio.run(_run(_gf_bc.run, ctx.obj["dsn"]))


@cli.command("fill-ontario")
@click.pass_context
def cmd_fill_ontario(ctx: click.Context) -> None:
    """Fill Ontario MPP personal URLs + socials via OLP caucus / Wikipedia / Wikidata / DNS-probe."""
    async def _wrap(db: Database) -> None:
        stats = await _gf_ontario.fill_ontario(db)
        console.print(
            f"[green]fill-ontario summary[/green]: "
            f"personal_urls={stats['personal_urls']} "
            f"socials={stats['socials']} "
            f"unmatched={stats['unmatched']}"
        )
    asyncio.run(_run(_wrap, ctx.obj["dsn"]))


# ─────────────────────────────────────────────────────────────────────
# Provincial legislative activity — bills (Nova Scotia first)
# ─────────────────────────────────────────────────────────────────────


@cli.command("ingest-ns-bills")
@click.option("--limit", type=int, default=None,
              help="Cap total records (for smoke tests). Default: all ~3.5k bills.")
@click.pass_context
def cmd_ingest_ns_bills(ctx: click.Context, limit) -> None:
    """Ingest Nova Scotia bills from the Socrata dataset iz5x-dzyf.

    Populates legislative_sessions, bills, and bill_events. Sponsor
    resolution is a separate pass — Socrata does not expose sponsor names.
    """
    async def _wrap(db: Database) -> None:
        stats = await ingest_ns_bills(db, limit=limit)
        console.print(
            f"[green]ingest-ns-bills[/green]: "
            f"bills={stats['bills']} events={stats['events']} "
            f"sessions={stats['sessions']} skipped={stats['skipped']}"
        )
    asyncio.run(_run(_wrap, ctx.obj["dsn"]))


@cli.command("fetch-ns-bill-pages")
@click.option("--limit", type=int, default=None,
              help="Max bills to fetch this run (default: all pending).")
@click.option("--force", is_flag=True,
              help="Re-fetch even bills whose HTML is already cached.")
@click.option("--delay", "delay_secs", type=float, default=4.0,
              help="Minimum delay between requests (seconds). Default 4.0.")
@click.option("--jitter", "jitter_secs", type=float, default=2.0,
              help="Additional 0..jitter seconds random delay. Default 2.0.")
@click.pass_context
def cmd_fetch_ns_bill_pages(ctx: click.Context, limit, force, delay_secs, jitter_secs) -> None:
    """Fetch + cache nslegislature.ca HTML for every bill (phase 2).

    Idempotent: skips bills with raw_html already populated unless --force.
    At 4–6 sec per request, a full 3,500-bill backlog takes ~4–6 hours.
    Halts on WAF fingerprint detection so progress isn't wasted fighting
    a live block.
    """
    async def _wrap(db: Database) -> None:
        stats = await fetch_ns_bill_pages(
            db, limit=limit, force=force,
            delay_secs=delay_secs, jitter_secs=jitter_secs,
        )
        flag = " [yellow](WAF-aborted)[/yellow]" if stats["waf_aborted"] else ""
        console.print(
            f"[green]fetch-ns-bill-pages[/green]{flag}: "
            f"ok={stats['ok']} err={stats['err']} total={stats['total']}"
        )
    asyncio.run(_run(_wrap, ctx.obj["dsn"]))


@cli.command("discover-on-bills")
@click.option("--parliament", type=int, default=44)
@click.option("--session", type=int, default=1)
@click.pass_context
def cmd_discover_on_bills(ctx: click.Context, parliament: int, session: int) -> None:
    """Enumerate Ontario bills from ola.org session index (phase 1)."""
    async def _wrap(db: Database) -> None:
        stats = await discover_ola_bills(db, parliament=parliament, session=session)
        console.print(
            f"[green]discover-on-bills[/green] P{parliament}-S{session}: "
            f"bills={stats['bills']}"
        )
    asyncio.run(_run(_wrap, ctx.obj["dsn"]))


@cli.command("fetch-on-bill-pages")
@click.option("--limit", type=int, default=None)
@click.option("--force", is_flag=True)
@click.option("--delay", "delay_secs", type=float, default=1.5)
@click.option("--jitter", "jitter_secs", type=float, default=1.0)
@click.pass_context
def cmd_fetch_on_bill_pages(ctx: click.Context, limit, force, delay_secs, jitter_secs) -> None:
    """Fetch + cache ola.org bill page + /status sub-page (phase 2)."""
    async def _wrap(db: Database) -> None:
        stats = await fetch_ola_bill_pages(
            db, limit=limit, force=force,
            delay_secs=delay_secs, jitter_secs=jitter_secs,
        )
        flag = " [yellow](WAF-aborted)[/yellow]" if stats["waf_aborted"] else ""
        console.print(
            f"[green]fetch-on-bill-pages[/green]{flag}: "
            f"main={stats['main_ok']} status={stats['status_ok']} "
            f"err={stats['err']} total={stats['total']}"
        )
    asyncio.run(_run(_wrap, ctx.obj["dsn"]))


@cli.command("parse-on-bill-pages")
@click.option("--limit", type=int, default=None)
@click.pass_context
def cmd_parse_on_bill_pages(ctx: click.Context, limit) -> None:
    """Parse cached ola.org HTML into sponsors + events (phase 3)."""
    async def _wrap(db: Database) -> None:
        stats = await parse_ola_bill_pages(db, limit=limit)
        console.print(
            f"[green]parse-on-bill-pages[/green]: "
            f"bills={stats['bills']} sponsors={stats['sponsors']} "
            f"events={stats['events']} titled={stats['titled']} "
            f"no_sponsor={stats['no_sponsor']}"
        )
    asyncio.run(_run(_wrap, ctx.obj["dsn"]))


@cli.command("ingest-ns-bills-rss")
@click.pass_context
def cmd_ingest_ns_bills_rss(ctx: click.Context) -> None:
    """Refresh current-session NS bills from the public RSS feed.

    One request — no WAF budget impact. Adds richer status text +
    commencement metadata for current-session bills that already
    exist in the DB (via Socrata). Idempotent and safe to schedule.
    """
    async def _wrap(db: Database) -> None:
        stats = await ingest_ns_rss(db)
        console.print(
            f"[green]ingest-ns-bills-rss[/green]: "
            f"items={stats['items']} matched={stats['matched']} "
            f"updated={stats['updated']} events_added={stats['events_added']} "
            f"unmatched={stats['unmatched']}"
        )
    asyncio.run(_run(_wrap, ctx.obj["dsn"]))


@cli.command("enrich-bc-member-ids")
@click.pass_context
def cmd_enrich_bc_member_ids(ctx: click.Context) -> None:
    """Populate politicians.lims_member_id via LIMS GraphQL allMembers.

    Name-matches active BC provincial politicians against the LIMS
    member roster. Run before ingest-bc-bills so sponsor resolution
    becomes an exact integer FK lookup.
    """
    async def _wrap(db: Database) -> None:
        stats = await enrich_bc_member_ids(db)
        console.print(
            f"[green]enrich-bc-member-ids[/green]: "
            f"scanned={stats['politicians_scanned']} "
            f"linked={stats['linked']} ambiguous={stats['ambiguous']}"
        )
    asyncio.run(_run(_wrap, ctx.obj["dsn"]))


@cli.command("ingest-bc-bills")
@click.option("--all-sessions", is_flag=True,
              help="Backfill every historical BC session (default: current only).")
@click.option("--parliament", type=int, default=None)
@click.option("--session", type=int, default=None)
@click.pass_context
def cmd_ingest_bc_bills(ctx: click.Context, all_sessions, parliament, session) -> None:
    """Ingest BC bills from LIMS PDMS.

    Default: current session only. Use --all-sessions for full history,
    or --parliament/--session for a single specific session.
    """
    async def _wrap(db: Database) -> None:
        stats = await ingest_bc_bills(
            db,
            current_only=not all_sessions and parliament is None,
            parliament=parliament, session=session,
        )
        console.print(
            f"[green]ingest-bc-bills[/green]: "
            f"sessions={stats['sessions_touched']} bills={stats['bills']} "
            f"events={stats['events']} sponsors={stats['sponsors']} "
            f"sponsors_linked={stats['sponsors_linked']}"
        )
    asyncio.run(_run(_wrap, ctx.obj["dsn"]))


@cli.command("resolve-bill-sponsors")
@click.option("--limit", type=int, default=None)
@click.pass_context
def cmd_resolve_bill_sponsors(ctx: click.Context, limit) -> None:
    """Link bill_sponsors → politicians via slug join + name match.

    Pure offline. Re-entrant: only touches rows with politician_id NULL.
    As it links by name, it backfills politicians.<source>_slug so
    subsequent runs short-circuit to the slug index.
    """
    async def _wrap(db: Database) -> None:
        stats = await resolve_sponsors(db, limit=limit)
        console.print(
            f"[green]resolve-bill-sponsors[/green]: "
            f"scanned={stats['scanned']} by_slug={stats['linked_by_slug']} "
            f"by_name={stats['linked_by_name']} "
            f"slugs_backfilled={stats['slugs_backfilled']} "
            f"unmatched={stats['unmatched']}"
        )
    asyncio.run(_run(_wrap, ctx.obj["dsn"]))


@cli.command("enrich-qc-mna-ids")
@click.pass_context
def cmd_enrich_qc_mna_ids(ctx: click.Context) -> None:
    """Populate politicians.qc_assnat_id by scraping the MNA index page.

    Numeric MNA ids are embedded in the profile-URL slug. Run before
    fetch-qc-bill-sponsors so bill sponsor resolution becomes an exact
    integer FK lookup — no name-fuzz, no ambiguity.
    """
    async def _wrap(db: Database) -> None:
        stats = await enrich_qc_mna_ids(db)
        console.print(
            f"[green]enrich-qc-mna-ids[/green]: "
            f"scanned={stats['politicians_scanned']} "
            f"linked={stats['linked']} ambiguous={stats['ambiguous']}"
        )
    asyncio.run(_run(_wrap, ctx.obj["dsn"]))


@cli.command("ingest-qc-bills")
@click.option("--all-sessions", is_flag=True,
              help="Ingest every session in the CSV (default: current only).")
@click.pass_context
def cmd_ingest_qc_bills(ctx: click.Context, all_sessions) -> None:
    """Ingest Quebec bills from the donneesquebec.ca CSV export.

    Authoritative daily snapshot — one HTTP GET for the whole roster.
    Emits one bill_events row per bill (the last stage reached). Run
    ingest-qc-bills-rss after this to fill in the full stage timeline.
    """
    async def _wrap(db: Database) -> None:
        stats = await ingest_qc_bills_csv(db, current_only=not all_sessions)
        console.print(
            f"[green]ingest-qc-bills[/green]: "
            f"rows={stats['rows']} sessions={stats['sessions_touched']} "
            f"bills={stats['bills']} events={stats['events']}"
        )
    asyncio.run(_run(_wrap, ctx.obj["dsn"]))


@cli.command("ingest-qc-bills-rss")
@click.pass_context
def cmd_ingest_qc_bills_rss(ctx: click.Context) -> None:
    """Refresh current-session QC stage events from the public RSS feed.

    One request — every stage transition on every current-session bill.
    Idempotent (bill_events_uniq). Safe to schedule daily.
    """
    async def _wrap(db: Database) -> None:
        stats = await ingest_qc_bills_rss(db)
        console.print(
            f"[green]ingest-qc-bills-rss[/green]: "
            f"items={stats['items']} matched={stats['matched']} "
            f"events_added={stats['events_added']} "
            f"unmatched={stats['unmatched']}"
        )
    asyncio.run(_run(_wrap, ctx.obj["dsn"]))


@cli.command("fetch-qc-bill-sponsors")
@click.option("--limit", type=int, default=None,
              help="Cap bills scanned this run (default: every un-sponsored bill).")
@click.option("--delay", type=float, default=2.0,
              help="Delay between HTTP requests (seconds).")
@click.pass_context
def cmd_fetch_qc_bill_sponsors(ctx: click.Context, limit, delay) -> None:
    """Fetch QC bill detail pages and link sponsors by MNA numeric id.

    ~150 bills/session; 2s default delay = ~5 min to complete. Direct
    FK lookup via politicians.qc_assnat_id, so any bill whose sponsor
    is a current sitting MNA resolves cleanly. Skips bills that already
    have a sponsor row — safe to re-run.
    """
    async def _wrap(db: Database) -> None:
        stats = await fetch_qc_bill_sponsors(db, limit=limit, delay_seconds=delay)
        console.print(
            f"[green]fetch-qc-bill-sponsors[/green]: "
            f"scanned={stats['scanned']} fetched={stats['pages_fetched']} "
            f"sponsors={stats['sponsors']} linked={stats['sponsors_linked']} "
            f"no_sponsor={stats['no_sponsor_found']} "
            f"not_found={stats['not_found']} errors={stats['errors']}"
        )
    asyncio.run(_run(_wrap, ctx.obj["dsn"]))


@cli.command("enrich-ab-mla-ids")
@click.pass_context
def cmd_enrich_ab_mla_ids(ctx: click.Context) -> None:
    """Populate politicians.ab_assembly_mid by scraping the MLAs index page.

    Zero-padded 4-char mids are embedded in profile-URL query strings.
    Run before ingest-ab-bills so sponsor resolution is an exact FK
    lookup — no name-fuzz.
    """
    async def _wrap(db: Database) -> None:
        stats = await enrich_ab_mla_ids(db)
        console.print(
            f"[green]enrich-ab-mla-ids[/green]: "
            f"scanned={stats['politicians_scanned']} "
            f"linked={stats['linked']} ambiguous={stats['ambiguous']}"
        )
    asyncio.run(_run(_wrap, ctx.obj["dsn"]))


@cli.command("ingest-ab-bills")
@click.option("--legislature", type=int, default=None,
              help="One specific legislature (pair with --session for one session).")
@click.option("--session", type=int, default=None,
              help="One specific session (requires --legislature).")
@click.option("--all-sessions-in-legislature", type=int, default=None,
              metavar="L", help="Every session in legislature L.")
@click.option("--all-sessions", is_flag=True,
              help="Backfill every session ever (Legislature 1 onward, ~137 sessions).")
@click.option("--delay", type=float, default=1.5,
              help="Delay between session fetches (seconds).")
@click.pass_context
def cmd_ingest_ab_bills(
    ctx: click.Context, legislature, session,
    all_sessions_in_legislature, all_sessions, delay,
) -> None:
    """Ingest Alberta bills from the Assembly Dashboard.

    One HTTP GET per session returns the full bill roster + stage
    history + sponsor. Default: current session only.
    """
    async def _wrap(db: Database) -> None:
        stats = await ingest_ab_bills(
            db,
            legislature=legislature, session=session,
            all_sessions_in_legislature=all_sessions_in_legislature,
            all_sessions=all_sessions,
            delay_seconds=delay,
        )
        console.print(
            f"[green]ingest-ab-bills[/green]: "
            f"sessions={stats['sessions_touched']} bills={stats['bills']} "
            f"events={stats['events']} sponsors={stats['sponsors']} "
            f"sponsors_linked={stats['sponsors_linked']}"
        )
    asyncio.run(_run(_wrap, ctx.obj["dsn"]))


@cli.command("ingest-nb-bills")
@click.option("--legislature", type=int, default=None,
              help="One specific legislature (pair with --session).")
@click.option("--session", type=int, default=None,
              help="One specific session (requires --legislature).")
@click.option("--all-sessions-in-legislature", type=int, default=None,
              metavar="L", help="Every session in legislature L.")
@click.option("--delay", type=float, default=1.5,
              help="Delay between bill detail-page fetches (seconds).")
@click.pass_context
def cmd_ingest_nb_bills(
    ctx: click.Context, legislature, session,
    all_sessions_in_legislature, delay,
) -> None:
    """Ingest New Brunswick bills from legnb.ca.

    Default: current session. Per-bill detail fetch is the cost —
    ~35 bills/session × 1.5s delay ≈ 1 minute. Sponsor resolution is
    name-based (legnb.ca exposes no numeric MLA id).
    """
    async def _wrap(db: Database) -> None:
        stats = await ingest_nb_bills(
            db,
            legislature=legislature, session=session,
            all_sessions_in_legislature=all_sessions_in_legislature,
            delay_seconds=delay,
        )
        console.print(
            f"[green]ingest-nb-bills[/green]: "
            f"sessions={stats['sessions_touched']} bills={stats['bills']} "
            f"events={stats['events']} sponsors={stats['sponsors']} "
            f"sponsors_linked={stats['sponsors_linked']}"
        )
    asyncio.run(_run(_wrap, ctx.obj["dsn"]))


@cli.command("ingest-nl-bills")
@click.option("--ga", type=int, default=None, metavar="G",
              help="General Assembly number (pair with --session).")
@click.option("--session", type=int, default=None,
              help="Session number (requires --ga).")
@click.option("--all-sessions-in-ga", type=int, default=None,
              metavar="G", help="Every session in GA G.")
@click.option("--all-sessions", is_flag=True,
              help="Every session in the index (GA 44 onwards, ~40 sessions).")
@click.option("--delay", type=float, default=1.0,
              help="Delay between session fetches (seconds).")
@click.pass_context
def cmd_ingest_nl_bills(
    ctx: click.Context, ga, session,
    all_sessions_in_ga, all_sessions, delay,
) -> None:
    """Ingest Newfoundland & Labrador bills from assembly.nl.ca.

    One HTTP GET per session = full stage timeline. **No sponsor data**
    (NL doesn't publish it on the list or per-bill pages). Default:
    current session.
    """
    async def _wrap(db: Database) -> None:
        stats = await ingest_nl_bills(
            db,
            ga=ga, session=session,
            all_sessions_in_ga=all_sessions_in_ga,
            all_sessions=all_sessions,
            delay_seconds=delay,
        )
        console.print(
            f"[green]ingest-nl-bills[/green]: "
            f"sessions={stats['sessions_touched']} "
            f"bills={stats['bills']} events={stats['events']}"
        )
    asyncio.run(_run(_wrap, ctx.obj["dsn"]))


@cli.command("ingest-nt-bills")
@click.option("--delay", type=float, default=1.5,
              help="Delay between per-bill detail-page fetches (seconds).")
@click.pass_context
def cmd_ingest_nt_bills(ctx: click.Context, delay) -> None:
    """Ingest Northwest Territories bills from ntassembly.ca.

    List page + per-bill detail pages. Assembly + session parsed from
    each detail page, so multi-session pages are handled implicitly.
    No sponsor data (consensus government).
    """
    async def _wrap(db: Database) -> None:
        stats = await ingest_nt_bills(db, delay_seconds=delay)
        console.print(
            f"[green]ingest-nt-bills[/green]: "
            f"sessions={stats['sessions_touched']} "
            f"bills={stats['bills']} events={stats['events']}"
        )
    asyncio.run(_run(_wrap, ctx.obj["dsn"]))


@cli.command("ingest-nu-bills")
@click.option("--assembly", type=int, default=None,
              help="Assembly number (default: current sitting).")
@click.option("--session", type=int, default=None,
              help="Session number (default: current sitting).")
@click.pass_context
def cmd_ingest_nu_bills(ctx: click.Context, assembly, session) -> None:
    """Ingest Nunavut bills from assembly.nu.ca/bills-and-legislation.

    Drupal 9 table view — one HTTP GET returns every current-session
    bill with typed <time> elements for each stage. Caller provides
    assembly/session (Drupal doesn't print them). No sponsor data
    (consensus government).
    """
    from .legislative.nu_bills import DEFAULT_ASSEMBLY, DEFAULT_SESSION
    async def _wrap(db: Database) -> None:
        stats = await ingest_nu_bills(
            db,
            assembly=assembly if assembly is not None else DEFAULT_ASSEMBLY,
            session=session if session is not None else DEFAULT_SESSION,
        )
        console.print(
            f"[green]ingest-nu-bills[/green]: "
            f"sessions={stats['sessions_touched']} "
            f"bills={stats['bills']} events={stats['events']}"
        )
    asyncio.run(_run(_wrap, ctx.obj["dsn"]))


@cli.command("parse-ns-bill-pages")
@click.option("--limit", type=int, default=None,
              help="Cap bills parsed this run (for iteration on the regex).")
@click.pass_context
def cmd_parse_ns_bill_pages(ctx: click.Context, limit) -> None:
    """Parse cached bill HTML into bill_sponsors + bill_events (phase 3).

    Pure offline. Safe to re-run. Skips bills that already have a
    sponsor row; delete from bill_sponsors to reparse.
    """
    async def _wrap(db: Database) -> None:
        stats = await parse_ns_bill_pages(db, limit=limit)
        console.print(
            f"[green]parse-ns-bill-pages[/green]: "
            f"bills={stats['bills']} sponsors={stats['sponsors']} "
            f"events={stats['events']} no_sponsor={stats['no_sponsor']}"
        )
    asyncio.run(_run(_wrap, ctx.obj["dsn"]))


# ─────────────────────────────────────────────────────────────────────
# Offices backfill (final gap fill)
# ─────────────────────────────────────────────────────────────────────
from .offices import backfill_offices  # noqa: E402


@cli.command("backfill-offices")
@click.pass_context
def cmd_backfill_offices(ctx: click.Context) -> None:
    """Materialise politicians.extras->'offices' into politician_offices.

    Idempotent one-time backfill. Ongoing ingestion also populates the
    table automatically via opennorth._upsert_politician.
    """
    async def _wrap(db: Database) -> None:
        stats = await backfill_offices(db)
        console.print(
            f"[green]backfill-offices[/green]: "
            f"inserted={stats['inserted']} skipped={stats['skipped']} "
            f"politicians_touched={stats['politicians_touched']} "
            f"parse_failures={stats['parse_failures']}"
        )
    asyncio.run(_run(_wrap, ctx.obj["dsn"]))


# ─────────────────────────────────────────────────────────────────────
# Senate ingestion (final gap fill)
# ─────────────────────────────────────────────────────────────────────
from .gap_fillers import senate as _gf_senate  # noqa: E402


@cli.command("ingest-senators")
@click.pass_context
def cmd_ingest_senators(ctx: click.Context) -> None:
    """Scrape sencanada.ca for the 105 Canadian senators (provincial seats).

    Open North has no representative-set for the Canadian Senate, so we go
    directly to the Senate's own Umbraco AJAX endpoints. Rows are upserted
    with level='federal', elected_office='Senator', and province_territory
    set to the constitutionally-apportioned province for each seat. Safe
    to re-run; source_id 'direct:sencanada-ca:<slug>' is idempotent.
    """
    asyncio.run(_run(_gf_senate.run, ctx.obj["dsn"]))


# ─────────────────────────────────────────────────────────────────────
# Personal-site social harvest (nationwide)
# ─────────────────────────────────────────────────────────────────────


@cli.command("harvest-personal-socials")
@click.option("--limit", type=int, default=None,
              help="Max politicians to process per run (default: all).")
@click.pass_context
def cmd_harvest_personal_socials(ctx: click.Context, limit) -> None:
    """Fetch every politician's personal site and harvest social handles
    from header/footer. Covers politicians whose personal_url came from
    gap fillers, Wikipedia-based scraping, etc. (not just Phase 5)."""
    from .harvest_personal_socials import harvest_all_personal_socials
    async def _wrap(db: Database) -> None:
        stats = await harvest_all_personal_socials(db, limit=limit)
        console.print(f"[green]{stats}[/green]")
    asyncio.run(_run(_wrap, ctx.obj["dsn"]))


# ─────────────────────────────────────────────────────────────────────
# Federal Hansard — speeches ingest (openparliament.ca)
# ─────────────────────────────────────────────────────────────────────

@cli.command("ingest-federal-hansard")
@click.option("--parliament", type=int, required=True,
              help="Parliament number (e.g. 44). Tags every speech ingested.")
@click.option("--session", type=int, required=True,
              help="Session number within the parliament (e.g. 1).")
@click.option("--since", type=str, default=None,
              help="Only fetch debates on/after this ISO date (YYYY-MM-DD).")
@click.option("--until", type=str, default=None,
              help="Only fetch debates on/before this ISO date (YYYY-MM-DD).")
@click.option("--limit-debates", type=int, default=None,
              help="Cap on sitting days fetched.")
@click.option("--limit-speeches", type=int, default=None,
              help="Cap on TOTAL speeches ingested. Smoke-test friendly.")
@click.pass_context
def cmd_ingest_federal_hansard(
    ctx: click.Context, parliament, session, since, until,
    limit_debates, limit_speeches,
) -> None:
    """Ingest federal House of Commons speeches from openparliament.ca.

    Lands rows in `speeches` with attribution captured at-time-of-speech
    (party / constituency parsed from openparliament's attribution line).
    Idempotent via UNIQUE (source_system, source_url, sequence); re-runs
    over the same date range are safe and update mutable columns.
    """
    from datetime import date as _date
    from .legislative.federal_hansard import ingest as _ingest, federal_session_bounds

    def _parse_d(s):
        return _date.fromisoformat(s) if s else None

    effective_since = _parse_d(since)
    effective_until = _parse_d(until)

    # Auto-derive date bounds from the parliament/session if the caller
    # didn't provide explicit --since / --until. Without this, the
    # underlying /debates/ walk enumerates every Hansard sitting day
    # openparliament has indexed (back to 1994) and tags them all with
    # whichever session we named — which is how 896k speeches ended up
    # mis-labeled as P43-S2 on 2026-04-18. Explicit flags still win.
    if effective_since is None and effective_until is None:
        try:
            auto_since, auto_until = federal_session_bounds(parliament, session)
            effective_since = auto_since
            effective_until = auto_until
            console.print(
                f"[dim]auto-deriving date range for P{parliament}-S{session}: "
                f"{effective_since} → {effective_until}[/dim]"
            )
        except ValueError as exc:
            console.print(f"[red]{exc}[/red]")
            raise click.Abort()

    async def _wrap(db: Database) -> None:
        stats = await _ingest(
            db,
            parliament=parliament,
            session=session,
            since=effective_since,
            until=effective_until,
            limit_debates=limit_debates,
            limit_speeches=limit_speeches,
        )
        console.print(
            f"[green]ingest-federal-hansard[/green]: "
            f"debates={stats.debates_scanned} seen={stats.speeches_seen} "
            f"inserted={stats.speeches_inserted} updated={stats.speeches_updated} "
            f"skipped_empty={stats.skipped_empty} "
            f"unresolved_slug={stats.speeches_unresolved}"
        )
    asyncio.run(_run(_wrap, ctx.obj["dsn"]))


@cli.command("ingest-ab-hansard")
@click.option("--legislature", type=int, required=True,
              help="Alberta Legislature number (e.g. 31).")
@click.option("--session", type=int, required=True,
              help="Session within the legislature (e.g. 2).")
@click.option("--since", type=str, default=None,
              help="Only fetch sittings on/after this ISO date (YYYY-MM-DD).")
@click.option("--until", type=str, default=None,
              help="Only fetch sittings on/before this ISO date (YYYY-MM-DD).")
@click.option("--limit-sittings", type=int, default=None,
              help="Cap on sitting PDFs fetched (newest-first).")
@click.option("--limit-speeches", type=int, default=None,
              help="Cap on TOTAL speeches ingested. Smoke-test friendly.")
@click.pass_context
def cmd_ingest_ab_hansard(
    ctx: click.Context, legislature, session, since, until,
    limit_sittings, limit_speeches,
) -> None:
    """Ingest Alberta Hansard by parsing sitting PDFs from docs.assembly.ab.ca.

    Scrapes the transcripts-by-type listing for the given legislature+session,
    fetches each sitting's PDF, extracts text via Poppler (`pdftotext`), and
    upserts one `speeches` row per speaker turn. Speaker attribution is
    resolved against AB MLAs via `politicians.ab_assembly_mid`-populated
    roster; surname collisions leave politician_id NULL.

    Idempotent via UNIQUE (source_system, source_url, sequence); re-runs
    over the same date range update mutable columns without duplicating.
    """
    from datetime import date as _date
    from .legislative.ab_hansard import ingest as _ingest

    def _parse_d(s):
        return _date.fromisoformat(s) if s else None

    async def _wrap(db: Database) -> None:
        stats = await _ingest(
            db,
            legislature=legislature,
            session=session,
            since=_parse_d(since),
            until=_parse_d(until),
            limit_sittings=limit_sittings,
            limit_speeches=limit_speeches,
        )
        console.print(
            f"[green]ingest-ab-hansard[/green]: "
            f"sittings={stats.sittings_scanned} seen={stats.speeches_seen} "
            f"inserted={stats.speeches_inserted} updated={stats.speeches_updated} "
            f"skipped_empty={stats.skipped_empty} "
            f"resolved={stats.speeches_resolved} role_only={stats.speeches_role_only} "
            f"ambiguous={stats.speeches_ambiguous} unresolved={stats.speeches_unresolved}"
        )
    asyncio.run(_run(_wrap, ctx.obj["dsn"]))


@cli.command("ingest-bc-hansard")
@click.option("--parliament", type=int, required=True,
              help="BC Parliament number (e.g. 43).")
@click.option("--session", type=int, required=True,
              help="Session within the parliament (e.g. 2).")
@click.option("--since", type=str, default=None,
              help="Only fetch sittings on/after this ISO date (YYYY-MM-DD).")
@click.option("--until", type=str, default=None,
              help="Only fetch sittings on/before this ISO date (YYYY-MM-DD).")
@click.option("--limit-sittings", type=int, default=None,
              help="Cap on sittings processed (newest-first when capped).")
@click.option("--limit-speeches", type=int, default=None,
              help="Cap on TOTAL speeches ingested. Smoke-test friendly.")
@click.option("--url", "one_off_url", type=str, default=None,
              help="Bypass discovery and ingest a single sitting URL directly. "
                   "Useful for smoke-testing the parser on a known file.")
@click.pass_context
def cmd_ingest_bc_hansard(
    ctx: click.Context, parliament, session, since, until,
    limit_sittings, limit_speeches, one_off_url,
) -> None:
    """Ingest BC Hansard from LIMS HDMS (Blues + Final HTML → speeches).

    Discovery: LIMS HDMS debate-index JSON at
      https://lims.leg.bc.ca/hdms/debates/{parl}{sess}
    gives every House sitting with Blues filename + Final redirect (when
    published). For each sitting we fetch the best-available HTML (Final
    if published, else Blues), parse speaker turns, and upsert into
    `speeches`.

    Blues vs Final use the same canonical `source_url` so Final replaces
    Blues in place on `ON CONFLICT DO UPDATE`. Speaker resolution uses
    politicians.lims_member_id (populated by bc_bills.enrich_bc_member_ids).

    Idempotent via UNIQUE (source_system, source_url, sequence).
    """
    from datetime import date as _date
    from .legislative.bc_hansard import ingest as _ingest

    def _parse_d(s):
        return _date.fromisoformat(s) if s else None

    async def _wrap(db: Database) -> None:
        stats = await _ingest(
            db,
            parliament=parliament,
            session=session,
            since=_parse_d(since),
            until=_parse_d(until),
            limit_sittings=limit_sittings,
            limit_speeches=limit_speeches,
            one_off_url=one_off_url,
        )
        console.print(
            f"[green]ingest-bc-hansard[/green]: "
            f"sittings={stats.sittings_scanned} seen={stats.speeches_seen} "
            f"inserted={stats.speeches_inserted} updated={stats.speeches_updated} "
            f"skipped_empty={stats.skipped_empty} parse_errors={stats.parse_errors} "
            f"resolved={stats.speeches_resolved} presiding={stats.speeches_presiding} "
            f"role_only={stats.speeches_role_only} ambiguous={stats.speeches_ambiguous} "
            f"unresolved={stats.speeches_unresolved}"
        )
    asyncio.run(_run(_wrap, ctx.obj["dsn"]))


@cli.command("resolve-bc-speakers")
@click.option("--limit", type=int, default=None,
              help="Cap speeches scanned (smoke-test aid).")
@click.pass_context
def cmd_resolve_bc_speakers(ctx: click.Context, limit: Optional[int]) -> None:
    """Re-resolve politician_id on BC speeches with NULL politician_id.

    Run after expanding the BC MLA roster, fixing name-normalization, or
    enriching lims_member_id on previously-unlinked politicians. Idempotent.
    """
    from .legislative.bc_hansard import resolve_bc_speakers as _resolve

    async def _wrap(db: Database) -> None:
        stats = await _resolve(db, limit=limit)
        console.print(
            f"[green]resolve-bc-speakers[/green]: "
            f"scanned={stats.speeches_scanned} updated={stats.speeches_updated} "
            f"still_unresolved={stats.still_unresolved}"
        )
    asyncio.run(_run(_wrap, ctx.obj["dsn"]))


@cli.command("backfill-politicians-openparliament")
@click.option("--limit", type=int, default=None,
              help="Cap slugs fetched (smoke-test aid). Omit for full backfill.")
@click.option("--resolve/--no-resolve", default=True,
              help="After upserting politicians, re-run speech/chunk resolution. Default on.")
@click.pass_context
def cmd_backfill_politicians_openparliament(
    ctx: click.Context, limit: Optional[int], resolve: bool,
) -> None:
    """Create missing politicians rows by fetching openparliament.ca.

    Discovers slugs referenced by speeches with NULL politician_id,
    fetches each from api.openparliament.ca, and upserts into the
    politicians table with source_id='op:<slug>'. Then re-resolves
    speeches.politician_id and speech_chunks.politician_id.

    Safe to re-run — skips slugs already present. 5 concurrent HTTP
    fetches; ~3 minutes for 700 slugs.
    """
    from .legislative.politicians_op_backfill import run as _run_backfill, resolve_missing

    async def _wrap(db: Database) -> None:
        stats = await _run_backfill(db, limit=limit)
        console.print(
            f"[green]backfill-politicians-openparliament[/green]: "
            f"considered={stats.slugs_considered} fetched={stats.fetched} "
            f"inserted={stats.inserted} updated={stats.updated} "
            f"errors={stats.fetch_errors}"
        )
        if resolve:
            res = await resolve_missing(db)
            console.print(
                f"[green]resolve[/green]: "
                f"speeches_resolved={res['speeches_resolved']} "
                f"chunks_resolved={res['chunks_resolved']}"
            )
    asyncio.run(_run(_wrap, ctx.obj["dsn"]))


@cli.command("backfill-politician-terms-openparliament")
@click.option("--limit", type=int, default=None,
              help="Cap politicians processed (smoke-test aid). Omit for full run.")
@click.option("--slug", type=str, default=None,
              help="Target exactly one openparliament slug (e.g. pierre-poilievre).")
@click.pass_context
def cmd_backfill_politician_terms_openparliament(
    ctx: click.Context, limit: Optional[int], slug: Optional[str],
) -> None:
    """Hydrate politician_terms from openparliament.ca `memberships`.

    For every federal politician with a known `openparliament_slug`,
    fetches `/politicians/<slug>/` and rewrites their politician_terms
    from the `memberships` array (one row per parliament served in,
    with real election start_date and end_date).

    Supersedes the Open North single-row federal current term when
    present — openparliament has the real dates, not the scrape date.
    Safe to re-run: each politician's `openparliament:memberships`
    rows are deleted and re-written atomically per fetch.

    ~1 req/sec against api.openparliament.ca; ~25 min for 1,300 MPs.
    """
    from .legislative.politicians_op_backfill import run_terms_backfill as _run_terms

    async def _wrap(db: Database) -> None:
        stats = await _run_terms(db, limit=limit, slug=slug)
        console.print(
            f"[green]backfill-politician-terms-openparliament[/green]: "
            f"considered={stats.politicians_considered} fetched={stats.fetched} "
            f"updated={stats.politicians_updated} inserted={stats.terms_inserted} "
            f"deleted={stats.terms_deleted} "
            f"no_memberships={stats.politicians_skipped_no_memberships} "
            f"errors={stats.fetch_errors}"
        )
    asyncio.run(_run(_wrap, ctx.obj["dsn"]))


@cli.command("jobs-worker")
@click.pass_context
def cmd_jobs_worker(ctx: click.Context) -> None:
    """Run the admin-panel jobs daemon (consumes scanner_jobs, expands schedules).

    Intended as the entrypoint of the `scanner-jobs` compose service.
    Stays up indefinitely; polls every JOBS_POLL_INTERVAL seconds.
    """
    from . import jobs_worker as _jw
    asyncio.run(_jw.main())


@cli.command("chunk-speeches")
@click.option("--limit", type=int, default=None,
              help="Max speeches to chunk this run (default: all pending).")
@click.pass_context
def cmd_chunk_speeches(ctx: click.Context, limit) -> None:
    """Split speeches.text into retrievable speech_chunks rows.

    Speaker-turn = one chunk by default. Long turns (> ~480 tokens)
    split at paragraph boundary with 50-token overlap. Tiny procedural
    turns (< 8 tokens) are skipped. Idempotent: re-runs only process
    speeches that don't yet have chunks.
    """
    from .legislative.speech_chunker import chunk_pending as _chunk

    async def _wrap(db: Database) -> None:
        stats = await _chunk(db, limit_speeches=limit)
        console.print(
            f"[green]chunk-speeches[/green]: seen={stats.speeches_seen} "
            f"chunked={stats.speeches_chunked} skipped={stats.speeches_skipped} "
            f"chunks={stats.chunks_inserted}"
        )
    asyncio.run(_run(_wrap, ctx.obj["dsn"]))


@cli.command("embed-speech-chunks")
@click.option("--limit", type=int, default=None,
              help="Max chunks to embed this run (default: all pending).")
@click.option("--batch-size", type=int, default=32,
              help="Texts per TEI /embed call. TEI's --max-client-batch-size (default 64) is the hard cap.")
@click.pass_context
def cmd_embed_speech_chunks(ctx: click.Context, limit, batch_size) -> None:
    """Fill speech_chunks.embedding via TEI (Qwen3-Embedding-0.6B).

    Calls TEI at EMBED_URL (default http://tei:80). Uses batched
    UPDATE ... FROM UNNEST for ~1 DB round-trip per batch instead of per
    chunk — measured at 50.9 chunks/sec end-to-end. Safe to interrupt
    and resume; unembedded chunks stay NULL and get picked up on next run.
    """
    from .legislative.speech_embedder import embed_pending as _embed

    async def _wrap(db: Database) -> None:
        stats = await _embed(db, limit_chunks=limit, batch_size=batch_size)
        console.print(
            f"[green]embed-speech-chunks[/green]: seen={stats.chunks_seen} "
            f"embedded={stats.chunks_embedded} batches={stats.batches} "
            f"errors={stats.errors} server_ms={stats.total_elapsed_ms}"
        )
    asyncio.run(_run(_wrap, ctx.obj["dsn"]))


@cli.command("refresh-coverage-stats")
@click.pass_context
def cmd_refresh_coverage_stats(ctx: click.Context) -> None:
    """Recompute jurisdiction_sources counts from live tables.

    Drives the public /coverage page. Flips Hansard status 'none'/
    'partial'/'live' based on speech-count thresholds, updates
    speeches_count / politicians_count / bills_count, stamps
    last_verified_at = now(). Status flags for bills/votes/committees
    are left alone — those are editorial.
    """
    from .legislative.coverage_stats import refresh_coverage_stats as _refresh

    async def _wrap(db: Database) -> None:
        report = await _refresh(db)
        for code, stats in sorted(report.items()):
            arrow = (
                f"hansard {stats['prev_hansard_status']}→{stats['hansard_status']}"
                if stats["prev_hansard_status"] != stats["hansard_status"]
                else f"hansard={stats['hansard_status']}"
            )
            console.print(
                f"[green]{code}[/green]: speeches={stats['speeches']} "
                f"(was {stats['prev_speeches']}) politicians={stats['politicians']} "
                f"bills={stats['bills']}  {arrow}"
            )
    asyncio.run(_run(_wrap, ctx.obj["dsn"]))


@cli.command("resolve-acting-speakers")
@click.option("--limit", type=int, default=None,
              help="Cap candidate speeches scanned (smoke-test aid).")
@click.pass_context
def cmd_resolve_acting_speakers(ctx: click.Context, limit) -> None:
    """Resolve politician_id on federal speeches tagged with a presiding-
    officer attribution like 'The Acting Speaker (Mr. McClelland)'.

    Openparliament doesn't populate politician_url for these turns, so
    they land with politician_id NULL at ingest. This walks them after
    the fact, extracts the parenthesised name, and unique-matches
    against the politicians table.
    """
    from .legislative.acting_speaker_resolver import resolve_acting_speakers as _resolve

    async def _wrap(db: Database) -> None:
        stats = await _resolve(db, limit=limit)
        console.print(
            f"[green]resolve-acting-speakers[/green]: "
            f"scanned={stats['scanned']} resolved={stats['resolved']} "
            f"ambiguous={stats['ambiguous']} "
            f"no_politician_found={stats['no_politician_found']} "
            f"no_parens={stats['no_parens']}"
        )
    asyncio.run(_run(_wrap, ctx.obj["dsn"]))


if __name__ == "__main__":
    try:
        cli(obj={})
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted[/yellow]")
        sys.exit(130)
