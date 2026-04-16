"""Alberta bills pipeline — single-page Assembly Dashboard scrape.

The Alberta Assembly Dashboard is the densest single-page bill roster
we've encountered. **One HTTP GET** of

    /assembly-business/assembly-dashboard?legl={L}&session={S}

returns a ~600 KB server-rendered HTML page containing every bill in
that session, each with:

  - bill number + title
  - sponsor (name + zero-padded numeric ``mid``)
  - type (Government / Private Member / Private)
  - amendments flag + money-bill flag
  - bill-text PDF link
  - full stage history: 1R, 2R, CW, 3R, RA, CF — with dates, pass/
    adjourned status, and Hansard PDF links + page ranges.

That single-page density is load-bearing for us: historical backfill
back to Legislature 1 Session 1 is free (1 GET per session ≈ 137
requests for the whole archive), and there are no per-bill detail
pages to budget for.

Sponsor resolution is an exact FK lookup via
``politicians.ab_assembly_mid`` (populated by
``enrich_ab_mla_ids``) — same leverage as BC's lims_member_id
and QC's qc_assnat_id.

Alberta introduces one canonical stage we haven't seen elsewhere:
**Comes into Force** (``rcf`` — the date a sanctioned act takes
effect, which can lag Royal Assent by weeks or months). Preserved as
a distinct ``bill_events.stage = 'comes_into_force'`` row so UX can
display full legislative→operational lineage.
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

SOURCE_SYSTEM = "ab-assembly"
REQUEST_TIMEOUT = 60
HEADERS = {
    "User-Agent": "SovereignWatch/1.0 (civic-transparency; +admin@thebunkerops.ca)",
    "Accept": "text/html,application/xhtml+xml",
}

DASHBOARD_URL = (
    "https://www.assembly.ab.ca/assembly-business/assembly-dashboard"
    "?legl={legislature}&session={session}"
)

BILL_ANCHOR_URL = (
    "https://www.assembly.ab.ca/assembly-business/assembly-dashboard"
    "?legl={legislature}&session={session}&rx=455&billinfoid={bid}"
    "&anchor=g{bid}#g{bid}"
)

# reading-code → (canonical_stage, display_label)
_STAGE_MAP: dict[str, tuple[str, str]] = {
    "r1r": ("first_reading",    "First Reading"),
    "r2r": ("second_reading",   "Second Reading"),
    "rcw": ("committee",        "Committee of the Whole"),
    "r3r": ("third_reading",    "Third Reading"),
    "rra": ("royal_assent",     "Royal Assent"),
    "rcf": ("comes_into_force", "Comes into Force"),
}

# Normalized bill-type labels.
_BILL_TYPE_MAP = {
    "Government Bills":       "government",
    "Private Bills":          "private",
    "Private Members' Public Bills": "private_member",
    "Private Member's Public Bills": "private_member",
}


# ─────────────────────────────────────────────────────────────────────
# Parsing helpers — regex-only so we don't drag in BS4 as a dep
# ─────────────────────────────────────────────────────────────────────

# Every bill block starts with `<a id="g{billinfoid}"></a>`. We split on
# that anchor and treat each slice up to the next one as a chunk.
_BILL_ANCHOR_RE = re.compile(r'<a\s+id="g(\d+)"[^>]*>\s*</a>', re.IGNORECASE)

# Header row pattern: `Bill&nbsp;{number}` then the title in the next
# sibling `<div>…</div>`. Dotall tolerates the whitespace-soup markup.
_BILL_HEADER_RE = re.compile(
    r'Bill&nbsp;(?P<number>\S+?)\s*</a>\s*</div>\s*'
    r'<div>\s*(?P<title>[^<]+?)\s*</div>',
    re.IGNORECASE | re.DOTALL,
)

# Each `<div class="detail"><div>LABEL</div><div>VALUE</div></div>` pair.
# VALUE can contain anchor tags and nested markup — capture lazily.
_DETAIL_RE = re.compile(
    r'<div\s+class="detail"[^>]*>\s*'
    r'<div>\s*(?P<label>[^<]+?)\s*</div>\s*'
    r'<div>(?P<value>.*?)</div>\s*</div>',
    re.IGNORECASE | re.DOTALL,
)

_SPONSOR_MID_RE = re.compile(r"mid=(?P<mid>\d+)", re.IGNORECASE)

# Stage events: `<div class="b_entry b_{short}">…</div>`. The short code
# (1R/2R/CW/3R/RA/CF) tells us which stage without needing to match the
# inner span's class. Dotall to span lines.
_STAGE_BLOCK_RE = re.compile(
    r'<div\s+class="b_entry\s+b_(?P<short>\w+)"[^>]*>(?P<body>.*?)</div>\s*</div>',
    re.IGNORECASE | re.DOTALL,
)

# Inner stage span, e.g. `<span title="First Reading" class="reading r1r billgt">1R</span>`
_STAGE_INNER_RE = re.compile(
    r'<span\s+title="(?P<title>[^"]+)"\s+class="reading\s+(?P<code>r\w+)[^"]*"',
    re.IGNORECASE,
)

_DATE_DIV_RE = re.compile(
    r'<div\s+class="b_date"[^>]*>(?P<body>.*?)</div>',
    re.IGNORECASE | re.DOTALL,
)

_STATUS_DIV_RE = re.compile(
    r'<div\s+class="b_status"[^>]*>(?P<body>.*?)</div>',
    re.IGNORECASE | re.DOTALL,
)

_HANSARD_HREF_RE = re.compile(
    r'<div\s+class="b_hansard"[^>]*>\s*<a[^>]*href=[\'"](?P<href>[^\'"]+)[\'"][^>]*>'
    r'.*?(?P<page>[\d-]+)\s*</a>',
    re.IGNORECASE | re.DOTALL,
)

_DOC_HREF_RE = re.compile(
    r'<div\s+class="doc_item"[^>]*>\s*<a[^>]*href="(?P<href>[^"]+\.pdf)"',
    re.IGNORECASE,
)

# Dates render as "Oct 23, 2025", often wrapped in an anchor. Strip tags
# and parse.
_TAG_RE = re.compile(r"<[^>]+>")
_AM_PM_RE = re.compile(r"\s+(am|pm)\b", re.IGNORECASE)


def _text(html: str) -> str:
    """Strip tags + collapse whitespace."""
    return re.sub(r"\s+", " ", _TAG_RE.sub("", html or "")).strip()


def _parse_date(s: str) -> Optional[date]:
    """Parse "Oct 23, 2025" (with optional " pm"/" am" trailer)."""
    if not s:
        return None
    s = _AM_PM_RE.sub("", s).strip().rstrip(",")
    for fmt in ("%b %d, %Y", "%B %d, %Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _parse_details(chunk: str) -> dict[str, str]:
    """Extract {label: raw-value-html} from the accordion body."""
    return {m.group("label").strip(): m.group("value")
            for m in _DETAIL_RE.finditer(chunk)}


def _parse_stage_event(short: str, body: str) -> Optional[dict[str, Any]]:
    inner = _STAGE_INNER_RE.search(body)
    if not inner:
        return None
    code = inner.group("code").lower()
    display_title = inner.group("title")

    date_m = _DATE_DIV_RE.search(body)
    date_raw = _text(date_m.group("body")) if date_m else ""
    event_date = _parse_date(date_raw)

    status_m = _STATUS_DIV_RE.search(body)
    status = _text(status_m.group("body")) if status_m else ""

    hansard = _HANSARD_HREF_RE.search(body)
    hansard_href = hansard.group("href") if hansard else None
    hansard_page = hansard.group("page").strip() if hansard else None

    canonical, label = _STAGE_MAP.get(code, ("other", display_title))
    return {
        "short": short,
        "code": code,
        "canonical": canonical,
        "label": label,
        "display_title": display_title,
        "event_date": event_date,
        "event_date_raw": date_raw,
        "status": status,
        "hansard_href": hansard_href,
        "hansard_page": hansard_page,
    }


def _parse_bill_chunk(
    billinfoid: str, chunk: str,
) -> Optional[dict[str, Any]]:
    header = _BILL_HEADER_RE.search(chunk)
    if not header:
        return None

    details = _parse_details(chunk)

    sponsor_raw = details.get("Sponsor", "") or ""
    sponsor_mid_m = _SPONSOR_MID_RE.search(sponsor_raw)
    sponsor_mid = sponsor_mid_m.group("mid") if sponsor_mid_m else None
    sponsor_name = _text(sponsor_raw)

    legsess = details.get("Legislature", "") or ""
    ls_m = re.search(r"Legislature\s+(\d+),\s+Session\s+(\d+)", legsess)
    legislature = int(ls_m.group(1)) if ls_m else None
    session = int(ls_m.group(2)) if ls_m else None

    bill_type = _BILL_TYPE_MAP.get(_text(details.get("Type", "") or ""), None)
    amendments = _text(details.get("Amendments", "") or "").lower() == "yes"
    money_bill = _text(details.get("Money Bill", "") or "").lower() == "yes"

    doc = _DOC_HREF_RE.search(details.get("Documents", "") or "")
    doc_url = doc.group("href") if doc else None

    events: list[dict[str, Any]] = []
    for sb in _STAGE_BLOCK_RE.finditer(chunk):
        ev = _parse_stage_event(sb.group("short"), sb.group("body"))
        if ev is not None:
            events.append(ev)

    return {
        "billinfoid":   billinfoid,
        "bill_number":  header.group("number").strip(),
        "title":        header.group("title").strip(),
        "sponsor_mid":  sponsor_mid,
        "sponsor_name": sponsor_name,
        "legislature":  legislature,
        "session":      session,
        "bill_type":    bill_type,
        "amendments":   amendments,
        "money_bill":   money_bill,
        "doc_url":      doc_url,
        "events":       events,
    }


def parse_dashboard(html: str) -> list[dict[str, Any]]:
    """Split the dashboard HTML into per-bill dicts.

    The dashboard renders every bill inside a shared accordion; anchors
    (`<a id="g{billinfoid}">`) delimit each bill's block. We split on
    those, then pull header + details + stages out of each slice.
    """
    matches = list(_BILL_ANCHOR_RE.finditer(html))
    if not matches:
        return []
    bills: list[dict[str, Any]] = []
    for i, m in enumerate(matches):
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(html)
        chunk = html[start:end]
        parsed = _parse_bill_chunk(m.group(1), chunk)
        if parsed:
            bills.append(parsed)
    return bills


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
        VALUES ('provincial', 'AB', $1, $2, $3, $4, $5)
        ON CONFLICT (level, province_territory, parliament_number, session_number)
        DO UPDATE SET
            name          = COALESCE(legislative_sessions.name, EXCLUDED.name),
            source_system = COALESCE(legislative_sessions.source_system, EXCLUDED.source_system),
            source_url    = COALESCE(legislative_sessions.source_url, EXCLUDED.source_url),
            updated_at    = now()
        RETURNING id
        """,
        legislature, session,
        f"Legislature {legislature}, Session {session}",
        SOURCE_SYSTEM,
        DASHBOARD_URL.format(legislature=legislature, session=session),
    )
    return str(row["id"])


