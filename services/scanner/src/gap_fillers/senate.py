"""Direct scraper for the Canadian Senate (sencanada.ca).

Open North has no representative-set for the Canadian Senate as of
2026-04-13 — ``/representatives/canadian-senate/`` and every other
candidate slug return ``total_count: 0`` and there is no senate entry
in the ``/representative-sets/`` index. The Senate's own Umbraco-backed
site exposes structured AJAX partials that we fetch directly:

    /umbraco/surface/SenatorsAjax/GetSenators?displayFor=senatorslist
    /umbraco/surface/SenatorsAjax/GetSenators?displayFor=senatorscontactinformation
    /umbraco/surface/SenatorBio/GetBio?displayFor=senatorheader&senatorId=<id>

Steps:

  1. Fetch ``senatorslist`` — gives every current senator's slug,
     affiliation code (CSG/ISG/PSG/C/Non-affiliated), province, and
     date of nomination.
  2. Fetch ``senatorscontactinformation`` — adds phone + legislative
     email (``*@sen.parl.gc.ca``), keyed on slug.
  3. For each senator, fetch ``/en/senators/<slug>/`` to find the
     ``senatorId`` in the embedded ``SenatorBio`` AJAX markers, then
     fetch ``senatorheader`` for the portrait, full affiliation name,
     personal website, and social-media links.
  4. Upsert each row via the shared helpers in ``gap_fillers.shared``.

Senate seats are apportioned constitutionally (ON 24, QC 24, NS/NB 10,
PE 4, MB/SK/AB/BC 6, NL 6, YT/NT/NU 1 each = 105). At any given time
several seats are vacant; this scraper only ingests the senators
currently appointed. We store each senator with
``level='federal'``, ``elected_office='Senator'`` and
``province_territory`` set to the province the senator represents
(the Senate is federal but each seat is apportioned to a province).
"""
from __future__ import annotations

import asyncio
import logging
import re
from html import unescape
from typing import Optional
from urllib.parse import urljoin

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
from .shared import BROWSER_UA, attach_socials, attach_website, upsert_politician

log = logging.getLogger(__name__)
console = Console()


BASE = "https://sencanada.ca"
LIST_URL = (
    f"{BASE}/umbraco/surface/SenatorsAjax/GetSenators"
    f"?displayFor=senatorslist&Lang=en"
)
CONTACT_URL = (
    f"{BASE}/umbraco/surface/SenatorsAjax/GetSenators"
    f"?displayFor=senatorscontactinformation&Lang=en"
)
HEADER_URL_TPL = (
    f"{BASE}/umbraco/surface/SenatorBio/GetBio"
    f"?displayFor=senatorheader&senatorId={{sid}}&columns=0&Lang=en"
)
PROFILE_URL_TPL = f"{BASE}/en/senators/{{slug}}/"
SOURCE_PREFIX = "direct:sencanada-ca"


# Map the affiliation short-codes used in the list view to the canonical
# party-group names the rest of the codebase uses. The Senate currently
# has five caucuses (CSG, ISG, PSG) plus the Conservative Party of
# Canada's Senate members (C) and Non-affiliated senators.
_AFFILIATION_NAME: dict[str, str] = {
    "CSG": "Canadian Senators Group",
    "ISG": "Independent Senators Group",
    "PSG": "Progressive Senate Group",
    "C":   "Conservative Party of Canada",
    "Non-affiliated": "Non-affiliated",
}

# 13 Canadian provinces/territories — Senate is apportioned to each.
_PROVINCE_BY_NAME: dict[str, str] = {
    "Alberta": "AB",
    "British Columbia": "BC",
    "Manitoba": "MB",
    "New Brunswick": "NB",
    "Newfoundland and Labrador": "NL",
    "Northwest Territories": "NT",
    "Nova Scotia": "NS",
    "Nunavut": "NU",
    "Ontario": "ON",
    "Prince Edward Island": "PE",
    "Quebec": "QC",
    "Québec": "QC",
    "Saskatchewan": "SK",
    "Yukon": "YT",
}

_LIST_ROW_RE = re.compile(r"<tr>(.*?)</tr>", re.IGNORECASE | re.DOTALL)
_LIST_SLUG_RE = re.compile(
    r'href="(/en/senators/([a-z0-9\-]+)/)"', re.IGNORECASE,
)
_LIST_NAME_RE = re.compile(
    r'href="/en/senators/[a-z0-9\-]+/"\s*>\s*([^<]+?)\s*</a>',
    re.IGNORECASE,
)
_LIST_AFF_RE = re.compile(
    r'data-search="aff-([A-Za-z\-]+)-"', re.IGNORECASE,
)
_LIST_PROV_RE = re.compile(
    r'data-search="province-([A-Z]{2})"[^>]*data-order="([^"]+)"'
    r'[^>]*>\s*([^<]+?)\s*</td>',
    re.DOTALL,
)
_LIST_DATE_RE = re.compile(
    r'data-search="gender-[^"]*"\s*data-order="(\d{4}-\d{2}-\d{2})',
    re.IGNORECASE,
)

