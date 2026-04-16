"""British Columbia bills pipeline — LIMS PDMS/GraphQL hybrid.

Discovery path (user-led, 2026-04-15):
  - `lims.leg.bc.ca/graphql` (public GraphQL, no auth) → session IDs,
    member IDs, parliament metadata
  - `lims.leg.bc.ca/pdms/bills/progress-of-bills/{sessionId}` → JSON
    array of bills for that session, with integer `memberId` sponsor
    references

Because BC gives us integer sponsor IDs that join to `politicians.
lims_member_id`, resolution is an exact FK lookup — no slug or name
fuzz needed. To make that work we ingest the LIMS member roster at
the top of the pipeline and write `lims_member_id` back onto existing
politician rows (matching by name within level='provincial' +
province='BC').

Pipeline:
  phase 1 — enrich politicians.lims_member_id from LIMS GraphQL
  phase 2 — upsert legislative_sessions for every BC session via GraphQL
  phase 3 — per session, fetch PDMS progress-of-bills → upsert bills,
            bill_events (per reading date), bill_sponsors (direct
            politician_id via memberId→lims_member_id join)

Current scope: current session (43rd Parliament, 2nd Session = LIMS
session ID 206). Historical backfill is safe to run anytime — PDMS
serves every session back to the 1800s.
"""
from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any, Optional

import httpx
import orjson

from ..db import Database

log = logging.getLogger(__name__)

GRAPHQL_URL = "https://lims.leg.bc.ca/graphql"
PDMS_BILLS_URL = "https://lims.leg.bc.ca/pdms/bills/progress-of-bills/{session_id}"
SOURCE_SYSTEM = "lims-bc"

REQUEST_TIMEOUT = 60
HEADERS = {
    "User-Agent": "SovereignWatch/1.0 (civic-transparency; +admin@thebunkerops.ca)",
    "Accept": "application/json",
    "Origin": "https://dyn.leg.bc.ca",
}


# ─────────────────────────────────────────────────────────────────────
# Phase 1 — member id enrichment from GraphQL
# ─────────────────────────────────────────────────────────────────────

_MEMBERS_QUERY = """
query {
  allMembers {
    nodes { id firstName lastName active }
  }
}
"""


async def enrich_bc_member_ids(db: Database) -> dict[str, int]:
    """Populate politicians.lims_member_id for BC MLAs via name match.

    GraphQL returns every member in BC history. We match active
    provincial BC politicians by normalized name and store the id.
    """
    async with httpx.AsyncClient(headers=HEADERS, follow_redirects=True) as client:
        r = await client.post(
            GRAPHQL_URL, json={"query": _MEMBERS_QUERY}, timeout=REQUEST_TIMEOUT,
        )
        r.raise_for_status()
        data = r.json()

    members = data.get("data", {}).get("allMembers", {}).get("nodes", []) or []
    log.info("enrich_bc_member_ids: LIMS returned %d members", len(members))

    # Our politicians table has current BC MLAs. Match on normalized
    # name pairs. A single shared imports _norm — keep it local to
    # avoid a cycle with sponsor_resolver.
    def _norm(s: str) -> str:
        import re, unicodedata
        s = unicodedata.normalize("NFKD", s or "")
        s = "".join(c for c in s if not unicodedata.combining(c))
        return re.sub(r"\s+", " ", re.sub(r"[^a-zA-Z\s]", " ", s.lower())).strip()

    # Build a lookup: (first+last normalized) → lims id.
    lims_by_name: dict[str, int] = {}
    for m in members:
        if m.get("active") is False:
            # Skip retired members — two MLAs can share a name across
            # decades. Active-only keeps the name→id map unambiguous
            # for current-bills sponsor resolution.
            continue
        first = m.get("firstName") or ""
        last = m.get("lastName") or ""
        key = _norm(f"{first} {last}")
        if key:
            lims_by_name[key] = int(m["id"])

    pol_rows = await db.fetch(
        """
        SELECT id, name, first_name, last_name
          FROM politicians
         WHERE level = 'provincial'
           AND province_territory = 'BC'
           AND is_active = true
           AND lims_member_id IS NULL
        """
    )
    stats = {"politicians_scanned": len(pol_rows), "linked": 0, "ambiguous": 0}
    for p in pol_rows:
        key_full = _norm(p["name"])
        key_fl = _norm(f"{p['first_name'] or ''} {p['last_name'] or ''}")
        lims_id = lims_by_name.get(key_full) or lims_by_name.get(key_fl)
        if lims_id is None:
            stats["ambiguous"] += 1
            continue
        await db.execute(
            "UPDATE politicians SET lims_member_id = $2, updated_at = now() WHERE id = $1",
            str(p["id"]), lims_id,
        )
        stats["linked"] += 1

    log.info(
        "enrich_bc_member_ids: scanned=%d linked=%d ambiguous=%d",
        stats["politicians_scanned"], stats["linked"], stats["ambiguous"],
    )
    return stats


