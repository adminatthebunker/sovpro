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
from typing import Awaitable, Callable, Optional

import httpx
from rich.console import Console
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn

from .db import Database

log = logging.getLogger(__name__)
console = Console()


OPENPARL_BASE = "https://api.openparliament.ca"
USER_AGENT = "CanadianPoliticalDataBot/1.0 (+https://canadianpoliticaldata.ca)"

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
        "UPDATE politicians SET personal_url = COALESCE(NULLIF(personal_url,''), $2), updated_at = now() WHERE id = $1",
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
    await _enrich_legislature(
        db, province="AB", label="Alberta MLA",
        host_match="%assembly.ab.ca%",
        website_re=ASSEMBLY_WEBSITE_RE,
        limit=limit,
    )


# ─────────────────────────────────────────────────────────────────────
# Generic provincial-legislature enricher (Phase 3)
# ─────────────────────────────────────────────────────────────────────
# Each `enrich_<prov>()` below follows the Alberta pattern: look up politicians
# for the province whose stored `websites.url` points at the assembly site,
# fetch that page, and try to find an external "Website/Personal/Campaign"
# link that isn't itself on the assembly hostname or a known social network.
#
# The regex patterns are best-effort — most provincial legislature sites do
# NOT expose personal URLs in a consistent way. Patterns with no match are
# harmless; they just yield "discovered 0 sites". A legislature gets a TODO
# stub when even the URL-discovery heuristic can't be expressed in regex.


def _legislature_website_re(exclude_host_substrs: tuple[str, ...]) -> re.Pattern[str]:
    """Build a regex that matches an <a href="URL"> ... (Website|Personal|Campaign|Constituency)
    block, excluding links back to the legislature host itself and common
    social / infra domains.
    """
    # Turn each host fragment into a negative-lookahead clause.
    excl = "".join(f"(?!{re.escape(h)})" for h in exclude_host_substrs)
    pattern = (
        r"<a[^>]*href=\""
        r"(https?://"
        + excl
        + r"(?!facebook|twitter|x\.com|instagram|youtube|linkedin|tiktok|mailto)"
        r"[^\"]+)\"[^>]*>\s*(?:Website|Personal|Campaign|Constituency)"
    )
    return re.compile(pattern, re.IGNORECASE)


# Pre-built regexes per legislature. Kept as module-level constants both for
# speed and so tests can import + exercise them directly.
BC_WEBSITE_RE          = _legislature_website_re(("leg.bc.ca",))
ON_WEBSITE_RE          = _legislature_website_re(("ola.org",))
QC_WEBSITE_RE          = _legislature_website_re(("assnat.qc.ca",))
MB_WEBSITE_RE          = _legislature_website_re(("gov.mb.ca",))
SK_WEBSITE_RE          = _legislature_website_re(("legassembly.sk.ca",))
NS_WEBSITE_RE          = _legislature_website_re(("nslegislature.ca",))
NB_WEBSITE_RE          = _legislature_website_re(("legnb.ca",))
PE_WEBSITE_RE          = _legislature_website_re(("assembly.pe.ca",))
NL_WEBSITE_RE          = _legislature_website_re(("assembly.nl.ca",))
YT_WEBSITE_RE          = _legislature_website_re(("yukonassembly.ca",))
NT_WEBSITE_RE          = _legislature_website_re(
    ("ntassembly.ca", "ntlegislativeassembly.ca"),
)


async def _scrape_legislature_page(
    client: httpx.AsyncClient, url: str, pattern: re.Pattern[str],
) -> Optional[str]:
    """Best-effort scrape of an arbitrary provincial-legislature member page."""
    try:
        r = await client.get(url)
        if r.status_code != 200:
            return None
        m = pattern.search(r.text)
        if m:
            return m.group(1).strip()
    except Exception:
        return None
    return None


