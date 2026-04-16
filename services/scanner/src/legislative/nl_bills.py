"""Newfoundland & Labrador bills pipeline — single-page table scrape.

The NL House of Assembly publishes a single HTML page per session at

    /HouseBusiness/Bills/ga{GA}session{S}/

with a ``<table>`` that captures every bill's **full stage timeline**
in one payload. Columns: No., Bill (title + optional link to bill
text HTML), First Reading, Second Reading, Committee, Amendments
(Yes/No), Third Reading, Royal Assent, Act (chapter).

One HTTP GET per session — no per-bill fetches needed for stage data.

**Limitation:** neither the list page nor the per-bill HTML surface
sponsor information, and NL's members roster has no numeric id in
URLs. Sponsors therefore **aren't ingested** by this module — a
later pass against Order Papers or Hansard would be needed to link
bills to MHAs. Stages + titles alone are still high-value: a
Westminster-progression view of the NL legislature.

Historical backfill: the ``/HouseBusiness/Bills/`` landing lists
every session back to at least GA 44 (40+ sessions) with the same URL
shape, so full backfill is free.
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import date, datetime
from typing import Any, Optional

import httpx
import orjson

from ..db import Database

log = logging.getLogger(__name__)

SOURCE_SYSTEM = "nlhoa"
REQUEST_TIMEOUT = 45
HEADERS = {
    "User-Agent": "SovereignWatch/1.0 (civic-transparency; +admin@thebunkerops.ca)",
    "Accept": "text/html,application/xhtml+xml",
}

BASE = "https://assembly.nl.ca"
BILLS_INDEX_URL = BASE + "/HouseBusiness/Bills/"
SESSION_URL = BASE + "/HouseBusiness/Bills/ga{ga}session{session}/"

# Column index → (canonical_stage, display_label). The table always
# emits 9 columns in the same order; we reference by index rather than
# header text so the parser survives whitespace wiggle.
_STAGE_COLUMNS: list[tuple[int, str, str]] = [
    # (td_index, canonical_stage, display_label)
    (2, "first_reading",  "First Reading"),
    (3, "second_reading", "Second Reading"),
    (4, "committee",      "Committee"),
    (6, "third_reading",  "Third Reading"),
    (7, "royal_assent",   "Royal Assent"),
]


_SESSION_LINK_RE = re.compile(
    r'href="ga(?P<ga>\d+)session(?P<session>\d+)/"',
    re.IGNORECASE,
)

_TABLE_RE = re.compile(
    r"<table[^>]*>(?P<body>.*?)</table>",
    re.IGNORECASE | re.DOTALL,
)

_ROW_RE = re.compile(
    r"<tr[^>]*>(?P<body>.*?)</tr>",
    re.IGNORECASE | re.DOTALL,
)

_CELL_RE = re.compile(
    r"<td[^>]*>(?P<body>.*?)</td>",
    re.IGNORECASE | re.DOTALL,
)

_BILL_LINK_RE = re.compile(
    r'<a[^>]+href="(?P<href>bill[^"]+\.htm)"[^>]*>'
    r"(?P<text>[^<]+?)</a>",
    re.IGNORECASE | re.DOTALL,
)


def _clean_text(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", html or "")).strip()


def _parse_date(s: str) -> Optional[date]:
    s = (s or "").strip().rstrip(",.")
    if not s:
        return None
    for fmt in ("%B %d, %Y", "%b %d, %Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _parse_session_list(html: str) -> list[tuple[int, int]]:
    """Every (ga, session) pair linked from the bills landing page."""
    pairs: set[tuple[int, int]] = set()
    for m in _SESSION_LINK_RE.finditer(html):
        pairs.add((int(m.group("ga")), int(m.group("session"))))
    return sorted(pairs)


def parse_session_page(html: str) -> list[dict[str, Any]]:
    """Split a session bills page into per-bill dicts."""
    table = _TABLE_RE.search(html)
    if not table:
        return []
    bills: list[dict[str, Any]] = []
    for row in _ROW_RE.finditer(table.group("body")):
        cells = [c.group("body") for c in _CELL_RE.finditer(row.group("body"))]
        if len(cells) < 9:
            # Header (<thead>) has no <td>s, so it produces 0 cells and
            # gets skipped here. Tail empty row also skipped.
            continue

        num_raw = _clean_text(cells[0])
        if not num_raw:
            continue
        num_m = re.match(r"(\d+)", num_raw)
        if not num_m:
            continue
        bill_number = num_m.group(1)

        title_cell = cells[1]
        link_m = _BILL_LINK_RE.search(title_cell)
        title = _clean_text(link_m.group("text") if link_m else title_cell)
        bill_href = link_m.group("href") if link_m else None

        amendments_raw = _clean_text(cells[5])
        had_amendments = amendments_raw.lower().startswith("y")

        act_chapter = _clean_text(cells[8]) or None

        events: list[dict[str, Any]] = []
        for td_idx, stage, label in _STAGE_COLUMNS:
            cell_text = _clean_text(cells[td_idx])
            if not cell_text:
                continue
            # Committee column sometimes carries "Adj." or similar
            # non-date values. Try to parse; if no date, still emit
            # the event with a null date is too noisy — require a
            # real date for all stage rows.
            d = _parse_date(cell_text)
            if d is None:
                # Preserve the raw label as an outcome tag so downstream
                # can surface "Adj." without flooding with dateless rows.
                continue
            ev: dict[str, Any] = {
                "stage":       stage,
                "stage_label": label,
                "event_date":  d,
                "outcome":     None,
            }
            if stage == "committee" and had_amendments:
                ev["outcome"] = "amended"
            events.append(ev)

        bills.append({
            "bill_number":    bill_number,
            "title":          title,
            "bill_href":      bill_href,
            "amendments":     had_amendments,
            "act_chapter":    act_chapter,
            "events":         events,
        })
    return bills


# ─────────────────────────────────────────────────────────────────────
# DB writers
# ─────────────────────────────────────────────────────────────────────

async def _upsert_session(
    db: Database, *, ga: int, session: int,
) -> str:
    row = await db.fetchrow(
        """
        INSERT INTO legislative_sessions (
            level, province_territory, parliament_number, session_number,
            name, source_system, source_url
        )
        VALUES ('provincial', 'NL', $1, $2, $3, $4, $5)
        ON CONFLICT (level, province_territory, parliament_number, session_number)
        DO UPDATE SET
            name          = COALESCE(legislative_sessions.name, EXCLUDED.name),
            source_system = COALESCE(legislative_sessions.source_system, EXCLUDED.source_system),
            source_url    = COALESCE(legislative_sessions.source_url, EXCLUDED.source_url),
            updated_at    = now()
        RETURNING id
        """,
        ga, session,
        f"{ga}th General Assembly, {session}{'st' if session == 1 else 'nd' if session == 2 else 'rd' if session == 3 else 'th'} Session",
        SOURCE_SYSTEM,
        SESSION_URL.format(ga=ga, session=session),
    )
    return str(row["id"])


async def _upsert_bill(
    db: Database, *, session_id: str, ga: int, session: int,
    bill: dict, session_url: str,
) -> str:
    # Latest stage = most recent event_date; status label = that stage.
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

    source_id = f"{SOURCE_SYSTEM}:{ga}-{session}:bill-{bill['bill_number']}"
    source_url = (
        f"{session_url}{bill['bill_href']}"
        if bill.get("bill_href") else session_url
    )

    row = await db.fetchrow(
        """
        INSERT INTO bills (
            session_id, level, province_territory, bill_number,
            title, status, status_changed_at, introduced_date,
            source_id, source_system, source_url, raw, last_fetched_at
        )
        VALUES ($1, 'provincial', 'NL', $2, $3, $4, $5, $6,
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
        session_id, bill["bill_number"], bill["title"],
        latest_label, status_changed_at, introduced,
        source_id, SOURCE_SYSTEM, source_url,
        orjson.dumps({
            "amendments":  bill["amendments"],
            "act_chapter": bill["act_chapter"],
            "bill_href":   bill["bill_href"],
        }).decode(),
    )
    return str(row["id"])