# ─────────────────────────────────────────────────────────────────────
# Phase 2 — sessions from GraphQL
# ─────────────────────────────────────────────────────────────────────

_SESSIONS_QUERY = """
query {
  allSessions(orderBy: ID_DESC) {
    nodes { id number parliamentId startDate endDate }
  }
}
"""


async def _upsert_bc_session(
    db: Database, *, parliament: int, session: int,
    start_date: Optional[str], end_date: Optional[str],
    lims_session_id: int,
) -> str:
    row = await db.fetchrow(
        """
        INSERT INTO legislative_sessions (
            level, province_territory, parliament_number, session_number,
            name, start_date, end_date, source_system, source_url
        )
        VALUES ('provincial', 'BC', $1, $2, $3, $4, $5, $6, $7)
        ON CONFLICT (level, province_territory, parliament_number, session_number)
        DO UPDATE SET
            start_date = COALESCE(EXCLUDED.start_date, legislative_sessions.start_date),
            end_date   = COALESCE(EXCLUDED.end_date, legislative_sessions.end_date),
            updated_at = now()
        RETURNING id
        """,
        parliament, session,
        f"{parliament}th Parliament, {session}{'st' if session == 1 else 'nd' if session == 2 else 'rd' if session == 3 else 'th'} Session",
        _parse_date(start_date), _parse_date(end_date),
        SOURCE_SYSTEM,
        PDMS_BILLS_URL.format(session_id=lims_session_id),
    )
    return str(row["id"])


# ─────────────────────────────────────────────────────────────────────
# Phase 3 — bills from PDMS
# ─────────────────────────────────────────────────────────────────────

# Map PDMS reading-date field → our bill_events stage vocabulary.
_READING_FIELDS: list[tuple[str, str, str]] = [
    # (field_name, canonical_stage, stage_label)
    ("firstReading",     "first_reading",  "First Reading"),
    ("secondReading",    "second_reading", "Second Reading"),
    ("committeeReading", "committee",      "Committee"),
    ("reportReading",    "report",         "Report Reading"),
    ("thirdReading",     "third_reading",  "Third Reading"),
    ("amendedReading",   "amended",        "Amended Reading"),
    ("royalAssent",      "royal_assent",   "Royal Assent"),
]


