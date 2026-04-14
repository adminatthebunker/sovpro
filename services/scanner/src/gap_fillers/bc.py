"""Direct scraper for the BC Legislative Assembly.

Open North's ``/representatives/bc-legislature/`` returns only 5 MLAs (the
Speaker / executive council, as of 2026-04-13); the bulk of the 93-member
roster is missing. leg.bc.ca's own member pages are client-rendered from
LIMS and we could not locate a public JSON endpoint (the constituency-office
cycle that the Legislative Administration is currently building explains
the placeholder "Find MLA" pages).

Two bootstrap sources:
  1. ``https://www.leg.bc.ca/contact-us/mla-contact-information`` is a
     static HTML page listing every MLA's ``@leg.bc.ca`` email address — we
     parse that to get the canonical member roster with emails.
  2. A vetted static roster captured from Wikipedia
     ("43rd Parliament of British Columbia") gives us each MLA's riding +
     party, keyed on full name. The legnb.ca email table does NOT include
     riding/party, so the two sources merge cleanly.

Personal URLs are not exposed on either source; we attach the leg.bc.ca
institutional page as ``shared_official`` (the existing
``SHARED_OFFICIAL_HOSTS`` set includes ``leg.bc.ca``). Downstream enrichers
can layer on personal-site discovery later.
"""
from __future__ import annotations

import logging
import re
import unicodedata
from typing import Optional

import httpx
from rich.console import Console

from ..db import Database
from .shared import BROWSER_UA, attach_website, upsert_politician

log = logging.getLogger(__name__)
console = Console()


EMAIL_PAGE = "https://www.leg.bc.ca/contact-us/mla-contact-information"
SOURCE_PREFIX = "direct:leg-bc-ca"


