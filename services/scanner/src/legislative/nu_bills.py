"""Nunavut bills pipeline — Drupal 9 HTML table scrape.

The Nunavut Legislative Assembly (``assembly.nu.ca``) publishes
current-session bills on a single Drupal 9 view at
``/bills-and-legislation`` as an HTML table. Each row is one bill
with its full stage timeline embedded as typed ``<time>`` elements,
one per column:

  view-title-table-column                    → bill number + PDF + title
  view-field-date-of-notice-table-column     → notice date (pre-1R)
  view-field-first-reading-table-column      → first_reading
  view-field-second-reading-table-column     → second_reading
  view-field-reported-table-column           → committee  (Standing Committee)
  view-field-reported-whole-table-column     → committee  (Committee of the Whole)
  view-field-third-reading-table-column      → third_reading
  view-field-date-of-assent-table-column     → royal_assent

Known limitations:

- **No sponsor.** Nunavut is a consensus-government territory with no
  partisan sponsor model; the public view doesn't expose a per-bill
  author. Pipeline writes bills + events only.
- **No assembly/session metadata in the page.** Drupal doesn't print
  the assembly number on the bills page. The CLI accepts
  ``--assembly N --session S`` overrides; default is the current
  sitting (7-1 as of 2026-04).
- **Drupal JSON serializer disabled.** `?_format=json` returns 406
  Not Acceptable. HTML scrape is the only route.

Current session has only 4 bills as of 2026-04 — smallest roster of
any province/territory. Ingestion cost: one HTTP GET.
"""
from __future__ import annotations

import html as _html_lib
import logging
import re
from datetime import date, datetime
from typing import Any, Optional

import httpx
import orjson

from ..db import Database

log = logging.getLogger(__name__)

SOURCE_SYSTEM = "assembly-nu"
REQUEST_TIMEOUT = 45
HEADERS = {
    "User-Agent": "SovereignWatch/1.0 (civic-transparency; +admin@thebunkerops.ca)",
    "Accept": "text/html,application/xhtml+xml",
}

LIST_URL = "https://www.assembly.nu.ca/bills-and-legislation"

# Defaults — Nunavut LA's 7th Assembly began after October 2025 general
# election. Adjust if we're backfilling a different session.
DEFAULT_ASSEMBLY = 7
DEFAULT_SESSION = 1


_ROW_RE = re.compile(
    r'<tr\s+class="(?:odd|even)"[^>]*>(?P<body>.*?)</tr>',
    re.IGNORECASE | re.DOTALL,
)

_CELL_RE = re.compile(
    r'<td[^>]+class="([^"]+)"[^>]*>(?P<body>.*?)</td>',
    re.IGNORECASE | re.DOTALL,
)

_TIME_RE = re.compile(
    r'<time\s+datetime="(?P<iso>[^"]+)"[^>]*>\s*(?P<text>[^<]+?)\s*</time>',
    re.IGNORECASE,
)

_BILL_NUM_RE = re.compile(r"BILL\s+(?P<number>\S+)", re.IGNORECASE)


# column-class-suffix → (canonical_stage, committee_name, event_type)
# Looked up by the "field-XXX" portion of views-field-field-XXX.
_COLUMN_MAP: dict[str, tuple[str, Optional[str], Optional[str]]] = {
    "date-of-notice":  ("introduced",    None,                      "notice"),
    "first-reading":   ("first_reading", None,                      None),
    "second-reading":  ("second_reading", None,                     None),
    "reported":        ("committee",     "Standing Committee",      "reported"),
    "reported-whole":  ("committee",     "Committee of the Whole",  "reported"),
    "third-reading":   ("third_reading", None,                      None),
    "date-of-assent":  ("royal_assent",  None,                      None),
}


def _strip_tags(s: str) -> str:
    return _html_lib.unescape(
        re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", s or "")).strip()
    )


