"""New Brunswick bills pipeline — legnb.ca HTML scrape.

Two-step server-rendered HTML scrape:

1. **List page** (`/en/legislation/bills/{legl}/{session}`) — enumerates
   every bill for the session. Each ``<a href=".../{n}/{slug}">`` is a
   bill, with number + title + slug visible inline.

2. **Detail page** (`/en/legislation/bills/{legl}/{session}/{n}/{slug}`) —
   per-bill rich data:
     - Bill Type (Government / Private / Private Member)
     - Status (= latest stage reached)
     - **Sponsor** as a ``<div class="member-card">`` with MLA name +
       party + constituency. No numeric member id surfaced; sponsor
       resolution is name-based against ``politicians.name``.
     - **Progression Timeline** — a ``<ul id="legislation-timeline">``
       with one ``<li class="timeline-segment">`` per stage, each
       containing one or more events with date + action label
       ("Introduced", "Passed", "Adjourned", "Reported with
       amendments", etc.).

Both pages are server-rendered HTML, no WAF, no JS. The list scrape
gives us URLs; the detail scrape gives us everything else. Sponsor
resolution happens inline at ingest time (no deferred resolver pass).

Historical backfill: URL-addressable all the way back to Legislature
52 or earlier — discovered via the ``--all-sessions-in-legislature``
scope.
"""
from __future__ import annotations

import asyncio
import html as _html_lib
import logging
import re
import unicodedata
from datetime import date, datetime
from typing import Any, Optional

import httpx
import orjson

from ..db import Database

log = logging.getLogger(__name__)

SOURCE_SYSTEM = "legnb"
REQUEST_TIMEOUT = 45
HEADERS = {
    "User-Agent": "SovereignWatch/1.0 (civic-transparency; +admin@thebunkerops.ca)",
    "Accept": "text/html,application/xhtml+xml",
}

BASE = "https://www.legnb.ca"
LIST_URL = BASE + "/en/legislation/bills/{legl}/{session}"

# Stage-name → (canonical, display). NB uses standard Westminster
# terminology on the detail page, so mapping is straightforward.
_STAGE_MAP: dict[str, tuple[str, str]] = {
    "first reading":  ("first_reading",  "First Reading"),
    "second reading": ("second_reading", "Second Reading"),
    "committee":      ("committee",      "Committee"),
    "third reading":  ("third_reading",  "Third Reading"),
    "royal assent":   ("royal_assent",   "Royal Assent"),
}

_BILL_TYPE_MAP = {
    "government bill":               "government",
    "private member's public bill":  "private_member",
    "private bill":                  "private",
}

# Bill list URLs on the list page. Capture (number, slug) so we can
# re-construct the detail URL deterministically.
_LIST_HREF_RE = re.compile(
    r'href="(?P<href>/en/legislation/bills/(?P<legl>\d+)/'
    r'(?P<session>\d+)/(?P<number>\d+)/(?P<slug>[^"/]+))"',
    re.IGNORECASE,
)

# Member-card name lives in a <h3> inside the sponsor block. The card
# text "Hon. Susan HOLT" uses uppercase surname; normalize at match
# time rather than pre-stripping.
_SPONSOR_NAME_RE = re.compile(
    r'<div\s+class="tabling-member"[^>]*>.*?'
    r'<h3>\s*(?P<name>[^<]+?)\s*</h3>',
    re.IGNORECASE | re.DOTALL,
)

_SPONSOR_PARTY_RE = re.compile(
    r'class="member-card-description-party"[^>]*>'
    r'(?:\s*<[^>]+>)*\s*(?P<party>[^<]+?)\s*</li>',
    re.IGNORECASE | re.DOTALL,
)

_SPONSOR_RIDING_RE = re.compile(
    r'class="member-card-description-riding"[^>]*>'
    r'(?:\s*<[^>]+>)*\s*(?P<riding>[^<]+?)\s*</li>',
    re.IGNORECASE | re.DOTALL,
)

# The heading is:
#     Bill No. 1</span>An Act to Perpetuate a Certain Ancient Right
#     </h1>
# so the bill number sits BEFORE the closing </span> and the title sits
# between the span close and the h1 close. Grab each separately rather
# than trying to make the wrapping-tag structure part of the pattern.
_HEADING_NUM_RE = re.compile(
    r"Bill\s*No\.\s*(?P<number>\S+?)\s*</span>",
    re.IGNORECASE,
)
_HEADING_TITLE_RE = re.compile(
    r"Bill\s*No\.[^<]*</span>\s*(?P<title>[^<]+?)\s*</h1>",
    re.IGNORECASE | re.DOTALL,
)

