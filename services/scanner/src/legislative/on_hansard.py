"""Ontario Hansard ingester — ola.org JSON → ``speeches`` table.

End-to-end:

1. **Discovery.** GET the session HTML at
   ``/en/legislative-business/house-documents/parliament-{P}/session-{S}/``
   and regex-extract every per-sitting href shaped like
   ``/{discovery}/{YYYY-MM-DD}/hansard``.
2. **Fetch.** For each sitting, GET ``{sitting_url}?_format=json``.
   Drupal returns a ``hansard_document`` node carrying ``body.value``
   (the transcript HTML) plus structured ``field_date`` /
   ``field_parliament_sessions`` / ``field_associated_bill_multi`` fields.
   We use ``body.value`` for parsing and ``field_date`` for spoken_at.
3. **Parse.** Delegate to :mod:`on_hansard_parse` which walks the
   ``<p class="speakerStart"><strong>{ATTR}:</strong>{TEXT}</p>``
   pattern.
4. **Resolve.** Name-based against ``politicians`` for ON. Three-tier
   cascade: parens_name first (exact match for presiding officers),
   then full_name, then surname-only (single-hit). Bare-role turns
   without parens defer to ``presiding_officer_resolver --province ON``.
5. **Upsert.** Write to ``speeches`` with
   ``source_system='hansard-on'`` and unique key
   ``(source_system, source_url, sequence)``. Full body HTML is stored
   only on the ``sequence=1`` row to avoid write amplification.

Chunking and embedding are jurisdiction-agnostic downstream passes
(``chunk-speeches`` + ``embed-speech-chunks``); they are NOT called
from this module.
"""
from __future__ import annotations

import asyncio
import logging
import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime
from typing import Optional

import httpx
import orjson

from ..db import Database
from . import on_hansard_parse as parse_mod

log = logging.getLogger(__name__)

SOURCE_SYSTEM = "hansard-on"
BASE_URL = "https://www.ola.org"
SESSION_INDEX_URL = (
    "https://www.ola.org/en/legislative-business/house-documents/"
    "parliament-{parliament}/session-{session}/"
)

REQUEST_TIMEOUT = 60
REQUEST_DELAY_SECONDS = 1.0  # Polite to ola.org

HEADERS = {
    "User-Agent": "CanadianPoliticalDataBot/1.0 (+https://canadianpoliticaldata.ca)",
    "Accept": "application/json,text/html",
    "Accept-Language": "en-CA,en;q=0.9",
}

RETRYABLE_EXC = (
    httpx.ReadTimeout,
    httpx.ConnectTimeout,
    httpx.PoolTimeout,
    httpx.ReadError,
    httpx.WriteError,
    httpx.ConnectError,
    httpx.RemoteProtocolError,
)
RETRY_BACKOFF_SECONDS = (2, 4, 8, 16)
RETRY_ON_STATUS = (500, 502, 503, 504)


async def _get_with_retry(client: httpx.AsyncClient, url: str) -> httpx.Response:
    last_exc: Optional[BaseException] = None
    for attempt, delay in enumerate((0,) + RETRY_BACKOFF_SECONDS):
        if delay:
            log.warning(
                "retry %d/%d after %ds: %s",
                attempt, len(RETRY_BACKOFF_SECONDS), delay, url,
            )
            await asyncio.sleep(delay)
        try:
            r = await client.get(url, timeout=REQUEST_TIMEOUT)
        except RETRYABLE_EXC as exc:
            last_exc = exc
            continue
        if r.status_code in RETRY_ON_STATUS:
            last_exc = httpx.HTTPStatusError(
                f"HTTP {r.status_code}", request=r.request, response=r,
            )
            continue
        return r
    if last_exc:
        raise last_exc
    raise RuntimeError(f"unreachable: {url}")


# ── Discovery ───────────────────────────────────────────────────────
# Per-sitting hrefs like:
#   /en/legislative-business/house-documents/parliament-44/session-1/2025-04-14/hansard
_SITTING_HREF_RE = re.compile(
    r"href=\"(?P<href>/en/legislative-business/house-documents/"
    r"parliament-(?P<parliament>\d+)/session-(?P<session>\d+)/"
    r"(?P<ymd>\d{4}-\d{2}-\d{2})/hansard)\"",
    re.IGNORECASE,
)


@dataclass
class SittingRef:
    href: str            # /en/legislative-business/.../{YYYY-MM-DD}/hansard
    url: str             # absolute URL
    sitting_date: date


