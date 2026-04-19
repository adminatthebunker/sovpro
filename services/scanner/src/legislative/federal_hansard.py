"""Federal Hansard ingester — openparliament.ca → `speeches` table.

Pulls speeches from openparliament.ca's public JSON API and lands them
as normalized rows in the `speeches` schema shipped in migration 0015.
First bar-raising step for the semantic-search layer: before we can
embed, we need rows.

## Source shape

Two endpoints drive the fetch:

- `GET /debates/?format=json` — paginated list of sitting days for
  the House of Commons. Use it to enumerate which `document_url`s to
  pull speeches from, newest-first.
- `GET /speeches/?format=json&document__url=...&limit=200` — paged
  list of speaker turns for one debate. Each object carries:
    * `time` — ISO timestamp (the /speeches/ endpoint occasionally
      returns a date in year 4043 — an upstream artifact we mask by
      re-deriving date from the debate's own `date` field instead of
      trusting per-speech times).
    * `attribution.{en,fr}` — human-readable speaker line, often with
      party + constituency in parens: `"Doug Eyolfson (Winnipeg West, Lib.)"`.
    * `content.{en,fr}` — HTML bodies with `<p data-HoCid="…"
      data-originallang="en|fr">` markers.
    * `politician_url` — `/politicians/<slug>/` or null. The trailing
      slug is exactly what lives in `politicians.openparliament_slug`.
    * `politician_membership_url` — `/politicians/memberships/<id>/`;
      the integer id uniquely identifies the member's term-of-service
      and is the surest way to resolve party/riding at-time-of-speech
      in a future pass.
    * `procedural` — bool, true for "Mr. Speaker, thank you"-style
      housekeeping. We still ingest these (they anchor session flow),
      but chunk-and-embed will skip them in a later pass.
    * `source_id` — HoC numeric id; stored in raw for traceability.

## Attribution at-time

`speeches.party_at_time` and `speeches.constituency_at_time` are
captured here at-ingest — we never backfill from the politician's
current record. The attribution-line regex extracts:

    "Name (Constituency, PartyAbbrev.)"  →  (Constituency, PartyAbbrev)

and falls back to the politician's current party/riding (via the
resolved `politician_id`) only when the attribution line doesn't carry
them. A future enhancement reads `politician_membership_url` for the
canonical term-level answer.

## Idempotency

The natural key is `(source_system, source_url, sequence)` with
`NULLS NOT DISTINCT` — each speech's path like
`/debates/2026/4/15/doug-eyolfson-1/` is unique per speaker-turn on a
given day. Re-runs over the same date range are safe. `ON CONFLICT`
updates the mutable columns (text, raw, politician_id) so later
politician-slug backfills reflect in prior rows.

## Session attribution

openparliament's `/speeches/` response carries no parliament/session
discriminator for chamber debates (committee URLs embed `44-1/` etc.
but debates use plain dates). The caller provides `--parliament N
--session S` at ingest time; we ensure the `legislative_sessions` row
exists and attach every speech in the run to it. Accurate long-term
auto-detection is a separate backfill job.

## Scope

This module only touches the `speeches` table. Chunking + embedding
are downstream commands (`chunk-speeches`, `embed-speech-chunks`).
Run in order.
"""
from __future__ import annotations

import asyncio
import hashlib
import html as html_lib
import logging
import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, AsyncIterator, Optional

import httpx
import orjson

from ..db import Database

log = logging.getLogger(__name__)

SOURCE_SYSTEM = "openparliament"
API_ROOT = "https://api.openparliament.ca"
WEB_ROOT = "https://openparliament.ca"
REQUEST_TIMEOUT = 60

