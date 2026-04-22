"""Manitoba bill stage timeline from ``billstatus.pdf``.

The MB Legislature exposes its stage timeline only in a session-scoped
PDF at

    https://manitoba.ca/legislature/business/billstatus.pdf

served without ETag/Last-Modified headers worth trusting, and
re-issued in place as the session progresses. We cache each fetch to
``data/mb/billstatus_{YYYYMMDD}.pdf`` keyed by run date, then pipe
through Poppler's ``pdftotext -layout`` (via ``pdf_utils.pdftotext``)
to get column-aligned text.

The parser reads ``pdftotext -raw`` output (content-stream order)
rather than ``-layout`` because MB's 2nd-reading column is narrow
enough that a date like "Dec. 4, 2025" wraps across two lines —
``-layout`` preserves the wrap but interleaves unrelated text from
other columns between the day and year, defeating regex extraction.
Raw mode emits cell content in document order without column
tabulation, so the wrapped year lands on the very next line with
only whitespace between.

Each bill row begins with a line matching ``^(\\d+)\\s+`` at column
zero (the bill-number cell). Continuation lines follow until the
next bill-number line. Within a block we extract every
``Mon. DD, YYYY`` date in document order and map them left-to-right
onto the known stage columns:

    [0] 1st Reading
    [1] 2nd Reading
    [2] Committee/Reported
    [3] Concurrence and 3rd Reading
    [4] Royal Assent
    [5] In Effect

Every bill that appears in the PDF has been at least introduced
(1st reading), so the first date is always the first-reading date.
Subsequent dates fill later stages left-to-right — MB doesn't
publish out-of-order stages in practice.

Formal bills (e.g. Bill 1 "An Act respecting the Administration of
Oaths of Office") carry the literal string ``FORMAL BILL`` instead of
stage-2+ dates; the parser treats those as "first reading only" and
emits exactly one event.

This module is **idempotent per session**: on each run it deletes
every ``bill_events`` row for the targeted session whose
``raw->>'source'`` is ``manitoba-billstatus`` and re-inserts from the
current parse. Any proxy events from other sources (there are none
for MB at the moment) are preserved.
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Iterable, Optional

import httpx
import orjson

from ..db import Database
from .pdf_utils import pdftotext

log = logging.getLogger(__name__)

SOURCE_SYSTEM = "manitoba-billstatus"
BILLSTATUS_URL = "https://manitoba.ca/legislature/business/billstatus.pdf"
# /data is read-only in the scanner container; /tmp is writable but
# ephemeral per invocation. That's fine — billstatus.pdf is ~270 KB
# and re-fetches in <1s, so we pay the round-trip once per command
# run rather than plumbing a new persistent volume mount.
DEFAULT_CACHE_DIR = Path(
    os.environ.get("MB_PDF_CACHE_DIR", "/tmp/mb_pdf_cache")
)

HEADERS = {
    "User-Agent": "CanadianPoliticalDataBot/1.0 (+https://canadianpoliticaldata.ca)",
    "Accept": "application/pdf,*/*;q=0.8",
}

# "Nov. 18, 2025" / "Mar. 4, 2026" / "Dec. 3. 2025" (upstream typo —
# we tolerate a period where a comma should be).
_DATE_RE = re.compile(
    r"\b(?P<mon>Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\.?\s+"
    r"(?P<day>\d{1,2})[.,]\s+(?P<year>\d{4})\b",
    re.IGNORECASE,
)

# Page header / footer boilerplate in pdftotext -raw output. Raw
# emits the repeating page header as a burst of short lines — we skip
# each line individually rather than trying to detect the full block.
_NOISE_RE = re.compile(
    r"^(?:\s*$|"
    r"Bill Status \d{4}-\d{2}|"
    r"LEGISLATIVE ASSEMBLY OF MANITOBA|"
    r"STATUS OF BILLS|"
    r"(?:FIRST|SECOND|THIRD|FOURTH|FIFTH) SESSION|"
    r"\(November |\(December |\(January |\(February |"
    r"\(March |\(April |\(May |\(June |"
    r"Bill\s*$|No\.\s*$|Title Sponsor|"
    r"1st\s*$|2nd\s*$|Committee/\s*$|Amended Report|Stage\s*$|Amend\.\s*$|"
    r"Reading\s*$|Concurrence\s*$|and 3rd\s*$|Royal\s*$|Assent\s*$|"
    r"In\s*$|Effect\s*$|Reported\s*$|"
    r"GOVERNMENT BILLS\s*$|"
    r"PRIVATE MEMBERS'? BILLS\s*$|"
    r"PRIVATE BILLS\s*$)",
    re.IGNORECASE,
)

# In -raw mode the bill number lands at column zero followed by a
# space and the title's first word.
_BILL_START_RE = re.compile(r"^(?P<num>\d{1,4})\s+[A-Z]")

_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

_STAGES: list[tuple[str, str]] = [
    ("first_reading",   "First Reading"),
    ("second_reading",  "Second Reading"),
    ("committee",       "Committee/Reported"),
    ("third_reading",   "Concurrence and Third Reading"),
    ("royal_assent",    "Royal Assent"),
    ("in_effect",       "In Effect"),
]


@dataclass
class BillBlock:
    bill_number: str
    raw_lines: list[str]

    @property
    def raw_text(self) -> str:
        return "\n".join(self.raw_lines)


@dataclass
class ParsedStage:
    stage: str
    stage_label: str
    event_date: date
    committee_name: Optional[str] = None


# ─────────────────────────────────────────────────────────────────────
# Fetch + cache
# ─────────────────────────────────────────────────────────────────────

async def fetch_and_cache(
    *, cache_dir: Path = DEFAULT_CACHE_DIR,
) -> Path:
    """Download ``billstatus.pdf`` to ``cache_dir/billstatus_YYYYMMDD.pdf``.

    Existing file for the current UTC date is reused (one fetch per
    calendar day is plenty; upstream updates at most daily). Older
    files are kept for debugging / historical diffs.
    """
    cache_dir.mkdir(parents=True, exist_ok=True)
    today = datetime.utcnow().strftime("%Y%m%d")
    target = cache_dir / f"billstatus_{today}.pdf"
    if target.exists() and target.stat().st_size > 1000:
        log.info("mb_billstatus: reusing cached %s", target)
        return target

    async with httpx.AsyncClient(headers=HEADERS, follow_redirects=True) as client:
        r = await client.get(BILLSTATUS_URL, timeout=60)
    r.raise_for_status()
    if not r.content or len(r.content) < 5000:
        raise RuntimeError(
            f"billstatus.pdf unexpectedly small ({len(r.content)} bytes)"
        )
    target.write_bytes(r.content)
    log.info(
        "mb_billstatus: fetched %s (%.1f KB)",
        target, len(r.content) / 1024,
    )
    return target


# ─────────────────────────────────────────────────────────────────────
# Parse
# ─────────────────────────────────────────────────────────────────────

def _is_noise(line: str) -> bool:
    return bool(_NOISE_RE.match(line))


def _blocks_from_text(text: str) -> Iterable[BillBlock]:
    """Yield one BillBlock per bill row in the PDF text."""
    current: Optional[BillBlock] = None
    for raw_line in text.splitlines():
        if _is_noise(raw_line):
            continue
        m = _BILL_START_RE.match(raw_line)
        if m:
            if current is not None:
                yield current
            current = BillBlock(bill_number=m.group("num"), raw_lines=[raw_line])
        elif current is not None:
            # Continuation line.
            current.raw_lines.append(raw_line)
    if current is not None:
        yield current


def _parse_date(mon_raw: str, day_raw: str, year_raw: str) -> Optional[date]:
    m = _MONTHS.get(mon_raw[:3].lower())
    if not m:
        return None
    try:
        return date(int(year_raw), m, int(day_raw))
    except ValueError:
        return None


def _extract_dates(block_text: str) -> list[date]:
    out: list[date] = []
    seen: set[date] = set()
    for m in _DATE_RE.finditer(block_text):
        d = _parse_date(m.group("mon"), m.group("day"), m.group("year"))
        if d is None:
            continue
        # Preserve document order, but collapse immediate duplicates
        # (the committee column sometimes repeats the report date on a
        # continuation line; we want each distinct date once).
        if d in seen and out and out[-1] == d:
            continue
        out.append(d)
        seen.add(d)
    return out


def _extract_committee_name(block_text: str) -> Optional[str]:
    """Best-effort: committee name sits between the 2nd date and the
    3rd (committee-report) date in the text. We approximate by taking
    the line(s) of non-date text immediately preceding the 3rd date.

    This is lossy for bills with multi-word committee names that wrap
    across lines — caller should treat the name as informational,
    not authoritative.
    """
    # Find the start of the 3rd date in text.
    matches = list(_DATE_RE.finditer(block_text))
    if len(matches) < 3:
        return None
    third_start = matches[2].start()
    preceding = block_text[matches[1].end():third_start]
    # Strip the title / sponsor / bilingual text; committee names are
    # capitalised identifiers (Justice, Social and Economic
    # Development, Legislative Affairs, etc.). Take the longest run of
    # non-digit words on a single line.
    candidates: list[str] = []
    for line in preceding.splitlines():
        stripped = line.strip()
        if not stripped or any(c.isdigit() for c in stripped):
            continue
        # Skip obvious title/sponsor continuation lines (lower-case
        # lead, slash-separated bilingual, etc.).
        if "/" in stripped or stripped[0].islower():
            continue
        # A committee name is short; title continuations are long.
        if len(stripped) > 60:
            continue
        candidates.append(stripped)
    if not candidates:
        return None
    # Concatenate multi-line committee names in order.
    return " ".join(candidates).strip() or None


def parse_block(block: BillBlock) -> list[ParsedStage]:
    """Map a bill's block into a list of ParsedStage rows."""
    text = block.raw_text
    dates = _extract_dates(text)
    if not dates:
        return []
    committee_name = _extract_committee_name(text) if len(dates) >= 3 else None
    formal_bill = "FORMAL BILL" in text.upper()

    stages: list[ParsedStage] = []
    for i, (canon, label) in enumerate(_STAGES):
        if i >= len(dates):
            break
        stages.append(ParsedStage(
            stage=canon,
            stage_label=label,
            event_date=dates[i],
            committee_name=committee_name if canon == "committee" else None,
        ))
        if formal_bill:
            # Formal bills never progress past first reading in the
            # status PDF; any later dates are spurious.
            break
    return stages