async def discover_sitting_refs(
    client: httpx.AsyncClient, *, parliament: int, session: int,
) -> list[SittingRef]:
    """Walk the session index and return sitting URLs, oldest-first."""
    index_url = SESSION_INDEX_URL.format(parliament=parliament, session=session)
    r = await _get_with_retry(client, index_url)
    r.raise_for_status()
    html = r.text
    seen: set[str] = set()
    refs: list[SittingRef] = []
    for m in _SITTING_HREF_RE.finditer(html):
        href = m.group("href")
        if href in seen:
            continue
        seen.add(href)
        try:
            sitting_date = date.fromisoformat(m.group("ymd"))
        except ValueError:
            continue
        refs.append(SittingRef(
            href=href, url=BASE_URL + href, sitting_date=sitting_date,
        ))
    refs.sort(key=lambda r: r.sitting_date)
    return refs


# ── Session upsert ──────────────────────────────────────────────────

async def ensure_session(
    db: Database, *, parliament: int, session: int,
) -> str:
    row = await db.fetchrow(
        """
        INSERT INTO legislative_sessions
            (level, province_territory, parliament_number, session_number,
             name, source_system, source_url)
        VALUES ('provincial', 'ON', $1, $2, $3, $4, $5)
        ON CONFLICT (level, province_territory, parliament_number, session_number)
        DO UPDATE SET updated_at = now()
        RETURNING id
        """,
        parliament, session,
        f"Parliament {parliament}, Session {session}",
        SOURCE_SYSTEM,
        SESSION_INDEX_URL.format(parliament=parliament, session=session),
    )
    return str(row["id"])


# ── Speaker resolution ──────────────────────────────────────────────

_WS_RE = re.compile(r"\s+")


def _norm(s: str) -> str:
    if not s:
        return ""
    t = unicodedata.normalize("NFKD", s.replace(" ", " "))
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = t.lower()
    t = re.sub(r"[^\w\s-]", " ", t)
    return _WS_RE.sub(" ", t).strip()


@dataclass
class SpeakerLookup:
    """Name-based lookup for ON politicians.

    ON Hansard transcripts publish speakers by name, not by per-speaker
    /members/<slug> anchors (unlike NS). So resolution is name-based:
    primary index by full_name, fallback by surname (single-hit only).
    """
    by_full_name: dict[str, list[dict]]
    by_surname: dict[str, list[dict]]


async def load_on_speaker_lookup(db: Database) -> SpeakerLookup:
    rows = await db.fetch(
        """
        SELECT id::text AS id,
               name, first_name, last_name,
               party, constituency_name
          FROM politicians
         WHERE level              = 'provincial'
           AND province_territory = 'ON'
        """
    )
    by_full_name: dict[str, list[dict]] = {}
    by_surname: dict[str, list[dict]] = {}
    for r in rows:
        row = dict(r)
        full = _norm(row["name"] or "")
        if full:
            by_full_name.setdefault(full, []).append(row)
        fl = _norm(f"{row['first_name'] or ''} {row['last_name'] or ''}")
        if fl and fl != full:
            by_full_name.setdefault(fl, []).append(row)
        last = _norm(row["last_name"] or "")
        if last:
            by_surname.setdefault(last, []).append(row)
    log.info(
        "on_hansard: loaded %d ON politicians "
        "(full_names=%d unique_surnames=%d)",
        len(rows), len(by_full_name),
        sum(1 for v in by_surname.values() if len(v) == 1),
    )
    return SpeakerLookup(by_full_name=by_full_name, by_surname=by_surname)


def _try_match_person(
    lookup: SpeakerLookup,
    full_name: Optional[str],
    surname: Optional[str],
) -> tuple[Optional[dict], str]:
    """Try full_name then surname-only; return (politician_row, status)."""
    if full_name:
        hits = lookup.by_full_name.get(_norm(full_name), [])
        if len(hits) == 1:
            return hits[0], "resolved"
        if len(hits) > 1:
            return None, "ambiguous"
    if surname:
        hits = lookup.by_surname.get(_norm(surname), [])
        if len(hits) == 1:
            return hits[0], "resolved"
        if len(hits) > 1:
            return None, "ambiguous"
    return None, "unresolved"


