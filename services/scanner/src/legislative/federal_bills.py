"""Federal bills ingester — openparliament.ca → `bills` / `bill_sponsors`.

Closes the long-standing gap where `jurisdiction_sources.federal.bills_status='partial'`
with 0 rows in `bills`. Federal Hansard (1.08M speeches) was ingested first;
this module gives federal bills the same treatment via the same JSON API.

## Source

`https://api.openparliament.ca/bills/?session={p}-{s}` returns paginated
JSON. Each list-level row carries: number, name (EN+FR), introduced date,
session, legisinfo_id, url. Per-bill detail at `/bills/{p}-{s}/{number}/`
adds: status_code, status (EN), short_title (EN+FR), sponsor_politician_url
(`/politicians/{slug}/` — joins to `politicians.openparliament_slug`),
text_url, vote_urls, private_member_bill, law.

## What this module does NOT do

- **No stage events.** openparliament.ca doesn't expose the bill stage
  timeline on its bills endpoints. Stage events (first reading, second
  reading, royal assent dates) would need LEGISinfo XML — a separate
  follow-up. Today we write only `bills` + `bill_sponsors`, no `bill_events`.
  The `status` field on `bills` carries the latest stage as a string.
- **No vote ingestion.** `vote_urls` array is captured into `raw` but not
  exploded into `votes` / `vote_positions` (those tables don't exist yet).

## Sponsor resolution

Per CLAUDE.md convention #1: federal uses `politicians.openparliament_slug`.
Sponsor resolution is an exact slug FK lookup. Bills with no sponsor
(rare; mostly procedural) leave the bill_sponsors row absent.

## Idempotency

`source_id = "openparliament:bills:{session}:{number}"` is the natural
key. Re-runs over the same session ON CONFLICT DO UPDATE the mutable
fields (status, raw, last_fetched_at).
"""
from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime
from typing import Any, Optional

import httpx
import orjson

from ..db import Database
from .current_session import current_session

log = logging.getLogger(__name__)

API_ROOT = "https://api.openparliament.ca"
WEB_ROOT = "https://openparliament.ca"
SOURCE_SYSTEM = "openparliament-bills"
REQUEST_TIMEOUT = 60

HEADERS = {
    "User-Agent": "SovereignWatch/1.0 (civic-transparency; +admin@thebunkerops.ca)",
    "Accept": "application/json",
}