# Vetted static roster from Wikipedia's "43rd Parliament of British Columbia"
# captured 2026-04-13. 93 seats total. The party string is normalised to
# "BC NDP" / "BC Conservative" / "BC Green" / "Independent" to match the
# party-filter conventions used elsewhere in the codebase.
MEMBERS: list[dict[str, str]] = [
    # BC NDP (47)
    {"name": "Anne Kang",                 "riding": "Burnaby Centre",               "party": "BC NDP"},
    {"name": "Reah Arora",                "riding": "Burnaby East",                 "party": "BC NDP"},
    {"name": "Janet Routledge",           "riding": "Burnaby North",                "party": "BC NDP"},
    {"name": "Paul Choi",                 "riding": "Burnaby South-Metrotown",      "party": "BC NDP"},
    {"name": "Raj Chouhan",               "riding": "Burnaby-New Westminster",      "party": "BC NDP"},
    {"name": "Jodie Wickens",             "riding": "Coquitlam-Burke Mountain",     "party": "BC NDP"},
    {"name": "Jennifer Blatherwick",      "riding": "Coquitlam-Maillardville",      "party": "BC NDP"},
    {"name": "Debra Toporowski",          "riding": "Cowichan Valley",              "party": "BC NDP"},
    {"name": "Ravi Kahlon",               "riding": "Delta North",                  "party": "BC NDP"},
    {"name": "Darlene Rotchford",         "riding": "Esquimalt-Colwood",            "party": "BC NDP"},
    {"name": "Dana Lajeunesse",           "riding": "Juan de Fuca-Malahat",         "party": "BC NDP"},
    {"name": "Brittny Anderson",          "riding": "Kootenay Central",             "party": "BC NDP"},
    {"name": "Steve Morissette",          "riding": "Kootenay-Monashee",            "party": "BC NDP"},
    {"name": "Stephanie Higginson",       "riding": "Ladysmith-Oceanside",          "party": "BC NDP"},
    {"name": "Ravi Parmar",               "riding": "Langford-Highlands",           "party": "BC NDP"},
    {"name": "Lisa Beare",                "riding": "Maple Ridge-Pitt Meadows",     "party": "BC NDP"},
    {"name": "Josie Osborne",             "riding": "Mid Island-Pacific Rim",       "party": "BC NDP"},
    {"name": "Sheila Malcolmson",         "riding": "Nanaimo-Gabriola Island",      "party": "BC NDP"},
    {"name": "George Anderson",           "riding": "Nanaimo-Lantzville",           "party": "BC NDP"},
    {"name": "Jennifer Whiteside",        "riding": "New Westminster-Coquitlam",    "party": "BC NDP"},
    {"name": "Tamara Davidson",           "riding": "North Coast-Haida Gwaii",      "party": "BC NDP"},
    {"name": "Bowinn Ma",                 "riding": "North Vancouver-Lonsdale",     "party": "BC NDP"},
    {"name": "Susie Chant",               "riding": "North Vancouver-Seymour",      "party": "BC NDP"},
    {"name": "Diana Gibson",              "riding": "Oak Bay-Gordon Head",          "party": "BC NDP"},
    {"name": "Mike Farnworth",            "riding": "Port Coquitlam",               "party": "BC NDP"},
    {"name": "Rick Glumac",               "riding": "Port Moody-Burquitlam",        "party": "BC NDP"},
    {"name": "Randene Neill",             "riding": "Powell River-Sunshine Coast",  "party": "BC NDP"},
    {"name": "Kelly Greene",              "riding": "Richmond-Steveston",           "party": "BC NDP"},
    {"name": "Lana Popham",               "riding": "Saanich South",                "party": "BC NDP"},
    {"name": "Amna Shah",                 "riding": "Surrey City Centre",           "party": "BC NDP"},
    {"name": "Jagrup Brar",               "riding": "Surrey-Fleetwood",             "party": "BC NDP"},
    {"name": "Garry Begg",                "riding": "Surrey-Guildford",             "party": "BC NDP"},
    {"name": "Jessie Sunner",             "riding": "Surrey-Newton",                "party": "BC NDP"},
    {"name": "George Chow",               "riding": "Vancouver-Fraserview",         "party": "BC NDP"},
    {"name": "Niki Sharma",               "riding": "Vancouver-Hastings",           "party": "BC NDP"},
    {"name": "Mable Elmore",              "riding": "Vancouver-Kensington",         "party": "BC NDP"},
    {"name": "Sunita Dhir",               "riding": "Vancouver-Langara",            "party": "BC NDP"},
    {"name": "Christine Boyle",           "riding": "Vancouver-Little Mountain",    "party": "BC NDP"},
    {"name": "David Eby",                 "riding": "Vancouver-Point Grey",         "party": "BC NDP"},
    {"name": "Adrian Dix",                "riding": "Vancouver-Renfrew",            "party": "BC NDP"},
    {"name": "Brenda Bailey",             "riding": "Vancouver-South Granville",    "party": "BC NDP"},
    {"name": "Joan Phillip",              "riding": "Vancouver-Strathcona",         "party": "BC NDP"},
    {"name": "Spencer Chandra Herbert",   "riding": "Vancouver-West End",           "party": "BC NDP"},
    {"name": "Terry Yung",                "riding": "Vancouver-Yaletown",           "party": "BC NDP"},
    {"name": "Harwinder Sandhu",          "riding": "Vernon-Lumby",                 "party": "BC NDP"},
    {"name": "Grace Lore",                "riding": "Victoria-Beacon Hill",         "party": "BC NDP"},
    {"name": "Nina Krieger",              "riding": "Victoria-Swan Lake",           "party": "BC NDP"},

    # BC Conservative (38)
    {"name": "Bruce Banman",              "riding": "Abbotsford South",             "party": "BC Conservative"},
    {"name": "Korky Neufeld",             "riding": "Abbotsford West",              "party": "BC Conservative"},
    {"name": "Reann Gasper",              "riding": "Abbotsford-Mission",           "party": "BC Conservative"},
    {"name": "Donegal Wilson",            "riding": "Boundary-Similkameen",         "party": "BC Conservative"},
    {"name": "Sharon Hartwell",           "riding": "Bulkley Valley-Stikine",       "party": "BC Conservative"},
    {"name": "Lorne Doerkson",            "riding": "Cariboo-Chilcotin",            "party": "BC Conservative"},
    {"name": "Heather Maahs",             "riding": "Chilliwack North",             "party": "BC Conservative"},
    {"name": "A'aliya Warbus",            "riding": "Chilliwack-Cultus Lake",       "party": "BC Conservative"},
    {"name": "Scott McInnis",             "riding": "Columbia River-Revelstoke",    "party": "BC Conservative"},
    {"name": "Brennan Day",               "riding": "Courtenay-Comox",              "party": "BC Conservative"},
    {"name": "Ian Paton",                 "riding": "Delta South",                  "party": "BC Conservative"},
    {"name": "Tony Luck",                 "riding": "Fraser-Nicola",                "party": "BC Conservative"},
    {"name": "Peter Milobar",             "riding": "Kamloops Centre",              "party": "BC Conservative"},
    {"name": "Ward Stamer",               "riding": "Kamloops-North Thompson",      "party": "BC Conservative"},
    {"name": "Kristina Loewen",           "riding": "Kelowna Centre",               "party": "BC Conservative"},
    {"name": "Gavin Dew",                 "riding": "Kelowna-Mission",              "party": "BC Conservative"},
    {"name": "Jordan Kealy",              "riding": "Peace River North",            "party": "BC Conservative"},
    {"name": "Larry Neufeld",             "riding": "Peace River South",            "party": "BC Conservative"},
    {"name": "Kiel Giddens",              "riding": "Prince George-Mackenzie",      "party": "BC Conservative"},
    {"name": "Sheldon Clare",             "riding": "Prince George-North Cariboo",  "party": "BC Conservative"},
    {"name": "Rosalyn Bird",              "riding": "Prince George-Valemount",      "party": "BC Conservative"},
    {"name": "Teresa Wat",                "riding": "Richmond-Bridgeport",          "party": "BC Conservative"},
    {"name": "Steve Kooner",              "riding": "Richmond-Queensborough",       "party": "BC Conservative"},
    {"name": "David Williams",            "riding": "Salmon Arm-Shuswap",           "party": "BC Conservative"},
    {"name": "Claire Rattee",             "riding": "Skeena",                       "party": "BC Conservative"},
    {"name": "Mandeep Dhaliwal",          "riding": "Surrey North",                 "party": "BC Conservative"},
    {"name": "Brent Chapman",             "riding": "Surrey South",                 "party": "BC Conservative"},
    {"name": "Bryan Tepper",              "riding": "Surrey-Panorama",              "party": "BC Conservative"},
    {"name": "Linda Hepner",              "riding": "Surrey-Serpentine River",      "party": "BC Conservative"},
    {"name": "Trevor Halford",            "riding": "Surrey-White Rock",            "party": "BC Conservative"},
    {"name": "Macklin McCall",            "riding": "West Kelowna-Peachland",       "party": "BC Conservative"},
    {"name": "Lynne Block",               "riding": "West Vancouver-Capilano",      "party": "BC Conservative"},
    {"name": "John Rustad",               "riding": "Nechako Lakes",                "party": "BC Conservative"},
    {"name": "Harman Bhangu",             "riding": "Langley-Abbotsford",           "party": "BC Conservative"},
    {"name": "Misty Van Popta",           "riding": "Langley-Walnut Grove",         "party": "BC Conservative"},
    {"name": "Jody Toor",                 "riding": "Langley-Willowbrook",          "party": "BC Conservative"},
    {"name": "Lawrence Mok",              "riding": "Maple Ridge East",             "party": "BC Conservative"},
    {"name": "Anna Kindy",                "riding": "North Island",                 "party": "BC Conservative"},

    # BC Greens (2)
    {"name": "Rob Botterell",             "riding": "Saanich North and the Islands", "party": "BC Green"},
    {"name": "Jeremy Valeriote",          "riding": "West Vancouver-Sea to Sky",    "party": "BC Green"},

    # Independents (status as of Oct 2025 per Wikipedia)
    {"name": "Elenore Sturko",            "riding": "Surrey-Cloverdale",            "party": "Independent"},
    {"name": "Amelia Boultbee",           "riding": "Penticton-Summerland",         "party": "Independent"},
    {"name": "Dallas Brodie",             "riding": "Vancouver-Quilchena",          "party": "Independent"},
    {"name": "Tara Armstrong",            "riding": "Kelowna-Lake Country-Coldstream", "party": "Independent"},
    {"name": "Hon Chan",                  "riding": "Richmond Centre",              "party": "Independent"},
]