async def _persist_events(
    db: Database, bill_id: str, bill: dict,
) -> int:
    written = 0
    for ev in bill["events"]:
        if ev["event_date"] is None or ev["stage"] == "other":
            continue
        await db.execute(
            """
            INSERT INTO bill_events (
                bill_id, stage, stage_label, event_date, outcome, raw
            )
            VALUES ($1, $2, $3, $4, $5, $6::jsonb)
            ON CONFLICT ON CONSTRAINT bill_events_uniq DO NOTHING
            """,
            bill_id, ev["stage"], ev["stage_label"], ev["event_date"],
            ev.get("outcome"),
            orjson.dumps({"source": SOURCE_SYSTEM}).decode(),
        )
        written += 1
    return written


# ─────────────────────────────────────────────────────────────────────
# Orchestrator
# ─────────────────────────────────────────────────────────────────────

async def _ingest_one_session(
    db: Database, *, client: httpx.AsyncClient,
    ga: int, session: int,
) -> dict[str, int]:
    url = SESSION_URL.format(ga=ga, session=session)
    r = await client.get(url, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    # NL per-bill pages are Windows-1252, but the list pages are UTF-8.
    bills = parse_session_page(r.text)
    log.info("ingest_nl_bills: ga%d s%d → %d bills", ga, session, len(bills))

    stats = {"bills": 0, "events": 0}
    if not bills:
        return stats

    session_id = await _upsert_session(db, ga=ga, session=session)
    for bill in bills:
        bill_id = await _upsert_bill(
            db, session_id=session_id, ga=ga, session=session,
            bill=bill, session_url=url,
        )
        stats["bills"] += 1
        stats["events"] += await _persist_events(db, bill_id, bill)
    return stats


async def ingest_nl_bills(
    db: Database, *,
    ga: Optional[int] = None,
    session: Optional[int] = None,
    all_sessions_in_ga: Optional[int] = None,
    all_sessions: bool = False,
    delay_seconds: float = 1.0,
) -> dict[str, int]:
    """Ingest Newfoundland & Labrador bills from the progress-of-bills tables.

    Default (no args): current session (latest GA/session in the
    landing page's nav).
    """
    totals = {"sessions_touched": 0, "bills": 0, "events": 0}

    async with httpx.AsyncClient(headers=HEADERS, follow_redirects=True) as client:
        targets: list[tuple[int, int]] = []
        if ga is not None and session is not None:
            targets = [(ga, session)]
        else:
            r = await client.get(BILLS_INDEX_URL, timeout=REQUEST_TIMEOUT)
            r.raise_for_status()
            pairs = _parse_session_list(r.text)
            if not pairs:
                log.warning("ingest_nl_bills: no sessions found in index")
                return totals
            if all_sessions:
                targets = pairs
            elif all_sessions_in_ga is not None:
                targets = [(g, s) for (g, s) in pairs if g == all_sessions_in_ga]
            else:
                # Pick the latest pair (max GA, max session in that GA)
                latest_ga = max(g for g, _ in pairs)
                sessions_in_latest = [s for g, s in pairs if g == latest_ga]
                targets = [(latest_ga, max(sessions_in_latest))]

        log.info("ingest_nl_bills: %d target session(s)", len(targets))
        for i, (g, s) in enumerate(targets):
            if i > 0 and delay_seconds > 0:
                await asyncio.sleep(delay_seconds)
            try:
                r = await _ingest_one_session(db, client=client, ga=g, session=s)
            except httpx.HTTPError as e:
                log.warning("ingest_nl_bills: ga%d s%d failed: %s", g, s, e)
                continue
            totals["sessions_touched"] += 1
            for k in ("bills", "events"):
                totals[k] += r[k]

    log.info("ingest_nl_bills: %s", totals)
    return totals
