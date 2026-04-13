"""Ingestion from Open North's Represent API.

Endpoints (all paginated with ?limit=):
  /representatives/house-of-commons/
  /representatives/alberta-legislature/
  /representatives/edmonton-city-council/
  /representatives/calgary-city-council/
  /boundaries/{set}/{slug}/simple_shape

Each representative object contains:
  name, first_name, last_name, party_name, elected_office,
  district_name, email, photo_url, personal_url, url (party profile),
  extra, offices, ... plus related_links including the boundary.
"""
from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from typing import Any, Iterable, Optional

import httpx
import orjson
from rich.console import Console
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn

from .db import Database

log = logging.getLogger(__name__)
console = Console()


BASE = os.environ.get("OPENNORTH_BASE", "https://represent.opennorth.ca")


@dataclass
class OpenNorthSet:
    path: str
    level: str
    province: Optional[str]
    office: str
    boundary_set: str
    boundary_level: str


SETS = {
    "federal_mps": OpenNorthSet(
        path="/representatives/house-of-commons/",
        level="federal",
        province=None,
        office="MP",
        boundary_set="federal-electoral-districts",
        boundary_level="federal",
    ),
    "alberta_mlas": OpenNorthSet(
        path="/representatives/alberta-legislature/",
        level="provincial",
        province="AB",
        office="MLA",
        boundary_set="alberta-electoral-districts",
        boundary_level="provincial",
    ),
    "edmonton_council": OpenNorthSet(
        path="/representatives/edmonton-city-council/",
        level="municipal",
        province="AB",
        office="City Councillor",
        boundary_set="edmonton-wards",
        boundary_level="municipal",
    ),
    "calgary_council": OpenNorthSet(
        path="/representatives/calgary-city-council/",
        level="municipal",
        province="AB",
        office="City Councillor",
        boundary_set="calgary-wards",
        boundary_level="municipal",
    ),
    # ── Additional Alberta municipal councils ────────────────────────
    "strathcona_county": OpenNorthSet(
        path="/representatives/strathcona-county-council/",
        level="municipal", province="AB", office="Councillor",
        boundary_set="strathcona-county-wards", boundary_level="municipal",
    ),
    "wood_buffalo": OpenNorthSet(
        path="/representatives/wood-buffalo-municipal-council/",
        level="municipal", province="AB", office="Councillor",
        boundary_set="wood-buffalo-wards", boundary_level="municipal",
    ),
    "lethbridge_council": OpenNorthSet(
        path="/representatives/lethbridge-city-council/",
        level="municipal", province="AB", office="City Councillor",
        boundary_set="lethbridge-wards", boundary_level="municipal",
    ),
    "grande_prairie_council": OpenNorthSet(
        path="/representatives/grande-prairie-city-council/",
        level="municipal", province="AB", office="City Councillor",
        boundary_set="grande-prairie-wards", boundary_level="municipal",
    ),
    "county_grande_prairie": OpenNorthSet(
        path="/representatives/county-of-grande-prairie-no-1-council/",
        level="municipal", province="AB", office="Councillor",
        boundary_set="county-of-grande-prairie-no-1-wards", boundary_level="municipal",
    ),
}


async def _fetch_json(client: httpx.AsyncClient, url: str) -> dict:
    r = await client.get(url)
    r.raise_for_status()
    return r.json()


async def _fetch_reps(client: httpx.AsyncClient, set_def: OpenNorthSet, limit: int) -> list[dict]:
    """Page through /representatives/... until exhausted or limit reached."""
    out: list[dict] = []
    next_url = f"{BASE}{set_def.path}?limit={min(limit, 100)}"
    while next_url and len(out) < limit:
        data = await _fetch_json(client, next_url)
        out.extend(data.get("objects", []))
        meta = data.get("meta", {})
        nxt = meta.get("next")
        next_url = f"{BASE}{nxt}" if nxt else None
    return out[:limit]


def _social_urls(rep: dict) -> dict:
    urls: dict[str, str] = {}
    for link in rep.get("extra", {}).get("urls", []) or []:
        note = (link.get("note") or "").lower()
        url = link.get("url")
        if not url:
            continue
        for key in ("twitter", "x.com", "facebook", "instagram", "youtube", "tiktok", "linkedin"):
            if key in note or key in url:
                urls[key.replace("x.com", "twitter")] = url
                break
    return urls


