"""Northwest Territories bills pipeline — Drupal HTML scrape.

The NT Legislative Assembly (``ntassembly.ca``) publishes bills as
Drupal 9 nodes. The list page at ``/documents-proceedings/bills``
enumerates every current-session bill linked by slug; each per-bill
page is a ``node--type-bills-and-legislation`` with well-named
``field--name-field-*`` classes for every stage date.

No sponsor is surfaced on the public pages (consensus-government
territory — there's no partisan sponsor in the Westminster sense).
The pipeline therefore writes bills + stage events but no
``bill_sponsors`` rows. Consensus-government context: NT has no
political parties in its legislature, so "sponsor" maps to Minister /
Premier which isn't exposed separately from the bill metadata here.

Stage vocabulary (field-name → canonical):
  field-first-reading-date       → first_reading
  field-second-reading-date      → second_reading
  field-to-standing-comm-date    → committee  (event_type="referred")
  field-standing-comm-amend-date → committee  (event_type="amended")
  field-to-whole-comm-date       → committee  (committee_name="Committee of the Whole",
                                               event_type="referred")
  field-whole-comm-amend-date    → committee  (committee_name="Committee of the Whole",
                                               event_type="amended")
  field-from-whole-comm-date     → committee  (committee_name="Committee of the Whole",
                                               event_type="reported")
  field-third-reading-date       → third_reading
  field-assent-date              → royal_assent

Historical backfill: the list page nav exposes assemblies 16–20.
Per-assembly URL routing isn't yet mapped; backfill support deferred.
"""
from __future__ import annotations

import asyncio
import html as _html_lib
import logging
import re
from datetime import date, datetime
from typing import Any, Optional

import httpx
import orjson

from ..db import Database

log = logging.getLogger(__name__)

SOURCE_SYSTEM = "ntassembly"
REQUEST_TIMEOUT = 45
HEADERS = {
    "User-Agent": "SovereignWatch/1.0 (civic-transparency; +admin@thebunkerops.ca)",
    "Accept": "text/html,application/xhtml+xml",
}

BASE = "https://www.ntassembly.ca"
LIST_URL = BASE + "/documents-proceedings/bills"


_BILL_LIST_HREF_RE = re.compile(
    r'href="(?P<href>/documents-proceedings/bills/(?P<slug>[^"/]+))"',
    re.IGNORECASE,
)

_H1_RE = re.compile(r"<h1[^>]*>(?P<body>.*?)</h1>", re.IGNORECASE | re.DOTALL)

_ASSEMBLY_SESSION_RE = re.compile(
    r'field--name-field-assembly-session.*?field__item[^>]*>\s*'
    r'(?P<body>[^<]+?)\s*<',
    re.IGNORECASE | re.DOTALL,
)

_BILL_NUM_TITLE_RE = re.compile(
    r"Bill\s+(?P<number>\S+?)\s*[-–]\s*(?P<title>.+?)\s*$",
    re.IGNORECASE | re.DOTALL,
)

# Each stage gets its own `<div class="field field--name-field-{stage}-date">`
# wrapping a `<details class="bills-and-legislation--date-progress--wrapper">`.
_STAGE_BLOCK_RE = re.compile(
    r"field--name-(?P<field>field-[a-z-]+-date)"  # e.g. field-first-reading-date
    r".*?bills-and-legislation--date-progress[^\"']*--status-(?P<state>\w+)"
    r".*?bills-and-legislation--date-progress--label[^>]*>\s*"
    r"(?P<label>[^<]+?)\s*</div>"
    r".*?bills-and-legislation--date-progress--status[^>]*>\s*"
    r"(?P<status>[^<]+?)\s*</div>",
    re.IGNORECASE | re.DOTALL,
)


# Field-name → (canonical_stage, committee_name, default_event_type)
_FIELD_MAP: dict[str, tuple[str, Optional[str], Optional[str]]] = {
    "field-first-reading-date":       ("first_reading",   None,                       None),
    "field-second-reading-date":      ("second_reading",  None,                       None),
    "field-to-standing-comm-date":    ("committee",       "Standing Committee",       "referred"),
    "field-standing-comm-amend-date": ("committee",       "Standing Committee",       "amended"),
    "field-to-whole-comm-date":       ("committee",       "Committee of the Whole",   "referred"),
    "field-whole-comm-amend-date":    ("committee",       "Committee of the Whole",   "amended"),
    "field-from-whole-comm-date":     ("committee",       "Committee of the Whole",   "reported"),
    "field-third-reading-date":       ("third_reading",   None,                       None),
    "field-assent-date":              ("royal_assent",    None,                       None),
}


def _strip_tags(s: str) -> str:
    return _html_lib.unescape(
        re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", s or "")).strip()
    )


def _parse_date(raw: str) -> Optional[date]:
    """Extract a "Month DD, YYYY" date from a freeform status string.

    Status text looks like "Completed on March 04, 2026" or occasionally
    "March 05, 2026" without the prefix. Pull the date out by pattern.
    """
    if not raw:
        return None
    m = re.search(
        r"(?P<m>January|February|March|April|May|June|July|August|"
        r"September|October|November|December)\s+"
        r"(?P<d>\d{1,2}),\s*(?P<y>\d{4})",
        raw,
    )
    if not m:
        return None
    try:
        return datetime.strptime(
            f"{m.group('m')} {int(m.group('d'))}, {m.group('y')}",
            "%B %d, %Y",
        ).date()
    except ValueError:
        return None


