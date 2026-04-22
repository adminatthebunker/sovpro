"""Manitoba bills pipeline — HTML roster scrape.

Single-page ingest for any session `{P}-{S}`:

    https://web2.gov.mb.ca/bills/{P}-{S}/index.php

The page contains one `<table class="index">` per bill category
(Government Bills, Private Members' Bills, etc.). Each row exposes
bill number, sponsor-as-text (with optional ministerial title on a
second line), title (linked to the per-bill text page), an optional
"amendment(s) adopted at Committee Stage" PDF, and an "As enacted"
link when applicable.

Per-bill pages (``b{NNN}e.php``) carry only the bill text as
distributed after First Reading — no sponsor block, no stage history,
no dates. The *status timeline* lives in a separate session-scoped
PDF (``billstatus.pdf``) which is handled by the ``mb_billstatus``
module. This module therefore only persists **bills + bill_sponsors**
and leaves ``bill_events`` untouched. Running Phase 2 without Phase 3
is a valid intermediate state — bills exist, events come in the
subsequent PDF parse.

Sponsor resolution is an exact FK join on ``politicians.mb_assembly_slug``
using the sponsor's surname. Fall back to no link (politician_id NULL)
if the surname doesn't map to a current MLA — ``resolve-mb-bill-sponsors``
in a later phase handles historical backfills.
"""
from __future__ import annotations

import html as html_lib
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import httpx
import orjson

from ..db import Database

log = logging.getLogger(__name__)

SOURCE_SYSTEM = "manitoba-bills"
REQUEST_TIMEOUT = 45
BASE = "https://web2.gov.mb.ca"
INDEX_URL = BASE + "/bills/{P}-{S}/index.php"
CURRENT_INDEX_URL = BASE + "/bills/"
HEADERS = {
    "User-Agent": "CanadianPoliticalDataBot/1.0 (+https://canadianpoliticaldata.ca)",
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "en-CA,en;q=0.9",
}