# Known federal parliament/session date ranges. Used to:
#   1. Auto-derive --since / --until when the user ingests by (parliament,
#      session) without explicit dates. Without this, fetch_debates walks
#      every Hansard sitting day back to 1994 regardless of the
#      parliament/session flags, which was the bug that mislabeled
#      ~896k speeches as P43-S2 on 2026-04-18/19.
#   2. Power the `fix-speech-sessions` retag command that repairs rows
#      already mis-tagged.
#
# Dates are the first and last sitting days of each session (inclusive).
# For the current session (no end yet), end_date is a sentinel far in the
# future; override with --until if you want tighter bounds.
#
# Source: ourcommons.ca parliamentary-sessions reference. Revisit when a
# parliament ends / new one begins.
FEDERAL_SESSION_DATES: list[tuple[int, int, date, date]] = [
    (35, 1, date(1994,  1, 17), date(1996,  2,  2)),
    (35, 2, date(1996,  2, 27), date(1997,  4, 27)),
    (36, 1, date(1997,  9, 22), date(1999,  9, 18)),
    (36, 2, date(1999, 10, 12), date(2000, 10, 22)),
    (37, 1, date(2001,  1, 29), date(2002,  9, 16)),
    (37, 2, date(2002,  9, 30), date(2003, 11, 12)),
    (37, 3, date(2004,  2,  2), date(2004,  5, 23)),
    (38, 1, date(2004, 10,  4), date(2005, 11, 29)),
    (39, 1, date(2006,  4,  3), date(2007,  9, 14)),
    (39, 2, date(2007, 10, 16), date(2008,  9,  7)),
    (40, 1, date(2008, 11, 18), date(2008, 12,  4)),
    (40, 2, date(2009,  1, 26), date(2009, 12, 30)),
    (40, 3, date(2010,  3,  3), date(2011,  3, 26)),
    (41, 1, date(2011,  6,  2), date(2013,  9, 13)),
    (41, 2, date(2013, 10, 16), date(2015,  8,  2)),
    (42, 1, date(2015, 12,  3), date(2019,  9, 11)),
    (43, 1, date(2019, 12,  5), date(2020,  8, 18)),
    (43, 2, date(2020,  9, 23), date(2021,  8, 15)),
    (44, 1, date(2021, 11, 22), date(2099, 12, 31)),   # open-ended; cap with --until when ended
]


def federal_session_bounds(parliament: int, session: int) -> tuple[date, date]:
    """Return (start, end) for a federal parliament/session. Raises
    ValueError for unknown tuples so callers don't silently walk the
    whole corpus."""
    for p, s, start, end in FEDERAL_SESSION_DATES:
        if p == parliament and s == session:
            return start, end
    raise ValueError(
        f"no date bounds known for Parliament {parliament}, Session {session}. "
        f"Add to FEDERAL_SESSION_DATES in federal_hansard.py."
    )


def federal_session_for_date(d: date) -> Optional[tuple[int, int]]:
    """Reverse lookup: which (parliament, session) does this date fall into?
    Returns None for dates outside all known ranges (pre-1994 or future)."""
    for p, s, start, end in FEDERAL_SESSION_DATES:
        if start <= d <= end:
            return p, s
    return None

HEADERS = {
    "User-Agent": "SovereignWatchBot/1.0 (+https://canadianpoliticaldata.ca; civic-transparency)",
    "Accept": "application/json",
}

# ── Attribution parser ───────────────────────────────────────────────
# Handles shapes like:
#   "Doug Eyolfson (Winnipeg West, Lib.)"
#   "Mr. Han Dong (Don Valley North, Lib.)"
#   "Mrs. Cathay Wagantall (Yorkton—Melville, CPC)"
#   "The Speaker"
#   "The Vice-Chair (Ms. Jean Yip)"
# We only pull constituency + party when the parens shape matches
# "(<riding>, <abbrev>)".
ATTRIB_RE = re.compile(
    r"^(?P<name>.+?)\s*\((?P<constituency>[^,()]+),\s*(?P<party>[^,()]+?)\.?\)\s*$"
)
TITLE_PREFIX = ("Mr.", "Mrs.", "Ms.", "Miss", "Dr.", "Hon.")


