"""Direct scraper for the New Brunswick Legislative Assembly.

Open North returns all 49 NB MLAs with an empty ``url`` field, leaving the
existing enricher with nothing to scrape. This scraper goes directly to
``https://www.legnb.ca/en/members/current`` to pull the roster and then
walks each ``/en/members/current/<id>/<lastname>-<firstname>`` member page
to extract name, riding, and party.

Legnb.ca profile pages don't expose external personal URLs (we sampled on
2026-04-13 — the only external hrefs are GNB-infrastructure boilerplate).
So this scraper's primary value is: (a) creating the politician roster that
Open North can't, and (b) attaching the legnb.ca profile URL which the
sovereignty scanner will DNS/GeoIP. Social + personal-site discovery will
hook in later via downstream enrichers once personal URLs turn up.
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


BASE = "https://www.legnb.ca"
ROSTER_URL = f"{BASE}/en/members/current"
SOURCE_PREFIX = "direct:legnb-ca"

# /en/members/current/<num>/<slug>
_MEMBER_HREF_RE = re.compile(
    r'href="(/en/members/current/(\d+)/([a-z][a-z\-]*))"',
    re.IGNORECASE,
)

_H1_RE = re.compile(r"<h1[^>]*>\s*(.*?)\s*</h1>", re.IGNORECASE | re.DOTALL)
_PARTY_RE = re.compile(
    r'style="color:[^"]*"></i>\s*([^<]+?)\s*</span>',
    re.IGNORECASE,
)
_RIDING_RE = re.compile(
    r'fa-map-marker-alt[^<]*></i>\s*([^<]+?)\s*</span>',
    re.IGNORECASE,
)
_PHONE_RE = re.compile(r'tel:([+()\d\s\-]{7,20})')
_PORTRAIT_RE = re.compile(
    r'<img[^>]+src="([^"]*(?:portrait|members)[^"]*\.(?:jpe?g|png))"',
    re.IGNORECASE,
)


def _clean_name(raw: str) -> str:
    """Strip honorifics + whitespace from an <h1>-extracted name."""
    n = re.sub(r"<[^>]+>", "", raw)
    n = re.sub(r"&\w+;", " ", n)
    n = re.sub(r"\s+", " ", n).strip()
    n = re.sub(
        r"^\s*(?:Hon\.|Honourable|Premier|Speaker|M\.|Mme\.?|Mr\.|Mrs\.|Ms\.|Dr\.)\s+",
        "",
        n,
        flags=re.IGNORECASE,
    )
    return n


def _split_name(name: str) -> tuple[Optional[str], Optional[str]]:
    parts = name.split()
    if len(parts) == 1:
        return parts[0], None
    return parts[0], " ".join(parts[1:])


async def _fetch_roster(
    client: httpx.AsyncClient,
) -> list[tuple[str, str]]:
    """Return deduplicated [(path, slug)] tuples from the roster HTML."""
    r = await client.get(ROSTER_URL)
    r.raise_for_status()
    seen: dict[str, str] = {}
    for m in _MEMBER_HREF_RE.finditer(r.text):
        path, _num, slug = m.groups()
        # Duplicate hrefs across photo + text; keep first.
        seen.setdefault(slug, path)
    return [(path, slug) for slug, path in seen.items()]


async def _fetch_profile(
    client: httpx.AsyncClient, path: str,
) -> dict[str, Optional[str]]:
    try:
        r = await client.get(f"{BASE}{path}")
        if r.status_code != 200:
            return {}
    except Exception as exc:
        log.debug("legnb profile fetch failed: %s: %s", path, exc)
        return {}
    html = r.text
    out: dict[str, Optional[str]] = {}

    m = _H1_RE.search(html)
    if m:
        out["name"] = _clean_name(m.group(1))

    m = _PARTY_RE.search(html)
    if m:
        party = re.sub(r"\s+", " ", m.group(1)).strip()
        # Legnb decorates with e.g. "Liberal Party" / "Progressive Conservative Party"
        if party:
            out["party"] = party

    m = _RIDING_RE.search(html)
    if m:
        riding = re.sub(r"\s+", " ", m.group(1)).strip()
        if riding:
            out["constituency_name"] = riding

    m = _PHONE_RE.search(html)
    if m:
        out["phone"] = re.sub(r"\s+", " ", m.group(1)).strip()

    m = _PORTRAIT_RE.search(html)
    if m:
        src = m.group(1).replace("\\", "/")
        if src.startswith("/"):
            src = BASE + src
        out["photo_url"] = src

    return out


async def run(db: Database) -> None:
    console.print("[cyan]New Brunswick: fetching legnb.ca roster[/cyan]")
    async with httpx.AsyncClient(
        timeout=30,
        headers={"User-Agent": BROWSER_UA},
        follow_redirects=True,
    ) as client:
        try:
            roster = await _fetch_roster(client)
        except Exception as exc:
            log.exception("NB roster fetch failed: %s", exc)
            console.print(f"[red]NB: roster fetch failed: {exc}[/red]")
            return

        console.print(f"[cyan]  got {len(roster)} member links[/cyan]")
        if not roster:
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
            task = progress.add_task("NB MLAs", total=len(roster))

            async def handle(path: str, slug: str) -> None:
                nonlocal ingested, urls_attached
                async with sem:
                    try:
                        prof = await _fetch_profile(client, path)
                        # Name is required. Fall back to the slug in a
                        # pinch ("holt-susan" -> "Susan Holt").
                        if "name" not in prof:
                            last_first = slug.split("-")
                            if len(last_first) >= 2:
                                prof["name"] = (
                                    f"{last_first[1].title()} "
                                    f"{last_first[0].title()}"
                                )
                            else:
                                prof["name"] = slug.title()
                        first, last = _split_name(prof["name"] or "")
                        source_id = f"{SOURCE_PREFIX}:{slug}"
                        profile_url = f"{BASE}{path}"
                        pid = await upsert_politician(
                            db,
                            source_id=source_id,
                            name=prof["name"],
                            first_name=first,
                            last_name=last,
                            level="provincial",
                            province="NB",
                            office="MLA",
                            party=prof.get("party"),
                            constituency_name=prof.get("constituency_name"),
                            phone=prof.get("phone"),
                            photo_url=prof.get("photo_url"),
                            official_url=profile_url,
                            extras={
                                "source": SOURCE_PREFIX,
                                "path": path,
                            },
                        )
                        ingested += 1
                        if await attach_website(
                            db, pid, profile_url, "official",
                        ):
                            urls_attached += 1
                    except Exception as exc:
                        log.exception(
                            "NB upsert failed for %s: %s", slug, exc,
                        )
                    finally:
                        progress.update(task, advance=1)

            await asyncio.gather(*(handle(p, s) for p, s in roster))

    console.print(
        f"[green]New Brunswick: ingested {ingested} MLAs, "
        f"{urls_attached} website rows attached[/green]"
    )