def _constituency_id(rep: dict, set_def: OpenNorthSet) -> Optional[str]:
    """Return '{boundary_set}/{slug-or-id}' parsed from rep['related']['boundary_url']."""
    related = rep.get("related") or {}
    b = related.get("boundary_url") or rep.get("boundary_url")
    if b:
        # b looks like '/boundaries/{set}/{slug}/'
        parts = [p for p in b.strip("/").split("/") if p]
        # ['boundaries', '{set}', '{slug}']
        if len(parts) >= 3 and parts[0] == "boundaries":
            return f"{parts[1]}/{parts[2]}"
    return None


async def _upsert_politician(db: Database, rep: dict, set_def: OpenNorthSet) -> str:
    """Return politician id."""
    source_id = f"opennorth:{set_def.path.rstrip('/').split('/')[-1]}:{rep.get('name','').lower().replace(' ','-')}"
    cid = _constituency_id(rep, set_def)
    row = await db.fetchrow(
        """
        INSERT INTO politicians (
            source_id, name, first_name, last_name, gender, party, elected_office,
            level, province_territory, constituency_name, constituency_id,
            email, photo_url, personal_url, official_url, social_urls, extras
        )
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17)
        ON CONFLICT (source_id) DO UPDATE SET
            name = EXCLUDED.name,
            first_name = EXCLUDED.first_name,
            last_name = EXCLUDED.last_name,
            party = EXCLUDED.party,
            elected_office = EXCLUDED.elected_office,
            constituency_name = EXCLUDED.constituency_name,
            constituency_id = EXCLUDED.constituency_id,
            email = EXCLUDED.email,
            photo_url = EXCLUDED.photo_url,
            personal_url = EXCLUDED.personal_url,
            official_url = EXCLUDED.official_url,
            social_urls = EXCLUDED.social_urls,
            extras = EXCLUDED.extras,
            updated_at = now()
        RETURNING id
        """,
        source_id,
        rep.get("name") or "Unknown",
        rep.get("first_name"),
        rep.get("last_name"),
        rep.get("gender"),
        rep.get("party_name"),
        rep.get("elected_office") or set_def.office,
        set_def.level,
        set_def.province or rep.get("extra", {}).get("province"),
        rep.get("district_name"),
        cid,
        rep.get("email"),
        rep.get("photo_url"),
        rep.get("personal_url"),
        rep.get("url"),
        orjson.dumps(_social_urls(rep)).decode(),
        orjson.dumps({k: v for k, v in rep.items()
                     if k not in ("name","first_name","last_name","party_name",
                                  "elected_office","district_name","email",
                                  "photo_url","personal_url","url")}).decode(),
    )
    return str(row["id"])


async def _attach_websites(db: Database, politician_id: str, rep: dict) -> None:
    for url, label in _extract_websites(rep):
        await db.execute(
            """
            INSERT INTO websites (owner_type, owner_id, url, label)
            VALUES ('politician', $1, $2, $3)
            ON CONFLICT (owner_type, owner_id, url) DO NOTHING
            """,
            politician_id, url, label,
        )


# Hostnames that are shared institutional infrastructure (NOT a personal
# political choice). They get scanned but excluded from headline stats.
SHARED_OFFICIAL_HOSTS: frozenset[str] = frozenset({
    # Federal / provincial parliament
    "www.ourcommons.ca",
    "www.assembly.ab.ca",
    # Major-metro councils
    "www.edmonton.ca", "edmonton.ca",
    "www.calgary.ca",  "calgary.ca",
    # Smaller AB municipalities (added 2026-04-13)
    "www.lethbridge.ca", "lethbridge.ca",
    "www.rmwb.ca",       "rmwb.ca",
    "www.strathcona.ca", "strathcona.ca",
    "cityofgp.com",      "www.cityofgp.com",
    "countygp.ab.ca",    "www.countygp.ab.ca",
})


def _label_for(url: str, default: str) -> str:
    """Return 'shared_official' if the URL points at known shared infra."""
    try:
        from urllib.parse import urlparse
        host = (urlparse(url).hostname or "").lower()
    except Exception:
        return default
    return "shared_official" if host in SHARED_OFFICIAL_HOSTS else default


def _extract_websites(rep: dict) -> Iterable[tuple[str, str]]:
    if rep.get("personal_url"):
        yield rep["personal_url"], _label_for(rep["personal_url"], "personal")
    if rep.get("url"):
        yield rep["url"], _label_for(rep["url"], "party")
    for link in rep.get("extra", {}).get("urls", []) or []:
        u = link.get("url")
        note = (link.get("note") or "").lower()
        if not u:
            continue
        if any(s in note for s in ("twitter","facebook","instagram","youtube","tiktok","linkedin","x.com")):
            continue
        if "campaign" in note or "personal" in note or "official" in note:
            yield u, _label_for(u, note or "related")


