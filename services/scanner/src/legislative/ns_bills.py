"""Nova Scotia bills ingester — Socrata dataset iz5x-dzyf.

Source: https://data.novascotia.ca/Government-Administration/Bills-introduced-in-the-Nova-Scotia-Legislature/iz5x-dzyf
API:    https://data.novascotia.ca/resource/iz5x-dzyf.json

Upstream record shape (observed 2026-04):
    {
      "title":               "Bill 127 - Protecting Nova Scotians Act",
      "link":                {"url": "https://nslegislature.ca/.../bill-127"},
      "description":         "Royal Assent",          # current status
      "date_status_changed": "2025-10-03T00:00:00.000",
      "assembly_and_session": "assembly-65-session-1",
      "bill_number":         "127",
      "_1st_reading_bill":   {"url": "..."},
      "_3rd_reading_bill":   {"url": "..."}
    }

No sponsor field is exposed by Socrata — sponsor resolution will be a
second pass that scrapes the per-bill HTML page at nslegislature.ca.

Idempotency: every bill has a stable ``source_id`` of the form
``socrata-ns:<assembly-and-session>:bill-<N>``. Reruns upsert by
``source_id`` so we can safely fetch the whole dataset each run.

Paging: Socrata's default page size is 1000 rows, max 50000. We page
through with ``$offset`` until an empty page is returned. Public app
tokens are optional; set ``SOCRATA_APP_TOKEN`` to raise the throttle.
"""
from __future__ import annotations

import logging
import os
import re
from datetime import date, datetime
from typing import Any, Iterable, Optional

import httpx
import orjson

from ..db import Database

log = logging.getLogger(__name__)

SOCRATA_ENDPOINT = "https://data.novascotia.ca/resource/iz5x-dzyf.json"
PAGE_SIZE = 1000
SOURCE_SYSTEM = "socrata-ns"

# "assembly-65-session-1" -> (65, 1)
_ASSEMBLY_RE = re.compile(r"^assembly-(\d+)-session-(\d+)$")

# Strip the "Bill N - " prefix Socrata bakes into every title.
_TITLE_PREFIX_RE = re.compile(r"^\s*Bill\s+\d+[A-Z]?\s*[-–—:]\s*", re.IGNORECASE)


def _parse_assembly(value: str) -> Optional[tuple[int, int]]:
    m = _ASSEMBLY_RE.match((value or "").strip().lower())
    if not m:
        return None
    return int(m.group(1)), int(m.group(2))


def _short_title(raw_title: str) -> str:
    return _TITLE_PREFIX_RE.sub("", raw_title or "").strip()


def _status_datetime(raw: dict[str, Any]) -> Optional[datetime]:
    """Parse Socrata floating-timestamp into a naive datetime.

    asyncpg binds timestamptz parameters at the binary protocol layer and
    refuses to coerce strings, so we must hand it a real datetime.
    """
    val = raw.get("date_status_changed")
    if not val:
        return None
    try:
        return datetime.fromisoformat(val)
    except ValueError:
        return None


def _status_date(raw: dict[str, Any]) -> Optional[date]:
    dt = _status_datetime(raw)
    return dt.date() if dt else None


# Map Socrata's free-text "description" (which is the current stage) to our
# canonical stage vocabulary for bill_events. Unknown labels pass through
# as 'other' with the verbatim label preserved in stage_label.
_STAGE_MAP = {
    "introduced":                 "introduced",
    "first reading":              "first_reading",
    "second reading":             "second_reading",
    "committee of the whole house": "committee",
    "law amendments committee":   "committee",
    "private and local bills":    "committee",
    "third reading":              "third_reading",
    "royal assent":               "royal_assent",
    "withdrawn":                  "withdrawn",
    "defeated":                   "defeated",
}


def _canon_stage(label: str) -> str:
    key = (label or "").strip().lower()
    return _STAGE_MAP.get(key, "other")


async def _fetch_page(
    client: httpx.AsyncClient, offset: int, limit: int
) -> list[dict[str, Any]]:
    params = {"$limit": str(limit), "$offset": str(offset), "$order": "date_status_changed ASC"}
    headers: dict[str, str] = {}
    token = os.environ.get("SOCRATA_APP_TOKEN")
    if token:
        headers["X-App-Token"] = token
    r = await client.get(SOCRATA_ENDPOINT, params=params, headers=headers, timeout=60)
    r.raise_for_status()
    return r.json()