def parse_bill_list(html: str) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for m in _BILL_LIST_HREF_RE.finditer(html):
        slug = m.group("slug")
        if slug in seen:
            continue
        seen.add(slug)
        out.append({"slug": slug, "detail_url": BASE + m.group("href")})
    return out


def parse_bill_detail(html: str) -> dict[str, Any]:
    h1 = _H1_RE.search(html)
    heading = _strip_tags(h1.group("body")) if h1 else ""
    num_m = _BILL_NUM_TITLE_RE.search(heading)
    bill_number = num_m.group("number").strip() if num_m else None
    title = num_m.group("title").strip() if num_m else heading

    ass = _ASSEMBLY_SESSION_RE.search(html)
    assembly_session_raw = _strip_tags(ass.group("body")) if ass else None
    assembly: Optional[int] = None
    session: Optional[int] = None
    if assembly_session_raw:
        asm = re.search(
            r"(\d+)(?:st|nd|rd|th)?\s+Assembly,\s*(\d+)(?:st|nd|rd|th)?\s+Session",
            assembly_session_raw, re.IGNORECASE,
        )
        if asm:
            assembly = int(asm.group(1))
            session = int(asm.group(2))

    events: list[dict[str, Any]] = []
    for sm in _STAGE_BLOCK_RE.finditer(html):
        field = sm.group("field")
        state = sm.group("state").lower()
        if state != "completed":
            continue
        mapping = _FIELD_MAP.get(field)
        if mapping is None:
            continue
        stage, committee_name, event_type = mapping
        label = _strip_tags(sm.group("label"))
        status_text = _strip_tags(sm.group("status"))
        event_date = _parse_date(status_text)
        if event_date is None:
            continue
        events.append({
            "field": field,
            "stage": stage,
            "stage_label": label,
            "event_date": event_date,
            "committee_name": committee_name,
            "event_type": event_type,
        })

    return {
        "bill_number":        bill_number,
        "title":              title,
        "assembly":           assembly,
        "session":            session,
        "assembly_session":   assembly_session_raw,
        "events":             events,
    }


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
        VALUES ('provincial', 'NT', $1, $2, $3, $4, $5)
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
    bill: dict, detail_url: str,
) -> tuple[str, int]:
    bill_number = bill["bill_number"] or "?"
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
        VALUES ($1, 'provincial', 'NT', $2, $3, $4, $5, $6,
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
        source_id, SOURCE_SYSTEM, detail_url,
        orjson.dumps({
            "assembly_session": bill["assembly_session"],
        }).decode(),
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
            orjson.dumps({"source": SOURCE_SYSTEM, "field": ev["field"]}).decode(),
        )
        written += 1
    return bill_id, written


# ─────────────────────────────────────────────────────────────────────
# Orchestrator
# ─────────────────────────────────────────────────────────────────────

async def ingest_nt_bills(
    db: Database, *, delay_seconds: float = 1.5,
) -> dict[str, int]:
    """Ingest NT bills from the current-session list + per-bill detail pages.

    One list GET + one GET per bill. Delay is per-bill to be polite.
    No sponsor data is ingested (NT doesn't publish sponsor info on
    public pages — consensus government, no partisan sponsor).
    """
    stats = {"sessions_touched": 0, "bills": 0, "events": 0}
    session_ids: dict[tuple[int, int], str] = {}

    async with httpx.AsyncClient(headers=HEADERS, follow_redirects=True) as client:
        r = await client.get(LIST_URL, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        bills = parse_bill_list(r.text)
        log.info("ingest_nt_bills: list → %d bills", len(bills))

        for i, b in enumerate(bills):
            if i > 0 and delay_seconds > 0:
                await asyncio.sleep(delay_seconds)
            try:
                dr = await client.get(b["detail_url"], timeout=REQUEST_TIMEOUT)
                dr.raise_for_status()
            except httpx.HTTPError as e:
                log.warning("nt detail fetch %s: %s", b["detail_url"], e)
                continue
            parsed = parse_bill_detail(dr.text)
            if parsed["assembly"] is None or parsed["session"] is None:
                log.warning(
                    "nt bill %s missing assembly/session — skipping",
                    b["slug"],
                )
                continue

            key = (parsed["assembly"], parsed["session"])
            if key not in session_ids:
                session_ids[key] = await _upsert_session(
                    db, assembly=key[0], session=key[1],
                )
                stats["sessions_touched"] += 1

            _, ev_w = await _upsert_bill_with_events(
                db,
                session_id=session_ids[key],
                assembly=parsed["assembly"], session=parsed["session"],
                bill=parsed, detail_url=b["detail_url"],
            )
            stats["bills"] += 1
            stats["events"] += ev_w

    log.info("ingest_nt_bills: %s", stats)
    return stats