def _parse_date(s: Optional[str]) -> Optional[date]:
    if not s:
        return None
    try:
        return datetime.strptime(s[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def _politician_slug_from_url(url: Optional[str]) -> Optional[str]:
    """`/politicians/chrystia-freeland/` → `chrystia-freeland`."""
    if not url:
        return None
    parts = url.strip("/").split("/")
    if len(parts) >= 2 and parts[0] == "politicians":
        slug = parts[1]
        # /politicians/memberships/4261/ — not a slug
        if slug == "memberships":
            return None
        return slug
    return None


async def _upsert_session(
    db: Database, *, parliament: int, session: int,
) -> str:
    row = await db.fetchrow(
        """
        INSERT INTO legislative_sessions (
            level, province_territory, parliament_number, session_number,
            name, source_system, source_url
        )
        VALUES ('federal', NULL, $1, $2, $3, $4, $5)
        ON CONFLICT (level, province_territory, parliament_number, session_number)
        DO UPDATE SET updated_at = now()
        RETURNING id
        """,
        parliament, session,
        f"{parliament}th Parliament, {session}{'st' if session == 1 else 'nd' if session == 2 else 'rd' if session == 3 else 'th'} Session",
        SOURCE_SYSTEM,
        f"{WEB_ROOT}/bills/?session={parliament}-{session}",
    )
    return str(row["id"])


async def _upsert_bill(
    db: Database, *, session_id: str, parliament: int, session: int, bill: dict,
) -> Optional[str]:
    number = (bill.get("number") or "").strip()
    if not number:
        return None
    name = bill.get("name") or {}
    title = name.get("en") or name.get("fr") or f"Bill {number}"
    short = bill.get("short_title") or {}
    short_title = (short.get("en") or short.get("fr") or "").strip() or None
    introduced = _parse_date(bill.get("introduced"))
    status_code = bill.get("status_code")
    status_obj = bill.get("status") or {}
    status = status_obj.get("en") or status_code

    source_id = f"{SOURCE_SYSTEM}:{parliament}-{session}:{number}"
    source_url = f"{WEB_ROOT}{bill.get('url', f'/bills/{parliament}-{session}/{number}/')}"

    row = await db.fetchrow(
        """
        INSERT INTO bills (
            session_id, level, province_territory, bill_number,
            title, short_title, status, introduced_date,
            source_id, source_system, source_url, raw, last_fetched_at
        )
        VALUES ($1, 'federal', NULL, $2, $3, $4, $5, $6,
                $7, $8, $9, $10::jsonb, now())
        ON CONFLICT (source_id) DO UPDATE SET
            title             = EXCLUDED.title,
            short_title       = COALESCE(EXCLUDED.short_title, bills.short_title),
            status            = EXCLUDED.status,
            introduced_date   = COALESCE(EXCLUDED.introduced_date, bills.introduced_date),
            source_url        = EXCLUDED.source_url,
            raw               = EXCLUDED.raw,
            last_fetched_at   = now(),
            updated_at        = now()
        RETURNING id
        """,
        session_id, number, title, short_title, status, introduced,
        source_id, SOURCE_SYSTEM, source_url,
        orjson.dumps(bill).decode(),
    )
    return str(row["id"])


async def _upsert_sponsor(
    db: Database, *, bill_id: str, bill: dict,
) -> tuple[int, int]:
    """Returns (added, linked)."""
    slug = _politician_slug_from_url(bill.get("sponsor_politician_url"))
    if not slug:
        return (0, 0)

    pol_id = await db.fetchval(
        "SELECT id FROM politicians WHERE openparliament_slug = $1",
        slug,
    )
    await db.execute(
        """
        INSERT INTO bill_sponsors (
            bill_id, politician_id, sponsor_slug, sponsor_name_raw,
            role, source_system
        )
        VALUES ($1, $2, $3, $4, 'sponsor', $5)
        ON CONFLICT (bill_id, sponsor_slug)
          WHERE sponsor_slug IS NOT NULL
          DO UPDATE SET
              politician_id = COALESCE(EXCLUDED.politician_id, bill_sponsors.politician_id),
              sponsor_name_raw = COALESCE(EXCLUDED.sponsor_name_raw, bill_sponsors.sponsor_name_raw)
        """,
        bill_id, pol_id, slug, slug.replace("-", " ").title(),
        SOURCE_SYSTEM,
    )
    return (1, 1 if pol_id is not None else 0)


async def _fetch_bill_detail(
    client: httpx.AsyncClient, parliament: int, session: int, number: str,
) -> Optional[dict]:
    url = f"{API_ROOT}/bills/{parliament}-{session}/{number}/"
    try:
        r = await client.get(url, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        return r.json()
    except httpx.HTTPError as e:
        log.warning("federal-bills detail %s: %s", url, e)
        return None


async def ingest_federal_bills(
    db: Database, *,
    parliament: Optional[int] = None,
    session: Optional[int] = None,
    all_sessions: bool = False,
    limit: Optional[int] = None,
    delay_seconds: float = 0.5,
) -> dict[str, int]:
    """Fetch federal bills from openparliament.ca and upsert into `bills`.

    Args:
        parliament/session: explicit session override.
        all_sessions: ignored unless parliament+session are None — when True,
          walk every federal session that has any rows in legislative_sessions.
          (Avoids hitting the upstream for backfill discovery; the existing
          federal Hansard ingest already populated those rows.)
        limit: cap on bills processed this run (smoke-test friendly).
        delay_seconds: between detail fetches; openparliament asks for politeness.
    """
    stats = {"sessions_touched": 0, "bills": 0, "sponsors": 0, "sponsors_linked": 0}

    # Resolve target sessions.
    if parliament is not None and session is not None:
        targets: list[tuple[int, int]] = [(parliament, session)]
    elif all_sessions:
        rows = await db.fetch(
            """
            SELECT parliament_number, session_number
              FROM legislative_sessions
             WHERE level='federal' AND province_territory IS NULL
             ORDER BY parliament_number, session_number
            """
        )
        targets = [(r["parliament_number"], r["session_number"]) for r in rows]
    else:
        p, s = await current_session(db, level="federal")
        targets = [(p, s)]

    if not targets:
        log.warning("ingest_federal_bills: no target sessions")
        return stats

    log.info("ingest_federal_bills: %d session(s)", len(targets))

    processed = 0
    async with httpx.AsyncClient(headers=HEADERS, follow_redirects=True) as client:
        for parl, sess in targets:
            local_session_id = await _upsert_session(
                db, parliament=parl, session=sess,
            )
            stats["sessions_touched"] += 1

            url = f"{API_ROOT}/bills/?session={parl}-{sess}&limit=200"
            while url:
                try:
                    r = await client.get(url, timeout=REQUEST_TIMEOUT)
                    r.raise_for_status()
                    payload = r.json()
                except httpx.HTTPError as e:
                    log.warning("ingest_federal_bills: list %s: %s", url, e)
                    break

                objects = payload.get("objects") or []
                for obj in objects:
                    if limit is not None and processed >= limit:
                        url = None
                        break
                    # List response is sparse — fetch detail for sponsor + status.
                    detail = await _fetch_bill_detail(
                        client, parl, sess, obj["number"]
                    ) or obj
                    bill_id = await _upsert_bill(
                        db, session_id=local_session_id,
                        parliament=parl, session=sess, bill=detail,
                    )
                    if bill_id is None:
                        continue
                    stats["bills"] += 1
                    added, linked = await _upsert_sponsor(
                        db, bill_id=bill_id, bill=detail,
                    )
                    stats["sponsors"] += added
                    stats["sponsors_linked"] += linked
                    processed += 1
                    if delay_seconds > 0:
                        await asyncio.sleep(delay_seconds)
                if url is None:
                    break
                next_path = (payload.get("pagination") or {}).get("next_url")
                url = f"{API_ROOT}{next_path}" if next_path else None
                if limit is not None and processed >= limit:
                    break

    log.info("ingest_federal_bills: %s", stats)
    return stats