@dataclass
class ParsedAttribution:
    name: str
    party: Optional[str]
    constituency: Optional[str]
    role: Optional[str]  # "The Speaker", "The Vice-Chair", etc.


def parse_attribution(raw: str) -> ParsedAttribution:
    """Best-effort split of openparliament's attribution string."""
    raw = raw.strip()
    if not raw:
        return ParsedAttribution(name="", party=None, constituency=None, role=None)
    m = ATTRIB_RE.match(raw)
    if m:
        name = m.group("name").strip()
        # Strip leading title so name matches politicians.name cleanly.
        for t in TITLE_PREFIX:
            if name.startswith(t + " "):
                name = name[len(t) + 1 :]
                break
        return ParsedAttribution(
            name=name,
            party=m.group("party").strip().rstrip("."),
            constituency=m.group("constituency").strip(),
            role=None,
        )
    # Role-style (The Speaker / The Vice-Chair / The Chair)
    if raw.lower().startswith(("the speaker", "the vice-chair", "the chair", "the deputy")):
        return ParsedAttribution(name=raw, party=None, constituency=None, role=raw)
    return ParsedAttribution(name=raw, party=None, constituency=None, role=None)


# ── HTML → text ──────────────────────────────────────────────────────
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def html_to_text(html: str) -> str:
    """Cheap HTML strip tuned for openparliament's `<p data-...>…</p>` bodies."""
    if not html:
        return ""
    # Preserve paragraph breaks before we strip tags.
    s = re.sub(r"</p\s*>", "\n", html, flags=re.I)
    s = re.sub(r"<br\s*/?>", "\n", s, flags=re.I)
    s = _TAG_RE.sub(" ", s)
    s = html_lib.unescape(s)
    # Collapse runs of whitespace but keep intentional line breaks.
    lines = [ _WS_RE.sub(" ", line).strip() for line in s.splitlines() ]
    return "\n".join([line for line in lines if line])


_ORIGINALLANG_RE = re.compile(r'data-originallang="([a-z]{2})"', re.I)


def detect_language(content: dict[str, str]) -> str:
    """Infer primary source language from data-originallang markers.

    openparliament renders every speech in both EN and FR; `originallang`
    identifies the language the speaker actually used. Take the most
    common value across the EN body's paragraphs as ground truth.
    """
    for key in ("en", "fr"):
        body = content.get(key) or ""
        codes = _ORIGINALLANG_RE.findall(body)
        if codes:
            # Plurality wins; ties resolve to 'en' as a deterministic default.
            en_count = sum(1 for c in codes if c.lower() == "en")
            fr_count = sum(1 for c in codes if c.lower() == "fr")
            if fr_count > en_count:
                return "fr"
            return "en"
    return "en"


def normalize_for_hash(text: str) -> str:
    """Whitespace+unicode-normalised text, used as the sha256 dedup key."""
    t = unicodedata.normalize("NFKC", text)
    t = _WS_RE.sub(" ", t).strip().lower()
    return t


def content_hash(text: str) -> str:
    return hashlib.sha256(normalize_for_hash(text).encode("utf-8")).hexdigest()


# ── API client ───────────────────────────────────────────────────────

# Transient HTTP errors we retry with exponential backoff. openparliament.ca
# occasionally returns 5xx, times out under load, or drops a connection
# mid-response. Before the retry wrapper, a single ReadTimeout mid-session
# would kill the whole ingest (observed 2026-04-18 on P43-S2 at T+1h30m,
# after 850 k speeches had landed).
RETRYABLE_EXC = (
    httpx.ReadTimeout,
    httpx.ConnectTimeout,
    httpx.PoolTimeout,
    httpx.ReadError,
    httpx.WriteError,
    httpx.ConnectError,
    httpx.RemoteProtocolError,
)
RETRY_BACKOFF_SECONDS = (2, 4, 8, 16, 32)
RETRY_ON_STATUS = (500, 502, 503, 504)


