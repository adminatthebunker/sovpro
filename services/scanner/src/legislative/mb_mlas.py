"""Manitoba MLA slug-stamp + insert-missing ingestion.

The province's 57 MLA rows are almost entirely already in `politicians`
via Open North's `manitoba_mlas` set (see `opennorth.py`). That source
does not surface the MB Legislature's canonical identifier — the
surname slug used in every MLA profile URL:

    https://www.gov.mb.ca/legislature/members/info/{surname}.html

This module visits the authoritative roster at

    https://www.gov.mb.ca/legislature/members/mla_list_constituency.html

parses the table, and:

  * matches each MLA against existing `politicians` by normalized
    full name (``level='provincial' AND province_territory='MB'``).
    On a unique match, UPDATE `mb_assembly_slug` onto that row.
  * on no match, INSERT a minimal politician row keyed by a fresh
    ``source_id='manitoba-assembly:{slug}'`` — Open North will later
    converge on the same person via a separate ``source_id``, which
    is fine; the canonical slug here is the join key for sponsor /
    speaker resolution, not person-identity.

Emits a one-line stats summary in the shape every other ingest uses.
"""
from __future__ import annotations

import html as html_lib
import logging
import re
from dataclasses import dataclass
from typing import Optional

import httpx
from rich.console import Console

from ..db import Database
from .sponsor_resolver import _norm

log = logging.getLogger(__name__)
console = Console()

ROSTER_URL = "https://www.gov.mb.ca/legislature/members/mla_list_constituency.html"

USER_AGENT = "CanadianPoliticalDataBot/1.0 (+https://canadianpoliticaldata.ca)"

# Match <tr>...</tr> greedy within bounds, and each <td>...</td> inside.
_ROW_RE = re.compile(r"<tr\b[^>]*>(.*?)</tr>", re.IGNORECASE | re.DOTALL)
_CELL_RE = re.compile(r"<td\b[^>]*>(.*?)</td>", re.IGNORECASE | re.DOTALL)
# Profile link inside a cell: href="info/<slug>.html" or href="./info/<slug>.html".
_PROFILE_HREF_RE = re.compile(r'href="[^"]*info/([a-z0-9_\-]+)\.html"', re.IGNORECASE)
# Strip any remaining tags from cell content.
_TAG_RE = re.compile(r"<[^>]+>")
_HONORIFIC_RE = re.compile(r"\b(Hon\.?|Mr\.?|Mrs\.?|Ms\.?|Dr\.?)\b", re.IGNORECASE)


@dataclass
class MBMlaRow:
    surname_slug: str
    full_name: str           # "Jodie Byram" — reconstructed in first-last order
    first_name: str
    last_name: str
    constituency: str
    party: Optional[str]     # "PC", "NDP", "IND", "IND LIB" — upstream codes
    profile_url: str


# Upstream party codes → long form for politician_terms.party / politicians.party.
PARTY_CODE_TO_NAME: dict[str, str] = {
    "PC": "Progressive Conservative",
    "NDP": "New Democratic Party",
    "LIB": "Liberal",
    "IND": "Independent",
    "IND LIB": "Independent Liberal",
}


def _text(cell_html: str) -> str:
    """Strip tags + decode entities + collapse whitespace."""
    no_tags = _TAG_RE.sub(" ", cell_html)
    decoded = html_lib.unescape(no_tags)
    return re.sub(r"\s+", " ", decoded).strip()


def _parse_member_cell(raw: str) -> tuple[str, str, str]:
    """Parse a Member cell like ``BYRAM, Jodie`` or ``SIMARD, Hon. Glen``.

    Returns ``(full_name, first_name, last_name)`` with the surname
    re-cased from ALL-CAPS and any honorific stripped from first.
    """
    cell = raw.strip()
    if "," in cell:
        last_raw, first_raw = cell.split(",", 1)
    else:
        parts = cell.split()
        last_raw, first_raw = parts[-1], " ".join(parts[:-1])
    last = last_raw.strip().title()
    first_stripped = _HONORIFIC_RE.sub("", first_raw)
    first = re.sub(r"\s+", " ", first_stripped).strip()
    full = f"{first} {last}".strip()
    return full, first, last