async def _upsert_bill(
    db: Database, *, session_id: str, bill: dict,
    source_url: str,
) -> str:
    # Current status = the latest (by date) stage label.
    latest_date: Optional[date] = None
    latest_label: Optional[str] = None
    introduced: Optional[date] = None
    for ev in bill["events"]:
        if ev["event_date"] is None:
            continue
        if ev["canonical"] == "first_reading" and introduced is None:
            introduced = ev["event_date"]
        if latest_date is None or ev["event_date"] >= latest_date:
            latest_date = ev["event_date"]
            latest_label = ev["label"]

    status_changed_at = (
        datetime.combine(latest_date, datetime.min.time())
        if latest_date else None
    )

    source_id = (
        f"{SOURCE_SYSTEM}:{bill['legislature']}-{bill['session']}:"
        f"bill-{bill['bill_number']}"
    )

    row = await db.fetchrow(
        """
        INSERT INTO bills (
            session_id, level, province_territory, bill_number,
            title, bill_type, status, status_changed_at, introduced_date,
            source_id, source_system, source_url, raw, last_fetched_at
        )
        VALUES ($1, 'provincial', 'AB', $2, $3, $4, $5, $6, $7,
                $8, $9, $10, $11::jsonb, now())
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
            last_fetched_at   = now(),
            updated_at        = now()
        RETURNING id
        """,
        session_id, bill["bill_number"], bill["title"], bill["bill_type"],
        latest_label, status_changed_at, introduced,
        source_id, SOURCE_SYSTEM, source_url,
        orjson.dumps({
            "billinfoid": bill["billinfoid"],
            "amendments": bill["amendments"],
            "money_bill": bill["money_bill"],
            "doc_url":    bill["doc_url"],
            "sponsor_mid":  bill["sponsor_mid"],
            "sponsor_name": bill["sponsor_name"],
        }).decode(),
    )
    return str(row["id"])


