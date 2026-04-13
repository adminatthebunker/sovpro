"""Enrichment: discover personal/campaign websites for politicians.

For each federal MP without a personal URL, we:
  1. Look up their ourcommons.ca MP page via Open Parliament's API
     (which surfaces the canonical ourcommons URL).
  2. Scrape that page for the `<h4>Website</h4><p><a href="...">` block.
  3. INSERT the discovered URL as a new `websites` row with label='personal'.

For Alberta MLAs we follow a similar pattern using assembly.ab.ca.
For municipal councillors there's no single source — we scrape the city
council page if we can find a per-member detail link.
"""
from __future__ import annotations

import asyncio
import logging
import re
import unicodedata
from typing import Optional

import httpx
from rich.console import Console
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn

from .db import Database

log = logging.getLogger(__name__)
console = Console()


OPENPARL_BASE = "https://api.openparliament.ca"
USER_AGENT = "SovereignWatchBot/1.0 (+https://sovereignwatch.ca)"

# Match the ourcommons "Website" block:  <h4>Website</h4>\s*<p><a href="URL">
WEBSITE_RE = re.compile(
    r"<h4>\s*Website\s*</h4>\s*<p>\s*<a[^>]*href=\"([^\"]+)\"",
    re.IGNORECASE,
)
ASSEMBLY_WEBSITE_RE = re.compile(
    r"<a[^>]*href=\"(https?://(?!www\.assembly\.ab\.ca)(?!facebook|twitter|x\.com|instagram|youtube|linkedin|tiktok|mailto)[^\"]+)\"[^>]*>\s*(?:Website|Personal|Campaign|Constituency)",
    re.IGNORECASE,
)


def _norm(name: str) -> str:
    """Aggressive normalization for fuzzy name matching."""
    n = unicodedata.normalize("NFKD", name)
    n = "".join(ch for ch in n if not unicodedata.combining(ch))
    n = n.lower()
    n = re.sub(r"[^a-z0-9]+", " ", n).strip()
    return n


async def _fetch_all_openparl_mps(client: httpx.AsyncClient) -> list[dict]:
    """Page through Open Parliament's politicians list."""
    out: list[dict] = []
    next_url = f"{OPENPARL_BASE}/politicians/?format=json&limit=100"
    while next_url:
        r = await client.get(next_url)
        r.raise_for_status()
        data = r.json()
        out.extend(data.get("objects", []))
        nxt = data.get("pagination", {}).get("next_url")
        next_url = f"{OPENPARL_BASE}{nxt}" if nxt else None
    return out


async def _fetch_openparl_detail(client: httpx.AsyncClient, slug_url: str) -> Optional[dict]:
    """slug_url is like '/politicians/parm-bains/' — fetch detail."""
    try:
        r = await client.get(f"{OPENPARL_BASE}{slug_url}?format=json")
        if r.status_code != 200:
            return None
        return r.json()
    except Exception:
        return None


async def _scrape_ourcommons(client: httpx.AsyncClient, url: str) -> Optional[str]:
    """Return the personal Website URL discovered on an ourcommons MP page."""
    try:
        r = await client.get(url)
        if r.status_code != 200:
            return None
        m = WEBSITE_RE.search(r.text)
        if m:
            return m.group(1).strip()
    except Exception as exc:
        log.debug("ourcommons fetch failed for %s: %s", url, exc)
    return None


async def _scrape_assembly(client: httpx.AsyncClient, url: str) -> Optional[str]:
    """Best-effort scrape of an assembly.ab.ca MLA page."""
    try:
        r = await client.get(url)
        if r.status_code != 200:
            return None
        m = ASSEMBLY_WEBSITE_RE.search(r.text)
        if m:
            return m.group(1).strip()
    except Exception:
        return None
    return None


async def _attach(db: Database, politician_id: str, url: str, label: str = "personal") -> bool:
    """Insert a website + update politician.personal_url. Returns True if new."""
    row = await db.fetchrow(
        """
        INSERT INTO websites (owner_type, owner_id, url, label)
        VALUES ('politician', $1, $2, $3)
        ON CONFLICT (owner_type, owner_id, url) DO NOTHING
        RETURNING id
        """,
        politician_id, url, label,
    )
    await db.execute(
        "UPDATE politicians SET personal_url = COALESCE(personal_url, $2), updated_at = now() WHERE id = $1",
        politician_id, url,
    )
    return row is not None


