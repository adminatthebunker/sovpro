"""Backfill federal politician data from openparliament.ca.

Two entry points live here, both sourcing from `api.openparliament.ca`:

1. `run()` — create missing `politicians` rows for slugs referenced by
   unresolved speeches. Why it exists: the federal Hansard ingest
   resolves politician_id by matching the openparliament slug embedded
   in each speech's `politician_url` against
   `politicians.openparliament_slug`. Our politicians table — seeded
   from Open North (represent.opennorth.ca) — only covers
   currently-sitting MPs. That's why ~470k historical speech_chunks
   are unresolved (Trudeau, Harper, Charlie Angus, Peter Julian,
   etc. — all had /politicians/<slug>/ URLs from openparliament but no
   matching row in our DB).

2. `run_terms_backfill()` — hydrate full `politician_terms` history
   from openparliament's `memberships` array. The `ingest-mps` Open
   North path produces only a single current-term row per MP, so
   `/politicians/:id#terms` showed a single-line timeline (e.g. Pierre
   Poilievre only back to 2026). Openparliament exposes the full
   membership history — every parliament a person sat in, with real
   start/end dates and the riding at that time. Writes one
   `politician_terms` row per membership and supersedes the Open North
   current-term row when present (openparliament has the actual
   election start_date; Open North has the scrape date).

Both paths share the same fetch/retry/rate-limit machinery.
Rate-limited sequentially (1 req / ~1.2s); openparliament's TOS asks
for reasonable throughput and we're a single ingest, not a mirror.
"""
from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from datetime import date
from typing import Any, Optional

import httpx
import orjson

from ..db import Database

log = logging.getLogger(__name__)

API_ROOT = "https://api.openparliament.ca"
MEDIA_ROOT = "https://openparliament.ca"
REQUEST_TIMEOUT = 30.0
HEADERS = {
    "User-Agent": "sovereignwatch/1.0 (+https://canadianpoliticaldata.ca)",
    "Accept": "application/json",
}
# openparliament.ca's API rate limit is aggressive (~1 req/sec sustained).
# Sequential requests with a short sleep keep us under it reliably; the
# backfill runs once, so throughput isn't a priority.
CONCURRENCY = 1
REQUEST_SPACING_S = 1.2
MAX_429_RETRIES = 5


@dataclass
class BackfillStats:
    slugs_considered: int = 0
    already_present: int = 0
    fetched: int = 0
    inserted: int = 0
    updated: int = 0
    fetch_errors: int = 0


async def _collect_missing_slugs(db: Database) -> list[str]:
    """Unique politician slugs referenced by NULL-politician_id speeches
    that aren't already in `politicians.openparliament_slug`."""
    rows = await db.fetch(
        """
        WITH referenced AS (
          SELECT DISTINCT
                 substring(raw->'op_speech'->>'politician_url'
                           FROM '^/politicians/([^/]+)/?$') AS slug
            FROM speeches
           WHERE politician_id IS NULL
             AND raw->'op_speech'->>'politician_url' IS NOT NULL
        )
        SELECT r.slug
          FROM referenced r
          LEFT JOIN politicians p ON p.openparliament_slug = r.slug
         WHERE r.slug IS NOT NULL AND r.slug <> ''
           AND p.id IS NULL
         ORDER BY r.slug
        """
    )
    return [r["slug"] for r in rows]