def _resolve_speech(
    lookup: SpeakerLookup, ps: parse_mod.ParsedSpeech,
) -> tuple[Optional[dict], str, float]:
    """Return (politician_row, status, confidence).

    Resolution cascade (matches plan):
      1. parens_name set AND role set (e.g. "The Speaker (Hon. Donna Skelly)"):
         match parens_name → resolved (confidence 0.95).
      2. Plain person: match full_name then surname → resolved (1.0).
      3. Bare role (speaker_role set, no parens person): defer to
         presiding-officer resolver (politician_id NULL).
      4. Otherwise: unresolved.
    """
    # Case 1: role with inline parens person — try to resolve from parens.
    if ps.speaker_role and ps.full_name:
        # full_name was decomposed from the parens person.
        row, status = _try_match_person(lookup, ps.full_name, ps.surname)
        if status == "resolved":
            return row, "resolved", 0.95
        if status == "ambiguous":
            return None, "ambiguous", 0.0
        # Parens person not in roster — fall through to role bucket.
        return None, "role", 0.5

    # Case 2: plain person attribution.
    if not ps.speaker_role and (ps.full_name or ps.surname):
        row, status = _try_match_person(lookup, ps.full_name, ps.surname)
        if status == "resolved":
            return row, "resolved", 1.0
        if status == "ambiguous":
            return None, "ambiguous", 0.0
        return None, "unresolved", 0.0

    # Case 3: bare role.
    if ps.speaker_role:
        return None, "role", 0.5

    return None, "unresolved", 0.0


# ── Upsert ──────────────────────────────────────────────────────────

@dataclass
class IngestStats:
    sittings_scanned: int = 0
    speeches_seen: int = 0
    speeches_inserted: int = 0
    speeches_updated: int = 0
    speeches_resolved: int = 0
    speeches_role_only: int = 0
    speeches_ambiguous: int = 0
    speeches_unresolved: int = 0
    parse_errors: int = 0
    skipped_empty: int = 0


async def _upsert_speech(
    db: Database, *, session_id: str, ref: SittingRef,
    parsed: parse_mod.ParsedSpeech,
    politician: Optional[dict], confidence: float,
    page_html: Optional[str], parent_meta: dict,
) -> str:
    if not parsed.text.strip():
        return "skipped"
    politician_id = politician["id"] if politician else None
    party = politician["party"] if politician else None
    constituency = politician["constituency_name"] if politician else None

    raw_payload = {
        "on_hansard": {
            "sitting_date": parsed.spoken_at.date().isoformat(),
            "parliament": parent_meta.get("parliament"),
            "session": parent_meta.get("session"),
            "node_id": parent_meta.get("nid"),
            "field_associated_bills": parent_meta.get("field_associated_bills"),
            "speaker_role": parsed.speaker_role,
            "parens_name": parsed.parens_name,
            "honorific": parsed.honorific,
            "surname": parsed.surname,
            "full_name": parsed.full_name,
        }
    }
    raw_json = orjson.dumps(raw_payload).decode("utf-8")

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
            $1, $2, 'provincial', 'ON',
            $3, $4, $5, $6,
            $7, $8, $9, $10, $11,
            $12, $13,
            $14, $15, NULL,
            $16::jsonb, $17, $18
        )
        ON CONFLICT (source_system, source_url, sequence)
        DO UPDATE SET
            politician_id = EXCLUDED.politician_id,
            speaker_name_raw = EXCLUDED.speaker_name_raw,
            speaker_role = EXCLUDED.speaker_role,
            party_at_time = EXCLUDED.party_at_time,
            constituency_at_time = EXCLUDED.constituency_at_time,
            confidence = EXCLUDED.confidence,
            speech_type = EXCLUDED.speech_type,
            spoken_at = EXCLUDED.spoken_at,
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
        parsed.speaker_name_raw,
        parsed.speaker_role,
        party,
        constituency,
        confidence,
        parsed.speech_type,
        parsed.spoken_at,
        parsed.sequence,
        parsed.language,
        parsed.text,
        parsed.word_count,
        SOURCE_SYSTEM,
        ref.url,
        raw_json,
        page_html if parsed.sequence == 1 else None,
        parsed.content_hash,
    )
    return "inserted" if result and result["inserted"] else "updated"


# ── Helpers ─────────────────────────────────────────────────────────

def _extract_associated_bills(node: dict) -> list[str]:
    """Pull bill UUIDs / target_ids from the JSON node's bill refs."""
    out: list[str] = []
    for f in ("field_associated_bill", "field_associated_bill_multi"):
        for entry in node.get(f, []) or []:
            tid = entry.get("target_uuid") or entry.get("target_id")
            if tid is not None:
                out.append(str(tid))
    return out