async def _get_with_retry(client: httpx.AsyncClient, url: str) -> httpx.Response:
    """httpx GET with exponential-backoff retry on transient failures.

    Retries on network-layer exceptions (timeouts, pool exhaustion,
    dropped connections) and 5xx responses. 4xx responses are surfaced
    immediately — those are real errors.
    """
    last_exc: Optional[BaseException] = None
    for attempt, delay in enumerate((0,) + RETRY_BACKOFF_SECONDS):
        if delay:
            log.warning(
                "openparliament retry %d/%d after %ds — last error: %s",
                attempt, len(RETRY_BACKOFF_SECONDS), delay, last_exc,
            )
            await asyncio.sleep(delay)
        try:
            r = await client.get(url)
            if r.status_code in RETRY_ON_STATUS:
                last_exc = httpx.HTTPStatusError(
                    f"upstream {r.status_code}", request=r.request, response=r
                )
                continue
            return r
        except RETRYABLE_EXC as exc:
            last_exc = exc
            continue
    # Exhausted retries — re-raise the last exception so the caller gets
    # a traceable failure at the original call site.
    assert last_exc is not None
    raise last_exc


async def _iter_json(
    client: httpx.AsyncClient, path: str
) -> AsyncIterator[dict[str, Any]]:
    """Follow the next_url chain on any openparliament list endpoint."""
    next_path: Optional[str] = path
    while next_path:
        url = next_path if next_path.startswith("http") else f"{API_ROOT}{next_path}"
        r = await _get_with_retry(client, url)
        r.raise_for_status()
        data = r.json()
        for obj in data.get("objects", []):
            yield obj
        next_path = (data.get("pagination") or {}).get("next_url")


async def fetch_debates(
    client: httpx.AsyncClient,
    *,
    since: Optional[date] = None,
    until: Optional[date] = None,
    limit: Optional[int] = None,
) -> list[dict[str, Any]]:
    """Fetch sitting-day debate documents, newest first."""
    params = ["format=json", "limit=100"]
    if since:
        params.append(f"date__gte={since.isoformat()}")
    if until:
        params.append(f"date__lte={until.isoformat()}")
    qs = "&".join(params)
    out: list[dict[str, Any]] = []
    async for obj in _iter_json(client, f"/debates/?{qs}"):
        out.append(obj)
        if limit and len(out) >= limit:
            break
    return out


async def fetch_speeches_for_document(
    client: httpx.AsyncClient, document_url: str
) -> list[dict[str, Any]]:
    """Return every speech for one Hansard document in the API's natural
    order — which is already chronological within the day."""
    qs = f"format=json&document__url={document_url}&limit=500"
    rows: list[dict[str, Any]] = []
    async for obj in _iter_json(client, f"/speeches/?{qs}"):
        rows.append(obj)
    # openparliament returns speeches in sitting-day order; we preserve
    # it. source_id shapes aren't uniform (numeric for most, "p<digits>"
    # for some), so sorting on it would fail.
    return rows


# ── Upsert helpers ───────────────────────────────────────────────────


async def ensure_session(
    db: Database, parliament: int, session: int
) -> str:
    """Return the legislative_sessions.id for federal (parliament, session),
    creating the row if absent."""
    row = await db.fetchrow(
        """
        INSERT INTO legislative_sessions
            (level, province_territory, parliament_number, session_number,
             name, source_system, source_url)
        VALUES ('federal', NULL, $1, $2, $3, $4, $5)
        ON CONFLICT (level, province_territory, parliament_number, session_number)
        DO UPDATE SET updated_at = now()
        RETURNING id
        """,
        parliament,
        session,
        f"{parliament}th Parliament, Session {session}",
        SOURCE_SYSTEM,
        f"{WEB_ROOT}/debates/",
    )
    return str(row["id"])


async def load_slug_to_politician(db: Database) -> dict[str, str]:
    """One-shot cache of openparliament_slug → politicians.id (uuid str)."""
    rows = await db.fetch(
        "SELECT id, openparliament_slug FROM politicians "
        "WHERE openparliament_slug IS NOT NULL"
    )
    return {r["openparliament_slug"]: str(r["id"]) for r in rows}