def parse_pdf_text(text: str) -> dict[str, list[ParsedStage]]:
    out: dict[str, list[ParsedStage]] = {}
    for block in _blocks_from_text(text):
        stages = parse_block(block)
        if stages:
            out[block.bill_number] = stages
    return out


# ─────────────────────────────────────────────────────────────────────
# DB writes
# ─────────────────────────────────────────────────────────────────────

async def _session_id(
    db: Database, *, parliament: int, session: int,
) -> Optional[str]:
    row = await db.fetchval(
        """
        SELECT id FROM legislative_sessions
         WHERE level='provincial' AND province_territory='MB'
           AND parliament_number=$1 AND session_number=$2
        """,
        parliament, session,
    )
    return str(row) if row else None


async def _bill_lookup(
    db: Database, *, session_id: str,
) -> dict[str, str]:
    rows = await db.fetch(
        "SELECT bill_number, id FROM bills WHERE session_id = $1",
        session_id,
    )
    return {r["bill_number"]: str(r["id"]) for r in rows}


async def _apply(
    db: Database, *, session_id: str,
    parsed: dict[str, list[ParsedStage]],
) -> dict[str, int]:
    stats = {
        "bills_seen": 0,
        "bills_no_match": 0,
        "events_deleted": 0,
        "events_inserted": 0,
        "latest_status_updated": 0,
    }
    bill_ids = await _bill_lookup(db, session_id=session_id)

    # Delete all previously-written billstatus events for this session
    # in one shot — cheaper than per-bill deletes.
    session_bill_ids = list(bill_ids.values())
    if session_bill_ids:
        deleted = await db.execute(
            """
            DELETE FROM bill_events
             WHERE bill_id = ANY($1::uuid[])
               AND raw->>'source' = $2
            """,
            session_bill_ids, SOURCE_SYSTEM,
        )
        stats["events_deleted"] = (
            int(deleted.split()[-1]) if deleted.startswith("DELETE") else 0
        )

    for bill_number, stages in parsed.items():
        stats["bills_seen"] += 1
        bill_id = bill_ids.get(bill_number)
        if not bill_id:
            stats["bills_no_match"] += 1
            log.debug("mb_billstatus: no bill row for %s (skipped)", bill_number)
            continue
        latest: Optional[ParsedStage] = None
        for st in stages:
            await db.execute(
                """
                INSERT INTO bill_events (
                    bill_id, stage, stage_label, event_date,
                    event_type, committee_name, raw
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb)
                ON CONFLICT ON CONSTRAINT bill_events_uniq DO NOTHING
                """,
                bill_id, st.stage, st.stage_label, st.event_date,
                st.stage_label, st.committee_name,
                orjson.dumps({"source": SOURCE_SYSTEM}).decode(),
            )
            stats["events_inserted"] += 1
            if latest is None or st.event_date >= latest.event_date:
                latest = st

        if latest is not None:
            await db.execute(
                """
                UPDATE bills
                   SET status = $2,
                       status_changed_at = $3,
                       updated_at = now()
                 WHERE id = $1
                """,
                bill_id,
                latest.stage_label,
                datetime.combine(latest.event_date, datetime.min.time()),
            )
            stats["latest_status_updated"] += 1

    return stats