def _date_from_node(node: dict) -> Optional[date]:
    fd = (node.get("field_date") or [{}])[0]
    val = fd.get("value")
    if not val:
        return None
    try:
        return date.fromisoformat(val[:10])
    except (ValueError, TypeError):
        return None


def _ymd_from_url(url: str) -> Optional[date]:
    m = re.search(r"/(?P<ymd>\d{4}-\d{2}-\d{2})/hansard", url)
    if not m:
        return None
    try:
        return date.fromisoformat(m.group("ymd"))
    except ValueError:
        return None


# ── Orchestrator ────────────────────────────────────────────────────

async def ingest(
    db: Database,
    *,
    parliament: int,
    session: int,
    since: Optional[date] = None,
    until: Optional[date] = None,
    limit_sittings: Optional[int] = None,
    limit_speeches: Optional[int] = None,
    one_off_url: Optional[str] = None,
) -> IngestStats:
    stats = IngestStats()
    session_id = await ensure_session(
        db, parliament=parliament, session=session,
    )
    lookup = await load_on_speaker_lookup(db)

    async with httpx.AsyncClient(
        timeout=REQUEST_TIMEOUT, headers=HEADERS, follow_redirects=True,
    ) as client:
        if one_off_url:
            sd = _ymd_from_url(one_off_url)
            if sd is None:
                log.error("on_hansard: bad --url %s (no YYYY-MM-DD)", one_off_url)
                return stats
            href_guess = one_off_url.replace(BASE_URL, "")
            refs = [SittingRef(href=href_guess, url=one_off_url, sitting_date=sd)]
        else:
            refs = await discover_sitting_refs(
                client, parliament=parliament, session=session,
            )
            if since:
                refs = [r for r in refs if r.sitting_date >= since]
            if until:
                refs = [r for r in refs if r.sitting_date <= until]
            if limit_sittings:
                refs = refs[-limit_sittings:]

        log.info(
            "on_hansard: processing %d sittings (parliament=%d session=%d)",
            len(refs), parliament, session,
        )

        for ref in refs:
            if limit_speeches and (
                stats.speeches_inserted + stats.speeches_updated
            ) >= limit_speeches:
                break
            stats.sittings_scanned += 1
            json_url = ref.url + "?_format=json"
            try:
                r = await _get_with_retry(client, json_url)
                r.raise_for_status()
                node = r.json()
            except Exception as exc:
                log.warning("on_hansard: sitting %s: fetch failed: %s", ref.url, exc)
                continue

            body_obj = (node.get("body") or [{}])[0]
            body_html = body_obj.get("value") or ""
            if not body_html:
                log.warning("on_hansard: sitting %s: empty body.value", ref.url)
                stats.parse_errors += 1
                await asyncio.sleep(REQUEST_DELAY_SECONDS)
                continue

            sitting_date = _date_from_node(node) or ref.sitting_date

            try:
                result = parse_mod.extract_speeches(
                    body_html,
                    sitting_url=ref.url,
                    sitting_date=sitting_date,
                )
            except Exception as exc:
                log.warning("on_hansard: sitting %s: parse failed: %s", ref.url, exc)
                stats.parse_errors += 1
                await asyncio.sleep(REQUEST_DELAY_SECONDS)
                continue

            if len(result.speeches) < 3:
                log.warning(
                    "on_hansard: sitting %s: only %d speeches parsed — skipping",
                    ref.url, len(result.speeches),
                )
                stats.parse_errors += 1
                await asyncio.sleep(REQUEST_DELAY_SECONDS)
                continue

            log.info(
                "on_hansard: sitting %s date=%s → %d speeches",
                ref.href, sitting_date, len(result.speeches),
            )

            parent_meta = {
                "parliament": parliament,
                "session": session,
                "nid": (node.get("nid") or [{}])[0].get("value"),
                "field_associated_bills": _extract_associated_bills(node),
            }

            for ps in result.speeches:
                if limit_speeches and (
                    stats.speeches_inserted + stats.speeches_updated
                ) >= limit_speeches:
                    break
                stats.speeches_seen += 1

                politician, status, confidence = _resolve_speech(lookup, ps)
                if status == "resolved":
                    stats.speeches_resolved += 1
                elif status == "role":
                    stats.speeches_role_only += 1
                elif status == "ambiguous":
                    stats.speeches_ambiguous += 1
                else:
                    stats.speeches_unresolved += 1

                outcome = await _upsert_speech(
                    db,
                    session_id=session_id,
                    ref=ref,
                    parsed=ps,
                    politician=politician,
                    confidence=confidence,
                    page_html=body_html,
                    parent_meta=parent_meta,
                )
                if outcome == "inserted":
                    stats.speeches_inserted += 1
                elif outcome == "updated":
                    stats.speeches_updated += 1
                elif outcome == "skipped":
                    stats.skipped_empty += 1

            await asyncio.sleep(REQUEST_DELAY_SECONDS)

    # Sync denormalised politician_id onto chunks so /search joins stay
    # consistent when we re-resolve speakers after more roster lands.
    await db.execute(
        """
        UPDATE speech_chunks sc
           SET politician_id = s.politician_id
          FROM speeches s
         WHERE sc.speech_id = s.id
           AND s.level = 'provincial'
           AND s.province_territory = 'ON'
           AND s.source_system = $1
           AND sc.politician_id IS DISTINCT FROM s.politician_id
        """,
        SOURCE_SYSTEM,
    )

    log.info(
        "on_hansard done: %d sittings, %d speeches "
        "(inserted=%d updated=%d skipped=%d parse_errors=%d) "
        "resolved=%d role=%d ambiguous=%d unresolved=%d",
        stats.sittings_scanned, stats.speeches_seen,
        stats.speeches_inserted, stats.speeches_updated,
        stats.skipped_empty, stats.parse_errors,
        stats.speeches_resolved, stats.speeches_role_only,
        stats.speeches_ambiguous, stats.speeches_unresolved,
    )
    return stats