async def _fetch_boundary(client: httpx.AsyncClient, set_def: OpenNorthSet, constituency_id: str) -> Optional[dict]:
    # constituency_id is "{boundary_set}/{slug}" produced by _constituency_id.
    if "/" not in constituency_id:
        constituency_id = f"{set_def.boundary_set}/{constituency_id}"
    url = f"{BASE}/boundaries/{constituency_id}/simple_shape"
    # Open North rate-limits aggressively; retry with backoff on 429/5xx.
    for attempt in range(5):
        try:
            r = await client.get(url)
            if r.status_code == 200:
                return r.json()
            if r.status_code in (429, 502, 503, 504):
                await asyncio.sleep(0.5 * (2 ** attempt))
                continue
            return None
        except Exception:
            await asyncio.sleep(0.5 * (2 ** attempt))
    return None


async def _upsert_boundary(db: Database, set_def: OpenNorthSet, constituency_id: str,
                           name: str, geojson: dict) -> None:
    # PostGIS ingests GeoJSON via ST_GeomFromGeoJSON. Cast to MultiPolygon.
    geom_sql = """
      ST_Multi(ST_SetSRID(ST_GeomFromGeoJSON($4), 4326))
    """
    await db.execute(
        f"""
        INSERT INTO constituency_boundaries
          (constituency_id, name, level, province_territory, source_set,
           boundary, boundary_simple, centroid, area_sqkm)
        VALUES ($1, $2, $3, $5, $6, {geom_sql}, {geom_sql}, ST_Centroid({geom_sql}),
                ST_Area({geom_sql}::geography)/1000000)
        ON CONFLICT (constituency_id) DO UPDATE SET
          name = EXCLUDED.name,
          level = EXCLUDED.level,
          province_territory = EXCLUDED.province_territory,
          source_set = EXCLUDED.source_set,
          boundary = EXCLUDED.boundary,
          boundary_simple = EXCLUDED.boundary_simple,
          centroid = EXCLUDED.centroid,
          area_sqkm = EXCLUDED.area_sqkm,
          updated_at = now()
        """,
        constituency_id, name, set_def.boundary_level,
        orjson.dumps(geojson).decode(),
        set_def.province, set_def.boundary_set,
    )


async def _ingest_set(db: Database, set_def: OpenNorthSet, limit: int) -> None:
    async with httpx.AsyncClient(timeout=30, headers={"User-Agent": "SovereignWatchBot/1.0"}) as client:
        console.print(f"[cyan]Fetching {set_def.path}[/cyan]")
        reps = await _fetch_reps(client, set_def, limit)
        console.print(f"[cyan]  got {len(reps)} representatives[/cyan]")

        with Progress(
            SpinnerColumn(), TextColumn("{task.description}"), BarColumn(),
            TextColumn("{task.completed}/{task.total}"), TimeElapsedColumn(),
        ) as progress:
            task = progress.add_task("Upserting politicians", total=len(reps))
            bsem = asyncio.Semaphore(3)
            seen_ids: set[str] = set()

            async def handle(rep: dict) -> None:
                try:
                    pid = await _upsert_politician(db, rep, set_def)
                    await _attach_websites(db, pid, rep)
                    cid = _constituency_id(rep, set_def)
                    if cid and cid not in seen_ids:
                        seen_ids.add(cid)
                        async with bsem:
                            gj = await _fetch_boundary(client, set_def, cid)
                            if gj and gj.get("coordinates"):
                                await _upsert_boundary(
                                    db, set_def, cid,
                                    rep.get("district_name") or cid, gj)
                except Exception as exc:
                    log.exception("ingest failed for %s: %s", rep.get("name"), exc)
                finally:
                    progress.update(task, advance=1)

            await asyncio.gather(*(handle(r) for r in reps))


async def ingest_mps(db: Database, *, limit: int = 500) -> None:
    await _ingest_set(db, SETS["federal_mps"], limit)


async def ingest_mlas(db: Database, *, limit: int = 100) -> None:
    await _ingest_set(db, SETS["alberta_mlas"], limit)


async def ingest_councils(db: Database) -> None:
    await _ingest_set(db, SETS["edmonton_council"], 25)
    await _ingest_set(db, SETS["calgary_council"], 25)


async def ingest_alberta_extras(db: Database) -> None:
    """Ingest the smaller AB municipal councils (Strathcona, Wood Buffalo,
    Lethbridge, Grande Prairie city + county)."""
    for key in ("strathcona_county", "wood_buffalo", "lethbridge_council",
                "grande_prairie_council", "county_grande_prairie"):
        await _ingest_set(db, SETS[key], 25)