async def enrich_federal_mps(db: Database, *, limit: Optional[int] = None,
                              force: bool = False) -> None:
    """Find personal websites for federal MPs."""
    cond = "p.level = 'federal' AND p.is_active = true"
    if not force:
        cond += " AND (p.personal_url IS NULL OR p.personal_url = '')"
    sql = f"SELECT id, name FROM politicians p WHERE {cond} ORDER BY name"
    if limit:
        sql += f" LIMIT {int(limit)}"
    targets = await db.fetch(sql)
    if not targets:
        console.print("[yellow]No MPs needing enrichment[/yellow]")
        return

    console.print(f"[cyan]Enriching {len(targets)} federal MPs[/cyan]")

    async with httpx.AsyncClient(
        timeout=30, headers={"User-Agent": USER_AGENT}, follow_redirects=True,
        limits=httpx.Limits(max_connections=4, max_keepalive_connections=4),
    ) as client:
        # Build a name -> openparl slug_url map from the bulk endpoint
        all_mps = await _fetch_all_openparl_mps(client)
        name_to_url = { _norm(m["name"]): m["url"] for m in all_mps if m.get("url") }
        console.print(f"[cyan]Open Parliament: {len(name_to_url)} current MPs[/cyan]")

        sem = asyncio.Semaphore(3)
        found = 0
        miss_no_match = 0
        miss_no_link = 0
        miss_detail = 0
        miss_no_oc = 0

        with Progress(
            SpinnerColumn(), TextColumn("{task.description}"), BarColumn(),
            TextColumn("{task.completed}/{task.total}"), TimeElapsedColumn(),
        ) as progress:
            task = progress.add_task("Discovering", total=len(targets))

            async def handle(row) -> None:
                nonlocal found, miss_no_match, miss_no_link, miss_detail, miss_no_oc
                async with sem:
                    try:
                        slug_url = name_to_url.get(_norm(row["name"]))
                        if not slug_url:
                            miss_no_match += 1
                            return
                        detail = await _fetch_openparl_detail(client, slug_url)
                        if not detail:
                            miss_detail += 1
                            return
                        oc_url: Optional[str] = None
                        for link in detail.get("links") or []:
                            u = link.get("url") or ""
                            if "ourcommons.ca/members" in u:
                                oc_url = u
                                break
                        if not oc_url:
                            miss_no_oc += 1
                            return
                        personal = await _scrape_ourcommons(client, oc_url)
                        if not personal:
                            miss_no_link += 1
                            return
                        if not personal.startswith("http"):
                            personal = "http://" + personal
                        is_new = await _attach(db, str(row["id"]), personal, "personal")
                        if is_new:
                            found += 1
                    except Exception as exc:
                        log.warning("enrich exception for %s: %s", row["name"], exc)
                    finally:
                        progress.update(task, advance=1)

            await asyncio.gather(*(handle(r) for r in targets))

    console.print(
        f"[green]✓ discovered {found} personal sites · "
        f"{miss_no_match} unmatched names · "
        f"{miss_detail} openparl detail failed · "
        f"{miss_no_oc} no ourcommons link · "
        f"{miss_no_link} no website on page[/green]"
    )


async def enrich_alberta_mlas(db: Database, *, limit: Optional[int] = None) -> None:
    rows = await db.fetch(
        """
        SELECT p.id, p.name, w.url
        FROM politicians p
        JOIN websites w ON w.owner_type='politician' AND w.owner_id=p.id
        WHERE p.level='provincial' AND p.province_territory='AB'
          AND (p.personal_url IS NULL OR p.personal_url='')
          AND w.url ILIKE '%assembly.ab.ca%'
        """ + (f" LIMIT {int(limit)}" if limit else "")
    )
    if not rows:
        console.print("[yellow]No MLAs needing enrichment[/yellow]")
        return
    console.print(f"[cyan]Enriching {len(rows)} Alberta MLAs[/cyan]")

    async with httpx.AsyncClient(
        timeout=20, headers={"User-Agent": USER_AGENT}, follow_redirects=True
    ) as client:
        sem = asyncio.Semaphore(4)
        found = 0
        with Progress(
            SpinnerColumn(), TextColumn("{task.description}"), BarColumn(),
            TextColumn("{task.completed}/{task.total}"), TimeElapsedColumn(),
        ) as progress:
            task = progress.add_task("Discovering", total=len(rows))

            async def handle(r) -> None:
                nonlocal found
                async with sem:
                    try:
                        url = await _scrape_assembly(client, r["url"])
                        if url:
                            if not url.startswith("http"):
                                url = "http://" + url
                            if await _attach(db, str(r["id"]), url, "personal"):
                                found += 1
                    finally:
                        progress.update(task, advance=1)

            await asyncio.gather(*(handle(r) for r in rows))

    console.print(f"[green]✓ discovered {found} MLA personal sites[/green]")
