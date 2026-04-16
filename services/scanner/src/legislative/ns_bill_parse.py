"""Parse cached nslegislature.ca bill HTML into structured fields.

Phase 3 of the NS bills pipeline (fetcher is phase 2). Reads
``bills.raw_html`` and populates:

  * ``bill_sponsors`` — sponsor name, profile slug, ministerial role
  * ``bill_events``    — full stage history (first reading, second
                         reading debates, second reading passed,
                         law amendments committee, third reading,
                         royal assent, withdrawn, ...)

Pure offline: never touches the network. Safe to re-run any number of
times; both tables are idempotent via unique indexes.

Parser strategy: regex over the known markup patterns. The codebase
convention is stdlib-only HTML parsing (see gap_fillers/ontario.py)
so we stay consistent rather than adding a BeautifulSoup dep.
"""
from __future__ import annotations

import logging
import re
from datetime import date, datetime
from html import unescape
from typing import Iterable, Optional

import orjson

from ..db import Database

log = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────
# Regexes — each keyed off a distinctive, stable marker in the HTML.
#
# Sponsor line example (observed 2026-04):
#   <p>Introduced by Honourable <a href="/members/profiles/jill-balser">
#   Jill Balser</a>, Minister of Service Nova Scotia</p>
#
# Some variants omit "Honourable" (private members' bills); some
# bills have no sponsor block at all in older sessions.
# ─────────────────────────────────────────────────────────────────────
_SPONSOR_RE = re.compile(
    r"<p>\s*Introduced\s+by\s+"
    r"(?:(?P<honorific>Honourable|Hon\.|Mr\.|Mrs\.|Ms\.|Dr\.)\s+)?"
    r"<a\s+href=\"/members/profiles/(?P<slug>[^\"]+)\">"
    r"(?P<name>[^<]+)</a>"
    r"(?:,\s*(?P<role>[^<]+?))?"
    r"\s*</p>",
    re.IGNORECASE,
)

# Event rows look like:
#   <tr class="odd"><td>First Reading</td><td>September 23, 2025 - <a ...></a></td></tr>
#   <tr class="even"><td>Royal Assent</td><td><a ...>October 3, 2025</a></td></tr>
#
# We match any 2-column <tr> whose first cell is a known stage label.
_EVENT_ROW_RE = re.compile(
    r"<tr[^>]*>\s*"
    r"<td[^>]*>\s*(?P<stage>[^<]+?)\s*</td>\s*"
    r"<td[^>]*>(?P<body>.*?)</td>\s*"
    r"</tr>",
    re.IGNORECASE | re.DOTALL,
)

# Month-name date. nslegislature.ca uses "September 23, 2025" style.
_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4,
    "may": 5, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12,
}
_DATE_RE = re.compile(
    r"(?P<month>January|February|March|April|May|June|July|August|"
    r"September|October|November|December)\s+"
    r"(?P<day>\d{1,2}),\s*(?P<year>\d{4})",
    re.IGNORECASE,
)

# Canonical stage vocabulary. Keys are the lower-cased verbatim labels
# from nslegislature.ca; values are our internal enum.
_STAGE_MAP = {
    "first reading":                "first_reading",
    "second reading":               "second_reading",
    "second reading debates":       "second_reading",
    "second reading passed":        "second_reading_passed",
    "law amendments committee":     "committee",
    "public bills committee":       "committee",
    "private and local bills":      "committee",
    "private & local bills":        "committee",
    "committee of the whole house": "committee_whole",
    "third reading":                "third_reading",
    "third reading debates":        "third_reading",
    "royal assent":                 "royal_assent",
    "withdrawn":                    "withdrawn",
    "defeated":                     "defeated",
    "introduced":                   "introduced",
}

# Rows whose first-cell label we deliberately ignore — they're the
# layout header rows that repeat the stage vocabulary as a visual key.
_STAGE_KEY_NOISE = {
    "first reading", "second reading", "committee",
    "third reading", "royal assent",
}


def _canon_stage(label: str) -> str:
    key = (label or "").strip().lower()
    return _STAGE_MAP.get(key, "other")


def _parse_event_date(body: str) -> Optional[date]:
    # Strip tags so we can match date text even when wrapped in <a>.
    text = re.sub(r"<[^>]+>", " ", body or "")
    text = unescape(text)
    m = _DATE_RE.search(text)
    if not m:
        return None
    try:
        return date(int(m.group("year")),
                   _MONTHS[m.group("month").lower()],
                   int(m.group("day")))
    except (ValueError, KeyError):
        return None


# ─────────────────────────────────────────────────────────────────────
# Extractors
# ─────────────────────────────────────────────────────────────────────