async def _enrich_legislature(
    db: Database,
    *,
    province: str,
    label: str,
    host_match: str,
    website_re: re.Pattern[str],
    limit: Optional[int] = None,
) -> int:
    """Shared driver for per-legislature enrichment.

    Returns the count of newly-discovered personal URLs.
    """
    rows = await db.fetch(
        """
        SELECT p.id, p.name, w.url
        FROM politicians p
        JOIN websites w ON w.owner_type='politician' AND w.owner_id=p.id
        WHERE p.level='provincial' AND p.province_territory=$1
          AND (p.personal_url IS NULL OR p.personal_url='')
          AND w.url ILIKE $2
        """ + (f" LIMIT {int(limit)}" if limit else ""),
        province, host_match,
    )
    if not rows:
        console.print(f"[yellow]No {label}s needing enrichment[/yellow]")
        return 0

    console.print(f"[cyan]Enriching {len(rows)} {label}s[/cyan]")

    async with httpx.AsyncClient(
        timeout=20, headers={"User-Agent": USER_AGENT}, follow_redirects=True,
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
                        url = await _scrape_legislature_page(client, r["url"], website_re)
                        if url:
                            if not url.startswith("http"):
                                url = "http://" + url
                            if await _attach(db, str(r["id"]), url, "personal"):
                                found += 1
                    finally:
                        progress.update(task, advance=1)

            await asyncio.gather(*(handle(r) for r in rows))

    console.print(f"[green]✓ discovered {found} {label} personal sites[/green]")
    return found


async def enrich_bc_mlas(db: Database, *, limit: Optional[int] = None) -> None:
    # NOTE: www.leg.bc.ca member pages are rendered client-side from LIMS data
    # and Open North's per-rep `url` is typically empty for BC. Enrichment here
    # will usually be a no-op until ingestion starts populating per-MLA pages.
    await _enrich_legislature(
        db, province="BC", label="BC MLA",
        host_match="%leg.bc.ca%",
        website_re=BC_WEBSITE_RE, limit=limit,
    )


async def enrich_ontario_mpps(db: Database, *, limit: Optional[int] = None) -> None:
    # ola.org doesn't link to personal sites on /en/members/all/<slug> pages
    # we've sampled (2026-04-13). Pattern is kept in case a subset of members
    # add a "Website" section. TODO: revisit if yield remains 0% after a run.
    await _enrich_legislature(
        db, province="ON", label="Ontario MPP",
        host_match="%ola.org%",
        website_re=ON_WEBSITE_RE, limit=limit,
    )


async def enrich_quebec_mnas(db: Database, *, limit: Optional[int] = None) -> None:
    # assnat.qc.ca /fr/deputes/<slug>/index.html — regex targets French labels.
    # The exclusion list also blocks returning any assnat page as "personal".
    await _enrich_legislature(
        db, province="QC", label="Quebec MNA",
        host_match="%assnat.qc.ca%",
        website_re=QC_WEBSITE_RE, limit=limit,
    )


async def enrich_manitoba_mlas(db: Database, *, limit: Optional[int] = None) -> None:
    await _enrich_legislature(
        db, province="MB", label="Manitoba MLA",
        host_match="%gov.mb.ca%",
        website_re=MB_WEBSITE_RE, limit=limit,
    )


async def enrich_saskatchewan_mlas(db: Database, *, limit: Optional[int] = None) -> None:
    await _enrich_legislature(
        db, province="SK", label="Saskatchewan MLA",
        host_match="%legassembly.sk.ca%",
        website_re=SK_WEBSITE_RE, limit=limit,
    )


async def enrich_nova_scotia_mlas(db: Database, *, limit: Optional[int] = None) -> None:
    await _enrich_legislature(
        db, province="NS", label="Nova Scotia MLA",
        host_match="%nslegislature.ca%",
        website_re=NS_WEBSITE_RE, limit=limit,
    )


async def enrich_new_brunswick_mlas(db: Database, *, limit: Optional[int] = None) -> None:
    # Open North currently emits empty `url` for all NB reps, so there's
    # nothing to scrape until ingestion begins populating legnb.ca member
    # pages. TODO: switch to scraping the legnb.ca roster directly if needed.
    await _enrich_legislature(
        db, province="NB", label="New Brunswick MLA",
        host_match="%legnb.ca%",
        website_re=NB_WEBSITE_RE, limit=limit,
    )


async def enrich_pei_mlas(db: Database, *, limit: Optional[int] = None) -> None:
    await _enrich_legislature(
        db, province="PE", label="PEI MLA",
        host_match="%assembly.pe.ca%",
        website_re=PE_WEBSITE_RE, limit=limit,
    )


async def enrich_nl_mhas(db: Database, *, limit: Optional[int] = None) -> None:
    # Open North currently emits empty `url` for NL reps as well. Pattern is
    # ready for when member pages start appearing under assembly.nl.ca.
    await _enrich_legislature(
        db, province="NL", label="Newfoundland & Labrador MHA",
        host_match="%assembly.nl.ca%",
        website_re=NL_WEBSITE_RE, limit=limit,
    )


async def enrich_yukon_mlas(db: Database, *, limit: Optional[int] = None) -> None:
    # yukonassembly.ca /member/<slug> exists but responds 403 to unauth bots.
    # Enrichment will be zero-yield until we add a browser-UA / anti-bot
    # workaround. Keeping the scaffold in place for symmetry.
    await _enrich_legislature(
        db, province="YT", label="Yukon MLA",
        host_match="%yukonassembly.ca%",
        website_re=YT_WEBSITE_RE, limit=limit,
    )


async def enrich_nwt_mlas(db: Database, *, limit: Optional[int] = None) -> None:
    # ntlegislativeassembly.ca member pages exist but contain only biographical
    # text — no personal URL link patterns detected as of 2026-04-13.
    await _enrich_legislature(
        db, province="NT", label="NWT MLA",
        host_match="%ntlegislativeassembly.ca%",
        website_re=NT_WEBSITE_RE, limit=limit,
    )


async def enrich_nunavut_mlas(db: Database, *, limit: Optional[int] = None) -> None:
    """TODO: Nunavut has no Open North representative set, so no politicians
    are currently ingested for NU. Once the upstream feed (or a custom
    assembly.nu.ca scraper) exists, implement this enricher by mirroring
    `enrich_alberta_mlas`. For now this is a stub that reports 0 and returns.
    """
    console.print(
        "[yellow]Nunavut enrichment skipped — no ingested Nunavut MLAs "
        "(upstream Open North set is empty; see opennorth.py TODO).[/yellow]"
    )


# Registry for the coordinating `enrich_all_legislatures` helper + CLI runner.
# Keyed by province code → async enricher function.
PROVINCIAL_ENRICHERS: dict[str, Callable[..., Awaitable[None]]] = {
    "AB": enrich_alberta_mlas,
    "BC": enrich_bc_mlas,
    "ON": enrich_ontario_mpps,
    "QC": enrich_quebec_mnas,
    "MB": enrich_manitoba_mlas,
    "SK": enrich_saskatchewan_mlas,
    "NS": enrich_nova_scotia_mlas,
    "NB": enrich_new_brunswick_mlas,
    "PE": enrich_pei_mlas,
    "NL": enrich_nl_mhas,
    "YT": enrich_yukon_mlas,
    "NT": enrich_nwt_mlas,
    "NU": enrich_nunavut_mlas,
}


async def enrich_all_legislatures(db: Database, *, limit: Optional[int] = None) -> None:
    """Run every provincial/territorial enricher in sequence.

    Each enricher is independent; failures are logged but do not abort the run.
    """
    for prov, fn in PROVINCIAL_ENRICHERS.items():
        console.print(f"[cyan bold]━━ enrich {prov} ━━[/cyan bold]")
        try:
            await fn(db, limit=limit)
        except Exception as exc:
            log.exception("enrich %s failed: %s", prov, exc)
            console.print(f"[red]  {prov}: {exc}[/red]")