# ── Main ingest loop ────────────────────────────────────────────────


@dataclass
class IngestStats:
    debates_scanned: int = 0
    speeches_seen: int = 0
    speeches_inserted: int = 0
    speeches_updated: int = 0
    speeches_unresolved: int = 0
    skipped_empty: int = 0


def _extract_slug(politician_url: Optional[str]) -> Optional[str]:
    """'/politicians/doug-eyolfson/' → 'doug-eyolfson'"""
    if not politician_url:
        return None
    m = re.match(r"^/politicians/([^/]+)/?$", politician_url)
    return m.group(1) if m else None


def _pick_speech_type(document_url: str) -> str:
    if document_url.startswith("/debates/"):
        return "floor"
    if document_url.startswith("/committees/"):
        return "committee"
    return "other"


async def ingest(
    db: Database,
    *,
    parliament: int,
    session: int,
    since: Optional[date] = None,
    until: Optional[date] = None,
    limit_debates: Optional[int] = None,
    limit_speeches: Optional[int] = None,
) -> IngestStats:
    """Fetch + upsert federal Hansard speeches.

    Args:
        db: connected Database.
        parliament / session: federal session the fetched speeches belong
            to. Used to create / attach the `legislative_sessions` row.
        since / until: optional date bounds (inclusive).
        limit_debates: cap on sitting days to fetch.
        limit_speeches: cap on TOTAL speeches ingested across all debates.
            Useful for smoke tests.
    """
    stats = IngestStats()
    session_id = await ensure_session(db, parliament, session)
    slug_to_pol = await load_slug_to_politician(db)

    async with httpx.AsyncClient(
        timeout=REQUEST_TIMEOUT, headers=HEADERS, follow_redirects=True
    ) as client:
        debates = await fetch_debates(
            client, since=since, until=until, limit=limit_debates
        )
        log.info("fetched %d debates (parl %d sess %d, since=%s)",
                 len(debates), parliament, session, since)
        for debate in debates:
            if limit_speeches and stats.speeches_inserted + stats.speeches_updated >= limit_speeches:
                break
            stats.debates_scanned += 1
            doc_url = debate.get("url")  # e.g. /debates/2026/4/15/
            if not doc_url:
                continue
            debate_date_raw = debate.get("date")
            try:
                debate_date = (
                    datetime.strptime(debate_date_raw, "%Y-%m-%d").date()
                    if debate_date_raw
                    else None
                )
            except (TypeError, ValueError):
                debate_date = None

            speeches = await fetch_speeches_for_document(client, doc_url)
            log.info("debate %s: %d speeches", doc_url, len(speeches))
            for seq, sp in enumerate(speeches):
                if limit_speeches and stats.speeches_inserted + stats.speeches_updated >= limit_speeches:
                    break
                stats.speeches_seen += 1
                inserted = await _upsert_speech(
                    db,
                    session_id=session_id,
                    speech=sp,
                    sequence=seq,
                    debate_date=debate_date,
                    slug_to_pol=slug_to_pol,
                )
                if inserted == "inserted":
                    stats.speeches_inserted += 1
                elif inserted == "updated":
                    stats.speeches_updated += 1
                elif inserted == "skipped":
                    stats.skipped_empty += 1
                if sp.get("politician_url") and not slug_to_pol.get(
                    _extract_slug(sp["politician_url"]) or ""
                ):
                    stats.speeches_unresolved += 1

    log.info(
        "ingest done: %d debates, %d speeches seen, %d inserted, %d updated, "
        "%d skipped_empty, %d with unresolved politician slug",
        stats.debates_scanned,
        stats.speeches_seen,
        stats.speeches_inserted,
        stats.speeches_updated,
        stats.skipped_empty,
        stats.speeches_unresolved,
    )
    return stats


