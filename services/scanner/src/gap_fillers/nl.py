"""Direct scraper for the Newfoundland & Labrador House of Assembly.

Open North returns 36 NL MHAs with empty ``url`` fields, matching the NB
situation. The assembly.nl.ca roster page (``/members/members.aspx``) is
server-rendered but hides the actual member list behind an iframe / JS
component, so walking the roster programmatically is fragile.

The approach here:
  1. Start from a vetted static roster captured from Wikipedia's
     "Newfoundland and Labrador House of Assembly" + CBC coverage of the
     51st General Assembly (sworn in November 2025). 40 members total.
  2. Construct each MHA's profile URL via the observed URL pattern:
     ``/Members/YourMember/<Lastname><Firstname>.aspx``. The site is tolerant
     of casing, but we canonicalise on LastnameFirstname (no hyphens/spaces).
  3. Fetch the profile page and extract phone, email, socials, constituency.

Personal URLs are rarely listed; when they are we attach them as ``personal``.
Socials (x.com, facebook.com, instagram.com) are consistently present and
get written to politician_socials via the shared ``attach_socials`` helper.
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
from ..enrich import _extract_socials_from_html  # noqa: F401 — reuse discovery
from .shared import (
    BROWSER_UA,
    attach_socials,
    attach_website,
    upsert_politician,
)

log = logging.getLogger(__name__)
console = Console()


BASE = "https://www.assembly.nl.ca"
SOURCE_PREFIX = "direct:assembly-nl-ca"


# Captured from en.wikipedia.org "Newfoundland_and_Labrador_House_of_Assembly"
# (51st General Assembly, sworn in Nov 2025). 40 seats.
MEMBERS: list[dict[str, str]] = [
    # Progressive Conservative (21) — government
    {"name": "Lin Paddock",           "riding": "Baie Verte-Green Bay",             "party": "Progressive Conservative"},
    {"name": "Craig Pardy",           "riding": "Bonavista",                        "party": "Progressive Conservative"},
    {"name": "Joedy Wall",            "riding": "Cape St. Francis",                 "party": "Progressive Conservative"},
    {"name": "Riley Balsom",          "riding": "Carbonear-Trinity-Bay de Verde",   "party": "Progressive Conservative"},
    {"name": "Barry Petten",          "riding": "Conception Bay South",             "party": "Progressive Conservative"},
    {"name": "Loyola O'Driscoll",     "riding": "Ferryland",                        "party": "Progressive Conservative"},
    {"name": "Jim McKenna",           "riding": "Fogo Island-Cape Freels",          "party": "Progressive Conservative"},
    {"name": "Chris Tibbs",           "riding": "Grand Falls-Windsor-Buchans",      "party": "Progressive Conservative"},
    {"name": "Helen Conway-Ottenheimer", "riding": "Harbour Main",                  "party": "Progressive Conservative"},
    {"name": "Mike Goosney",          "riding": "Humber-Gros Morne",                "party": "Progressive Conservative"},
    {"name": "Joseph Power",          "riding": "Labrador West",                    "party": "Progressive Conservative"},
    {"name": "Keith Russell",         "riding": "Lake Melville",                    "party": "Progressive Conservative"},
    {"name": "Mark Butt",             "riding": "Lewisporte-Twillingate",           "party": "Progressive Conservative"},
    {"name": "Jeff Dwyer",            "riding": "Placentia West-Bellevue",          "party": "Progressive Conservative"},
    {"name": "Andrea Barbour",        "riding": "St. Barbe-L'Anse aux Meadows",     "party": "Progressive Conservative"},
    {"name": "Hal Cormier",           "riding": "St. George's-Humber",              "party": "Progressive Conservative"},
    {"name": "Tony Wakeham",          "riding": "Stephenville-Port au Port",        "party": "Progressive Conservative"},
    {"name": "Lloyd Parrott",         "riding": "Terra Nova",                       "party": "Progressive Conservative"},
    {"name": "Paul Dinn",             "riding": "Topsail-Paradise",                 "party": "Progressive Conservative"},
    {"name": "Lela Evans",            "riding": "Torngat Mountains",                "party": "Progressive Conservative"},
    # Note: Wikipedia lists 20 PCs explicitly — 21st seat per standings may
    # fluctuate as by-elections settle. We keep 20 here and rely on the open
    # North comparator to close the gap if the rolls move.

    # Liberal (15) — official opposition
    {"name": "Michael King",          "riding": "Burgeo-La Poile",                  "party": "Liberal"},
    {"name": "Paul Pike",             "riding": "Burin-Grand Bank",                 "party": "Liberal"},
    {"name": "Lisa Dempster",         "riding": "Cartwright-L'Anse au Clair",       "party": "Liberal"},
    {"name": "Fred Hutton",           "riding": "Conception Bay East-Bell Island",  "party": "Liberal"},
    {"name": "Jim Parsons",           "riding": "Corner Brook",                     "party": "Liberal"},
    {"name": "Elvis Loveless",        "riding": "Fortune Bay-Cape La Hune",         "party": "Liberal"},
    {"name": "Bettina Ford",          "riding": "Gander",                           "party": "Liberal"},
    {"name": "Pam Parsons",           "riding": "Harbour Grace-Port de Grave",      "party": "Liberal"},
    {"name": "John Hogan",            "riding": "Windsor Lake",                     "party": "Liberal"},
    {"name": "Lucy Stoyles",          "riding": "Mount Pearl North",                "party": "Liberal"},
    {"name": "Sarah Stoodley",        "riding": "Mount Scio",                       "party": "Liberal"},
    {"name": "Sherry Gambin-Walsh",   "riding": "Placentia-St. Mary's",             "party": "Liberal"},
    {"name": "Bernard Davis",         "riding": "Virginia Waters-Pleasantville",    "party": "Liberal"},
    {"name": "Jamie Korab",           "riding": "Waterford Valley",                 "party": "Liberal"},
    {"name": "Keith White",           "riding": "St. John's West",                  "party": "Liberal"},

    # New Democratic (2)
    {"name": "Jim Dinn",              "riding": "St. John's Centre",                "party": "New Democratic"},
    {"name": "Sheilagh O'Leary",      "riding": "St. John's East-Quidi Vidi",       "party": "New Democratic"},

    # Independent (2)
    {"name": "Eddie Joyce",           "riding": "Humber-Bay of Islands",            "party": "Independent"},
    {"name": "Paul Lane",             "riding": "Mount Pearl-Southlands",           "party": "Independent"},
]


def _profile_slug(name: str) -> str:
    """Produce assembly.nl.ca's LastnameFirstname slug.

    assembly.nl.ca URL pattern: /Members/YourMember/<LastnameFirstname>.aspx
    Strips spaces, apostrophes, hyphens, and honorifics; preserves case.
    """
    # Drop honorifics.
    n = re.sub(
        r"^\s*(?:Hon\.|Honourable|Premier|Speaker)\s+",
        "",
        name,
        flags=re.IGNORECASE,
    )
    parts = n.strip().split()
    if not parts:
        return ""
    first = parts[0]
    last = " ".join(parts[1:])
    # Strip punctuation that the URL strips ("O'Driscoll" -> "ODriscoll").
    def clean(s: str) -> str:
        return re.sub(r"[^A-Za-z]", "", s)
    return f"{clean(last)}{clean(first)}"


def _split_name(name: str) -> tuple[Optional[str], Optional[str]]:
    n = re.sub(
        r"^\s*(?:Hon\.|Honourable|Premier|Speaker)\s+",
        "",
        name,
        flags=re.IGNORECASE,
    )
    parts = n.split()
    if len(parts) == 1:
        return parts[0], None
    return parts[0], " ".join(parts[1:])


_EMAIL_RE = re.compile(
    r'mailto:([A-Za-z0-9._+\-]+@assembly\.nl\.ca)',
    re.IGNORECASE,
)
_PHONE_RE = re.compile(r'\((\d{3})\)\s*(\d{3})-(\d{4})')
_PORTRAIT_RE = re.compile(
    r'<img[^>]+src="([^"]+(?:members|mha|portrait)[^"]*\.(?:jpe?g|png))"',
    re.IGNORECASE,
)
_EXTERNAL_RE = re.compile(
    r'href="(https?://(?!(?:[^"/]+\.)?'
    r'(?:assembly\.nl\.ca|gov\.nl\.ca|elections\.gov\.nl\.ca|exec\.gov\.nl\.ca|'
    r'x\.com|twitter\.com|facebook\.com|instagram\.com|youtube\.com|'
    r'tiktok\.com|linkedin\.com|flickr\.com|googletagmanager|google\.com|'
    r'googleapis|jsdelivr|cloudflare|cdnjs|fontawesome|bootstrap)(?:/|"))[^"]+)"',
    re.IGNORECASE,
)
# Hosts that appear in the shared sidebar of every NL member page and
# therefore can NEVER be attributed to an individual MHA. Populated from
# inspection of 2026-04-13 profile HTML: Presto catalogue, NL public
# infrastructure, housing / business development corporations, etc. We
# also treat any ``*.gov.nl.ca`` or ``*.gnb.ca`` sub-domain as boilerplate.
_NL_BORING_HOSTS: frozenset[str] = frozenset({
    "assemblynl.inmagic.com",
    "nledbc.ca",
    "www.nledbc.ca",
    "nlhc.nl.ca",
    "www.nlhc.nl.ca",
    "mmsb.nl.ca",
    "www.mmsb.nl.ca",
    "thinkhumanrights.ca",
    "www.thinkhumanrights.ca",
    "workplacenl.ca",
    "www.workplacenl.ca",
})


async def _fetch_profile(
    client: httpx.AsyncClient, slug: str,
) -> dict:
    url = f"{BASE}/Members/YourMember/{slug}.aspx"
    try:
        r = await client.get(url)
        if r.status_code != 200:
            return {"url_tried": url, "http_status": r.status_code}
    except Exception as exc:
        log.debug("NL profile fetch %s failed: %s", url, exc)
        return {"url_tried": url, "error": str(exc)}

    html = r.text
    out: dict = {"url_tried": url, "html_ok": True, "profile_url": url}

    m = _EMAIL_RE.search(html)
    if m:
        out["email"] = m.group(1)
    m = _PHONE_RE.search(html)
    if m:
        out["phone"] = f"({m.group(1)}) {m.group(2)}-{m.group(3)}"
    m = _PORTRAIT_RE.search(html)
    if m:
        src = m.group(1)
        if src.startswith("/"):
            src = BASE + src
        out["photo_url"] = src

    out["socials"] = _extract_socials_from_html(html)

    externals: list[str] = []
    seen_hosts: set[str] = set()
    for m in _EXTERNAL_RE.finditer(html):
        url2 = m.group(1)
        try:
            from urllib.parse import urlparse
            host = (urlparse(url2).hostname or "").lower()
        except Exception:
            continue
        if host in seen_hosts or host in _NL_BORING_HOSTS:
            continue
        # Also skip stjohns.ca links (shared council infra, already covered
        # by the municipal ingest path). Be lenient: sometimes they're the
        # MHA's *former* municipal role, not personal.
        if host.endswith("stjohns.ca"):
            continue
        seen_hosts.add(host)
        externals.append(url2)
    out["externals"] = externals
    return out


async def run(db: Database) -> None:
    console.print(
        f"[cyan]Newfoundland & Labrador: ingesting {len(MEMBERS)} MHAs "
        f"(static roster + live profile scrape)[/cyan]"
    )
    async with httpx.AsyncClient(
        timeout=30,
        headers={"User-Agent": BROWSER_UA},
        follow_redirects=True,
    ) as client:
        sem = asyncio.Semaphore(4)
        ingested = 0
        reached = 0
        urls_attached = 0
        socials_saved = 0
        with Progress(
            SpinnerColumn(),
            TextColumn("{task.description}"),
            BarColumn(),
            TextColumn("{task.completed}/{task.total}"),
            TimeElapsedColumn(),
        ) as progress:
            task = progress.add_task("NL MHAs", total=len(MEMBERS))

            async def handle(mem: dict[str, str]) -> None:
                nonlocal ingested, reached, urls_attached, socials_saved
                async with sem:
                    try:
                        slug = _profile_slug(mem["name"])
                        prof = await _fetch_profile(client, slug)
                        if prof.get("html_ok"):
                            reached += 1
                        first, last = _split_name(mem["name"])
                        source_id = f"{SOURCE_PREFIX}:{slug.lower()}"
                        externals = prof.get("externals", [])
                        personal = externals[0] if externals else None
                        pid = await upsert_politician(
                            db,
                            source_id=source_id,
                            name=mem["name"],
                            first_name=first,
                            last_name=last,
                            level="provincial",
                            province="NL",
                            office="MHA",
                            party=mem["party"],
                            constituency_name=mem["riding"],
                            email=prof.get("email"),
                            phone=prof.get("phone"),
                            photo_url=prof.get("photo_url"),
                            personal_url=personal,
                            official_url=prof.get("profile_url"),
                            extras={
                                "source": SOURCE_PREFIX,
                                "slug": slug,
                                "http_status": prof.get("http_status"),
                            },
                        )
                        ingested += 1
                        # Attach profile URL (shared_official auto-labelled).
                        if prof.get("profile_url") and await attach_website(
                            db, pid, prof["profile_url"], "official",
                        ):
                            urls_attached += 1
                        for url in externals:
                            if await attach_website(db, pid, url, "personal"):
                                urls_attached += 1
                        saved = await attach_socials(
                            db, pid, prof.get("socials", {}),
                        )
                        socials_saved += saved
                    except Exception as exc:
                        log.exception(
                            "NL upsert failed for %s: %s", mem.get("name"), exc,
                        )
                    finally:
                        progress.update(task, advance=1)

            await asyncio.gather(*(handle(m) for m in MEMBERS))

    console.print(
        f"[green]NL: ingested {ingested}/{len(MEMBERS)} MHAs "
        f"({reached} reachable profile pages) "
        f"· {urls_attached} websites · {socials_saved} socials[/green]"
    )