async def _persist_stage_events(
    db: Database, bill_id: str, bill: dict,
) -> int:
    written = 0
    for ev in bill["events"]:
        if ev["event_date"] is None or ev["canonical"] == "other":
            continue
        committee_name = (
            "Committee of the Whole" if ev["canonical"] == "committee" else None
        )
        # event_type stores the outcome status to allow same-stage same-day
        # rows with different outcomes (rare but possible — e.g. two 2R
        # sittings in one day that adjourn then pass).
        event_type = ev["status"] or None
        await db.execute(
            """
            INSERT INTO bill_events (
                bill_id, stage, stage_label, event_date,
                event_type, outcome, committee_name, raw
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb)
            ON CONFLICT ON CONSTRAINT bill_events_uniq DO NOTHING
            """,
            bill_id, ev["canonical"], ev["label"], ev["event_date"],
            event_type, ev["status"] or None, committee_name,
            orjson.dumps({
                "source": SOURCE_SYSTEM,
                "short": ev["short"],
                "code":  ev["code"],
                "display_title": ev["display_title"],
                "hansard_href":  ev["hansard_href"],
                "hansard_page":  ev["hansard_page"],
                "event_date_raw": ev["event_date_raw"],
            }).decode(),
        )
        written += 1
    return written


async def _persist_sponsor(
    db: Database, bill_id: str, bill: dict,
) -> tuple[int, int]:
    """Returns (written, linked)."""
    mid = bill["sponsor_mid"]
    if not mid:
        return 0, 0

    pol_id = await db.fetchval(
        "SELECT id FROM politicians WHERE ab_assembly_mid = $1 "
        "  AND level = 'provincial' AND province_territory = 'AB'",
        mid,
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
              politician_id    = COALESCE(EXCLUDED.politician_id, bill_sponsors.politician_id),
              sponsor_name_raw = COALESCE(EXCLUDED.sponsor_name_raw, bill_sponsors.sponsor_name_raw)
        """,
        bill_id, pol_id, mid, bill["sponsor_name"], SOURCE_SYSTEM,
    )
    return 1, (1 if pol_id else 0)


# ─────────────────────────────────────────────────────────────────────
# Orchestrator
# ─────────────────────────────────────────────────────────────────────

async def _ingest_one_session(
    db: Database, *, client: httpx.AsyncClient,
    legislature: int, session: int,
) -> dict[str, int]:
    url = DASHBOARD_URL.format(legislature=legislature, session=session)
    r = await client.get(url, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    html = r.text

    bills = parse_dashboard(html)
    log.info(
        "ingest_ab_bills: legl=%d session=%d parsed %d bills",
        legislature, session, len(bills),
    )

    stats = {"bills": 0, "events": 0, "sponsors": 0, "sponsors_linked": 0}
    if not bills:
        return stats

    session_id = await _upsert_session(
        db, legislature=legislature, session=session,
    )

    for bill in bills:
        # The parsed block tells us its own (legl, session) — trust
        # that over the URL params in case of any historical redirect
        # quirks. Fallback only if the detail block lacked them.
        bill.setdefault("legislature", legislature)
        bill.setdefault("session", session)
        if bill["legislature"] is None:
            bill["legislature"] = legislature
        if bill["session"] is None:
            bill["session"] = session

        source_url = BILL_ANCHOR_URL.format(
            legislature=bill["legislature"],
            session=bill["session"],
            bid=bill["billinfoid"],
        )
        bill_id = await _upsert_bill(
            db, session_id=session_id, bill=bill, source_url=source_url,
        )
        stats["bills"] += 1
        stats["events"] += await _persist_stage_events(db, bill_id, bill)
        w, linked = await _persist_sponsor(db, bill_id, bill)
        stats["sponsors"] += w
        stats["sponsors_linked"] += linked

    return stats


async def ingest_ab_bills(
    db: Database, *,
    legislature: Optional[int] = None,
    session: Optional[int] = None,
    all_sessions_in_legislature: Optional[int] = None,
    all_sessions: bool = False,
    delay_seconds: float = 1.5,
) -> dict[str, int]:
    """Ingest Alberta bills from the Assembly Dashboard.

    Scope options (mutually exclusive; first applies):
      - ``legislature``+``session``: one specific session.
      - ``all_sessions_in_legislature``: every session inside the
        named legislature (discovered from the current dashboard's
        session-nav links).
      - ``all_sessions``: every session the dashboard navigates to
        (Legislature 1 onwards — ~137 sessions).
      - default: current session (the one returned when no params
        are supplied in the URL).
    """
    totals = {"sessions_touched": 0, "bills": 0, "events": 0,
              "sponsors": 0, "sponsors_linked": 0}

    async with httpx.AsyncClient(headers=HEADERS, follow_redirects=True) as client:
        # Fetch the default page once to (a) serve the "current session"
        # case with zero extra work, and (b) read the session-nav list
        # when we need a broader scope.
        default_r = await client.get(
            "https://www.assembly.ab.ca/assembly-business/assembly-dashboard",
            timeout=REQUEST_TIMEOUT,
        )
        default_r.raise_for_status()
        default_html = default_r.text

        targets: list[tuple[int, int]] = []
        if legislature is not None and session is not None:
            targets = [(legislature, session)]
        elif all_sessions_in_legislature is not None:
            pairs = _extract_session_pairs(default_html)
            targets = sorted(
                (l, s) for (l, s) in pairs if l == all_sessions_in_legislature
            )
        elif all_sessions:
            targets = sorted(_extract_session_pairs(default_html))
        else:
            # Current session: parse the already-fetched default page
            # directly instead of re-fetching.
            bills = parse_dashboard(default_html)
            if not bills:
                log.warning("ingest_ab_bills: default dashboard had no bills")
                return totals
            current = (bills[0]["legislature"] or 0, bills[0]["session"] or 0)
            targets = [current]

        log.info("ingest_ab_bills: %d target sessions", len(targets))

        for i, (L, S) in enumerate(targets):
            if i > 0 and delay_seconds > 0:
                await asyncio.sleep(delay_seconds)
            try:
                s = await _ingest_one_session(
                    db, client=client, legislature=L, session=S,
                )
            except httpx.HTTPError as e:
                log.warning("ingest_ab_bills: %d-%d failed: %s", L, S, e)
                continue
            totals["sessions_touched"] += 1
            for k in ("bills", "events", "sponsors", "sponsors_linked"):
                totals[k] += s[k]

    log.info("ingest_ab_bills: %s", totals)
    return totals


_NAV_RE = re.compile(
    r"assembly-dashboard\?legl=(\d+)&(?:amp;)?session=(\d+)",
    re.IGNORECASE,
)


def _extract_session_pairs(html: str) -> set[tuple[int, int]]:
    """Harvest every (legislature, session) pair the dashboard links to.

    The dashboard renders a session-picker nav with every legislature
    and session ever held. We treat that as the authoritative session
    index when the caller asks for a broad scope.
    """
    return {(int(m.group(1)), int(m.group(2))) for m in _NAV_RE.finditer(html)}