# ─────────────────────────────────────────────────────────────────────
# Orchestrators (one per Click command)
# ─────────────────────────────────────────────────────────────────────

async def fetch(db: Database, *, cache_dir: Optional[str] = None) -> dict[str, int]:
    """Click `fetch-mb-billstatus-pdf` — download + cache only."""
    target = await fetch_and_cache(
        cache_dir=Path(cache_dir) if cache_dir else DEFAULT_CACHE_DIR,
    )
    return {"path_bytes": target.stat().st_size, "cached": 1}


async def parse_events(
    db: Database, *, parliament: int, session: int,
    cache_dir: Optional[str] = None,
) -> dict[str, int]:
    """Click `parse-mb-bill-events` — parse cached PDF + upsert events."""
    cd = Path(cache_dir) if cache_dir else DEFAULT_CACHE_DIR
    # Pick the most recent cached PDF; if none, fetch.
    pdf_path = await fetch_and_cache(cache_dir=cd)
    pdf_bytes = pdf_path.read_bytes()
    text = pdftotext(pdf_bytes, raw=True)
    parsed = parse_pdf_text(text)
    log.info("mb_billstatus: parsed %d bill blocks", len(parsed))

    sid = await _session_id(db, parliament=parliament, session=session)
    if not sid:
        raise RuntimeError(
            f"No legislative_sessions row for MB {parliament}-{session}. "
            "Run ingest-mb-bills first."
        )
    return await _apply(db, session_id=sid, parsed=parsed)
