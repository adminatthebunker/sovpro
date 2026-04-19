"""Ontario bills pipeline — ola.org scraper.

Three phases, mirroring the NS structure but with a discovery step
replacing Socrata (ola.org has no API):

  phase 1 — discovery:  scrape the session index page to enumerate
                        every bill URL, upsert minimal rows.
  phase 2 — fetch:      cache both the main bill page (sponsor +
                        current status) and the /status sub-page
                        (event history table).
  phase 3 — parse:      offline regex extraction → bill_sponsors and
                        bill_events.

Scope for this pass: Parliament 44, Session 1 only. Backfill of
earlier Parliaments is deferred — see docs/research/ontario.md.

No WAF has been observed on ola.org across probe traffic, so pacing is
~1.5 sec/req (vs. NS's 4–6 sec). Still polite — ola.org is a small
Drupal site. We keep WAF-fingerprint detection in place defensively.
"""
from __future__ import annotations

import asyncio
import logging
import random
import re
from datetime import date, datetime, timezone
from html import unescape
from typing import Optional

import httpx
import orjson

from ..db import Database
from ..gap_fillers.shared import BROWSER_UA

log = logging.getLogger(__name__)

SOURCE_SYSTEM = "ola-on"
BASE = "https://www.ola.org"
INDEX_TEMPLATE = BASE + "/en/legislative-business/bills/parliament-{p}/session-{s}"
BILL_URL_RE = re.compile(
    r'href="(/en/legislative-business/bills/parliament-(\d+)/session-(\d+)/bill-(\d+[a-z]?))"'
)

DEFAULT_DELAY_SECS = 1.5
DEFAULT_JITTER_SECS = 1.0
REQUEST_TIMEOUT = 30

_WAF_MARKER = "Request Rejected"

_HEADERS = {
    "User-Agent": BROWSER_UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
}


# ─────────────────────────────────────────────────────────────────────
# Phase 1 — Discovery
# ─────────────────────────────────────────────────────────────────────