def _parse_iso_date(iso: str) -> Optional[date]:
    try:
        return datetime.strptime(iso[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def parse_bill_list(html: str) -> list[dict[str, Any]]:
    bills: list[dict[str, Any]] = []
    for row_m in _ROW_RE.finditer(html):
        body = row_m.group("body")

        # Collect cells keyed by the `views-field-field-XXX` class suffix.
        cells: dict[str, str] = {}
        title_cell: Optional[str] = None
        for cm in _CELL_RE.finditer(body):
            classes = cm.group(1)
            cell_body = cm.group("body")
            if "views-field-title" in classes:
                title_cell = cell_body
                continue
            m = re.search(r"views-field-field-([a-z0-9-]+)", classes)
            if m:
                cells[m.group(1)] = cell_body

        if title_cell is None:
            continue

        num_m = _BILL_NUM_RE.search(_strip_tags(title_cell))
        if not num_m:
            continue
        bill_number = num_m.group("number").strip()

        # Title text is inside the <a>; PDF href is the <a href="...">
        a_m = re.search(
            r'<a[^>]+href="(?P<href>[^"]+\.pdf)"[^>]*>(?P<text>[^<]+?)</a>',
            title_cell,
            re.IGNORECASE,
        )
        if a_m:
            title = _strip_tags(a_m.group("text"))
            pdf_url = a_m.group("href")
        else:
            title = _strip_tags(title_cell)
            pdf_url = None

        events: list[dict[str, Any]] = []
        for col_suffix, (stage, committee_name, event_type) in _COLUMN_MAP.items():
            cell = cells.get(col_suffix)
            if not cell:
                continue
            t = _TIME_RE.search(cell)
            if not t:
                continue
            d = _parse_iso_date(t.group("iso"))
            if d is None:
                continue
            events.append({
                "column": col_suffix,
                "stage": stage,
                "stage_label": {
                    "date-of-notice":  "Notice of Motion",
                    "first-reading":   "First Reading",
                    "second-reading":  "Second Reading",
                    "reported":        "Reported from Standing Committee",
                    "reported-whole":  "Reported from Committee of the Whole",
                    "third-reading":   "Third Reading",
                    "date-of-assent":  "Royal Assent",
                }[col_suffix],
                "event_date":    d,
                "event_type":    event_type,
                "committee_name": committee_name,
            })

        bills.append({
            "bill_number": bill_number,
            "title":       title,
            "pdf_url":     pdf_url,
            "events":      events,
        })
    return bills


# ─────────────────────────────────────────────────────────────────────
# DB writers
# ─────────────────────────────────────────────────────────────────────

async def _upsert_session(
    db: Database, *, assembly: int, session: int,
) -> str:
    row = await db.fetchrow(
        """
        INSERT INTO legislative_sessions (
            level, province_territory, parliament_number, session_number,
            name, source_system, source_url
        )
        VALUES ('provincial', 'NU', $1, $2, $3, $4, $5)
        ON CONFLICT (level, province_territory, parliament_number, session_number)
        DO UPDATE SET
            name          = COALESCE(legislative_sessions.name, EXCLUDED.name),
            source_system = COALESCE(legislative_sessions.source_system, EXCLUDED.source_system),
            source_url    = COALESCE(legislative_sessions.source_url, EXCLUDED.source_url),
            updated_at    = now()
        RETURNING id
        """,
        assembly, session,
        f"{assembly}th Assembly, {session}{'st' if session == 1 else 'nd' if session == 2 else 'rd' if session == 3 else 'th'} Session",
        SOURCE_SYSTEM, LIST_URL,
    )
    return str(row["id"])


async def _upsert_bill_with_events(
    db: Database, *, session_id: str, assembly: int, session: int,
    bill: dict,
) -> tuple[str, int]:
    bill_number = bill["bill_number"]
    source_id = f"{SOURCE_SYSTEM}:{assembly}-{session}:bill-{bill_number}"

    latest_date: Optional[date] = None
    latest_label: Optional[str] = None
    introduced: Optional[date] = None
    for ev in bill["events"]:
        if ev["event_date"] is None:
            continue
        if ev["stage"] == "first_reading" and introduced is None:
            introduced = ev["event_date"]
        if latest_date is None or ev["event_date"] >= latest_date:
            latest_date = ev["event_date"]
            latest_label = ev["stage_label"]
    status_changed_at = (
        datetime.combine(latest_date, datetime.min.time())
        if latest_date else None
    )

    row = await db.fetchrow(
        """
        INSERT INTO bills (
            session_id, level, province_territory, bill_number,
            title, status, status_changed_at, introduced_date,
            source_id, source_system, source_url, raw, last_fetched_at
        )
        VALUES ($1, 'provincial', 'NU', $2, $3, $4, $5, $6,
                $7, $8, $9, $10::jsonb, now())
        ON CONFLICT (source_id) DO UPDATE SET
            title             = EXCLUDED.title,
            status            = EXCLUDED.status,
            status_changed_at = CASE
                WHEN EXCLUDED.status_changed_at IS NOT NULL
                 AND (EXCLUDED.status_changed_at > bills.status_changed_at
                      OR bills.status_changed_at IS NULL)
                THEN EXCLUDED.status_changed_at
                ELSE bills.status_changed_at
            END,
            introduced_date   = COALESCE(EXCLUDED.introduced_date, bills.introduced_date),
            source_url        = EXCLUDED.source_url,
            raw               = EXCLUDED.raw,
            last_fetched_at   = now(),
            updated_at        = now()
        RETURNING id
        """,
        session_id, bill_number, bill["title"],
        latest_label, status_changed_at, introduced,
        source_id, SOURCE_SYSTEM, LIST_URL,
        orjson.dumps({"pdf_url": bill["pdf_url"]}).decode(),
    )
    bill_id = str(row["id"])

    written = 0
    for ev in bill["events"]:
        await db.execute(
            """
            INSERT INTO bill_events (
                bill_id, stage, stage_label, event_date,
                event_type, outcome, committee_name, raw
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb)
            ON CONFLICT ON CONSTRAINT bill_events_uniq DO NOTHING
            """,
            bill_id, ev["stage"], ev["stage_label"], ev["event_date"],
            ev.get("event_type"), ev.get("event_type"),
            ev.get("committee_name"),
            orjson.dumps({"source": SOURCE_SYSTEM, "column": ev["column"]}).decode(),
        )
        written += 1
    return bill_id, written


# ─────────────────────────────────────────────────────────────────────
# Orchestrator
# ─────────────────────────────────────────────────────────────────────

async def ingest_nu_bills(
    db: Database, *,
    assembly: int = DEFAULT_ASSEMBLY,
    session: int = DEFAULT_SESSION,
) -> dict[str, int]:
    """Ingest Nunavut bills from assembly.nu.ca/bills-and-legislation.

    One HTTP GET. Caller provides assembly/session because the Drupal
    view doesn't print those on the page.
    """
    stats = {"sessions_touched": 0, "bills": 0, "events": 0}

    async with httpx.AsyncClient(headers=HEADERS, follow_redirects=True) as client:
        r = await client.get(LIST_URL, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        bills = parse_bill_list(r.text)
        log.info("ingest_nu_bills: list → %d bills", len(bills))

    if not bills:
        return stats

    session_id = await _upsert_session(db, assembly=assembly, session=session)
    stats["sessions_touched"] = 1

    for bill in bills:
        _, ev_w = await _upsert_bill_with_events(
            db,
            session_id=session_id,
            assembly=assembly, session=session, bill=bill,
        )
        stats["bills"] += 1
        stats["events"] += ev_w

    log.info("ingest_nu_bills: %s", stats)
    return stats