def _parse_roster_html(html: str) -> list[MBMlaRow]:
    """Extract MLA rows from the roster HTML by regex on <tr>/<td>."""
    out: list[MBMlaRow] = []
    seen_slugs: set[str] = set()
    for tr_match in _ROW_RE.finditer(html):
        tr = tr_match.group(1)
        slug_match = _PROFILE_HREF_RE.search(tr)
        if not slug_match:
            continue
        slug = slug_match.group(1).lower()
        if slug in seen_slugs:
            continue
        cells = _CELL_RE.findall(tr)
        if len(cells) < 3:
            continue
        seen_slugs.add(slug)

        constituency = _text(cells[0])
        member_text = _text(cells[1])
        party_raw = _text(cells[2])
        full_name, first, last = _parse_member_cell(member_text)
        party = party_raw.upper() if party_raw else None

        out.append(MBMlaRow(
            surname_slug=slug,
            full_name=full_name,
            first_name=first,
            last_name=last,
            constituency=constituency,
            party=party,
            profile_url=f"https://www.gov.mb.ca/legislature/members/info/{slug}.html",
        ))
    return out


async def _fetch_roster(client: httpx.AsyncClient) -> str:
    r = await client.get(ROSTER_URL, timeout=30)
    r.raise_for_status()
    return r.text


async def _find_existing(db: Database, row: MBMlaRow) -> Optional[str]:
    """Return politicians.id for a unique name match at level=provincial / MB, or None."""
    candidates = await db.fetch(
        """
        SELECT id, name, first_name, last_name, mb_assembly_slug
          FROM politicians
         WHERE level = 'provincial'
           AND province_territory = 'MB'
        """,
    )
    target_full = _norm(row.full_name)
    target_flast = _norm(f"{row.first_name} {row.last_name}")
    matches: list[str] = []
    for c in candidates:
        name_norm = _norm(c["name"] or "")
        flast_norm = _norm(f"{c['first_name'] or ''} {c['last_name'] or ''}")
        if target_full and (name_norm == target_full or flast_norm == target_full):
            matches.append(str(c["id"]))
            continue
        if target_flast and (name_norm == target_flast or flast_norm == target_flast):
            matches.append(str(c["id"]))
    unique = list(dict.fromkeys(matches))
    if len(unique) == 1:
        return unique[0]
    return None


async def _insert_new(db: Database, row: MBMlaRow) -> str:
    """Insert a minimal politicians row for an MLA not yet covered by Open North."""
    source_id = f"manitoba-assembly:{row.surname_slug}"
    existing = await db.fetchval(
        "SELECT id FROM politicians WHERE source_id = $1", source_id
    )
    if existing:
        return str(existing)
    new_id = await db.fetchval(
        """
        INSERT INTO politicians (
            source_id, name, first_name, last_name,
            party, elected_office, level, province_territory,
            constituency_name, official_url, mb_assembly_slug, is_active
        )
        VALUES ($1, $2, $3, $4, $5, 'MLA', 'provincial', 'MB', $6, $7, $8, true)
        RETURNING id
        """,
        source_id,
        row.full_name,
        row.first_name,
        row.last_name,
        PARTY_CODE_TO_NAME.get(row.party or "", row.party),
        row.constituency,
        row.profile_url,
        row.surname_slug,
    )
    return str(new_id)


async def ingest(db: Database) -> dict[str, int]:
    stats = {
        "fetched": 0,
        "matched_existing": 0,
        "slugs_set": 0,
        "slugs_already_correct": 0,
        "slug_conflict": 0,
        "ambiguous_no_match": 0,
        "inserted": 0,
    }
    async with httpx.AsyncClient(
        headers={"User-Agent": USER_AGENT, "Accept-Language": "en-CA,en;q=0.9"},
        follow_redirects=True,
    ) as client:
        html = await _fetch_roster(client)

    rows = _parse_roster_html(html)
    stats["fetched"] = len(rows)

    for row in rows:
        pol_id = await _find_existing(db, row)
        if pol_id is None:
            pol_id = await _insert_new(db, row)
            stats["inserted"] += 1
            stats["slugs_set"] += 1
            continue

        stats["matched_existing"] += 1

        current = await db.fetchval(
            "SELECT mb_assembly_slug FROM politicians WHERE id = $1", pol_id
        )
        if current == row.surname_slug:
            stats["slugs_already_correct"] += 1
            continue
        if current is not None and current != row.surname_slug:
            log.warning(
                "mb_mlas: slug conflict for %s: existing=%s incoming=%s — leaving existing",
                row.full_name, current, row.surname_slug,
            )
            stats["slug_conflict"] += 1
            continue
        await db.execute(
            "UPDATE politicians SET mb_assembly_slug = $2, updated_at = now() WHERE id = $1",
            pol_id, row.surname_slug,
        )
        stats["slugs_set"] += 1

    log.info(
        "mb_mlas: fetched=%d matched=%d inserted=%d slugs_set=%d already=%d conflicts=%d",
        stats["fetched"], stats["matched_existing"], stats["inserted"],
        stats["slugs_set"], stats["slugs_already_correct"], stats["slug_conflict"],
    )
    return stats