# Two property-label fields we want: "Bill Type" and "Status".
def _extract_property(label: str, html: str) -> Optional[str]:
    pat = re.compile(
        rf'<span\s+class="property-label"[^>]*>\s*{re.escape(label)}\s*</span>'
        r'\s*(?P<value>[^<]+?)\s*<',
        re.IGNORECASE,
    )
    m = pat.search(html)
    return m.group("value").strip() if m else None


# Timeline segment + events
_SEGMENT_RE = re.compile(
    r'<li\s+class="timeline-segment"[^>]*>(?P<body>.*?)'
    r'(?=<li\s+class="timeline-segment"|</ul>)',
    re.IGNORECASE | re.DOTALL,
)

_SEGMENT_HEADER_RE = re.compile(
    r'class="timeline-segment-header"[^>]*>(?:.*?<h4[^>]*>)'
    r'\s*(?:<[^>]+>\s*)*(?P<name>[^<]+?)\s*(?:<[^>]+>\s*)*</h4>',
    re.IGNORECASE | re.DOTALL,
)

_EVENT_RE = re.compile(
    r'<li\s+class="timeline-segment-event"[^>]*>(?P<body>.*?)</li>',
    re.IGNORECASE | re.DOTALL,
)

_EVENT_DATE_RE = re.compile(
    r'class="timeline-segment-event-date"[^>]*>\s*<span>\s*'
    r'(?P<date>[^<]+?)\s*</span>',
    re.IGNORECASE | re.DOTALL,
)

_EVENT_ACTION_RE = re.compile(
    r'class="timeline-segment-event-action[^"]*"[^>]*>\s*'
    r'(?P<action>[^<]+?)\s*</span>',
    re.IGNORECASE | re.DOTALL,
)


# ─────────────────────────────────────────────────────────────────────
# Text helpers
# ─────────────────────────────────────────────────────────────────────

def _strip_tags(s: str) -> str:
    cleaned = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", s or "")).strip()
    return _html_lib.unescape(cleaned)