async def _upsert_session(
    db: Database, parliament: int, session: int
) -> str:
    row = await db.fetchrow(
        """
        INSERT INTO legislative_sessions (
            level, province_territory, parliament_number, session_number,
            name, source_system
        )
        VALUES ('provincial', 'NS', $1, $2, $3, $4)
        ON CONFLICT (level, province_territory, parliament_number, session_number)
        DO UPDATE SET updated_at = now()
        RETURNING id
        """,
        parliament,
        session,
        f"{parliament}th General Assembly, Session {session}",
        SOURCE_SYSTEM,
    )
    return str(row["id"])


async def _upsert_bill(
    db: Database, *, session_id: str, record: dict[str, Any]
) -> Optional[str]:
    assembly = record.get("assembly_and_session") or ""
    bill_number = str(record.get("bill_number") or "").strip()
    if not bill_number:
        return None

    title_full = record.get("title") or ""
    short_title = _short_title(title_full)
    status = (record.get("description") or "").strip() or None
    status_changed = _status_datetime(record)
    link = (record.get("link") or {}).get("url")
    source_id = f"{SOURCE_SYSTEM}:{assembly}:bill-{bill_number}"

    row = await db.fetchrow(
        """
        INSERT INTO bills (
            session_id, level, province_territory, bill_number,
            title, short_title, status, status_changed_at,
            source_id, source_system, source_url, raw, last_fetched_at
        )
        VALUES ($1, 'provincial', 'NS', $2,
                $3, $4, $5, $6,
                $7, $8, $9, $10::jsonb, now())
        ON CONFLICT (source_id) DO UPDATE SET
            title             = EXCLUDED.title,
            short_title       = EXCLUDED.short_title,
            status            = EXCLUDED.status,
            status_changed_at = EXCLUDED.status_changed_at,
            source_url        = EXCLUDED.source_url,
            raw               = EXCLUDED.raw,
            last_fetched_at   = now(),
            updated_at        = now()
        RETURNING id
        """,
        session_id,
        bill_number,
        title_full,
        short_title,
        status,
        status_changed,
        source_id,
        SOURCE_SYSTEM,
        link,
        orjson.dumps(record).decode(),
    )
    return str(row["id"])


async def _record_current_stage(
    db: Database, *, bill_id: str, record: dict[str, Any]
) -> None:
    """Synthesize a single bill_events row for the current stage.

    Socrata doesn't give us historical stage transitions — only the current
    stage + date. We record just that, idempotently; the HTML-scraper pass
    (future work) will backfill earlier transitions.
    """
    label = (record.get("description") or "").strip()
    if not label:
        return
    event_date = _status_date(record)
    if not event_date:
        return
    stage = _canon_stage(label)
    await db.execute(
        """
        INSERT INTO bill_events (bill_id, stage, stage_label, event_date, source_url, raw)
        VALUES ($1, $2, $3, $4, $5, $6::jsonb)
        ON CONFLICT ON CONSTRAINT bill_events_uniq DO NOTHING
        """,
        bill_id,
        stage,
        label,
        event_date,
        (record.get("link") or {}).get("url"),
        orjson.dumps({"source": "socrata-current-stage"}).decode(),
    )


async def ingest_ns_bills(
    db: Database, *, limit: Optional[int] = None
) -> dict[str, int]:
    """Fetch every bill from the NS Socrata dataset and upsert.

    Args:
        limit: optional cap on total records (for smoke tests).
    Returns:
        stats dict with counts of bills and events processed.
    """
    stats = {"bills": 0, "events": 0, "sessions": 0, "skipped": 0}
    seen_sessions: dict[tuple[int, int], str] = {}

    async with httpx.AsyncClient() as client:
        offset = 0
        while True:
            page_size = PAGE_SIZE if limit is None else min(PAGE_SIZE, limit - stats["bills"])
            if page_size <= 0:
                break
            page = await _fetch_page(client, offset, page_size)
            if not page:
                break

            for record in page:
                assembly = record.get("assembly_and_session") or ""
                parsed = _parse_assembly(assembly)
                if parsed is None:
                    log.warning("ns_bills: unparseable assembly_and_session=%r", assembly)
                    stats["skipped"] += 1
                    continue

                key = parsed
                session_id = seen_sessions.get(key)
                if session_id is None:
                    session_id = await _upsert_session(db, key[0], key[1])
                    seen_sessions[key] = session_id
                    stats["sessions"] += 1

                bill_id = await _upsert_bill(db, session_id=session_id, record=record)
                if bill_id is None:
                    stats["skipped"] += 1
                    continue
                stats["bills"] += 1

                await _record_current_stage(db, bill_id=bill_id, record=record)
                stats["events"] += 1

            offset += len(page)
            if limit is not None and stats["bills"] >= limit:
                break

    log.info(
        "ns_bills: ingested bills=%d events=%d sessions=%d skipped=%d",
        stats["bills"], stats["events"], stats["sessions"], stats["skipped"],
    )
    return stats
