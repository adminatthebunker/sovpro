"""Resolve federal MPs to their openparliament.ca URL slug.

openparliament.ca keys everything on a URL slug (e.g. `justin-trudeau`). Our
politicians table stores names, not slugs. This module fetches openparliament's
public list and name-matches our federal MPs against their records, writing
any matches to `politicians.openparliament_slug`.

Idempotent and re-entrant: only touches rows where the slug is NULL. Safe to
run after each federal ingest to pick up by-election winners.

The slug is later used by the API's `/api/v1/politicians/:id/openparliament`
endpoint to fetch + cache per-MP detail on-demand when users open a profile.

Entry points
------------
    resolve_slugs(db) -> ResolveResult
        Returns matched / unmatched counts plus a list of unmatched names.

CLI:
    docker compose run --rm scanner resolve-openparliament-slugs
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import httpx
from rich.console import Console

from .db import Database
from .socials_enrichment import _list_openparl_politicians, _normalize_name

log = logging.getLogger(__name__)
console = Console()


OPENPARL_USER_AGENT = (
    "CanadianPoliticalDataBot/1.0 "
    "(+https://canadianpoliticaldata.ca; admin@thebunkerops.ca)"
)


@dataclass
class ResolveResult:
    politicians_checked: int = 0
    matched: int = 0
    already_had_slug: int = 0
    unmatched_names: list[str] = field(default_factory=list)


def _slug_from_url(url: str) -> str:
    """Extract `justin-trudeau` from `/politicians/justin-trudeau/` (or absolute)."""
    path = url.strip()
    if path.startswith("http"):
        # Strip scheme + host (openparliament.ca)
        idx = path.find("/politicians/")
        if idx < 0:
            return ""
        path = path[idx:]
    parts = [p for p in path.split("/") if p]
    # Expect ["politicians", "<slug>"] or just ["<slug>"]
    if parts and parts[0] == "politicians" and len(parts) >= 2:
        return parts[1]
    if parts:
        return parts[-1]
    return ""


async def resolve_slugs(db: Database) -> ResolveResult:
    result = ResolveResult()

    rows = await db.fetch(
        """
        SELECT id, name, openparliament_slug
          FROM politicians
         WHERE is_active = true AND level = 'federal'
        """
    )
    result.politicians_checked = len(rows)

    # Politicians with a slug already are skipped but counted — rerunning is
    # normal.
    needing: dict[str, str] = {}  # normalized name -> politician_id
    for r in rows:
        if r["openparliament_slug"]:
            result.already_had_slug += 1
            continue
        key = _normalize_name(r["name"])
        if key:
            needing[key] = str(r["id"])

    if not needing:
        console.print("[yellow]No federal MPs need slug resolution.[/yellow]")
        return result

    async with httpx.AsyncClient(
        headers={"User-Agent": OPENPARL_USER_AGENT},
        timeout=30.0,
        follow_redirects=True,
    ) as client:
        console.print("[cyan]Listing openparliament.ca politicians…[/cyan]")
        try:
            listing: list[dict[str, Any]] = await _list_openparl_politicians(client)
        except httpx.HTTPError as exc:
            console.print(f"[red]openparliament listing failed: {exc}[/red]")
            raise

    console.print(f"[cyan]  {len(listing)} entries listed[/cyan]")

    # Build a normalized-name → slug map from openparliament's side.
    op_by_name: dict[str, str] = {}
    for entry in listing:
        name = str(entry.get("name") or "").strip()
        url = str(entry.get("url") or "").strip()
        slug = _slug_from_url(url)
        if not name or not slug:
            continue
        key = _normalize_name(name)
        if key and key not in op_by_name:
            op_by_name[key] = slug

    # Match + upsert.
    unmatched_names: list[str] = []
    for r in rows:
        if r["openparliament_slug"]:
            continue
        key = _normalize_name(r["name"])
        slug = op_by_name.get(key)
        if not slug:
            unmatched_names.append(r["name"])
            continue
        await db.execute(
            "UPDATE politicians SET openparliament_slug = $1 WHERE id = $2",
            slug,
            r["id"],
        )
        result.matched += 1

    result.unmatched_names = unmatched_names
    console.print(
        f"[green]✓ resolved {result.matched} slugs "
        f"(already had: {result.already_had_slug}, unmatched: {len(unmatched_names)})[/green]"
    )
    if unmatched_names:
        console.print("[yellow]Unmatched federal MPs (names may have nicknames / accents):[/yellow]")
        for n in unmatched_names[:20]:
            console.print(f"  - {n}")
        if len(unmatched_names) > 20:
            console.print(f"  … and {len(unmatched_names) - 20} more")
    return result