async def discover_ola_bills(
    db: Database, *, parliament: int, session: int
) -> dict[str, int]:
    """Scrape the session index and upsert minimal bill rows.

    Idempotent: source_id '<system>:<parliament>-<session>:bill-<N>' is
    stable across runs.
    """
    url = INDEX_TEMPLATE.format(p=parliament, s=session)
    async with httpx.AsyncClient(headers=_HEADERS, follow_redirects=True) as client:
        r = await client.get(url, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        html = r.text

    seen: set[str] = set()
    bill_numbers: list[str] = []
    for m in BILL_URL_RE.finditer(html):
        path, p, s, n = m.groups()
        if int(p) != parliament or int(s) != session:
            continue
        if n in seen:
            continue
        seen.add(n)
        bill_numbers.append(n)

    log.info("discover_ola_bills: %d bills in P%d-S%d", len(bill_numbers), parliament, session)

    session_id = await _upsert_session(db, parliament, session)
    stats = {"session_id": 1, "bills": 0}
    for n in bill_numbers:
        await _upsert_bill_stub(
            db,
            session_id=session_id,
            parliament=parliament,
            session=session,
            bill_number=n,
        )
        stats["bills"] += 1
    return stats


async def _upsert_session(db: Database, parliament: int, session: int) -> str:
    row = await db.fetchrow(
        """
        INSERT INTO legislative_sessions (
            level, province_territory, parliament_number, session_number,
            name, source_system, source_url
        )
        VALUES ('provincial', 'ON', $1, $2, $3, $4, $5)
        ON CONFLICT (level, province_territory, parliament_number, session_number)
        DO UPDATE SET updated_at = now(),
                      name = EXCLUDED.name,
                      source_url = EXCLUDED.source_url
        RETURNING id
        """,
        parliament,
        session,
        f"{parliament}th Parliament, {session}st Session",
        SOURCE_SYSTEM,
        INDEX_TEMPLATE.format(p=parliament, s=session),
    )
    return str(row["id"])


async def _upsert_bill_stub(
    db: Database, *, session_id: str, parliament: int, session: int, bill_number: str
) -> str:
    """Create a bare row for a bill before we've fetched its detail page.

    Title is set to a placeholder that the parse step overwrites with
    the real bill title. Source_url is the canonical /bill-N page.
    """
    source_id = f"{SOURCE_SYSTEM}:{parliament}-{session}:bill-{bill_number}"
    source_url = f"{BASE}/en/legislative-business/bills/parliament-{parliament}/session-{session}/bill-{bill_number}"
    row = await db.fetchrow(
        """
        INSERT INTO bills (
            session_id, level, province_territory, bill_number,
            title, short_title, source_id, source_system, source_url, raw
        )
        VALUES ($1, 'provincial', 'ON', $2, $3, NULL, $4, $5, $6, '{}'::jsonb)
        ON CONFLICT (source_id) DO UPDATE SET
            source_url = EXCLUDED.source_url,
            last_fetched_at = now(),
            updated_at = now()
        RETURNING id
        """,
        session_id,
        bill_number,
        f"Bill {bill_number}",  # placeholder until parse fills in real title
        source_id,
        SOURCE_SYSTEM,
        source_url,
    )
    return str(row["id"])


# ─────────────────────────────────────────────────────────────────────
# Phase 2 — Fetch
# ─────────────────────────────────────────────────────────────────────

class WAFBlocked(Exception):
    pass


async def _fetch(client: httpx.AsyncClient, url: str) -> tuple[Optional[str], Optional[str]]:
    try:
        r = await client.get(url, timeout=REQUEST_TIMEOUT)
        if r.status_code != 200:
            return None, f"http {r.status_code}"
        if len(r.text) < 1000 and _WAF_MARKER in r.text:
            raise WAFBlocked(url)
        if len(r.text) < 500:
            return None, f"suspiciously short ({len(r.text)}b)"
        return r.text, None
    except httpx.HTTPError as exc:
        return None, f"{type(exc).__name__}: {exc}"


async def fetch_ola_bill_pages(
    db: Database,
    *,
    limit: Optional[int] = None,
    force: bool = False,
    delay_secs: float = DEFAULT_DELAY_SECS,
    jitter_secs: float = DEFAULT_JITTER_SECS,
) -> dict[str, int]:
    """Cache both the main bill page and the /status sub-page.

    For each bill we do up to 2 requests. Already-cached halves are
    skipped unless --force.
    """
    if force:
        sql = """
            SELECT id, source_url FROM bills
             WHERE source_system = $1 AND source_url IS NOT NULL
             ORDER BY bill_number
        """
    else:
        sql = """
            SELECT id, source_url FROM bills
             WHERE source_system = $1 AND source_url IS NOT NULL
               AND (raw_html IS NULL OR raw_status_html IS NULL)
             ORDER BY bill_number
        """
    if limit is not None:
        sql += f" LIMIT {int(limit)}"
    rows = await db.fetch(sql, SOURCE_SYSTEM)
    total = len(rows)
    log.info("fetch_ola_bill_pages: %d bills queued (force=%s)", total, force)

    stats = {"main_ok": 0, "status_ok": 0, "err": 0, "waf_aborted": 0, "total": total}
    if not rows:
        return stats

    waf_hits = 0
    async with httpx.AsyncClient(headers=_HEADERS, follow_redirects=True) as client:
        for i, row in enumerate(rows, start=1):
            bill_id = str(row["id"])
            base_url = row["source_url"]
            status_url = base_url + "/status"

            aborted = False
            for which, url, col_html, col_fetched, col_err, col_err_at in (
                ("main",   base_url,   "raw_html",        "html_fetched_at",
                 "html_last_error", "html_last_error_at"),
                ("status", status_url, "raw_status_html", "status_html_fetched_at",
                 "status_html_last_error", "status_html_last_error_at"),
            ):
                # Skip the half already cached (unless force).
                if not force:
                    cur = await db.fetchrow(
                        f"SELECT {col_html} FROM bills WHERE id=$1",
                        bill_id,
                    )
                    if cur and cur[col_html]:
                        continue

                try:
                    html, err = await _fetch(client, url)
                except WAFBlocked:
                    waf_hits += 1
                    log.warning(
                        "fetch_ola_bill_pages: WAF hit %d/2 at %s", waf_hits, url,
                    )
                    if waf_hits >= 2:
                        stats["waf_aborted"] = 1
                        log.error("fetch_ola_bill_pages: aborting — WAF block live")
                        aborted = True
                        break
                    await asyncio.sleep(60)
                    continue
                waf_hits = 0

                now = datetime.now(timezone.utc)
                if html is not None:
                    await db.execute(
                        f"UPDATE bills SET {col_html}=$2, {col_fetched}=$3, "
                        f"{col_err}=NULL, {col_err_at}=NULL, updated_at=now() "
                        f"WHERE id=$1",
                        bill_id, html, now,
                    )
                    stats[f"{which}_ok"] += 1
                else:
                    await db.execute(
                        f"UPDATE bills SET {col_err}=$2, {col_err_at}=$3, "
                        f"updated_at=now() WHERE id=$1",
                        bill_id, err, now,
                    )
                    stats["err"] += 1

                pause = delay_secs + (random.random() * jitter_secs if jitter_secs > 0 else 0.0)
                await asyncio.sleep(pause)

            if aborted:
                break
            if i % 25 == 0:
                log.info(
                    "fetch_ola_bill_pages: %d/%d done main=%d status=%d err=%d",
                    i, total, stats["main_ok"], stats["status_ok"], stats["err"],
                )

    log.info(
        "fetch_ola_bill_pages: finished main=%d status=%d err=%d waf_aborted=%d",
        stats["main_ok"], stats["status_ok"], stats["err"], stats["waf_aborted"],
    )
    return stats


# ─────────────────────────────────────────────────────────────────────
# Phase 3 — Parse
# ─────────────────────────────────────────────────────────────────────

# Sponsor block on main bill page:
#   <div class="views-field views-field-field-member">
#     <div class="field-content">
#       <p><a href="/members/all/rob-flack">Flack, Hon. Rob</a>
#          <i>Minister of Municipal Affairs and Housing</i></p>
#     </div>
#   </div>
_SPONSOR_RE = re.compile(
    r'<div class="views-field views-field-field-member">.*?'
    r'<a[^>]+href="/members/all/(?P<slug>[^"]+)"[^>]*>(?P<name>[^<]+)</a>'
    r'(?:\s*<i>(?P<role>[^<]*)</i>)?',
    re.DOTALL,
)

# Title / short title on main page:
#   <title>Bill 100, Better Regional Governance Act, 2026 - Legislative Assembly of Ontario</title>
_TITLE_RE = re.compile(
    r"<title>\s*Bill\s+\d+[a-z]?\s*,\s*(?P<title>.+?)\s*-\s*Legislative Assembly",
    re.IGNORECASE | re.DOTALL,
)

# Current status (used when /status page doesn't yield a usable terminal event):
_STATUS_RE = re.compile(
    r'views-field-field-current-status-1"[^>]*>\s*'
    r'<span class="field-content">\s*Current status:\s*(?P<status>.+?)\s*</span>',
    re.DOTALL | re.IGNORECASE,
)

# Month-name date: "June 5, 2025"
_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
}
_DATE_RE = re.compile(
    r"(January|February|March|April|May|June|July|August|"
    r"September|October|November|December)\s+(\d{1,2}),\s*(\d{4})",
    re.IGNORECASE,
)