def _parse_date(s: Optional[str]) -> Optional[date]:
    if not s:
        return None
    try:
        return datetime.strptime(s[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def _latest_stage(bill: dict) -> tuple[Optional[str], Optional[date]]:
    """Derive current status + most-recent stage date."""
    latest: tuple[Optional[str], Optional[date]] = (None, None)
    for field, stage, label in _READING_FIELDS:
        d = _parse_date(bill.get(field))
        if d is not None and (latest[1] is None or d >= latest[1]):
            latest = (label, d)
    return latest


async def _upsert_bc_bill(
    db: Database, *, session_id: str, bill: dict
) -> Optional[str]:
    bill_number = str(bill.get("billNumber") or "").strip()
    if not bill_number:
        return None
    title = bill.get("title") or f"Bill {bill_number}"
    lims_bill_id = bill.get("billId")
    source_id = f"{SOURCE_SYSTEM}:session-{bill.get('__session_id')}:bill-{bill_number}"

    current_status, status_changed = _latest_stage(bill)
    introduced = _parse_date(bill.get("firstReading"))

    row = await db.fetchrow(
        """
        INSERT INTO bills (
            session_id, level, province_territory, bill_number,
            title, short_title, status, status_changed_at, introduced_date,
            source_id, source_system, source_url, raw, last_fetched_at
        )
        VALUES ($1, 'provincial', 'BC', $2, $3, $3, $4, $5, $6,
                $7, $8, $9, $10::jsonb, now())
        ON CONFLICT (source_id) DO UPDATE SET
            title             = EXCLUDED.title,
            short_title       = EXCLUDED.short_title,
            status            = EXCLUDED.status,
            status_changed_at = EXCLUDED.status_changed_at,
            introduced_date   = COALESCE(EXCLUDED.introduced_date, bills.introduced_date),
            source_url        = EXCLUDED.source_url,
            raw               = EXCLUDED.raw,
            last_fetched_at   = now(),
            updated_at        = now()
        RETURNING id
        """,
        session_id, bill_number, title, current_status, status_changed, introduced,
        source_id, SOURCE_SYSTEM,
        f"https://www.leg.bc.ca/parliamentary-business/bills?billId={lims_bill_id}",
        orjson.dumps(bill).decode(),
    )
    return str(row["id"])


async def _persist_reading_events(db: Database, bill_id: str, bill: dict) -> int:
    written = 0
    for field, stage, label in _READING_FIELDS:
        event_date = _parse_date(bill.get(field))
        if event_date is None:
            continue
        await db.execute(
            """
            INSERT INTO bill_events (
                bill_id, stage, stage_label, event_date, raw
            )
            VALUES ($1, $2, $3, $4, $5::jsonb)
            ON CONFLICT ON CONSTRAINT bill_events_uniq DO NOTHING
            """,
            bill_id, stage, label, event_date,
            orjson.dumps({"source": SOURCE_SYSTEM, "pdms_field": field}).decode(),
        )
        written += 1
    return written


async def _persist_sponsor(db: Database, bill_id: str, bill: dict) -> int:
    lims_member_id = bill.get("memberId")
    if not lims_member_id:
        return 0
    # Resolve directly via the integer FK. No slug, no name fuzz.
    pol_id = await db.fetchval(
        "SELECT id FROM politicians WHERE lims_member_id = $1 "
        "  AND level = 'provincial' AND province_territory = 'BC'",
        int(lims_member_id),
    )
    # We still insert the sponsor row even if unresolved — the integer
    # member id is the upstream identifier, preserved in sponsor_slug
    # (typed as text, so stringify). Name is unknown without another
    # GraphQL call; leave NULL and let a later pass fill it.
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
              politician_id    = COALESCE(EXCLUDED.politician_id, bill_sponsors.politician_id),
              sponsor_name_raw = COALESCE(EXCLUDED.sponsor_name_raw, bill_sponsors.sponsor_name_raw)
        """,
        bill_id,
        pol_id,
        str(lims_member_id),  # store as text to match schema
        bill.get("memberAlias"),
        SOURCE_SYSTEM,
    )
    return 1


# ─────────────────────────────────────────────────────────────────────
# Orchestrator
# ─────────────────────────────────────────────────────────────────────

async def ingest_bc_bills(
    db: Database, *, current_only: bool = True,
    parliament: Optional[int] = None, session: Optional[int] = None,
) -> dict[str, int]:
    """Fetch BC sessions + bills from LIMS and upsert into normalized schema.

    Args:
        current_only: if True (default), only ingest the most recent
            session. Set to False to backfill all historical sessions.
        parliament/session: if set, only ingest that specific session
            (matched against LIMS GraphQL allSessions).
    """
    stats = {"sessions_touched": 0, "bills": 0, "events": 0,
             "sponsors": 0, "sponsors_linked": 0}

    async with httpx.AsyncClient(headers=HEADERS, follow_redirects=True) as client:
        # Pull all sessions.
        r = await client.post(
            GRAPHQL_URL, json={"query": _SESSIONS_QUERY}, timeout=REQUEST_TIMEOUT,
        )
        r.raise_for_status()
        sessions = r.json()["data"]["allSessions"]["nodes"]

        # Filter by args.
        if parliament is not None and session is not None:
            sessions = [
                s for s in sessions
                if s["parliamentId"] == parliament and s["number"] == session
            ]
        elif current_only:
            sessions = sessions[:1]

        for s in sessions:
            lims_session_id = int(s["id"])
            parliament_id = int(s["parliamentId"])
            session_num = int(s["number"])
            local_session_id = await _upsert_bc_session(
                db,
                parliament=parliament_id, session=session_num,
                start_date=s.get("startDate"),
                end_date=s.get("endDate"),
                lims_session_id=lims_session_id,
            )

            r = await client.get(
                PDMS_BILLS_URL.format(session_id=lims_session_id),
                timeout=REQUEST_TIMEOUT,
            )
            r.raise_for_status()
            bills = r.json()
            if not isinstance(bills, list):
                log.warning(
                    "ingest_bc_bills: unexpected PDMS shape for session %d: %r",
                    lims_session_id, type(bills).__name__,
                )
                continue

            stats["sessions_touched"] += 1
            for b in bills:
                # Thread through the session id so source_id is unique
                # across sessions (multiple sessions reuse bill-1, bill-2, etc.)
                b["__session_id"] = lims_session_id
                bill_id = await _upsert_bc_bill(db, session_id=local_session_id, bill=b)
                if bill_id is None:
                    continue
                stats["bills"] += 1
                stats["events"] += await _persist_reading_events(db, bill_id, b)
                added = await _persist_sponsor(db, bill_id, b)
                stats["sponsors"] += added
                if added and b.get("memberId"):
                    # Was this sponsor row actually linked to a politician?
                    linked = await db.fetchval(
                        "SELECT politician_id IS NOT NULL FROM bill_sponsors "
                        "WHERE bill_id = $1 AND sponsor_slug = $2",
                        bill_id, str(b["memberId"]),
                    )
                    if linked:
                        stats["sponsors_linked"] += 1

    log.info("ingest_bc_bills: %s", stats)
    return stats