_CONTACT_ROW_RE = re.compile(r"<tr>(.*?)</tr>", re.IGNORECASE | re.DOTALL)
_CONTACT_SLUG_RE = re.compile(
    r'href="/en/senators/([a-z0-9\-]+)/"', re.IGNORECASE,
)
_CONTACT_PHONE_RE = re.compile(
    r"<td>\s*(\d{3}-\d{3}-\d{4})\s*</td>", re.IGNORECASE,
)
_CONTACT_EMAIL_RE = re.compile(
    r'href="mailto:([^"]+@sen\.parl\.gc\.ca)"', re.IGNORECASE,
)

_PROFILE_SENATOR_ID_RE = re.compile(
    r'SenatorBio[^{]*\{[^}]*senatorId&quot;:(\d+)',
    re.IGNORECASE,
)

_HEADER_PHOTO_RE = re.compile(
    r'sc-senator-bio-senatorheader-content-photo.*?'
    r'<img[^>]*\bsrc="([^"]+)"',
    re.IGNORECASE | re.DOTALL,
)
# Each "<li>" in the header card starts with a <span class="...-label">LABEL:</span>
# followed by the value. Build a generic extractor so we catch Personal
# Website, Follow (socials), etc.
_HEADER_LI_RE = re.compile(
    r'<li[^>]*sc-senator-bio-senatorheader-content-card-list-item[^>]*>'
    r'(.*?)</li>',
    re.IGNORECASE | re.DOTALL,
)
_LABEL_RE = re.compile(
    r'list-item-label[^>]*>\s*([^<]+?)\s*(?::\s*)?</span>',
    re.IGNORECASE | re.DOTALL,
)
_ANY_HREF_RE = re.compile(r'href="([^"]+)"', re.IGNORECASE)


# Stock affiliation hyperlinks that appear on the sencanada.ca "Follow"
# icons for each caucus landing page. These are NOT per-senator accounts
# — they're the caucus group's own handles. We must filter them out from
# social discovery; every CSG senator shows the same ``@csg_gsc`` link.
_CAUCUS_SOCIAL_HOSTS: frozenset[str] = frozenset({
    "twitter.com/csg_gsc",
    "twitter.com/isg_gsi",
    "twitter.com/psg_gps",
    "x.com/csg_gsc",
    "x.com/isg_gsi",
    "x.com/psg_gps",
})


def _strip_tags(html: str) -> str:
    return re.sub(r"<[^>]+>", " ", html)


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", unescape(text)).strip()


def _split_display_name(name: str) -> tuple[Optional[str], Optional[str], str]:
    """Split ``"Adler, Charles S."`` into ``(first, last, display)``.

    The sencanada list view uses "Last, First" ordering. We rebuild a
    display name in "First Last" order and return first/last separately
    for first_name/last_name columns.
    """
    name = _clean(name)
    if "," in name:
        last, first = [p.strip() for p in name.split(",", 1)]
    else:
        parts = name.split()
        if len(parts) < 2:
            return None, name, name
        first, last = parts[0], " ".join(parts[1:])
    display = f"{first} {last}".strip()
    return first or None, last or None, display


def _extract_province(text: str) -> tuple[Optional[str], Optional[str]]:
    """Return ``(code, designation)`` given a "Province (Designation)" string.

    Example inputs::
        "Ontario"                       -> ("ON", None)
        "Ontario (Toronto)"             -> ("ON", "Toronto")
        "Quebec (De Lorimier)"          -> ("QC", "De Lorimier")
        "Northwest Territories"         -> ("NT", None)
    """
    text = _clean(text)
    m = re.match(r"^(.+?)\s*\(\s*([^)]+)\s*\)\s*$", text)
    if m:
        prov_name = m.group(1).strip()
        desig = m.group(2).strip() or None
    else:
        prov_name = text
        desig = None
    return _PROVINCE_BY_NAME.get(prov_name), desig


