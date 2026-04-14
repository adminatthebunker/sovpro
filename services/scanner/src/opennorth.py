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

from . import compare_politicians
from .db import Database
from .offices import _upsert_offices

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
    # ── Other provincial + territorial legislatures ─────────────────
    # Slugs below verified against https://represent.opennorth.ca/representative-sets/
    # on 2026-04-13. boundary_set values come from the actual boundary_url emitted
    # by each legislature's representatives (so constituency look-ups resolve).
    "bc_mlas": OpenNorthSet(
        path="/representatives/bc-legislature/",
        level="provincial", province="BC", office="MLA",
        boundary_set="british-columbia-electoral-districts-2015-redistribution",
        boundary_level="provincial",
    ),
    "ontario_mpps": OpenNorthSet(
        path="/representatives/ontario-legislature/",
        level="provincial", province="ON", office="MPP",
        boundary_set="ontario-electoral-districts-representation-act-2015",
        boundary_level="provincial",
    ),
    "quebec_mnas": OpenNorthSet(
        # Note: canonical slug is 'quebec-assemblee-nationale' on Open North,
        # not 'quebec-assembly'. Verified 2026-04-13 (124 members).
        path="/representatives/quebec-assemblee-nationale/",
        level="provincial", province="QC", office="MNA",
        boundary_set="quebec-electoral-districts-2017",
        boundary_level="provincial",
    ),
    "manitoba_mlas": OpenNorthSet(
        path="/representatives/manitoba-legislature/",
        level="provincial", province="MB", office="MLA",
        boundary_set="manitoba-electoral-districts-2018",
        boundary_level="provincial",
    ),
    "saskatchewan_mlas": OpenNorthSet(
        path="/representatives/saskatchewan-legislature/",
        level="provincial", province="SK", office="MLA",
        boundary_set="saskatchewan-electoral-districts-representation-act-2012",
        boundary_level="provincial",
    ),
    "nova_scotia_mlas": OpenNorthSet(
        path="/representatives/nova-scotia-legislature/",
        level="provincial", province="NS", office="MLA",
        boundary_set="nova-scotia-electoral-districts-2019",
        boundary_level="provincial",
    ),
    "new_brunswick_mlas": OpenNorthSet(
        # Open North doesn't currently populate boundary_url on NB reps; we still
        # point at the latest NB boundary set so manual look-ups work.
        path="/representatives/new-brunswick-legislature/",
        level="provincial", province="NB", office="MLA",
        boundary_set="new-brunswick-electoral-districts-2024",
        boundary_level="provincial",
    ),
    "pei_mlas": OpenNorthSet(
        # Canonical slug is 'pei-legislature' (26 members). The plan suggested
        # 'pei-legislative-assembly' but that endpoint returns 0 results.
        path="/representatives/pei-legislature/",
        level="provincial", province="PE", office="MLA",
        boundary_set="prince-edward-island-electoral-districts-2017",
        boundary_level="provincial",
    ),
    "nl_mhas": OpenNorthSet(
        # Canonical slug is 'newfoundland-labrador-legislature' (36 members).
        path="/representatives/newfoundland-labrador-legislature/",
        level="provincial", province="NL", office="MHA",
        boundary_set="newfoundland-and-labrador-electoral-districts",
        boundary_level="provincial",
    ),
    "yukon_mlas": OpenNorthSet(
        # Canonical slug is 'yukon-legislature' (19 members). The plan suggested
        # 'yukon-legislative-assembly' but that endpoint returns 0 results.
        path="/representatives/yukon-legislature/",
        level="provincial", province="YT", office="MLA",
        boundary_set="yukon-electoral-districts-2015",
        boundary_level="provincial",
    ),
    "nwt_mlas": OpenNorthSet(
        # Canonical slug is 'northwest-territories-legislature' (19 members).
        # The plan suggested 'northwest-territories-assembly' — returns 0 results.
        path="/representatives/northwest-territories-legislature/",
        level="provincial", province="NT", office="MLA",
        boundary_set="northwest-territories-electoral-districts-2013",
        boundary_level="provincial",
    ),
    # TODO: Nunavut — Open North has no representative-set for the Nunavut
    # Legislative Assembly as of 2026-04-13. All candidate slugs
    # (nunavut-legislature, nunavut-assembly, nunavut-legislative-assembly) return
    # zero objects and there is no 'nunavut-electoral-districts' boundary set.
    # Nunavut's 22 MLAs are non-partisan (consensus government) and the Assembly
    # publishes member data only as static HTML on assembly.nu.ca. Re-check
    # periodically and populate the path below once upstream data exists.
    "nunavut_mlas": OpenNorthSet(
        path="/representatives/nunavut-legislature/",  # placeholder — 0 results
        level="provincial", province="NU", office="MLA",
        boundary_set="nunavut-electoral-districts",  # does not exist upstream yet
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

# Merge in the full dynamic catalogue of municipal councils harvested from
# Open North's /representative-sets index by scripts/generate_muni_sets.py.
# The static AB keys above (edmonton_council, calgary_council, etc.) remain
# since their key names differ from the generator's derived keys.
from ._muni_sets_generated import MUNICIPAL_SETS  # noqa: E402

SETS.update(MUNICIPAL_SETS)


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


def _build_source_id(rep: dict, set_def: OpenNorthSet) -> str:
    return f"opennorth:{set_def.path.rstrip('/').split('/')[-1]}:{rep.get('name','').lower().replace(' ','-')}"


async def _upsert_politician(db: Database, rep: dict, set_def: OpenNorthSet) -> tuple[str, str]:
    """Upsert the politician, detecting and recording changes.

    Returns a tuple of ``(politician_id, source_id)``. Change-tracking is
    non-fatal: any exception in compare_politicians is logged and swallowed
    so ingestion proceeds.
    """
    source_id = _build_source_id(rep, set_def)
    cid = _constituency_id(rep, set_def)

    # Pre-upsert snapshot so we can diff.
    existing = await db.fetchrow(
        """
        SELECT id, name, party, elected_office, constituency_id, level,
               province_territory
          FROM politicians
         WHERE source_id = $1
        """,
        source_id,
    )

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
            updated_at = now(),
            is_active = true
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
    politician_id = str(row["id"])

    # Change-detection must not break ingestion — wrap in try/except.
    try:
        changes = await compare_politicians.diff_and_record(
            db, existing, rep, set_def
        )
        if changes:
            await compare_politicians.apply_changes(
                db, politician_id, changes, set_def=set_def, incoming=rep,
            )
        if existing is None:
            await compare_politicians.open_initial_term(
                db, politician_id, rep, set_def,
            )
    except Exception as exc:
        log.exception(
            "compare_politicians failed for %s (%s): %s",
            rep.get("name"), source_id, exc,
        )

    # Materialise Open North's `offices` array into the normalized
    # politician_offices table. Kept non-fatal so a parse failure on one
    # rep's postal string never aborts the ingest batch.
    try:
        rep_offices = rep.get("offices")
        if rep_offices is None:
            # Some rep objects nest offices under 'extra'; be defensive.
            rep_offices = (rep.get("extra") or {}).get("offices")
        if rep_offices:
            await _upsert_offices(db, politician_id, rep_offices)
    except Exception as exc:
        log.warning(
            "_upsert_offices failed for %s (%s): %s",
            rep.get("name"), source_id, exc,
        )

    return politician_id, source_id


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
    # Canadian Senate — every senator's institutional page is hosted at
    # sencanada.ca, so the host is shared infrastructure (not a personal
    # political choice).
    "sencanada.ca",                  "www.sencanada.ca",
    # Other provincial / territorial legislatures (verified 2026-04-13).
    # These are shared infrastructure — every MLA/MPP/MNA/MHA on a province has
    # a page under the same hostname — so sites with these hosts must not count
    # as a "personal" political website in headline stats.
    "www.leg.bc.ca",                 "leg.bc.ca",
    "www.ola.org",                   "ola.org",
    "www.assnat.qc.ca",              "assnat.qc.ca",
    "www.gov.mb.ca",                 "gov.mb.ca",
    "www.legassembly.sk.ca",         "legassembly.sk.ca",
    "nslegislature.ca",              "www.nslegislature.ca",
    "www.legnb.ca",                  "legnb.ca",
    "www.assembly.pe.ca",            "assembly.pe.ca",
    "www.assembly.nl.ca",            "assembly.nl.ca",
    "yukonassembly.ca",              "www.yukonassembly.ca",
    "www.ntassembly.ca",             "ntassembly.ca",
    "www.ntlegislativeassembly.ca",  "ntlegislativeassembly.ca",
    "assembly.nu.ca",                "www.assembly.nu.ca",
    # Major-metro councils
    "www.edmonton.ca", "edmonton.ca",
    "www.calgary.ca",  "calgary.ca",
    # Smaller AB municipalities (added 2026-04-13)
    "www.lethbridge.ca", "lethbridge.ca",
    "www.rmwb.ca",       "rmwb.ca",
    "www.strathcona.ca", "strathcona.ca",
    "cityofgp.com",      "www.cityofgp.com",
    "countygp.ab.ca",    "www.countygp.ab.ca",
    # ── Phase 4 municipal expansion (snapshot 2026-04-13) ──
    # Produced by extend_shared_hosts_from_db() after a full
    # ingest-all-councils run. Regenerate periodically.
    "abbotsford.ca",                 "www.abbotsford.ca",
    "brampton.ca",                   "www.brampton.ca",
    "burlington.ca",                 "www.burlington.ca",
    "burnaby.ca",                    "www.burnaby.ca",
    "caledon.ca",                    "www.caledon.ca",
    "cbrm.ns.ca",                    "www.cbrm.ns.ca",
    "chatham-kent.ca",               "www.chatham-kent.ca",
    "cityofkingston.ca",             "www.cityofkingston.ca",
    "citywindsor.ca",                "www.citywindsor.ca",
    "coquitlam.ca",                  "www.coquitlam.ca",
    "forterie.ca",                   "www.forterie.ca",
    "fredericton.ca",                "www.fredericton.ca",
    "gatineau.ca",                   "www.gatineau.ca",
    "georgina.ca",                   "www.georgina.ca",
    "greatersudbury.ca",             "www.greatersudbury.ca",
    "halifax.ca",                    "www.halifax.ca",
    "hamilton.ca",                   "www.hamilton.ca",
    "king.ca",                       "www.king.ca",
    "kitchener.ca",                  "www.kitchener.ca",
    "lincoln.ca",                    "www.lincoln.ca",
    "longueuil.quebec",              "www.longueuil.quebec",
    "markham.ca",                    "www.markham.ca",
    "milton.ca",                     "www.milton.ca",
    "mississauga.ca",                "www.mississauga.ca",
    "montreal.ca",                   "www.montreal.ca",
    "newmarket.ca",                  "www.newmarket.ca",
    "niagararegion.ca",              "www.niagararegion.ca",
    "oakville.ca",                   "www.oakville.ca",
    "ottawa.ca",                     "www.ottawa.ca",
    "peelregion.ca",                 "www.peelregion.ca",
    "pickering.ca",                  "www.pickering.ca",
    "regina.ca",                     "www.regina.ca",
    "regionofwaterloo.ca",           "www.regionofwaterloo.ca",
    "richmond.ca",                   "www.richmond.ca",
    "richmondhill.ca",               "www.richmondhill.ca",
    "saintjohn.ca",                  "www.saintjohn.ca",
    "saultstemarie.ca",              "www.saultstemarie.ca",
    "sherbrooke.ca",                 "www.sherbrooke.ca",
    "sjsr.ca",                       "www.sjsr.ca",
    "stjohns.ca",                    "www.stjohns.ca",
    "surrey.ca",                     "www.surrey.ca",
    "thunderbay.ca",                 "www.thunderbay.ca",
    "tol.ca",                        "www.tol.ca",
    "toronto.ca",                    "www.toronto.ca",
    "townofws.ca",                   "www.townofws.ca",
    "v3r.net",                       "www.v3r.net",
    "vaughan.ca",                    "www.vaughan.ca",
    "victoria.ca",                   "www.victoria.ca",
    "waterloo.ca",                   "www.waterloo.ca",
    "welland.ca",                    "www.welland.ca",
    "winnipeg.ca",                   "www.winnipeg.ca",
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
    # boundary_simple: ~0.005° tolerance (~555m) is sub-pixel at z4-z8. Wrap
    # ST_Simplify in ST_MakeValid + polygon-only extract so self-intersections
    # produced by the simplifier don't break the MultiPolygon column.
    simple_sql = f"""
      ST_Multi(
        ST_CollectionExtract(
          ST_MakeValid(ST_Simplify({geom_sql}, 0.005)),
          3))
    """
    await db.execute(
        f"""
        INSERT INTO constituency_boundaries
          (constituency_id, name, level, province_territory, source_set,
           boundary, boundary_simple, centroid, area_sqkm)
        VALUES ($1, $2, $3, $5, $6, {geom_sql}, {simple_sql}, ST_Centroid({geom_sql}),
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
    async with httpx.AsyncClient(timeout=30, headers={"User-Agent": "CanadianPoliticalDataBot/1.0"}) as client:
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
            seen_source_ids: set[str] = set()

            async def handle(rep: dict) -> None:
                try:
                    pid, sid = await _upsert_politician(db, rep, set_def)
                    seen_source_ids.add(sid)
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

        # Only run retirement detection when we processed a *full* set (not a
        # --limit=N debugging run), otherwise every politician past the limit
        # would look retired.
        if len(reps) >= limit:
            # Limit was the cap; we may not have a complete picture. Skip.
            log.info(
                "skipping retirement detection for %s (limit=%d hit; ran partial fetch)",
                set_def.path, limit,
            )
        else:
            try:
                await compare_politicians.detect_retirements(
                    db, set_def, seen_source_ids,
                )
            except Exception as exc:
                log.exception(
                    "detect_retirements failed for %s: %s", set_def.path, exc,
                )


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


# ─────────────────────────────────────────────────────────────────────
# Provincial / territorial legislature ingestion (Phase 2)
# ─────────────────────────────────────────────────────────────────────
# One thin wrapper per legislature so each can be invoked individually
# from the CLI (e.g. `python -m src ingest-bc-mlas`). All of them call
# `_ingest_set` with the corresponding SETS entry.


# Ordered list of provincial/territorial SETS keys. The coordinating
# `ingest_all_legislatures` helper walks this list. Federal MPs and
# municipal councils are intentionally *not* included.
PROVINCIAL_SET_KEYS: tuple[str, ...] = (
    "alberta_mlas",
    "bc_mlas",
    "ontario_mpps",
    "quebec_mnas",
    "manitoba_mlas",
    "saskatchewan_mlas",
    "nova_scotia_mlas",
    "new_brunswick_mlas",
    "pei_mlas",
    "nl_mhas",
    "yukon_mlas",
    "nwt_mlas",
    "nunavut_mlas",  # currently 0 reps upstream; skipped with a warning
)


async def ingest_bc_mlas(db: Database, *, limit: int = 200) -> None:
    await _ingest_set(db, SETS["bc_mlas"], limit)


async def ingest_ontario_mpps(db: Database, *, limit: int = 200) -> None:
    await _ingest_set(db, SETS["ontario_mpps"], limit)


async def ingest_quebec_mnas(db: Database, *, limit: int = 200) -> None:
    await _ingest_set(db, SETS["quebec_mnas"], limit)


async def ingest_manitoba_mlas(db: Database, *, limit: int = 200) -> None:
    await _ingest_set(db, SETS["manitoba_mlas"], limit)


async def ingest_saskatchewan_mlas(db: Database, *, limit: int = 200) -> None:
    await _ingest_set(db, SETS["saskatchewan_mlas"], limit)


async def ingest_nova_scotia_mlas(db: Database, *, limit: int = 200) -> None:
    await _ingest_set(db, SETS["nova_scotia_mlas"], limit)


async def ingest_new_brunswick_mlas(db: Database, *, limit: int = 200) -> None:
    await _ingest_set(db, SETS["new_brunswick_mlas"], limit)


async def ingest_pei_mlas(db: Database, *, limit: int = 200) -> None:
    await _ingest_set(db, SETS["pei_mlas"], limit)


async def ingest_nl_mhas(db: Database, *, limit: int = 200) -> None:
    await _ingest_set(db, SETS["nl_mhas"], limit)


async def ingest_yukon_mlas(db: Database, *, limit: int = 200) -> None:
    await _ingest_set(db, SETS["yukon_mlas"], limit)


async def ingest_nwt_mlas(db: Database, *, limit: int = 200) -> None:
    await _ingest_set(db, SETS["nwt_mlas"], limit)


async def ingest_nunavut_mlas(db: Database, *, limit: int = 200) -> None:
    # Open North has no usable set for Nunavut as of 2026-04-13; calling this
    # is safe (will just print "got 0 representatives") but we log a hint.
    console.print(
        "[yellow]Nunavut: Open North currently returns 0 MLAs — "
        "see TODO in SETS['nunavut_mlas'].[/yellow]"
    )
    await _ingest_set(db, SETS["nunavut_mlas"], limit)


async def ingest_all_legislatures(db: Database, *, limit: int = 200) -> None:
    """Ingest every provincial + territorial legislature from Open North.

    Does NOT ingest federal MPs or municipal councils — use `ingest_mps` and
    `ingest_councils` (and friends) for those. Intended to be run nightly /
    weekly from cron to keep rep rosters fresh across all 13 legislatures.
    """
    for key in PROVINCIAL_SET_KEYS:
        console.print(f"[cyan bold]━━ {key} ━━[/cyan bold]")
        try:
            await _ingest_set(db, SETS[key], limit)
        except Exception as exc:
            # Don't let a single legislature's hiccup abort the whole run.
            log.exception("ingest %s failed: %s", key, exc)
            console.print(f"[red]  {key}: {exc}[/red]")


# ─────────────────────────────────────────────────────────────────────
# Municipal ingestion (Phase 4)
# ─────────────────────────────────────────────────────────────────────


async def ingest_all_councils(db: Database, limit_per_set: int = 200) -> None:
    """Iterate every generated municipal set and ingest up to ``limit_per_set``
    councillors per set.

    Per-council failures (404, timeout, schema drift, etc.) are logged and the
    batch continues; we never abort the nationwide run because one city's
    scraper is down.
    """
    total = len(MUNICIPAL_SETS)
    console.print(f"[cyan]ingest_all_councils: {total} municipal sets "
                  f"(limit_per_set={limit_per_set})[/cyan]")
    succeeded: list[str] = []
    failed: list[tuple[str, str]] = []
    for i, (key, set_def) in enumerate(sorted(MUNICIPAL_SETS.items()), start=1):
        console.print(f"[cyan][{i}/{total}] {key} — {set_def.path}[/cyan]")
        try:
            await _ingest_set(db, set_def, limit_per_set)
            succeeded.append(key)
        except httpx.HTTPStatusError as exc:
            msg = f"HTTP {exc.response.status_code} {exc.request.url}"
            log.warning("council ingest failed: %s: %s", key, msg)
            console.print(f"[yellow]  skipped {key}: {msg}[/yellow]")
            failed.append((key, msg))
        except Exception as exc:  # pragma: no cover — defensive
            log.exception("council ingest failed: %s", key)
            console.print(f"[yellow]  skipped {key}: {exc}[/yellow]")
            failed.append((key, str(exc)))
    console.print(
        f"[green]ingest_all_councils complete — "
        f"ok={len(succeeded)} failed={len(failed)} total={total}[/green]"
    )
    if failed:
        console.print("[yellow]failed sets:[/yellow]")
        for key, msg in failed:
            console.print(f"  - {key}: {msg}")


async def extend_shared_hosts_from_db(db: Database) -> frozenset[str]:
    """Return a frozenset of hostnames that appear on multiple municipal
    politicians' official sites (i.e. shared institutional infrastructure).

    A host qualifies if at least 3 distinct politicians at the ``municipal``
    level share the same hostname on a website labelled ``shared_official``,
    ``official``, ``personal`` or ``party``. We also include any host matching
    the canonical ``www.<slug>.ca`` pattern derived from municipal slugs.

    The result is the **union** of the compile-time ``SHARED_OFFICIAL_HOSTS``
    and the DB-derived set, so callers can adopt it without losing existing
    entries. Intended to be called periodically and dumped back into the
    static list.
    """
    rows = await db.fetch(
        """
        SELECT LOWER(split_part(regexp_replace(w.url, '^https?://', ''), '/', 1))
               AS host,
               COUNT(DISTINCT w.owner_id) AS n
        FROM websites w
        JOIN politicians p ON p.id = w.owner_id
        WHERE w.owner_type = 'politician'
          AND p.level = 'municipal'
          AND w.url IS NOT NULL
        GROUP BY 1
        HAVING COUNT(DISTINCT w.owner_id) >= 3
        """
    )
    hosts: set[str] = set()
    for r in rows or []:
        host = (r["host"] or "").strip().lstrip(".")
        if not host:
            continue
        # Strip port if present.
        host = host.split(":", 1)[0]
        hosts.add(host)
        # Also add the www.<host> / bare variants to match both forms.
        if host.startswith("www."):
            hosts.add(host[4:])
        else:
            hosts.add(f"www.{host}")
    return frozenset(SHARED_OFFICIAL_HOSTS | hosts)
