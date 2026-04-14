"""Direct scraper for the Nunavut Legislative Assembly (assembly.nu.ca).

Open North has no representative-set for Nunavut as of 2026-04-13, so we
scrape the 22-member MLA roster from ``https://assembly.nu.ca/members/mla``
and per-member ``/node/<id>`` profile pages directly.

Nunavut is a consensus government — members are elected as independents and
party affiliation is left NULL. The scraper:

  1. Fetches the main roster page and extracts (name, constituency, node_id).
  2. For each node, fetches the profile page to find:
       - portrait image
       - primary phone / legislative email (``*@assembly.nu.ca``)
       - any external constituency website (e.g. ``southbaffinmla.ca``)
  3. Upserts the politician and attaches any external URL to ``websites``
     so the existing sovereignty pipeline DNS/GeoIPs it.
"""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Optional

import httpx
from rich.console import Console
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
)

from ..db import Database
from .shared import BROWSER_UA, attach_website, upsert_politician

log = logging.getLogger(__name__)
console = Console()


ROSTER_URL = "https://assembly.nu.ca/members/mla"
BASE = "https://assembly.nu.ca"
SOURCE_PREFIX = "direct:assembly-nu-ca"

# The roster page renders each MLA as a Drupal views-row card. Each row
# links at /node/<id>, shows a <strong class="field-content">Name</strong>,
# then a ``views-field-field-member-mla`` block containing the
# constituency name.
_NODE_RE = re.compile(
    r'<a\s+href="(/node/\d+)"[^>]*>.*?</a>\s*</span>\s*</span>\s*'
    r'<span[^>]*views-field-field-member-name[^>]*>\s*'
    r'<strong[^>]*>([^<]+)</strong>.*?'
    r'views-field-field-member-mla.*?'
    r'<div class="field-content">([^<]+)</div>',
    re.IGNORECASE | re.DOTALL,
)

# Per-profile regex for constituency. Each profile page has:
#   <div class="field--name-field-electoral-district"> ... <a ...>District</a>
_DISTRICT_RE = re.compile(
    r'field--name-field-electoral-district[^<]*(?:<[^>]+>\s*)*'
    r'([^<][^<]*?)\s*<',
    re.IGNORECASE | re.DOTALL,
)

# Emails on Nunavut profile pages appear as plain text ("Email: foo@bar.ca")
# rather than mailto: links. We match both forms. Legislative emails are
# ``<handle>@assembly.nu.ca``; constituency-office emails frequently use a
# custom per-riding domain (e.g. ``southbaffinmla.ca``) which is the gold
# we actually want the sovereignty scanner to DNS/GeoIP.
_LEG_EMAIL_RE = re.compile(
    r'([A-Za-z0-9._+\-]+@assembly\.nu\.ca)',
    re.IGNORECASE,
)
_ANY_EMAIL_RE = re.compile(
    r'([A-Za-z0-9._+\-]+@(?!assembly\.nu\.ca|gov\.nu\.ca|canada\.ca)'
    r'[A-Za-z0-9.\-]+\.(?:ca|com|org|net))',
    re.IGNORECASE,
)
_PHONE_RE = re.compile(r'\((\d{3})\)\s*(\d{3})-(\d{4})')
_PORTRAIT_RE = re.compile(
    r'<img[^>]+src="([^"]+/member_image/[^"]+)"',
    re.IGNORECASE,
)
# Drupal boilerplate URLs we never want to treat as a member's own site.
_BORING_HOSTS = frozenset({
    "www.nunavut.ca", "nunavut.ca",
    "www.gov.nu.ca", "gov.nu.ca",
    "www.elections.nu.ca", "elections.nu.ca",
    "www.jus.gov.nu.ca", "jus.gov.nu.ca",
    "www.stats.gov.nu.ca", "stats.gov.nu.ca",
    "www.canada.ca", "canada.ca",
})


def _split_name(full_name: str) -> tuple[Optional[str], Optional[str]]:
    """Best-effort split into (first, last). Strips honorifics like 'Hon.'."""
    cleaned = re.sub(
        r"^\s*(?:Hon\.|Honourable|Premier|Speaker|Dr\.|Mr\.|Mrs\.|Ms\.)\s+",
        "",
        full_name,
        flags=re.IGNORECASE,
    )
    parts = cleaned.split()
    if len(parts) == 1:
        return parts[0], None
    return parts[0], " ".join(parts[1:])


async def _fetch_roster(client: httpx.AsyncClient) -> list[dict]:
    """Return list of {name, node_path, constituency} dicts from roster."""
    r = await client.get(ROSTER_URL)
    r.raise_for_status()
    html = r.text
    seen: dict[str, dict] = {}
    for m in _NODE_RE.finditer(html):
        node_path = m.group(1)
        name = re.sub(r"\s+", " ", m.group(2)).strip()
        name = re.sub(r"&\w+;", " ", name).strip()
        constituency = re.sub(r"\s+", " ", m.group(3)).strip()
        if not name or len(name.split()) < 2 or len(name) > 80:
            continue
        seen.setdefault(name, {
            "name": name,
            "node_path": node_path,
            "constituency": constituency,
        })
    return list(seen.values())