def _parse_list(html: str) -> list[dict]:
    """Parse the senatorslist partial into one dict per senator."""
    seen: dict[str, dict] = {}
    for m in _LIST_ROW_RE.finditer(html):
        row = m.group(1)
        if "href=\"/en/senators/" not in row:
            continue
        slug_m = _LIST_SLUG_RE.search(row)
        name_m = _LIST_NAME_RE.search(row)
        aff_m = _LIST_AFF_RE.search(row)
        prov_m = _LIST_PROV_RE.search(row)
        date_m = _LIST_DATE_RE.search(row)
        if not (slug_m and name_m and prov_m):
            continue
        slug = slug_m.group(2)
        display_raw = _clean(name_m.group(1))
        code, desig = _extract_province(prov_m.group(3))
        if not code:
            # Skip anything we can't geolocate to a province.
            log.warning("senate: unknown province on slug=%s raw=%r",
                        slug, prov_m.group(3))
            continue
        aff_code = (aff_m.group(1) if aff_m else "").strip()
        party = _AFFILIATION_NAME.get(aff_code)
        seen.setdefault(slug, {
            "slug": slug,
            "profile_path": slug_m.group(1),
            "display_raw": display_raw,  # "Last, First"
            "province": code,
            "designation": desig,
            "aff_code": aff_code,
            "party": party,
            "nominated_on": date_m.group(1) if date_m else None,
        })
    return list(seen.values())


def _parse_contact(html: str) -> dict[str, dict]:
    """Parse senatorscontactinformation into ``{slug: {phone, email}}``."""
    out: dict[str, dict] = {}
    for m in _CONTACT_ROW_RE.finditer(html):
        row = m.group(1)
        slug_m = _CONTACT_SLUG_RE.search(row)
        if not slug_m:
            continue
        phone_m = _CONTACT_PHONE_RE.search(row)
        email_m = _CONTACT_EMAIL_RE.search(row)
        out[slug_m.group(1)] = {
            "phone": phone_m.group(1) if phone_m else None,
            "email": email_m.group(1).lower() if email_m else None,
        }
    return out


def _platform_of(url: str) -> Optional[str]:
    """Return a platform hint for a social URL, or None if it's not one."""
    u = url.lower()
    if "twitter.com" in u or "x.com" in u:
        return "twitter"
    if "facebook.com" in u or "fb.com" in u:
        return "facebook"
    if "instagram.com" in u:
        return "instagram"
    if "youtube.com" in u or "youtu.be" in u:
        return "youtube"
    if "tiktok.com" in u:
        return "tiktok"
    if "linkedin.com" in u:
        return "linkedin"
    if "bsky.app" in u:
        return "bluesky"
    if "threads.net" in u:
        return "threads"
    return None


def _is_caucus_social(url: str) -> bool:
    """Filter out caucus-group social handles (shared across all members)."""
    u = url.lower().rstrip("/")
    for marker in _CAUCUS_SOCIAL_HOSTS:
        if marker in u:
            return True
    return False


def _parse_header(html: str) -> dict:
    """Pull photo/personal_url/socials out of the senatorheader partial.

    Returns a dict with keys that may be absent:
      - photo_url
      - personal_url
      - affiliation_full (e.g. "Canadian Senators Group")
      - socials: dict[platform, url]
    """
    out: dict = {"socials": {}}
    m = _HEADER_PHOTO_RE.search(html)
    if m:
        src = m.group(1).split("?", 1)[0]
        out["photo_url"] = urljoin(BASE, src)
    for li_m in _HEADER_LI_RE.finditer(html):
        li = li_m.group(1)
        label_m = _LABEL_RE.search(li)
        if not label_m:
            continue
        label = _clean(label_m.group(1)).lower().rstrip(":").strip()
        if label.startswith("personal website"):
            href_m = _ANY_HREF_RE.search(li)
            if href_m:
                url = href_m.group(1).strip()
                if url.startswith("http"):
                    out["personal_url"] = url
        elif label.startswith("follow"):
            for href_m in _ANY_HREF_RE.finditer(li):
                url = href_m.group(1).strip()
                if not url.startswith("http"):
                    continue
                if _is_caucus_social(url):
                    continue
                plat = _platform_of(url)
                if plat:
                    out["socials"].setdefault(plat, url)
        elif label.startswith("affiliation"):
            # Remove leading <span class="...-label">Affiliation:</span>
            # then whitespace/br to get the caucus name.
            after = li[label_m.end():]
            # The value is whatever text follows the span (ignore any
            # internal icons / tags).
            value = _clean(_strip_tags(after))
            if value:
                out["affiliation_full"] = value
    return out


async def _fetch_text(client: httpx.AsyncClient, url: str) -> Optional[str]:
    try:
        r = await client.get(url)
        if r.status_code != 200:
            log.debug("senate: non-200 %s -> %s", url, r.status_code)
            return None
        return r.text
    except Exception as exc:
        log.debug("senate: fetch failed %s: %s", url, exc)
        return None