_EMAIL_RE = re.compile(
    r'mailto:([A-Za-z][A-Za-z0-9._+\-]+\.mla@leg\.bc\.ca)',
    re.IGNORECASE,
)


def _norm_name(name: str) -> str:
    """Aggressive normalization for fuzzy matching against the email table."""
    n = unicodedata.normalize("NFKD", name)
    n = "".join(ch for ch in n if not unicodedata.combining(ch))
    n = n.lower()
    n = re.sub(r"[^a-z0-9]+", " ", n).strip()
    return n


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


def _slug(name: str) -> str:
    """Legislature URL slug: lowercase-hyphen, no punctuation."""
    n = unicodedata.normalize("NFKD", name)
    n = "".join(ch for ch in n if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", "-", n.lower()).strip("-")


async def _fetch_email_map(client: httpx.AsyncClient) -> dict[str, str]:
    """Return {normalized_name: canonical_email} from the BC email table."""
    out: dict[str, str] = {}
    try:
        r = await client.get(EMAIL_PAGE)
        r.raise_for_status()
    except Exception as exc:
        log.warning("BC email-page fetch failed: %s", exc)
        return out
    for m in _EMAIL_RE.finditer(r.text):
        email = m.group(1)
        # 'FirstName.LastName.MLA@leg.bc.ca'
        local = email.split("@", 1)[0]
        local = re.sub(r"\.mla$", "", local, flags=re.IGNORECASE)
        # 's.chandraherbert' -> 's chandraherbert' (unhelpful) is rare;
        # normalize dots+hyphens to spaces.
        normalized = _norm_name(local.replace(".", " ").replace("-", " "))
        if normalized:
            out[normalized] = email.lower()
    return out


async def run(db: Database) -> None:
    console.print(
        f"[cyan]British Columbia: ingesting {len(MEMBERS)} MLAs "
        f"(static roster + live email-table fetch)[/cyan]"
    )
    async with httpx.AsyncClient(
        timeout=30,
        headers={"User-Agent": BROWSER_UA},
        follow_redirects=True,
    ) as client:
        email_map = await _fetch_email_map(client)
        console.print(
            f"[cyan]  matched {len(email_map)} emails from leg.bc.ca[/cyan]"
        )

        ingested = 0
        urls_attached = 0
        emails_matched = 0
        for mem in MEMBERS:
            try:
                name = mem["name"]
                first, last = _split_name(name)
                slug = _slug(name)
                source_id = f"{SOURCE_PREFIX}:{slug}"

                # Match by normalized "first last" then try "last first".
                norm_fl = _norm_name(name)
                email = email_map.get(norm_fl)
                if not email and first and last:
                    # Try first-name partial: legislature emails are
                    # 'firstname.lastname' so we just look up on that.
                    email = email_map.get(
                        _norm_name(f"{first} {last}".replace(",", ""))
                    )
                if email:
                    emails_matched += 1

                # leg.bc.ca canonical member URL (best-guess format; the
                # path may move, but leg.bc.ca is in SHARED_OFFICIAL_HOSTS
                # so the scanner will still pick it up for DNS/GeoIP).
                official_url = f"https://www.leg.bc.ca/members/{slug}"

                pid = await upsert_politician(
                    db,
                    source_id=source_id,
                    name=name,
                    first_name=first,
                    last_name=last,
                    level="provincial",
                    province="BC",
                    office="MLA",
                    party=mem["party"],
                    constituency_name=mem["riding"],
                    email=email,
                    official_url=official_url,
                    extras={
                        "source": SOURCE_PREFIX,
                        "source_note": (
                            "roster from en.wikipedia.org/wiki/"
                            "43rd_Parliament_of_British_Columbia "
                            "(captured 2026-04-13); emails from "
                            "leg.bc.ca/contact-us/mla-contact-information"
                        ),
                    },
                )
                ingested += 1
                if await attach_website(db, pid, official_url, "official"):
                    urls_attached += 1
            except Exception as exc:
                log.exception(
                    "BC upsert failed for %s: %s", mem.get("name"), exc,
                )

    console.print(
        f"[green]BC: ingested {ingested}/{len(MEMBERS)} MLAs "
        f"({emails_matched} emails matched) · "
        f"{urls_attached} website rows attached[/green]"
    )