def extract_sponsor(html: str) -> Optional[dict]:
    """Return ``{name, slug, role}`` or None."""
    m = _SPONSOR_RE.search(html or "")
    if not m:
        return None
    name = unescape(m.group("name")).strip()
    slug = m.group("slug").strip()
    role = (m.group("role") or "").strip() or None
    if role:
        role = unescape(role)
        # Trim stray trailing tags / whitespace that leak in from the
        # non-greedy match boundary.
        role = re.sub(r"\s+", " ", role).strip(",;:. ")
    return {"name": name, "slug": slug, "role": role}


def extract_events(html: str) -> list[dict]:
    """Return a list of ``{stage, stage_label, event_date}`` dicts.

    Deduplicates on (canonical_stage, event_date) within a single bill —
    the rendered page sometimes repeats a row in multiple visual blocks.
    """
    out: list[dict] = []
    seen: set[tuple[str, Optional[date]]] = set()
    for m in _EVENT_ROW_RE.finditer(html or ""):
        raw_stage = unescape(m.group("stage")).strip()
        low = raw_stage.lower()
        # Skip the header-row "key" that lists every stage without dates.
        if low in _STAGE_KEY_NOISE and not _DATE_RE.search(m.group("body")):
            continue
        # Ignore rows with no recognisable stage label.
        stage = _canon_stage(raw_stage)
        if stage == "other":
            continue
        event_date = _parse_event_date(m.group("body"))
        key = (stage, event_date)
        if key in seen:
            continue
        seen.add(key)
        out.append({
            "stage": stage,
            "stage_label": raw_stage,
            "event_date": event_date,
        })
    return out


# ─────────────────────────────────────────────────────────────────────
# Persisters
# ─────────────────────────────────────────────────────────────────────

async def _persist_sponsor(db: Database, bill_id: str, sponsor: dict) -> int:
    # Unique index uq_bill_sponsors_slug makes this idempotent.
    r = await db.execute(
        """
        INSERT INTO bill_sponsors (
            bill_id, sponsor_name_raw, sponsor_slug, sponsor_role,
            role, source_system
        )
        VALUES ($1, $2, $3, $4, 'sponsor', 'nslegislature-html')
        ON CONFLICT (bill_id, sponsor_slug)
          WHERE sponsor_slug IS NOT NULL
          DO UPDATE SET
              sponsor_name_raw = EXCLUDED.sponsor_name_raw,
              sponsor_role     = EXCLUDED.sponsor_role
        """,
        bill_id, sponsor["name"], sponsor["slug"], sponsor["role"],
    )
    # asyncpg execute() returns "INSERT 0 1" on insert; treat any
    # non-empty result as one row touched.
    return 1 if r else 0


async def _persist_events(db: Database, bill_id: str, events: list[dict]) -> int:
    written = 0
    for ev in events:
        if ev["event_date"] is None:
            # bill_events.event_date is part of the uniqueness key; rows
            # without a date would collide on (bill_id, stage, NULL).
            # Skip — parser will pick them up once the HTML reveals a date.
            continue
        await db.execute(
            """
            INSERT INTO bill_events (
                bill_id, stage, stage_label, event_date, raw
            )
            VALUES ($1, $2, $3, $4, $5::jsonb)
            ON CONFLICT ON CONSTRAINT bill_events_uniq DO NOTHING
            """,
            bill_id, ev["stage"], ev["stage_label"], ev["event_date"],
            orjson.dumps({"source": "nslegislature-html"}).decode(),
        )
        written += 1
    return written


# ─────────────────────────────────────────────────────────────────────
# Orchestrator
# ─────────────────────────────────────────────────────────────────────

async def parse_ns_bill_pages(
    db: Database, *, limit: Optional[int] = None
) -> dict[str, int]:
    """Parse every bill that has cached HTML but no parsed sponsor yet.

    ``bill_sponsors`` presence is the "already parsed" marker — bills
    with a populated sponsor row are skipped on subsequent runs. Pass
    ``limit`` for smoke tests.
    """
    sql = """
        SELECT b.id, b.raw_html
          FROM bills b
          LEFT JOIN bill_sponsors s ON s.bill_id = b.id
         WHERE b.raw_html IS NOT NULL
           AND s.id IS NULL
         ORDER BY b.status_changed_at DESC NULLS LAST
    """
    if limit is not None:
        sql += f" LIMIT {int(limit)}"
    rows = await db.fetch(sql)

    stats = {"bills": 0, "sponsors": 0, "events": 0, "no_sponsor": 0}
    for row in rows:
        bill_id = str(row["id"])
        html = row["raw_html"]
        stats["bills"] += 1

        sponsor = extract_sponsor(html)
        if sponsor:
            stats["sponsors"] += await _persist_sponsor(db, bill_id, sponsor)
        else:
            stats["no_sponsor"] += 1

        events = extract_events(html)
        stats["events"] += await _persist_events(db, bill_id, events)

    log.info(
        "ns_bill_parse: bills=%d sponsors=%d events=%d no_sponsor=%d",
        stats["bills"], stats["sponsors"], stats["events"], stats["no_sponsor"],
    )
    return stats