async def _fetch_senator_id(
    client: httpx.AsyncClient, slug: str
) -> Optional[int]:
    """Scrape the senator's profile page for the numeric senatorId used
    by the SenatorBio AJAX endpoint."""
    html = await _fetch_text(client, PROFILE_URL_TPL.format(slug=slug))
    if not html:
        return None
    m = _PROFILE_SENATOR_ID_RE.search(html)
    if m:
        try:
            return int(m.group(1))
        except ValueError:
            return None
    return None


async def _fetch_header(
    client: httpx.AsyncClient, senator_id: int
) -> dict:
    html = await _fetch_text(client, HEADER_URL_TPL.format(sid=senator_id))
    if not html:
        return {"socials": {}}
    return _parse_header(html)


async def run(db: Database) -> None:
    """Scrape sencanada.ca and upsert every currently-seated senator."""
    console.print("[cyan]Senate: fetching sencanada.ca roster[/cyan]")
    async with httpx.AsyncClient(
        timeout=30,
        headers={"User-Agent": BROWSER_UA},
        follow_redirects=True,
    ) as client:
        list_html = await _fetch_text(client, LIST_URL)
        if not list_html:
            console.print("[red]Senate: list partial fetch failed[/red]")
            return
        senators = _parse_list(list_html)
        console.print(f"[cyan]  parsed {len(senators)} senators from list[/cyan]")
        if not senators:
            console.print("[yellow]Senate: roster empty — skipping[/yellow]")
            return

        contact_html = await _fetch_text(client, CONTACT_URL)
        contacts = _parse_contact(contact_html or "")
        console.print(f"[cyan]  parsed {len(contacts)} contact rows[/cyan]")

        sem = asyncio.Semaphore(4)
        ingested = 0
        urls_attached = 0
        socials_saved = 0

        with Progress(
            SpinnerColumn(),
            TextColumn("{task.description}"),
            BarColumn(),
            TextColumn("{task.completed}/{task.total}"),
            TimeElapsedColumn(),
        ) as progress:
            task = progress.add_task("Senators", total=len(senators))

            async def handle(sen: dict) -> None:
                nonlocal ingested, urls_attached, socials_saved
                async with sem:
                    slug = sen["slug"]
                    try:
                        contact = contacts.get(slug, {})
                        sid = await _fetch_senator_id(client, slug)
                        header: dict = {"socials": {}}
                        if sid is not None:
                            header = await _fetch_header(client, sid)

                        first, last, display = _split_display_name(
                            sen["display_raw"]
                        )
                        party = header.get("affiliation_full") or sen["party"]
                        source_id = f"{SOURCE_PREFIX}:{slug}"
                        official_url = urljoin(BASE, sen["profile_path"])

                        extras = {
                            "source": SOURCE_PREFIX,
                            "slug": slug,
                            "senate_affiliation_code": sen["aff_code"] or None,
                            "senate_designation": sen.get("designation"),
                            "nominated_on": sen.get("nominated_on"),
                            "sencanada_senator_id": sid,
                        }

                        pid = await upsert_politician(
                            db,
                            source_id=source_id,
                            name=display,
                            first_name=first,
                            last_name=last,
                            level="federal",
                            province=sen["province"],
                            office="Senator",
                            party=party,
                            constituency_name=sen.get("designation"),
                            constituency_id=None,
                            email=contact.get("email"),
                            phone=contact.get("phone"),
                            photo_url=header.get("photo_url"),
                            personal_url=header.get("personal_url"),
                            official_url=official_url,
                            social_urls=header.get("socials") or {},
                            extras=extras,
                        )
                        ingested += 1

                        # Attach the sencanada.ca profile URL itself — the
                        # label_for helper will automatically mark this as
                        # shared_official once sencanada.ca is added to
                        # SHARED_OFFICIAL_HOSTS.
                        if await attach_website(
                            db, pid, official_url, "official"
                        ):
                            urls_attached += 1

                        personal_url = header.get("personal_url")
                        if personal_url:
                            if await attach_website(
                                db, pid, personal_url, "personal"
                            ):
                                urls_attached += 1

                        socials_saved += await attach_socials(
                            db, pid, header.get("socials") or {}
                        )
                    except Exception as exc:
                        log.exception(
                            "senate upsert failed for %s: %s",
                            sen.get("display_raw"), exc,
                        )
                    finally:
                        progress.update(task, advance=1)

            await asyncio.gather(*(handle(s) for s in senators))

    console.print(
        f"[green]Senate: ingested {ingested} senators, "
        f"{urls_attached} website rows attached, "
        f"{socials_saved} social handles saved[/green]"
    )