# One `<table class="index">` per bill category. Greedy inside bounds.
_TABLE_RE = re.compile(
    r'<table\s+class="index"[^>]*>(?P<body>.*?)</table>',
    re.IGNORECASE | re.DOTALL,
)
# The category title lives in the first <thead> <td class="centerbig">.
_CATEGORY_RE = re.compile(
    r'<thead>.*?<td[^>]*class="centerbig"[^>]*>(?P<title>.*?)</td>',
    re.IGNORECASE | re.DOTALL,
)
# Each <tr> is a bill row once we're inside the <tbody> (there is no
# <tbody> wrapper — thead rows come first, data rows follow).
_ROW_RE = re.compile(r"<tr\b[^>]*>(?P<body>.*?)</tr>", re.IGNORECASE | re.DOTALL)
# MB markup frequently omits `</td>` between data cells — the parser
# must split on `<td ...>` boundaries rather than look for matched
# open/close pairs. Each resulting segment is one cell's content,
# truncated at the next `</td>` or `</tr>` if present.
_CELL_SPLIT_RE = re.compile(r"<td\b[^>]*>", re.IGNORECASE | re.DOTALL)
_CELL_END_RE = re.compile(r"</td>|</tr>", re.IGNORECASE | re.DOTALL)
# Cells that are part of the header — have a <td colspan="5"> or the
# "No." / "Sponsored by" label markup. We skip any row where every cell
# sits inside a <thead>.
# Linked "b{NNN}e.php" — the per-bill text page.
_TEXT_HREF_RE = re.compile(r'href="(b\d+[a-zA-Z]?\.php)"', re.IGNORECASE)
# Committee-stage amendment PDF.
_COMMITTEE_HREF_RE = re.compile(r'href="(b\d+cs\.pdf)"', re.IGNORECASE)
# Main bill-text PDF.
_PDF_HREF_RE = re.compile(r'href="(pdf/b\d+\.pdf)"', re.IGNORECASE)
# The "As enacted" column is the final cell. When a bill is enacted,
# MB places a link (to /laws/statutes/, a c{NNN}.pdf, or similar) in
# that last cell; otherwise the cell is just "&nbsp;". So the
# enacted-vs-not decision is simply: does the last cell contain any
# anchor. We only scan cells[-1] for this so normal bill-text PDFs
# (in cell 4) don't falsely imply enactment.
_ANY_HREF_RE = re.compile(r'href="([^"]+)"', re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")
_HONORIFIC_RE = re.compile(
    r"\b(Hon\.?|Honourable|Mr\.?|Mrs\.?|Ms\.?|Miss\.?|Dr\.?|Madam|Minister|MLA)\b",
    re.IGNORECASE,
)


# Category title (raw HTML/text) → (bill_type, display label).
def _classify_category(title: str) -> tuple[Optional[str], str]:
    t = title.lower()
    if "government" in t:
        return "government", "Government Bills"
    if "private member" in t:
        return "private_member", "Private Members' Bills"
    if "private" in t:
        return "private", "Private Bills"
    return None, title.strip() or "Unclassified"


@dataclass
class MBBillRow:
    bill_number: str
    title: Optional[str]
    bill_type: Optional[str]
    category_label: str
    sponsor_name_raw: Optional[str]
    sponsor_title: Optional[str]   # "Minister of Justice" line when present
    sponsor_surname: Optional[str] # derived for slug lookup
    text_page_path: Optional[str]  # "b002e.php"
    pdf_path: Optional[str]        # "pdf/b002.pdf"
    committee_amendments_path: Optional[str]  # "b002cs.pdf"
    enacted_href: Optional[str]
    row_html: str                  # raw <tr>...</tr> for bills.raw_html


def _strip(s: str) -> str:
    cleaned = _TAG_RE.sub(" ", s or "")
    return re.sub(r"\s+", " ", html_lib.unescape(cleaned)).strip()


def _surname_candidates(sponsor_line: str) -> list[str]:
    """Return slug candidates for the MLA's surname, most specific first.

    Examples:
      "Hon. Mr. Wiebe"          → ["wiebe"]
      "Hon. Minister Sala"      → ["sala"]
      "Mrs. Cook"               → ["cook"]
      "MLA Dela Cruz"           → ["delacruz", "dela-cruz", "cruz"]

    Compound surnames are common in MB (Dela Cruz, Smith Lamont) and
    the profile-URL slug collapses them into one token, so try the
    joined form before the last-token form.
    """
    cleaned = _HONORIFIC_RE.sub(" ", sponsor_line or "")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if not cleaned:
        return []
    tokens = [t.lower() for t in cleaned.split() if t]
    if not tokens:
        return []
    out: list[str] = []
    if len(tokens) >= 2:
        out.append("".join(tokens))          # "delacruz"
        out.append("-".join(tokens))         # "dela-cruz"
    out.append(tokens[-1])                   # "cruz"
    # Preserve order, dedupe.
    return list(dict.fromkeys(out))


def _surname_from_sponsor(sponsor_line: str) -> Optional[str]:
    """Best-guess single slug for storage — the first (most specific) candidate."""
    cands = _surname_candidates(sponsor_line)
    return cands[0] if cands else None


def _parse_sponsor_cell(cell_html: str) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """Return (sponsor_name_raw, sponsor_title, sponsor_surname)."""
    # The cell uses <br> to split "Hon. Mr. Wiebe" from "Minister of Justice".
    parts = re.split(r"<br\s*/?>", cell_html, flags=re.IGNORECASE)
    parts = [_strip(p) for p in parts if _strip(p)]
    if not parts:
        return None, None, None
    name_line = parts[0]
    title_line = parts[1] if len(parts) >= 2 else None
    return name_line, title_line, _surname_from_sponsor(name_line)


def _split_cells(row_html: str) -> list[str]:
    """Split a <tr> body into cells, tolerating unclosed <td> tags."""
    parts = _CELL_SPLIT_RE.split(row_html)
    # parts[0] is the pre-first-<td> prefix; drop it.
    cells: list[str] = []
    for p in parts[1:]:
        end = _CELL_END_RE.search(p)
        cells.append(p[:end.start()] if end else p)
    return cells


def _parse_row(row_html: str, *, category_label: str,
               bill_type: Optional[str]) -> Optional[MBBillRow]:
    cells = _split_cells(row_html)
    if len(cells) < 2:
        return None
    # First cell = bill number (digits).
    bill_num_raw = _strip(cells[0])
    if not re.fullmatch(r"\d+", bill_num_raw):
        return None  # skip header / interstitial rows

    sponsor_cell = cells[1]
    name_raw, title_line, surname = _parse_sponsor_cell(sponsor_cell)

    # The title lives in cell 3; formal bills collapse cells 3 and 4 via
    # colspan, so we always search the remainder of the row for the
    # first <a href="b...e.php"> (per-bill page) and capture the anchor
    # text as the title. If no anchor (formal bill), fall back to the
    # italicised title inside the cell.
    remaining = " ".join(cells[2:])
    text_m = _TEXT_HREF_RE.search(remaining)
    text_page = text_m.group(1) if text_m else None

    # Title: text inside the <a href="b...e.php">...</a>, or first <i>...</i>.
    title: Optional[str] = None
    if text_page:
        title_pattern = re.compile(
            rf'<a\s+href="{re.escape(text_page)}"[^>]*>(?P<t>.*?)</a>',
            re.IGNORECASE | re.DOTALL,
        )
        m = title_pattern.search(remaining)
        if m:
            title = _strip(m.group("t"))
    if not title:
        m = re.search(r"<i>(?P<t>.*?)</i>", remaining, re.IGNORECASE | re.DOTALL)
        if m:
            title = _strip(m.group("t"))

    committee_m = _COMMITTEE_HREF_RE.search(remaining)
    pdf_m = _PDF_HREF_RE.search(remaining)
    # Restrict enacted detection to the final cell only — that's the
    # "As enacted" column. All other cells may contain unrelated PDF
    # links (committee amendments, bill text) that we don't want to
    # misinterpret as royal assent.
    last_cell = cells[-1] if cells else ""
    enacted_m = _ANY_HREF_RE.search(last_cell) if cells[2:] else None

    return MBBillRow(
        bill_number=bill_num_raw,
        title=title,
        bill_type=bill_type,
        category_label=category_label,
        sponsor_name_raw=name_raw,
        sponsor_title=title_line,
        sponsor_surname=surname,
        text_page_path=text_page,
        pdf_path=pdf_m.group(1) if pdf_m else None,
        committee_amendments_path=committee_m.group(1) if committee_m else None,
        enacted_href=enacted_m.group(1) if enacted_m else None,
        row_html=row_html,
    )


def parse_index_page(html: str) -> list[MBBillRow]:
    """Parse every bill row across every <table class="index"> on the page."""
    out: list[MBBillRow] = []
    seen_numbers: set[tuple[str, str]] = set()  # (category, bill_number)
    for table_m in _TABLE_RE.finditer(html):
        body = table_m.group("body")
        cat_m = _CATEGORY_RE.search(body)
        title_raw = _strip(cat_m.group("title")) if cat_m else "Unclassified"
        bill_type, category_label = _classify_category(title_raw)
        for row_m in _ROW_RE.finditer(body):
            row = _parse_row(
                row_m.group("body"),
                category_label=category_label,
                bill_type=bill_type,
            )
            if row is None:
                continue
            key = (category_label, row.bill_number)
            if key in seen_numbers:
                continue
            seen_numbers.add(key)
            out.append(row)
    return out


async def _fetch_index(client: httpx.AsyncClient, parliament: int,
                       session: int) -> str:
    url = INDEX_URL.format(P=parliament, S=session)
    r = await client.get(url, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    return r.text


async def _upsert_session(db: Database, *, parliament: int, session: int,
                          source_url: str) -> str:
    row = await db.fetchrow(
        """
        INSERT INTO legislative_sessions (
            level, province_territory, parliament_number, session_number,
            name, source_system, source_url
        )
        VALUES ('provincial', 'MB', $1, $2, $3, $4, $5)
        ON CONFLICT (level, province_territory, parliament_number, session_number)
        DO UPDATE SET
            name          = COALESCE(legislative_sessions.name, EXCLUDED.name),
            source_system = COALESCE(legislative_sessions.source_system, EXCLUDED.source_system),
            source_url    = COALESCE(legislative_sessions.source_url, EXCLUDED.source_url),
            updated_at    = now()
        RETURNING id
        """,
        parliament, session,
        f"{parliament}{_ordinal(parliament)} Legislature, "
        f"{session}{_ordinal(session)} Session",
        SOURCE_SYSTEM, source_url,
    )
    return str(row["id"])


def _ordinal(n: int) -> str:
    if 10 <= n % 100 <= 20:
        return "th"
    return {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")


async def _resolve_politician_by_surname(
    db: Database, sponsor_line: str,
) -> tuple[Optional[str], Optional[str]]:
    """Exact FK join on politicians.mb_assembly_slug.

    Returns (politician_id, matched_slug). Tries each surname candidate
    (compound-joined first, last-token last) so "Dela Cruz" resolves to
    slug "delacruz" rather than "cruz".
    """
    for cand in _surname_candidates(sponsor_line):
        pid = await db.fetchval(
            """
            SELECT id FROM politicians
             WHERE mb_assembly_slug = $1
               AND level = 'provincial'
               AND province_territory = 'MB'
             LIMIT 2
            """,
            cand,
        )
        if pid:
            return str(pid), cand
    return None, None


async def _upsert_bill(
    db: Database, *, session_id: str, parliament: int, session: int,
    row: MBBillRow,
) -> tuple[str, bool]:
    source_id = f"{SOURCE_SYSTEM}:{parliament}-{session}:bill-{row.bill_number}"
    base_url = f"{BASE}/bills/{parliament}-{session}/"
    detail_url = base_url + row.text_page_path if row.text_page_path else base_url

    status_label: Optional[str] = None
    if row.enacted_href:
        status_label = "Enacted"
    elif row.committee_amendments_path:
        status_label = "Reported with amendments"

    raw = {
        "category": row.category_label,
        "sponsor_title": row.sponsor_title,
        "pdf_path": row.pdf_path,
        "committee_amendments_path": row.committee_amendments_path,
        "enacted_href": row.enacted_href,
        "text_page_path": row.text_page_path,
    }

    result = await db.fetchrow(
        """
        INSERT INTO bills (
            session_id, level, province_territory, bill_number,
            title, bill_type, status,
            source_id, source_system, source_url,
            raw, raw_html, last_fetched_at
        )
        VALUES ($1, 'provincial', 'MB', $2, $3, $4, $5,
                $6, $7, $8, $9::jsonb, $10, now())
        ON CONFLICT (source_id) DO UPDATE SET
            title           = EXCLUDED.title,
            bill_type       = COALESCE(EXCLUDED.bill_type, bills.bill_type),
            -- Status on the upstream page is authoritative; it can
            -- revert to NULL when we realise a prior run misread the
            -- "As enacted" column. parse-mb-bill-events will overwrite
            -- with the PDF-derived status once run.
            status          = EXCLUDED.status,
            source_url      = EXCLUDED.source_url,
            raw             = EXCLUDED.raw,
            raw_html        = EXCLUDED.raw_html,
            last_fetched_at = now(),
            updated_at      = now()
        RETURNING id, (xmax = 0) AS inserted
        """,
        session_id, row.bill_number,
        row.title or f"Bill {row.bill_number}",
        row.bill_type, status_label,
        source_id, SOURCE_SYSTEM, detail_url,
        orjson.dumps(raw).decode(),
        row.row_html,
    )
    return str(result["id"]), bool(result["inserted"])


async def _upsert_sponsor(
    db: Database, *, bill_id: str, row: MBBillRow,
) -> tuple[int, int]:
    """Replace all manitoba-bills sponsor rows for this bill; insert one fresh."""
    if not row.sponsor_name_raw:
        return 0, 0
    pol_id, matched_slug = await _resolve_politician_by_surname(
        db, row.sponsor_name_raw,
    )
    # Prefer the matched slug; fall back to the best-guess surname so
    # resolve-mb-bill-sponsors has something to work with on later runs.
    stored_slug = matched_slug or row.sponsor_surname
    # Delete-and-reinsert so re-runs that produce a refined slug don't
    # leave stale bill_sponsors rows behind. There is exactly one
    # sponsor per MB bill on the upstream index.
    await db.execute(
        "DELETE FROM bill_sponsors WHERE bill_id = $1 AND source_system = $2",
        bill_id, SOURCE_SYSTEM,
    )
    await db.execute(
        """
        INSERT INTO bill_sponsors (
            bill_id, politician_id, sponsor_name_raw, sponsor_slug,
            role, source_system
        )
        VALUES ($1, $2, $3, $4, 'sponsor', $5)
        """,
        bill_id, pol_id, row.sponsor_name_raw, stored_slug,
        SOURCE_SYSTEM,
    )
    return 1, (1 if pol_id else 0)


async def ingest(
    db: Database, *,
    parliament: int, session: int,
) -> dict[str, int]:
    stats = {
        "bills": 0,
        "bills_inserted": 0,
        "bills_updated": 0,
        "sponsors": 0,
        "sponsors_linked": 0,
        "rows_skipped": 0,
    }
    source_url = INDEX_URL.format(P=parliament, S=session)
    async with httpx.AsyncClient(headers=HEADERS, follow_redirects=True) as client:
        html = await _fetch_index(client, parliament, session)

    rows = parse_index_page(html)
    log.info(
        "mb_bills: %d-%d index → %d rows parsed", parliament, session, len(rows),
    )
    if not rows:
        return stats

    session_id = await _upsert_session(
        db, parliament=parliament, session=session, source_url=source_url,
    )

    for row in rows:
        try:
            bill_id, inserted = await _upsert_bill(
                db, session_id=session_id,
                parliament=parliament, session=session, row=row,
            )
        except Exception as exc:
            log.warning("mb_bills: upsert failed bill %s: %s", row.bill_number, exc)
            stats["rows_skipped"] += 1
            continue
        stats["bills"] += 1
        if inserted:
            stats["bills_inserted"] += 1
        else:
            stats["bills_updated"] += 1
        sp_w, sp_l = await _upsert_sponsor(db, bill_id=bill_id, row=row)
        stats["sponsors"] += sp_w
        stats["sponsors_linked"] += sp_l

    log.info("mb_bills: %s", stats)
    return stats
