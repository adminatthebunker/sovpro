"""Quebec MNA roster ingester — name → numeric id enrichment.

Quebec's Assemblée nationale embeds a stable integer MNA id in every
profile URL slug:

    /en/deputes/jolin-barrette-simon-15359/index.html
                                  ^^^^^ — our qc_assnat_id

Scraping the MNA index page once gives us (slug, id, name) for every
sitting member. We name-match to politicians where level='provincial'
and province='QC', then write the id back. Once that's done, bill
sponsor resolution becomes an exact integer FK lookup — same leverage
as BC's lims_member_id, no name-fuzz needed.

This module is deliberately narrow: *roster only*. Bill / vote /
committee fetchers live in their own modules and assume this has
already run.
"""
from __future__ import annotations

import logging
import re
import unicodedata
from typing import Any

import httpx

from ..db import Database

log = logging.getLogger(__name__)

MNA_INDEX_URL = "https://www.assnat.qc.ca/en/deputes/index.html"
REQUEST_TIMEOUT = 30
HEADERS = {
    "User-Agent": "SovereignWatch/1.0 (civic-transparency; +admin@thebunkerops.ca)",
    "Accept": "text/html,application/xhtml+xml",
}

# /en/deputes/{surname}-{given}-{id}/index.html
# The slug is surname-then-given (reversed from spoken order). We keep
# both keys in the lookup to tolerate whichever order the DB has.
_MNA_URL_RE = re.compile(
    r"/en/deputes/(?P<slug>[a-z0-9-]+)-(?P<id>\d+)/index\.html"
)


def _norm(s: str) -> str:
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.lower()
    s = re.sub(r"[^a-z0-9\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _split_slug(slug: str) -> tuple[str, str]:
    """Guess (surname, given) from a slug like "jolin-barrette-simon".

    Slugs are hyphen-joined and Quebec's convention is surname first.
    For single-token surnames (most common) the final token is the
    given name. For compound surnames ("Jolin-Barrette") Quebec still
    puts the *compound* ahead of the given name, so the final token is
    still the given name and everything preceding it is the surname.
    """
    parts = slug.split("-")
    if len(parts) < 2:
        return slug, ""
    return " ".join(parts[:-1]), parts[-1]


async def enrich_qc_mna_ids(db: Database) -> dict[str, int]:
    """Populate politicians.qc_assnat_id for current Quebec MNAs.

    Strategy:
      1. Fetch MNA index HTML (one request, ~125 rows).
      2. Build (normalized_name → id) lookup from slug parts.
      3. For every active QC provincial politician without qc_assnat_id,
         try matching by politicians.name, then by (first_name, last_name).
    """
    stats = {"politicians_scanned": 0, "linked": 0, "ambiguous": 0}

    async with httpx.AsyncClient(headers=HEADERS, follow_redirects=True) as client:
        r = await client.get(MNA_INDEX_URL, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        html = r.text

    # Uniq set — every MNA profile link appears 2+ times on the page
    # (photo + name). Dedup by (id, slug).
    seen: set[tuple[str, int]] = set()
    entries: list[dict[str, Any]] = []
    for m in _MNA_URL_RE.finditer(html):
        slug = m.group("slug")
        mna_id = int(m.group("id"))
        key = (slug, mna_id)
        if key in seen:
            continue
        seen.add(key)
        surname, given = _split_slug(slug)
        entries.append({
            "id": mna_id,
            "slug": slug,
            "surname": surname,
            "given": given,
        })
    log.info("enrich_qc_mna_ids: roster page yielded %d unique MNAs", len(entries))

    # Two-way lookup: normalized "given surname" *and* "surname given".
    # Gives us tolerance for DB rows that stored name either way.
    name_to_id: dict[str, int] = {}
    for e in entries:
        for key in (
            _norm(f"{e['given']} {e['surname']}"),
            _norm(f"{e['surname']} {e['given']}"),
        ):
            if key:
                name_to_id.setdefault(key, e["id"])

    rows = await db.fetch(
        """
        SELECT id, name, first_name, last_name
          FROM politicians
         WHERE level = 'provincial'
           AND province_territory = 'QC'
           AND is_active = true
           AND qc_assnat_id IS NULL
        """
    )
    stats["politicians_scanned"] = len(rows)
    for p in rows:
        candidates = {
            _norm(p["name"]),
            _norm(f"{p['first_name'] or ''} {p['last_name'] or ''}"),
            _norm(f"{p['last_name'] or ''} {p['first_name'] or ''}"),
        }
        mna_id = next((name_to_id[c] for c in candidates if c in name_to_id), None)
        if mna_id is None:
            stats["ambiguous"] += 1
            continue
        await db.execute(
            "UPDATE politicians SET qc_assnat_id = $2, updated_at = now() WHERE id = $1",
            str(p["id"]), mna_id,
        )
        stats["linked"] += 1

    log.info(
        "enrich_qc_mna_ids: scanned=%d linked=%d ambiguous=%d",
        stats["politicians_scanned"], stats["linked"], stats["ambiguous"],
    )
    return stats