# bill stage canonicalization — Ontario labels map to our enum.
_STAGE_MAP = {
    "first reading":  "first_reading",
    "second reading": "second_reading",
    "third reading":  "third_reading",
    "royal assent":   "royal_assent",
    "committee":      "committee",
    "introduced":     "introduced",
    "withdrawn":      "withdrawn",
}


def _canon_stage(label: str) -> str:
    return _STAGE_MAP.get((label or "").strip().lower(), "other")


def _strip(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", s or "")).strip()


def _normalize_name(raw: str) -> str:
    """ola.org renders as "Last, Hon. First". Convert to "First Last"."""
    raw = unescape(raw).strip()
    if "," in raw:
        last, rest = raw.split(",", 1)
        rest = re.sub(r"^\s*(Hon\.|Mr\.|Mrs\.|Ms\.|Dr\.)\s*", "", rest).strip()
        return f"{rest} {last.strip()}".strip()
    return raw


def extract_main(html: str) -> dict:
    out: dict = {"sponsor": None, "title": None, "status": None}

    m = _SPONSOR_RE.search(html or "")
    if m:
        role = m.group("role")
        out["sponsor"] = {
            "name": _normalize_name(m.group("name")),
            "slug": m.group("slug").strip(),
            "role": _strip(role) if role else None,
        }

    m = _TITLE_RE.search(html or "")
    if m:
        out["title"] = unescape(m.group("title")).strip()

    m = _STATUS_RE.search(html or "")
    if m:
        out["status"] = _strip(m.group("status"))

    return out


def extract_status_events(html: str) -> list[dict]:
    """Parse the /status tab's 5-column event table.

    Returns rows of {stage, stage_label, event_type, outcome,
    committee_name, event_date}.
    """
    if not html:
        return []

    # Locate the status table — it lives inside the field-status-table
    # wrapper. We slice that wrapper to avoid matching nav tables.
    wrap = re.search(
        r'field-status-table[^>]*>(.*?)</div>\s*</div>\s*</div>',
        html, re.DOTALL,
    )
    region = wrap.group(1) if wrap else html

    out: list[dict] = []
    seen: set[tuple] = set()
    # ola.org emits malformed markup — rows are missing their </tr> closers.
    # Split the region on <tr> tokens and treat everything up to the next
    # <tr> (or </table>) as one row.
    chunks = re.split(r"<tr[^>]*>", region)
    for chunk in chunks:
        # Stop a chunk at the first </tr> or </table> we encounter, so
        # content after the table can't leak into the last row.
        chunk = re.split(r"</tr>|</table>", chunk, maxsplit=1)[0]
        cells = re.findall(r"<t[dh][^>]*>(.*?)(?=</t[dh]>|<t[dh][^>]*>|$)",
                           chunk, re.DOTALL)
        if len(cells) < 5:
            continue
        date_txt = _strip(cells[0])
        stage_txt = _strip(cells[1])
        event_txt = _strip(cells[2])
        outcome_txt = _strip(cells[3])
        committee_txt = _strip(cells[4])

        if stage_txt.lower() in ("bill stage", ""):  # header row
            continue

        dm = _DATE_RE.search(date_txt)
        if not dm:
            continue
        try:
            event_date = date(int(dm.group(3)),
                              _MONTHS[dm.group(1).lower()],
                              int(dm.group(2)))
        except (KeyError, ValueError):
            continue

        stage = _canon_stage(stage_txt)
        event_type = event_txt or None
        outcome = outcome_txt if outcome_txt and outcome_txt != "-" else None
        committee = committee_txt if committee_txt and committee_txt != "-" else None

        key = (stage, event_date, event_type, committee)
        if key in seen:
            continue
        seen.add(key)
        out.append({
            "stage": stage,
            "stage_label": stage_txt,
            "event_type": event_type,
            "outcome": outcome,
            "committee_name": committee,
            "event_date": event_date,
        })
    return out


async def _persist_sponsor(db: Database, bill_id: str, sponsor: dict) -> int:
    await db.execute(
        """
        INSERT INTO bill_sponsors (
            bill_id, sponsor_name_raw, sponsor_slug, sponsor_role,
            role, source_system
        )
        VALUES ($1, $2, $3, $4, 'sponsor', $5)
        ON CONFLICT (bill_id, sponsor_slug)
          WHERE sponsor_slug IS NOT NULL
          DO UPDATE SET
              sponsor_name_raw = EXCLUDED.sponsor_name_raw,
              sponsor_role     = EXCLUDED.sponsor_role
        """,
        bill_id, sponsor["name"], sponsor["slug"], sponsor["role"], SOURCE_SYSTEM,
    )
    return 1


async def _persist_bill_meta(db: Database, bill_id: str, meta: dict) -> None:
    # Only update fields we actually parsed. Title is our main one.
    if meta.get("title"):
        await db.execute(
            """
            UPDATE bills SET
                title = $2,
                short_title = $2,
                status = COALESCE($3, status),
                updated_at = now()
            WHERE id = $1
            """,
            bill_id, meta["title"], meta.get("status"),
        )


async def _persist_events(db: Database, bill_id: str, events: list[dict]) -> int:
    written = 0
    for ev in events:
        await db.execute(
            """
            INSERT INTO bill_events (
                bill_id, stage, stage_label, event_type, outcome,
                committee_name, event_date, raw
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb)
            ON CONFLICT ON CONSTRAINT bill_events_uniq DO NOTHING
            """,
            bill_id, ev["stage"], ev["stage_label"],
            ev["event_type"], ev["outcome"], ev["committee_name"],
            ev["event_date"],
            orjson.dumps({"source": SOURCE_SYSTEM}).decode(),
        )
        written += 1
    return written


async def parse_ola_bill_pages(
    db: Database, *, limit: Optional[int] = None
) -> dict[str, int]:
    """Parse every Ontario bill that has both HTML halves cached.

    Re-entrant: updates title/status + upserts sponsor + inserts events.
    Safe to re-run; unique constraints handle dedup.
    """
    sql = """
        SELECT id, raw_html, raw_status_html
          FROM bills
         WHERE source_system = $1
           AND raw_html IS NOT NULL
         ORDER BY bill_number
    """
    if limit is not None:
        sql += f" LIMIT {int(limit)}"
    rows = await db.fetch(sql, SOURCE_SYSTEM)

    stats = {"bills": 0, "sponsors": 0, "events": 0, "no_sponsor": 0, "titled": 0}
    for row in rows:
        bill_id = str(row["id"])
        stats["bills"] += 1

        main = extract_main(row["raw_html"])
        if main["title"]:
            await _persist_bill_meta(db, bill_id, main)
            stats["titled"] += 1
        if main["sponsor"]:
            stats["sponsors"] += await _persist_sponsor(db, bill_id, main["sponsor"])
        else:
            stats["no_sponsor"] += 1

        if row["raw_status_html"]:
            events = extract_status_events(row["raw_status_html"])
            stats["events"] += await _persist_events(db, bill_id, events)

    log.info(
        "parse_ola_bill_pages: bills=%d sponsors=%d events=%d titled=%d no_sponsor=%d",
        stats["bills"], stats["sponsors"], stats["events"],
        stats["titled"], stats["no_sponsor"],
    )
    return stats