def _latest_membership(memberships: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
    """Pick the membership with the latest start_date (end_date may be null
    for sitting members)."""
    if not memberships:
        return None
    sortable = sorted(memberships, key=lambda m: m.get("start_date") or "", reverse=True)
    return sortable[0]


def _is_active_from(latest: Optional[dict[str, Any]]) -> bool:
    """Active iff latest membership has no end_date."""
    if not latest:
        return False
    return not latest.get("end_date")


def _social_urls_from(info: dict[str, Any]) -> dict[str, str]:
    """Extract handles openparliament exposes in `other_info`."""
    out: dict[str, str] = {}
    twitter = info.get("twitter") or info.get("twitter_id")
    if isinstance(twitter, list) and twitter:
        t = str(twitter[0])
        if not t.startswith("http"):
            out["twitter"] = f"https://twitter.com/{t}"
    return out


def _parse_province(prov: Optional[str]) -> Optional[str]:
    """Openparliament ships 2-letter codes already — sanity-check."""
    if not prov:
        return None
    prov = prov.strip().upper()
    return prov if re.fullmatch(r"[A-Z]{2}", prov) else None


async def _fetch_slug(
    client: httpx.AsyncClient, slug: str
) -> Optional[dict[str, Any]]:
    """Fetch one politician's JSON. Handles openparliament's 429 with
    exponential backoff (Retry-After when provided)."""
    url = f"{API_ROOT}/politicians/{slug}/"
    backoff = 2.0
    for attempt in range(MAX_429_RETRIES):
        try:
            r = await client.get(url, params={"format": "json"})
            if r.status_code == 404:
                log.warning("op slug 404: %s", slug)
                return None
            if r.status_code == 429:
                retry_after = r.headers.get("retry-after")
                wait = float(retry_after) if retry_after and retry_after.isdigit() else backoff
                log.info("op 429 for %s; sleeping %.1fs (attempt %d/%d)",
                         slug, wait, attempt + 1, MAX_429_RETRIES)
                await asyncio.sleep(wait)
                backoff = min(backoff * 2, 30.0)
                continue
            r.raise_for_status()
            return r.json()
        except (httpx.HTTPError, ValueError) as exc:
            log.warning("op fetch failed for %s: %s", slug, exc)
            return None
    log.warning("op fetch gave up after %d 429s: %s", MAX_429_RETRIES, slug)
    return None


async def _upsert_politician(
    db: Database, slug: str, payload: dict[str, Any]
) -> str:
    """UPSERT on source_id='op:<slug>'. Returns 'inserted' | 'updated'."""
    latest = _latest_membership(payload.get("memberships") or [])
    party_name: Optional[str] = None
    constituency_name: Optional[str] = None
    province: Optional[str] = None
    if latest:
        party = latest.get("party") or {}
        party_short = (party.get("short_name") or {}).get("en") if isinstance(party, dict) else None
        party_long = (party.get("name") or {}).get("en") if isinstance(party, dict) else None
        party_name = party_short or party_long
        riding = latest.get("riding") or {}
        if isinstance(riding, dict):
            riding_name = riding.get("name") or {}
            constituency_name = riding_name.get("en") if isinstance(riding_name, dict) else None
            province = _parse_province(riding.get("province"))

    photo_relative = payload.get("image")
    photo_url = f"{MEDIA_ROOT}{photo_relative}" if photo_relative else None

    info = payload.get("other_info") or {}
    social_urls = _social_urls_from(info)
    extras = {k: v for k, v in payload.items() if k not in {
        "name", "given_name", "family_name", "gender", "image",
        "memberships", "other_info",
    }}
    extras["op_other_info"] = info

    source_id = f"op:{slug}"
    row = await db.fetchrow(
        """
        INSERT INTO politicians (
            source_id, name, first_name, last_name, gender,
            party, elected_office,
            level, province_territory, constituency_name, constituency_id,
            photo_url, official_url,
            social_urls, extras,
            is_active, openparliament_slug
        ) VALUES (
            $1, $2, $3, $4, $5,
            $6, 'MP',
            'federal', $7, $8, NULL,
            $9, $10,
            $11::jsonb, $12::jsonb,
            $13, $14
        )
        ON CONFLICT (source_id) DO UPDATE SET
            name = EXCLUDED.name,
            first_name = EXCLUDED.first_name,
            last_name = EXCLUDED.last_name,
            gender = COALESCE(EXCLUDED.gender, politicians.gender),
            party = COALESCE(EXCLUDED.party, politicians.party),
            province_territory = COALESCE(EXCLUDED.province_territory, politicians.province_territory),
            constituency_name = COALESCE(EXCLUDED.constituency_name, politicians.constituency_name),
            photo_url = COALESCE(EXCLUDED.photo_url, politicians.photo_url),
            social_urls = politicians.social_urls || EXCLUDED.social_urls,
            extras = politicians.extras || EXCLUDED.extras,
            is_active = EXCLUDED.is_active,
            openparliament_slug = EXCLUDED.openparliament_slug,
            updated_at = now()
        RETURNING (xmax = 0) AS inserted
        """,
        source_id,
        payload.get("name") or slug.replace("-", " ").title(),
        payload.get("given_name"),
        payload.get("family_name"),
        payload.get("gender"),
        party_name,
        province,
        constituency_name,
        photo_url,
        f"{MEDIA_ROOT}/politicians/{slug}/",
        orjson.dumps(social_urls).decode("utf-8"),
        orjson.dumps(extras, default=str).decode("utf-8"),
        _is_active_from(latest),
        slug,
    )
    return "inserted" if row and row["inserted"] else "updated"


async def run(db: Database, *, limit: Optional[int] = None) -> BackfillStats:
    """Entry point. Set `limit` to cap slugs processed (smoke-test aid)."""
    slugs = await _collect_missing_slugs(db)
    if limit is not None:
        slugs = slugs[:limit]
    stats = BackfillStats(slugs_considered=len(slugs))
    log.info("backfill: %d missing openparliament slugs", len(slugs))

    async with httpx.AsyncClient(
        timeout=REQUEST_TIMEOUT, headers=HEADERS, follow_redirects=True
    ) as client:
        # Sequential (concurrency=1) with explicit request spacing.
        # Parallelism bought us 429s; sequential + 1.2s spacing is the
        # reliable path.
        for i, slug in enumerate(slugs):
            if i > 0:
                await asyncio.sleep(REQUEST_SPACING_S)
            payload = await _fetch_slug(client, slug)
            if payload is None:
                stats.fetch_errors += 1
                continue
            stats.fetched += 1
            try:
                result = await _upsert_politician(db, slug, payload)
            except Exception as exc:
                log.warning("upsert failed for %s: %s", slug, exc)
                stats.fetch_errors += 1
                continue
            if result == "inserted":
                stats.inserted += 1
            else:
                stats.updated += 1
            if (i + 1) % 50 == 0:
                log.info("backfill progress: %d/%d (inserted=%d errors=%d)",
                         i + 1, len(slugs), stats.inserted, stats.fetch_errors)

    log.info(
        "backfill done: considered=%d fetched=%d inserted=%d updated=%d errors=%d",
        stats.slugs_considered, stats.fetched, stats.inserted, stats.updated, stats.fetch_errors,
    )
    return stats


async def resolve_missing(db: Database, *, batch_size: int = 5000) -> dict[str, int]:
    """Post-backfill resolution pass. Updates speeches.politician_id and
    speech_chunks.politician_id for rows whose upstream slug now has a
    politicians row. Batched to stay under asyncpg's 60s command timeout.

    Returns counts for telemetry.
    """
    def _count(tag: str) -> int:
        try:
            return int(tag.rsplit(" ", 1)[-1])
        except (ValueError, AttributeError):
            return 0

    # speeches: resolve in batches keyed on primary id so each UPDATE
    # scans a bounded slice. We loop until an iteration makes no
    # progress (no more resolvable rows in that slice OR whole table).
    speeches_total = 0
    while True:
        tag = await db.execute(
            """
            WITH target AS (
              SELECT s.id, p.id AS pid
                FROM speeches s
                JOIN politicians p
                  ON p.openparliament_slug = substring(
                       s.raw->'op_speech'->>'politician_url'
                       FROM '^/politicians/([^/]+)/?$')
               WHERE s.politician_id IS NULL
                 AND p.openparliament_slug IS NOT NULL
               LIMIT $1
            )
            UPDATE speeches s
               SET politician_id = t.pid,
                   updated_at = now()
              FROM target t
             WHERE s.id = t.id
            """,
            batch_size,
        )
        n = _count(tag)
        speeches_total += n
        log.info("resolve speeches batch: +%d (running total %d)", n, speeches_total)
        if n < batch_size:
            break

    # chunks: same pattern — batch from the set of chunks whose parent
    # speech is now resolved but the chunk isn't.
    chunks_total = 0
    while True:
        tag = await db.execute(
            """
            WITH target AS (
              SELECT sc.id, s.politician_id AS pid
                FROM speech_chunks sc
                JOIN speeches s ON s.id = sc.speech_id
               WHERE sc.politician_id IS NULL
                 AND s.politician_id IS NOT NULL
               LIMIT $1
            )
            UPDATE speech_chunks sc
               SET politician_id = t.pid
              FROM target t
             WHERE sc.id = t.id
            """,
            batch_size,
        )
        n = _count(tag)
        chunks_total += n
        log.info("resolve chunks batch: +%d (running total %d)", n, chunks_total)
        if n < batch_size:
            break

    return {"speeches_resolved": speeches_total, "chunks_resolved": chunks_total}


# ─────────────────────────────────────────────────────────────────────
# politician_terms backfill (openparliament memberships → term history)
# ─────────────────────────────────────────────────────────────────────

TERMS_SOURCE = "openparliament:memberships"
# Open North's federal source emits one scrape-dated "current" row per MP;
# we supersede it when we successfully fetch an openparliament membership
# history (real election start_date, full history). Provincial/Senate
# sources are untouched.
SUPERSEDED_SOURCES = (TERMS_SOURCE, "opennorth:house-of-commons")


@dataclass
class TermsBackfillStats:
    politicians_considered: int = 0
    fetched: int = 0
    fetch_errors: int = 0
    politicians_updated: int = 0
    terms_inserted: int = 0
    terms_deleted: int = 0
    politicians_skipped_no_memberships: int = 0


def _party_from_membership(m: dict[str, Any]) -> Optional[str]:
    party = m.get("party") or {}
    if not isinstance(party, dict):
        return None
    short = (party.get("short_name") or {}).get("en") if isinstance(party.get("short_name"), dict) else None
    long_ = (party.get("name") or {}).get("en") if isinstance(party.get("name"), dict) else None
    return short or long_


def _province_from_membership(m: dict[str, Any]) -> Optional[str]:
    riding = m.get("riding") or {}
    if not isinstance(riding, dict):
        return None
    return _parse_province(riding.get("province"))


async def _replace_terms_for_politician(
    db: Database,
    politician_id: str,
    memberships: list[dict[str, Any]],
) -> tuple[int, int]:
    """DELETE superseded rows and INSERT one row per membership in a
    single transaction. Returns (deleted, inserted) counts."""
    async with db.pool.acquire() as conn:
        async with conn.transaction():
            # Delete prior rows we own, plus the Open North federal
            # current-term row (openparliament supersedes it).
            deleted_tag = await conn.execute(
                """
                DELETE FROM politician_terms
                 WHERE politician_id = $1
                   AND source = ANY($2::text[])
                """,
                politician_id,
                list(SUPERSEDED_SOURCES),
            )
            try:
                deleted = int(deleted_tag.rsplit(" ", 1)[-1])
            except (ValueError, AttributeError):
                deleted = 0

            inserted = 0
            for m in memberships:
                started_at = _parse_iso_date(m.get("start_date"))
                if started_at is None:
                    # `started_at` is NOT NULL; skip malformed rows.
                    continue
                ended_at = _parse_iso_date(m.get("end_date"))
                party = _party_from_membership(m)
                province = _province_from_membership(m)
                await conn.execute(
                    """
                    INSERT INTO politician_terms (
                        politician_id, office, party, level,
                        province_territory, constituency_id,
                        started_at, ended_at, source
                    ) VALUES (
                        $1, 'MP', $2, 'federal',
                        $3, NULL,
                        $4, $5, $6
                    )
                    """,
                    politician_id,
                    party,
                    province,
                    started_at,
                    ended_at,
                    TERMS_SOURCE,
                )
                inserted += 1
            return deleted, inserted


def _parse_iso_date(value: Any) -> Optional[date]:
    """Accept ISO YYYY-MM-DD strings, return a `date` for asyncpg.
    Openparliament ships plain dates ('2015-10-19'); anything else is
    treated as missing."""
    if not value or not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


async def _collect_federal_politicians(
    db: Database, *, slug: Optional[str]
) -> list[tuple[str, str]]:
    """Return [(politician_id, openparliament_slug)] for federal
    politicians with a known openparliament slug. Pass `slug` to target
    one politician."""
    if slug:
        rows = await db.fetch(
            """
            SELECT id, openparliament_slug
              FROM politicians
             WHERE openparliament_slug = $1
             LIMIT 1
            """,
            slug,
        )
    else:
        rows = await db.fetch(
            """
            SELECT id, openparliament_slug
              FROM politicians
             WHERE openparliament_slug IS NOT NULL
               AND level = 'federal'
             ORDER BY openparliament_slug
            """
        )
    return [(str(r["id"]), r["openparliament_slug"]) for r in rows]


async def run_terms_backfill(
    db: Database,
    *,
    limit: Optional[int] = None,
    slug: Optional[str] = None,
) -> TermsBackfillStats:
    """Entry point. Iterates federal politicians with openparliament
    slugs, fetches each `/politicians/<slug>/` payload, and rewrites
    `politician_terms` from the `memberships` array.

    Set `limit` to cap how many politicians are processed (smoke-test).
    Set `slug` to target exactly one politician.
    """
    targets = await _collect_federal_politicians(db, slug=slug)
    if limit is not None:
        targets = targets[:limit]
    stats = TermsBackfillStats(politicians_considered=len(targets))
    log.info("terms backfill: %d politicians to process", len(targets))

    async with httpx.AsyncClient(
        timeout=REQUEST_TIMEOUT, headers=HEADERS, follow_redirects=True
    ) as client:
        for i, (pol_id, op_slug) in enumerate(targets):
            if i > 0:
                await asyncio.sleep(REQUEST_SPACING_S)
            payload = await _fetch_slug(client, op_slug)
            if payload is None:
                stats.fetch_errors += 1
                continue
            stats.fetched += 1
            memberships = payload.get("memberships") or []
            if not memberships:
                stats.politicians_skipped_no_memberships += 1
                continue
            try:
                deleted, inserted = await _replace_terms_for_politician(
                    db, pol_id, memberships
                )
            except Exception as exc:
                log.warning("terms replace failed for %s: %s", op_slug, exc)
                stats.fetch_errors += 1
                continue
            stats.terms_deleted += deleted
            stats.terms_inserted += inserted
            if inserted > 0:
                stats.politicians_updated += 1
            if (i + 1) % 50 == 0:
                log.info(
                    "terms backfill progress: %d/%d (updated=%d inserted=%d errors=%d)",
                    i + 1, len(targets),
                    stats.politicians_updated, stats.terms_inserted, stats.fetch_errors,
                )

    log.info(
        "terms backfill done: considered=%d fetched=%d updated=%d "
        "inserted=%d deleted=%d no_memberships=%d errors=%d",
        stats.politicians_considered, stats.fetched, stats.politicians_updated,
        stats.terms_inserted, stats.terms_deleted,
        stats.politicians_skipped_no_memberships, stats.fetch_errors,
    )
    return stats
