"""Alberta MLA roster ingester — name → zero-padded mid enrichment.

The Alberta Legislative Assembly embeds a zero-padded 4-character
`mid` in every profile URL:

    /members/members-of-the-legislative-assembly/member-information?mid=0814
                                                                        ^^^^ → ab_assembly_mid

Bill sponsor links on the Assembly Dashboard use the same mid, so
populating ``politicians.ab_assembly_mid`` once turns bill sponsor
resolution into an exact-match FK lookup. Parallel to
``enrich_bc_member_ids`` (LIMS integer) and ``enrich_qc_mna_ids``
(Assnat integer).

Source: one HTTP GET of the public MLAs index page
(`/members/members-of-the-legislative-assembly`). The page is
server-rendered HTML with every sitting MLA linked via an
``<a href="...?mid=NNNN">Last, First</a>`` pattern.
"""
from __future__ import annotations

import logging
import re
import unicodedata
from typing import Any

import httpx

from ..db import Database

log = logging.getLogger(__name__)

ROSTER_URL = "https://www.assembly.ab.ca/members/members-of-the-legislative-assembly"
REQUEST_TIMEOUT = 30
HEADERS = {
    "User-Agent": "SovereignWatch/1.0 (civic-transparency; +admin@thebunkerops.ca)",
    "Accept": "text/html,application/xhtml+xml",
}

# Capture the mid plus the anchor text (which is the MLA's display
# name in "Last, First" order). Dotall so the regex tolerates whatever
# whitespace/markup lives between the tag and the text.
_MLA_LINK_RE = re.compile(
    r'href="[^"]*/member-information\?mid=(?P<mid>\d+)[^"]*"[^>]*>'
    r"\s*(?P<name>[^<]+?)\s*</a>",
    re.IGNORECASE | re.DOTALL,
)


_HONORIFICS_RE = re.compile(
    r"\b(?:member|honourable|honorable|hon\.?|mr\.?|mrs\.?|ms\.?|"
    r"miss\.?|dr\.?|prof\.?|premier|minister|speaker|deputy|"
    r"kc|qc)\b",
    re.IGNORECASE,
)


def _norm(s: str) -> str:
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.lower()
    s = re.sub(r"[^a-z0-9\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _strip_titles(s: str) -> str:
    """Remove honorifics and collapse any empty comma-separated chunks.

    Alberta's roster renders names as "LastName, [Title] FirstName" but
    with free-form title positions — e.g. "Amery, KC, Honourable Mickey"
    has the post-nominal (KC) *between* the surname and the forename,
    wedged in its own comma-delimited slot. Strip every title token,
    then drop empty fragments so "Amery, KC, Honourable Mickey" lands
    at "Amery, Mickey" and we can split cleanly.
    """
    cleaned = _HONORIFICS_RE.sub(" ", s or "")
    parts = [p.strip() for p in cleaned.split(",")]
    parts = [p for p in parts if p]
    return ", ".join(parts)


def _name_keys(display: str) -> set[str]:
    """Generate (normalized) candidate name keys from a roster name.

    Roster names come in as "Smith, John" or sometimes with embedded
    honorifics like "Smith, KC, Honourable John". We yield keys in
    both orders so a DB row storing either form hits.
    """
    raw = _strip_titles(display)
    keys: set[str] = set()
    if "," in raw:
        last, first = [p.strip() for p in raw.split(",", 1)]
        keys.add(_norm(f"{first} {last}"))
        keys.add(_norm(f"{last} {first}"))
    else:
        keys.add(_norm(raw))
    return {k for k in keys if k}


async def enrich_ab_mla_ids(db: Database) -> dict[str, int]:
    """Populate politicians.ab_assembly_mid for current Alberta MLAs."""
    stats = {"politicians_scanned": 0, "linked": 0, "ambiguous": 0}

    async with httpx.AsyncClient(headers=HEADERS, follow_redirects=True) as client:
        r = await client.get(ROSTER_URL, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        html = r.text

    # Dedup by mid — each MLA link appears multiple times (photo, name).
    entries: dict[str, dict[str, Any]] = {}
    for m in _MLA_LINK_RE.finditer(html):
        mid = m.group("mid")
        # Keep the longest name version seen (usually the clean one).
        name = re.sub(r"\s+", " ", m.group("name") or "").strip()
        if not name:
            continue
        existing = entries.get(mid)
        if existing is None or len(name) > len(existing["name"]):
            entries[mid] = {"mid": mid, "name": name}
    log.info("enrich_ab_mla_ids: roster page yielded %d unique MLAs", len(entries))

    name_to_mid: dict[str, str] = {}
    for e in entries.values():
        for key in _name_keys(e["name"]):
            name_to_mid.setdefault(key, e["mid"])

    rows = await db.fetch(
        """
        SELECT id, name, first_name, last_name
          FROM politicians
         WHERE level = 'provincial'
           AND province_territory = 'AB'
           AND is_active = true
           AND ab_assembly_mid IS NULL
        """
    )
    stats["politicians_scanned"] = len(rows)
    for p in rows:
        candidates = {
            _norm(p["name"]),
            _norm(f"{p['first_name'] or ''} {p['last_name'] or ''}"),
            _norm(f"{p['last_name'] or ''} {p['first_name'] or ''}"),
        }
        mid = next((name_to_mid[c] for c in candidates if c in name_to_mid), None)
        if mid is None:
            stats["ambiguous"] += 1
            continue
        await db.execute(
            "UPDATE politicians SET ab_assembly_mid = $2, updated_at = now() WHERE id = $1",
            str(p["id"]), mid,
        )
        stats["linked"] += 1

    log.info(
        "enrich_ab_mla_ids: scanned=%d linked=%d ambiguous=%d",
        stats["politicians_scanned"], stats["linked"], stats["ambiguous"],
    )
    return stats
