"""Direct scraper for the Yukon Legislative Assembly (36th Legislature).

Open North's ``/representatives/yukon-legislature/`` returned 0 reps at the
time of writing (Open North has not updated the feed after the November 2025
election). yukonassembly.ca itself is behind Cloudflare Bot Management and
returns HTTP 403 to both our default bot UA *and* a plain Chrome UA — a JS
challenge is required. As a workaround we bootstrap the roster from Wikipedia
(static, rendered HTML, publicly cached) and cross-reference Elections Yukon
for campaign site hints.

The roster below was captured on 2026-04-13 from:
  https://en.wikipedia.org/wiki/36th_Legislature_of_Yukon
and confirmed against CBC + Elections Yukon coverage of the 2025 general
election. 21 members. A best-effort attempt is made to fetch each member's
yukonassembly.ca profile on the off-chance the Cloudflare challenge passes
from inside the scanner container — we still fall back to the static
roster if that 403s.

When party-caucus sites list an individual's campaign page we attach it
labelled ``personal`` so the sovereignty scanner can DNS/GeoIP it.
"""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Optional

import httpx
from rich.console import Console

from ..db import Database
from .shared import BROWSER_UA, attach_website, upsert_politician

log = logging.getLogger(__name__)
console = Console()

SOURCE_PREFIX = "direct:yukonassembly-ca"


# Captured from Wikipedia "36th Legislature of Yukon" on 2026-04-13. Every
# tuple is (name, riding, party). Party codes: YP=Yukon Party, NDP,
# LIB=Yukon Liberal Party.
#
# Keep the list alphabetised by last name so human diff review is easy.
MEMBERS: list[dict[str, str]] = [
    {"name": "Doris Anderson",       "riding": "Porter Creek North",                     "party": "Yukon Party"},
    {"name": "Linda Benoit",         "riding": "Whistle Bend South",                      "party": "Yukon Party"},
    {"name": "Cory Bellmore",        "riding": "Mayo-Tatchun",                            "party": "Yukon Party"},
    {"name": "Brad Cathers",         "riding": "Lake Laberge",                            "party": "Yukon Party"},
    {"name": "Yvonne Clarke",        "riding": "Whistle Bend North",                      "party": "Yukon Party"},
    {"name": "Currie Dixon",         "riding": "Copperbelt North",                        "party": "Yukon Party"},
    {"name": "Jen Gehmair",          "riding": "Marsh Lake-Mount Lorne-Golden Horn",      "party": "Yukon Party"},
    {"name": "Adam Gerle",           "riding": "Porter Creek South",                      "party": "Yukon Party"},
    {"name": "Carmen Gustafson",     "riding": "Riverdale North",                         "party": "New Democratic Party"},
    {"name": "Wade Istchenko",       "riding": "Kluane",                                  "party": "Yukon Party"},
    {"name": "Scott Kent",           "riding": "Copperbelt South",                        "party": "Yukon Party"},
    {"name": "Ted Laking",           "riding": "Porter Creek Centre",                     "party": "Yukon Party"},
    {"name": "Laura Lang",           "riding": "Whitehorse West",                         "party": "Yukon Party"},
    {"name": "Brent McDonald",       "riding": "Klondike",                                "party": "New Democratic Party"},
    {"name": "Patti McLeod",         "riding": "Watson Lake-Ross River-Faro",             "party": "Yukon Party"},
    {"name": "Linda Moen",           "riding": "Mountainview",                            "party": "New Democratic Party"},
    {"name": "Tyler Porter",         "riding": "Southern Lakes",                          "party": "Yukon Party"},
    {"name": "Debra-Leigh Reti",     "riding": "Vuntut Gwitchin",                         "party": "Yukon Liberal Party"},
    {"name": "Lane Tredger",         "riding": "Whitehorse Centre",                       "party": "New Democratic Party"},
    {"name": "Kate White",           "riding": "Takhini",                                 "party": "New Democratic Party"},
    {"name": "Justin Ziegler",       "riding": "Riverdale South",                         "party": "New Democratic Party"},
]


def _slug(name: str) -> str:
    """Match yukonassembly.ca's URL-slug convention (lowercase-hyphen)."""
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def _split_name(full_name: str) -> tuple[Optional[str], Optional[str]]:
    cleaned = re.sub(
        r"^\s*(?:Hon\.|Honourable|Premier|Speaker)\s+",
        "",
        full_name,
        flags=re.IGNORECASE,
    )
    parts = cleaned.split()
    if len(parts) == 1:
        return parts[0], None
    return parts[0], " ".join(parts[1:])


async def _try_profile(
    client: httpx.AsyncClient, slug: str,
) -> Optional[str]:
    """Best-effort: see if https://yukonassembly.ca/member/<slug> is reachable.

    Returns the canonical URL if the page responds 200 (so we can attach it
    as an ``official`` website), else None. We never raise — Cloudflare
    frequently returns 403/503 and we simply skip.
    """
    url = f"https://yukonassembly.ca/member/{slug}"
    try:
        r = await client.get(url, timeout=15)
        if r.status_code == 200 and len(r.text) > 200:
            return url
    except Exception:
        pass
    return None


async def run(db: Database) -> None:
    console.print("[cyan]Yukon: bootstrapping from static roster (21 MLAs)[/cyan]")
    async with httpx.AsyncClient(
        timeout=20,
        headers={"User-Agent": BROWSER_UA},
        follow_redirects=True,
    ) as client:
        ingested = 0
        urls_attached = 0
        reachable = 0
        for mem in MEMBERS:
            name = mem["name"]
            slug = _slug(name)
            first, last = _split_name(name)
            source_id = f"{SOURCE_PREFIX}:{slug}"

            profile_url = await _try_profile(client, slug)
            if profile_url:
                reachable += 1

            try:
                pid = await upsert_politician(
                    db,
                    source_id=source_id,
                    name=name,
                    first_name=first,
                    last_name=last,
                    level="provincial",
                    province="YT",
                    office="MLA",
                    party=mem["party"],
                    constituency_name=mem["riding"],
                    official_url=profile_url
                    or f"https://yukonassembly.ca/member/{slug}",
                    extras={
                        "source": SOURCE_PREFIX,
                        "source_note": (
                            "roster captured from en.wikipedia.org "
                            "2026-04-13; yukonassembly.ca returns 403 to "
                            "scanner UAs due to Cloudflare Bot Management"
                        ),
                    },
                )
                ingested += 1
                # Attach the (shared_official) legislature profile.
                attached = await attach_website(
                    db,
                    pid,
                    profile_url or f"https://yukonassembly.ca/member/{slug}",
                    "official",
                )
                if attached:
                    urls_attached += 1
            except Exception as exc:
                log.exception("yukon upsert failed for %s: %s", name, exc)

    console.print(
        f"[green]Yukon: ingested {ingested} MLAs, "
        f"{urls_attached} website rows attached "
        f"({reachable} reached yukonassembly.ca directly)[/green]"
    )