async def _upsert_speech(
    db: Database,
    *,
    session_id: str,
    speech: dict[str, Any],
    sequence: int,
    debate_date: Optional[date],
    slug_to_pol: dict[str, str],
) -> str:
    """Insert or update a single speech row. Returns 'inserted' | 'updated' | 'skipped'."""
    content = speech.get("content") or {}
    language = detect_language(content)
    # Primary text body = English first, French fallback. We keep both in
    # raw for bilingual display; the `text` column is source-language.
    body_html = content.get(language) or content.get("en") or content.get("fr") or ""
    text = html_to_text(body_html)
    if not text:
        return "skipped"

    attrib_line = (speech.get("attribution") or {}).get(language) or \
                  (speech.get("attribution") or {}).get("en") or ""
    parsed = parse_attribution(attrib_line)

    politician_slug = _extract_slug(speech.get("politician_url"))
    politician_id = slug_to_pol.get(politician_slug or "")

    source_url = f"{WEB_ROOT}{speech.get('url') or ''}"

    # Time handling: openparliament's `/speeches/` time field can drift
    # (year 4043 artifact observed); prefer the debate's own date when
    # the speech time looks invalid.
    spoken_at = None
    time_raw = speech.get("time")
    if time_raw:
        try:
            parsed_ts = datetime.strptime(time_raw, "%Y-%m-%d %H:%M:%S")
            # Reject obviously-wrong years; fall back to debate_date
            if 1900 <= parsed_ts.year <= 2100:
                spoken_at = parsed_ts.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            pass
    if spoken_at is None and debate_date is not None:
        spoken_at = datetime.combine(debate_date, datetime.min.time()).replace(
            tzinfo=timezone.utc
        )

    speech_type = _pick_speech_type(speech.get("document_url") or "")
    if speech.get("procedural"):
        # Keep the canonical type but tag in raw for chunk/embed filters.
        pass

    raw_payload = {
        "op_speech": speech,
    }
    raw_json = orjson.dumps(raw_payload).decode("utf-8")

    ch = content_hash(text)

    # UNIQUE NULLS NOT DISTINCT (source_system, source_url, sequence)
    # is already defined in migration 0015, so ON CONFLICT resolves on
    # that constraint.
    result = await db.fetchrow(
        """
        INSERT INTO speeches (
            session_id, politician_id, level, province_territory,
            speaker_name_raw, speaker_role, party_at_time, constituency_at_time,
            confidence, speech_type, spoken_at, sequence, language,
            text, word_count,
            source_system, source_url, source_anchor,
            raw, raw_html, content_hash
        ) VALUES (
            $1, $2, 'federal', NULL,
            $3, $4, $5, $6,
            $7, $8, $9, $10, $11,
            $12, $13,
            $14, $15, $16,
            $17::jsonb, $18, $19
        )
        ON CONFLICT (source_system, source_url, sequence)
        DO UPDATE SET
            politician_id = EXCLUDED.politician_id,
            speaker_name_raw = EXCLUDED.speaker_name_raw,
            speaker_role = EXCLUDED.speaker_role,
            party_at_time = EXCLUDED.party_at_time,
            constituency_at_time = EXCLUDED.constituency_at_time,
            language = EXCLUDED.language,
            text = EXCLUDED.text,
            word_count = EXCLUDED.word_count,
            raw = EXCLUDED.raw,
            raw_html = EXCLUDED.raw_html,
            content_hash = EXCLUDED.content_hash,
            updated_at = now()
        RETURNING (xmax = 0) AS inserted
        """,
        session_id,
        politician_id,
        parsed.name or attrib_line or "",
        parsed.role,
        parsed.party,
        parsed.constituency,
        1.0,
        speech_type,
        spoken_at,
        sequence,
        language,
        text,
        len(text.split()),
        SOURCE_SYSTEM,
        source_url,
        str(speech.get("source_id") or ""),
        raw_json,
        body_html,
        ch,
    )
    return "inserted" if result and result["inserted"] else "updated"