# ── Post-pass resolver ──────────────────────────────────────────────

@dataclass
class ResolveStats:
    speeches_scanned: int = 0
    speeches_updated: int = 0
    still_unresolved: int = 0


async def resolve_on_speakers(
    db: Database, *, limit: Optional[int] = None,
) -> ResolveStats:
    """Re-resolve politician_id on ON Hansard speeches with NULL politician_id.

    Call after expanding the ON MPP roster (e.g. after `ingest-ontario-mpps`)
    to pick up speeches whose name now resolves. Idempotent.
    """
    stats = ResolveStats()
    lookup = await load_on_speaker_lookup(db)

    query = """
        SELECT s.id::text AS id,
               s.raw->'on_hansard'->>'parens_name' AS parens_name,
               s.raw->'on_hansard'->>'full_name'   AS full_name,
               s.raw->'on_hansard'->>'surname'     AS surname,
               s.raw->'on_hansard'->>'speaker_role' AS speaker_role
          FROM speeches s
         WHERE s.level = 'provincial'
           AND s.province_territory = 'ON'
           AND s.source_system = $1
           AND s.politician_id IS NULL
    """
    if limit:
        query += f" LIMIT {int(limit)}"

    rows = await db.fetch(query, SOURCE_SYSTEM)
    for r in rows:
        stats.speeches_scanned += 1
        # Try parens_name first (it's the actual person under a role
        # attribution), then full_name, then surname.
        candidate_full = r["full_name"]
        candidate_surname = r["surname"]
        # parens_name decomposition is already captured into full_name
        # at parse time, so we don't re-decompose here.
        row, status = _try_match_person(lookup, candidate_full, candidate_surname)
        if status == "resolved" and row is not None:
            await db.execute(
                """
                UPDATE speeches
                   SET politician_id = $1::uuid,
                       party_at_time = COALESCE(party_at_time, $2),
                       constituency_at_time = COALESCE(constituency_at_time, $3),
                       confidence    = GREATEST(confidence, 0.9),
                       updated_at    = now()
                 WHERE id = $4::uuid
                """,
                row["id"], row["party"], row["constituency_name"], r["id"],
            )
            await db.execute(
                """
                UPDATE speech_chunks
                   SET politician_id = $1::uuid
                 WHERE speech_id = $2::uuid
                   AND politician_id IS DISTINCT FROM $1::uuid
                """,
                row["id"], r["id"],
            )
            stats.speeches_updated += 1
        else:
            stats.still_unresolved += 1

    log.info(
        "resolve_on_speakers: scanned=%d updated=%d still_unresolved=%d",
        stats.speeches_scanned, stats.speeches_updated, stats.still_unresolved,
    )
    return stats