async def _fetch_profile(
    client: httpx.AsyncClient, node_path: str
) -> dict[str, Optional[str]]:
    """Pull structured fields out of a /node/<id> profile page."""
    try:
        r = await client.get(f"{BASE}{node_path}")
        if r.status_code != 200:
            return {}
    except Exception as exc:
        log.debug("nunavut profile fetch failed: %s: %s", node_path, exc)
        return {}
    html = r.text
    out: dict[str, Optional[str]] = {}

    m = _DISTRICT_RE.search(html)
    if m:
        district = re.sub(r"\s+", " ", m.group(1)).strip()
        # Strip leading labels like "Electoral District" that sometimes leak in.
        district = re.sub(
            r"^\s*(?:electoral\s+district[:\s]*)",
            "",
            district,
            flags=re.IGNORECASE,
        )
        if 0 < len(district) < 80:
            out["constituency_name"] = district

    m = _LEG_EMAIL_RE.search(html)
    if m:
        out["email"] = m.group(1).lower()

    m = _PHONE_RE.search(html)
    if m:
        out["phone"] = f"({m.group(1)}) {m.group(2)}-{m.group(3)}"

    m = _PORTRAIT_RE.search(html)
    if m:
        url = m.group(1)
        if url.startswith("/"):
            url = BASE + url
        out["photo_url"] = url

    # Constituency-office emails often live on a custom domain — the
    # scanner's job is to discover those so it can DNS/GeoIP them. Build
    # a personal_url candidate by turning the email's host into https://.
    personal_urls: list[str] = []
    seen_hosts: set[str] = set()
    for m in _ANY_EMAIL_RE.finditer(html):
        email = m.group(1)
        host = email.split("@", 1)[1].lower()
        if host in _BORING_HOSTS or host in seen_hosts:
            continue
        seen_hosts.add(host)
        personal_urls.append(f"https://{host}")
    out["externals"] = personal_urls  # type: ignore[assignment]
    return out


async def run(db: Database) -> None:
    """Scrape assembly.nu.ca and upsert all 22 Nunavut MLAs."""
    console.print("[cyan]Nunavut: fetching assembly.nu.ca roster[/cyan]")
    async with httpx.AsyncClient(
        timeout=30,
        headers={"User-Agent": BROWSER_UA},
        follow_redirects=True,
    ) as client:
        try:
            roster = await _fetch_roster(client)
        except Exception as exc:
            log.exception("nunavut roster fetch failed: %s", exc)
            console.print(f"[red]Nunavut: roster fetch failed: {exc}[/red]")
            return

        console.print(f"[cyan]  got {len(roster)} MLAs[/cyan]")
        if not roster:
            console.print("[yellow]Nunavut: roster empty — skipping[/yellow]")
            return

        sem = asyncio.Semaphore(4)
        ingested = 0
        urls_attached = 0
        with Progress(
            SpinnerColumn(),
            TextColumn("{task.description}"),
            BarColumn(),
            TextColumn("{task.completed}/{task.total}"),
            TimeElapsedColumn(),
        ) as progress:
            task = progress.add_task("Nunavut MLAs", total=len(roster))

            async def handle(mem: dict) -> None:
                nonlocal ingested, urls_attached
                async with sem:
                    name = mem["name"]
                    node_path = mem["node_path"]
                    try:
                        profile = await _fetch_profile(client, node_path)
                        first, last = _split_name(name)
                        source_id = f"{SOURCE_PREFIX}:{node_path.strip('/')}"
                        externals = profile.pop("externals", None) or []
                        # Pick the first external URL as personal_url if any.
                        personal = externals[0] if externals else None
                        pid = await upsert_politician(
                            db,
                            source_id=source_id,
                            name=name,
                            first_name=first,
                            last_name=last,
                            level="provincial",
                            province="NU",
                            office="MLA",
                            party=None,  # consensus government
                            constituency_name=(
                                mem.get("constituency")
                                or profile.get("constituency_name")
                            ),
                            email=profile.get("email"),
                            phone=profile.get("phone"),
                            photo_url=profile.get("photo_url"),
                            personal_url=personal,
                            official_url=f"{BASE}{node_path}",
                            extras={
                                "source": SOURCE_PREFIX,
                                "node_path": node_path,
                            },
                        )
                        ingested += 1
                        # Attach the assembly.nu.ca profile itself (labelled
                        # shared_official automatically by label_for).
                        if await attach_website(
                            db, pid, f"{BASE}{node_path}", "official"
                        ):
                            urls_attached += 1
                        # Any external site -> personal.
                        for url in externals:
                            if await attach_website(db, pid, url, "personal"):
                                urls_attached += 1
                    except Exception as exc:
                        log.exception(
                            "nunavut upsert failed for %s: %s", name, exc,
                        )
                    finally:
                        progress.update(task, advance=1)

            await asyncio.gather(*(handle(m) for m in roster))

    console.print(
        f"[green]Nunavut: ingested {ingested} MLAs, "
        f"{urls_attached} new website rows attached[/green]"
    )
