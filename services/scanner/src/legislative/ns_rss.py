"""Nova Scotia bills RSS feed ingester.

Lightweight complement to the Socrata + HTML pipeline. The RSS feed at
    https://nslegislature.ca/legislative-business/bills-statutes/rss
returns every bill in the current session (one request, ~120 KB) with
richer status text than Socrata — commencement clauses, exceptions,
royal-assent dates with caveats. All in one request, so it doesn't
burn the F5 WAF budget the way per-bill page fetches do.

Scope:
  - Current session only (RSS has no historical bills)
  - No sponsor data (RSS doesn't expose sponsor slug)
  - Intended to run daily via cron for ongoing freshness

What it writes:
  - bills.status / bills.status_changed_at refreshed from RSS
  - bills.raw.rss updates with the full item payload
  - bill_events row for the current-stage transition (idempotent via
    bill_events_uniq constraint)

Matching: each RSS item links to a URL like
``/legislative-business/bills-statutes/bills/assembly-65-session-1/
bill-193``. We parse the assembly+bill-number and rebuild the Socrata
source_id (`socrata-ns:assembly-65-session-1:bill-193`) — bills we
don't already have from Socrata get skipped (the RSS doesn't carry
enough to create a new bill row from scratch).
"""
from __future__ import annotations

import logging
import re
from datetime import date, datetime
from typing import Any, Optional
from xml.etree import ElementTree as ET

import httpx
import orjson

from ..db import Database

log = logging.getLogger(__name__)

RSS_URL = "https://nslegislature.ca/legislative-business/bills-statutes/rss"
SOURCE_SYSTEM = "socrata-ns"  # we merge into the existing NS rows
REQUEST_TIMEOUT = 30

_BILL_URL_RE = re.compile(
    r"/bills/(?P<assembly>[\w-]+)/bill-(?P<number>[\w-]+)"
)

# RSS description lead reads like:
#   "Royal Assent - April 8, 2026; Commencement: ..."
#   "Second Reading - March 14, 2026"
# We pull "<stage>" + first date from the lead for bill_events.
_STATUS_LEAD_RE = re.compile(
    r"^\s*(?P<status>[A-Z][a-zA-Z ]+?)\s*[-–]\s*"
    r"(?P<month>January|February|March|April|May|June|July|August|"
    r"September|October|November|December)\s+"
    r"(?P<day>\d{1,2}),\s*(?P<year>\d{4})",
    re.IGNORECASE,
)

_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11,
    "december": 12,
}

# Mirror the NS parser vocabulary from ns_bill_parse. Keep inlined so
# the two modules stay independent — Hansard/votes layers can diverge.
_STAGE_MAP = {
    "introduced":                   "introduced",
    "first reading":                "first_reading",
    "second reading":               "second_reading",
    "committee of the whole house": "committee",
    "law amendments committee":     "committee",
    "private and local bills":      "committee",
    "third reading":                "third_reading",
    "royal assent":                 "royal_assent",
    "withdrawn":                    "withdrawn",
    "defeated":                     "defeated",
}


def _canon_stage(label: str) -> str:
    return _STAGE_MAP.get((label or "").strip().lower(), "other")


def _parse_item(item: ET.Element) -> Optional[dict[str, Any]]:
    def _t(tag: str) -> Optional[str]:
        el = item.find(tag)
        return el.text if (el is not None and el.text) else None

    link = _t("link") or ""
    m = _BILL_URL_RE.search(link)
    if not m:
        return None
    return {
        "assembly": m.group("assembly"),
        "bill_number": m.group("number"),
        "title": _t("title"),
        "description": _t("description"),
        "pub_date": _t("pubDate"),
        "link": link,
    }


def _parse_status(desc: str) -> tuple[Optional[str], Optional[date]]:
    if not desc:
        return None, None
    m = _STATUS_LEAD_RE.match(desc)
    if not m:
        return None, None
    try:
        d = date(int(m.group("year")),
                 _MONTHS[m.group("month").lower()],
                 int(m.group("day")))
    except (KeyError, ValueError):
        d = None
    return m.group("status").strip(), d


async def ingest_ns_rss(db: Database) -> dict[str, int]:
    """Fetch the NS bills RSS and refresh matching bill rows."""
    async with httpx.AsyncClient(follow_redirects=True) as client:
        r = await client.get(RSS_URL, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        xml = r.content

    root = ET.fromstring(xml)
    items = root.findall(".//item")
    stats = {"items": len(items), "matched": 0, "updated": 0,
             "events_added": 0, "unmatched": 0}

    for item in items:
        parsed = _parse_item(item)
        if not parsed:
            stats["unmatched"] += 1
            continue

        source_id = f"{SOURCE_SYSTEM}:{parsed['assembly']}:bill-{parsed['bill_number']}"
        bill_id = await db.fetchval(
            "SELECT id FROM bills WHERE source_id = $1", source_id,
        )
        if bill_id is None:
            stats["unmatched"] += 1
            continue
        stats["matched"] += 1

        status_text, status_date = _parse_status(parsed["description"] or "")
        status_changed_at = (
            datetime.combine(status_date, datetime.min.time())
            if status_date else None
        )

        # Update bills: merge rss payload into raw, refresh status text,
        # advance status_changed_at if RSS has a newer date.
        await db.execute(
            """
            UPDATE bills SET
                status = COALESCE($2, status),
                status_changed_at = CASE
                    WHEN $3::timestamptz IS NOT NULL
                     AND ($3::timestamptz > status_changed_at OR status_changed_at IS NULL)
                    THEN $3::timestamptz
                    ELSE status_changed_at
                END,
                raw = raw || jsonb_build_object('rss', $4::jsonb),
                last_fetched_at = now(),
                updated_at = now()
            WHERE id = $1
            """,
            bill_id,
            status_text,
            status_changed_at,
            orjson.dumps(parsed).decode(),
        )
        stats["updated"] += 1

        # Emit a bill_events row for the RSS-asserted stage. Unique key
        # (bill_id, stage, event_date, NULL, NULL) ensures idempotency.
        if status_text and status_date:
            stage = _canon_stage(status_text)
            if stage != "other":
                await db.execute(
                    """
                    INSERT INTO bill_events (
                        bill_id, stage, stage_label, event_date, raw
                    )
                    VALUES ($1, $2, $3, $4, $5::jsonb)
                    ON CONFLICT ON CONSTRAINT bill_events_uniq DO NOTHING
                    """,
                    bill_id, stage, status_text, status_date,
                    orjson.dumps({"source": "nslegislature-rss"}).decode(),
                )
                stats["events_added"] += 1

    log.info(
        "ns_rss: items=%d matched=%d updated=%d events_added=%d unmatched=%d",
        stats["items"], stats["matched"], stats["updated"],
        stats["events_added"], stats["unmatched"],
    )
    return stats