def _parse_date(s: str) -> Optional[date]:
    s = (s or "").strip().rstrip(",")
    for fmt in ("%B %d, %Y", "%b %d, %Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


_HONORIFICS_RE = re.compile(
    r"\b(?:hon\.?|honourable|honorable|mr\.?|mrs\.?|ms\.?|miss\.?|dr\.?|"
    r"premier|minister|speaker|deputy)\b",
    re.IGNORECASE,
)


def _norm_name(s: str) -> str:
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = _HONORIFICS_RE.sub(" ", s)
    s = s.lower()
    s = re.sub(r"[^a-z\s\-]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _clean_display_name(raw: str) -> str:
    """Convert "Hon. Susan HOLT" → "Susan Holt" (title-case surname)."""
    stripped = _HONORIFICS_RE.sub(" ", raw or "").strip()
    return re.sub(r"\s+", " ", " ".join(
        w.capitalize() if w.isupper() else w
        for w in stripped.split()
    )).strip()


# ─────────────────────────────────────────────────────────────────────
# Parsers
# ─────────────────────────────────────────────────────────────────────

def parse_list_page(html: str) -> list[dict[str, Any]]:
    """Extract bills (number + slug + detail URL) from a list page."""
    seen: set[tuple[str, str]] = set()
    bills: list[dict[str, Any]] = []
    for m in _LIST_HREF_RE.finditer(html):
        key = (m.group("number"), m.group("slug"))
        if key in seen:
            continue
        seen.add(key)
        bills.append({
            "legislature": int(m.group("legl")),
            "session":     int(m.group("session")),
            "bill_number": m.group("number"),
            "slug":        m.group("slug"),
            "detail_url":  BASE + m.group("href"),
        })
    return bills


def parse_bill_detail(html: str) -> dict[str, Any]:
    """Extract sponsor + progression from a bill detail page."""
    title_m = _HEADING_TITLE_RE.search(html)
    title = _strip_tags(title_m.group("title")) if title_m else None

    bill_type_raw = _extract_property("Bill Type", html)
    status_raw    = _extract_property("Status", html)

    sponsor_name_m = _SPONSOR_NAME_RE.search(html)
    sponsor_name_raw = _strip_tags(sponsor_name_m.group("name")) if sponsor_name_m else None
    sponsor_party = (
        _strip_tags(_SPONSOR_PARTY_RE.search(html).group("party"))
        if _SPONSOR_PARTY_RE.search(html) else None
    )
    sponsor_riding = (
        _strip_tags(_SPONSOR_RIDING_RE.search(html).group("riding"))
        if _SPONSOR_RIDING_RE.search(html) else None
    )

    events: list[dict[str, Any]] = []
    for seg in _SEGMENT_RE.finditer(html):
        seg_body = seg.group("body")
        h4 = _SEGMENT_HEADER_RE.search(seg_body)
        stage_name = _strip_tags(h4.group("name")) if h4 else None
        canon, label = _STAGE_MAP.get(
            (stage_name or "").strip().lower(),
            ("other", stage_name or ""),
        )
        for ev in _EVENT_RE.finditer(seg_body):
            ev_body = ev.group("body")
            d = _EVENT_DATE_RE.search(ev_body)
            a = _EVENT_ACTION_RE.search(ev_body)
            event_date = _parse_date(d.group("date")) if d else None
            action = _strip_tags(a.group("action")) if a else None
            if event_date is None:
                continue
            events.append({
                "stage": canon,
                "stage_label": label,
                "stage_raw": stage_name,
                "event_date": event_date,
                "action": action,
            })

    return {
        "title":             title,
        "bill_type":         _BILL_TYPE_MAP.get(
                                (bill_type_raw or "").strip().lower(),
                                None,
                             ),
        "bill_type_raw":     bill_type_raw,
        "status":            status_raw,
        "sponsor_name_raw":  sponsor_name_raw,
        "sponsor_display":   _clean_display_name(sponsor_name_raw or ""),
        "sponsor_party":     sponsor_party,
        "sponsor_riding":    sponsor_riding,
        "events":            events,
    }


# ─────────────────────────────────────────────────────────────────────
# DB writers
# ─────────────────────────────────────────────────────────────────────

async def _upsert_session(
    db: Database, *, legislature: int, session: int,
) -> str:
    row = await db.fetchrow(
        """
        INSERT INTO legislative_sessions (
            level, province_territory, parliament_number, session_number,
            name, source_system, source_url
        )
        VALUES ('provincial', 'NB', $1, $2, $3, $4, $5)
        ON CONFLICT (level, province_territory, parliament_number, session_number)
        DO UPDATE SET
            name          = COALESCE(legislative_sessions.name, EXCLUDED.name),
            source_system = COALESCE(legislative_sessions.source_system, EXCLUDED.source_system),
            source_url    = COALESCE(legislative_sessions.source_url, EXCLUDED.source_url),
            updated_at    = now()
        RETURNING id
        """,
        legislature, session,
        f"{legislature}th Legislature, {session}{'st' if session == 1 else 'nd' if session == 2 else 'rd' if session == 3 else 'th'} Session",
        SOURCE_SYSTEM,
        LIST_URL.format(legl=legislature, session=session),
    )
    return str(row["id"])


async def _resolve_sponsor_id(
    db: Database, *, display_name: str, riding: Optional[str],
) -> Optional[str]:
    if not display_name:
        return None
    target = _norm_name(display_name)
    if not target:
        return None

    rows = await db.fetch(
        """
        SELECT id, name, first_name, last_name, constituency_name
          FROM politicians
         WHERE level = 'provincial'
           AND province_territory = 'NB'
           AND is_active = true
        """
    )
    matches: list[dict] = []
    for r in rows:
        norm_full = _norm_name(r["name"] or "")
        norm_fl = _norm_name(f"{r['first_name'] or ''} {r['last_name'] or ''}")
        if target == norm_full or target == norm_fl:
            matches.append(r)

    if len(matches) == 1:
        return str(matches[0]["id"])
    if len(matches) > 1 and riding:
        r_norm = _norm_name(riding)
        riding_hits = [
            m for m in matches
            if _norm_name(m.get("constituency_name") or "") == r_norm
        ]
        if len(riding_hits) == 1:
            return str(riding_hits[0]["id"])
    return None


async def _upsert_bill_and_events(
    db: Database, *, session_id: str, legislature: int, session: int,
    bill_number: str, slug: str, detail_url: str, parsed: dict,
    raw_html: Optional[str] = None,
) -> tuple[str, int, int, int]:
    """Returns (bill_id, events_written, sponsors_written, sponsors_linked)."""

    # Current status + latest-stage date.
    latest_date: Optional[date] = None
    introduced: Optional[date] = None
    for ev in parsed["events"]:
        if ev["event_date"] is None:
            continue
        if (
            ev["stage"] == "first_reading"
            and (ev.get("action") or "").strip().lower() == "introduced"
            and introduced is None
        ):
            introduced = ev["event_date"]
        if latest_date is None or ev["event_date"] >= latest_date:
            latest_date = ev["event_date"]
    status_changed_at = (
        datetime.combine(latest_date, datetime.min.time())
        if latest_date else None
    )

    source_id = f"{SOURCE_SYSTEM}:{legislature}-{session}:bill-{bill_number}"
    title = parsed.get("title") or f"Bill {bill_number}"

    row = await db.fetchrow(
        """
        INSERT INTO bills (
            session_id, level, province_territory, bill_number,
            title, bill_type, status, status_changed_at, introduced_date,
            source_id, source_system, source_url, raw, last_fetched_at,
            raw_html, html_fetched_at
        )
        VALUES ($1, 'provincial', 'NB', $2, $3, $4, $5, $6, $7,
                $8, $9, $10, $11::jsonb, now(),
                $12::text,
                CASE WHEN $12::text IS NULL THEN NULL ELSE now() END)
        ON CONFLICT (source_id) DO UPDATE SET
            title             = EXCLUDED.title,
            bill_type         = COALESCE(EXCLUDED.bill_type, bills.bill_type),
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
            raw_html          = COALESCE(EXCLUDED.raw_html, bills.raw_html),
            html_fetched_at   = CASE
                WHEN EXCLUDED.raw_html IS NOT NULL THEN now()
                ELSE bills.html_fetched_at
            END,
            last_fetched_at   = now(),
            updated_at        = now()
        RETURNING id
        """,
        session_id, bill_number, title, parsed.get("bill_type"),
        parsed.get("status"), status_changed_at, introduced,
        source_id, SOURCE_SYSTEM, detail_url,
        orjson.dumps({
            "slug": slug,
            "bill_type_raw": parsed.get("bill_type_raw"),
            "sponsor_display": parsed.get("sponsor_display"),
            "sponsor_party":   parsed.get("sponsor_party"),
            "sponsor_riding":  parsed.get("sponsor_riding"),
        }).decode(),
        raw_html,
    )
    bill_id = str(row["id"])

    events_written = 0
    for ev in parsed["events"]:
        if ev["stage"] == "other" or ev["event_date"] is None:
            continue
        await db.execute(
            """
            INSERT INTO bill_events (
                bill_id, stage, stage_label, event_date,
                event_type, outcome, raw
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb)
            ON CONFLICT ON CONSTRAINT bill_events_uniq DO NOTHING
            """,
            bill_id, ev["stage"], ev["stage_label"], ev["event_date"],
            ev.get("action"), ev.get("action"),
            orjson.dumps({"source": SOURCE_SYSTEM, "stage_raw": ev["stage_raw"]}).decode(),
        )
        events_written += 1

    sponsors_written = 0
    sponsors_linked = 0
    if parsed.get("sponsor_name_raw"):
        pol_id = await _resolve_sponsor_id(
            db,
            display_name=parsed["sponsor_display"],
            riding=parsed.get("sponsor_riding"),
        )
        await db.execute(
            """
            INSERT INTO bill_sponsors (
                bill_id, politician_id, sponsor_name_raw,
                role, source_system
            )
            VALUES ($1, $2, $3, 'sponsor', $4)
            ON CONFLICT (bill_id, sponsor_name_raw)
              WHERE sponsor_slug IS NULL AND sponsor_name_raw IS NOT NULL
              DO UPDATE SET
                  politician_id = COALESCE(EXCLUDED.politician_id, bill_sponsors.politician_id)
            """,
            bill_id, pol_id,
            parsed["sponsor_display"] or parsed["sponsor_name_raw"],
            SOURCE_SYSTEM,
        )
        sponsors_written = 1
        if pol_id:
            sponsors_linked = 1

    return bill_id, events_written, sponsors_written, sponsors_linked


# ─────────────────────────────────────────────────────────────────────
# Orchestrator
# ─────────────────────────────────────────────────────────────────────

async def _ingest_one_session(
    db: Database, *, client: httpx.AsyncClient,
    legislature: int, session: int, delay_seconds: float,
) -> dict[str, int]:
    list_url = LIST_URL.format(legl=legislature, session=session)
    r = await client.get(list_url, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    bills = parse_list_page(r.text)
    log.info(
        "ingest_nb_bills: %d-%d list page → %d bills",
        legislature, session, len(bills),
    )

    stats = {"bills": 0, "events": 0, "sponsors": 0, "sponsors_linked": 0}
    if not bills:
        return stats

    session_id = await _upsert_session(
        db, legislature=legislature, session=session,
    )

    for i, bill in enumerate(bills):
        if i > 0 and delay_seconds > 0:
            await asyncio.sleep(delay_seconds)
        try:
            dr = await client.get(bill["detail_url"], timeout=REQUEST_TIMEOUT)
            dr.raise_for_status()
        except httpx.HTTPError as e:
            log.warning("nb detail fetch failed %s: %s", bill["detail_url"], e)
            continue
        parsed = parse_bill_detail(dr.text)
        _, ev_w, sp_w, sp_l = await _upsert_bill_and_events(
            db,
            session_id=session_id,
            legislature=bill["legislature"], session=bill["session"],
            bill_number=bill["bill_number"], slug=bill["slug"],
            detail_url=bill["detail_url"], parsed=parsed,
            raw_html=dr.text,
        )
        stats["bills"] += 1
        stats["events"] += ev_w
        stats["sponsors"] += sp_w
        stats["sponsors_linked"] += sp_l

    return stats


async def ingest_nb_bills(
    db: Database, *,
    legislature: Optional[int] = None,
    session: Optional[int] = None,
    all_sessions_in_legislature: Optional[int] = None,
    delay_seconds: float = 1.5,
) -> dict[str, int]:
    """Ingest New Brunswick bills from legnb.ca.

    Default (no args): current session (latest discovered on /en/legislation/bills).
    """
    totals = {"sessions_touched": 0, "bills": 0, "events": 0,
              "sponsors": 0, "sponsors_linked": 0}

    async with httpx.AsyncClient(headers=HEADERS, follow_redirects=True) as client:
        targets: list[tuple[int, int]] = []
        if legislature is not None and session is not None:
            targets = [(legislature, session)]
        elif all_sessions_in_legislature is not None:
            # The index page's bill-detail links only cover the CURRENT
            # session — historical backfill needs per-(L,S) probing.
            # NB legislatures run up to 5 sessions (Leg 54 had 5). Probe
            # S=1..6 and keep those that return at least one bill link.
            L = all_sessions_in_legislature
            for S in range(1, 7):
                list_url = LIST_URL.format(legl=L, session=S)
                try:
                    r = await client.get(list_url, timeout=REQUEST_TIMEOUT)
                except httpx.HTTPError as e:
                    log.warning("nb probe failed %s: %s", list_url, e)
                    continue
                if r.status_code != 200:
                    continue
                if _LIST_HREF_RE.search(r.text):
                    targets.append((L, S))
            if not targets:
                log.warning(
                    "ingest_nb_bills: no sessions with bills found in legislature %d",
                    L,
                )
                return totals
        else:
            # Default: discover current session from the "all bills" index.
            r = await client.get(
                BASE + "/en/legislation/bills", timeout=REQUEST_TIMEOUT,
            )
            r.raise_for_status()
            pairs = sorted({
                (int(m.group("legl")), int(m.group("session")))
                for m in _LIST_HREF_RE.finditer(r.text)
            }, reverse=True)
            if not pairs:
                log.warning("ingest_nb_bills: no sessions found on index")
                return totals
            targets = [pairs[0]]

        log.info("ingest_nb_bills: %d target session(s)", len(targets))
        for L, S in targets:
            s = await _ingest_one_session(
                db, client=client,
                legislature=L, session=S,
                delay_seconds=delay_seconds,
            )
            totals["sessions_touched"] += 1
            for k in ("bills", "events", "sponsors", "sponsors_linked"):
                totals[k] += s[k]

    log.info("ingest_nb_bills: %s", totals)
    return totals
