"""Nova Scotia Hansard ingester — HTML transcripts → ``speeches`` table.

End-to-end:

1. **Discovery.** Walk the session index at
   ``/legislative-business/hansard-debates/{parliament}-{session}``;
   collect every sitting URL shaped like
   ``/legislative-business/hansard-debates/assembly-{N}-session-{M}/house_{YYmonDD}``.
2. **Fetch.** HTTP GET each sitting with polite delays + retry on
   5xx/timeout. NS Hansard pages are on a different CDN path than
   the bill detail pages, so the WAF budget that throttles
   ``fetch-ns-bill-pages`` does NOT apply here.
3. **Parse.** Delegate to :mod:`ns_hansard_parse` which walks the
   transcript body extracting every ``<p>`` anchored at
   ``<a href="/members/profiles/<slug>">`` or
   ``<a href="/members/speaker/">``.
4. **Resolve.** Prefer a direct FK join to
   ``politicians.nslegislature_slug``. Presiding "The Speaker" turns
   have no slug — leave ``politician_id`` NULL and defer to
   ``presiding_officer_resolver`` (date-ranged term lookup).
5. **Upsert.** Write to ``speeches`` with
   ``source_system='hansard-ns'`` and unique key
   ``(source_system, source_url, sequence)``. Full page HTML is
   stored only on the ``sequence=1`` row to avoid write
   amplification (~200× per sitting otherwise).

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
from datetime import date
from typing import Optional

import httpx
import orjson

from ..db import Database
from . import ns_hansard_parse as parse_mod

log = logging.getLogger(__name__)

SOURCE_SYSTEM = "hansard-ns"
SESSION_INDEX_URL = (
    "https://nslegislature.ca/legislative-business/hansard-debates/{parliament}-{session}"
)
BASE_URL = "https://nslegislature.ca"

REQUEST_TIMEOUT = 60
REQUEST_DELAY_SECONDS = 1.5  # Polite to nslegislature.ca

HEADERS = {
    "User-Agent": "CanadianPoliticalDataBot/1.0 (+https://canadianpoliticaldata.ca)",
    "Accept": "text/html,application/xhtml+xml",
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
            log.warning("retry %d/%d after %ds: %s", attempt, len(RETRY_BACKOFF_SECONDS),
                        delay, url)
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

_SITTING_HREF_RE = re.compile(
    r"href=\"(?P<href>/legislative-business/hansard-debates/"
    r"assembly-\d+-session-\d+/house_\d{2}[a-z]{3}\d{2})\"",
    re.IGNORECASE,
)


@dataclass
class SittingRef:
    href: str                  # /legislative-business/.../house_YYmonDD
    url: str                   # absolute URL
    sitting_date: Optional[date] = None


async def discover_sitting_refs(
    client: httpx.AsyncClient, *, parliament: int, session: int,
) -> list[SittingRef]:
    """Walk the session index and return sitting URLs, oldest-first.

    NS lists sittings newest-first on the index. We reverse so the
    ingest loop flows chronologically — matches how operators tend to
    reason about Hansard date ranges.
    """
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
            meta = parse_mod.parse_url_meta(href)
            sitting_date = meta.sitting_date_from_url
        except ValueError:
            sitting_date = None
        refs.append(SittingRef(
            href=href, url=BASE_URL + href, sitting_date=sitting_date,
        ))
    refs.sort(key=lambda r: r.sitting_date or date(1970, 1, 1))
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
        VALUES ('provincial', 'NS', $1, $2, $3, $4, $5)
        ON CONFLICT (level, province_territory, parliament_number, session_number)
        DO UPDATE SET updated_at = now()
        RETURNING id
        """,
        parliament, session,
        f"Assembly {parliament}, Session {session}",
        SOURCE_SYSTEM,
        SESSION_INDEX_URL.format(parliament=parliament, session=session),
    )
    return str(row["id"])


# ── Speaker resolution ──────────────────────────────────────────────

_WS_RE = re.compile(r"\s+")


def _norm(s: str) -> str:
    if not s:
        return ""
    t = unicodedata.normalize("NFKD", s.replace(" ", " "))
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = t.lower()
    t = re.sub(r"[^\w\s-]", " ", t)
    return _WS_RE.sub(" ", t).strip()


@dataclass
class SpeakerLookup:
    """Maps ``nslegislature_slug → politician row``.

    Secondary fallback indexes by normalised last name for speeches
    whose anchor somehow lacks a slug (should not happen in practice,
    but belt-and-suspenders).
    """
    by_slug: dict[str, dict]
    by_surname: dict[str, list[dict]]
    by_full_name: dict[str, list[dict]]


async def load_ns_speaker_lookup(db: Database) -> SpeakerLookup:
    rows = await db.fetch(
        """
        SELECT id::text AS id,
               name, first_name, last_name,
               nslegislature_slug,
               party, constituency_name
          FROM politicians
         WHERE level              = 'provincial'
           AND province_territory = 'NS'
        """
    )
    by_slug: dict[str, dict] = {}
    by_surname: dict[str, list[dict]] = {}
    by_full_name: dict[str, list[dict]] = {}
    for r in rows:
        row = dict(r)
        slug = row["nslegislature_slug"]
        if slug:
            by_slug[slug.lower()] = row
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
        "ns_hansard: loaded %d NS politicians (slugs=%d unique_surnames=%d)",
        len(rows), len(by_slug),
        sum(1 for v in by_surname.values() if len(v) == 1),
    )
    return SpeakerLookup(
        by_slug=by_slug, by_surname=by_surname, by_full_name=by_full_name,
    )


def _resolve_speech(
    lookup: SpeakerLookup, ps: parse_mod.ParsedSpeech,
) -> tuple[Optional[dict], str]:
    """Return (politician_row, status)."""
    # Primary path: anchor slug exact join.
    if ps.speaker_slug:
        row = lookup.by_slug.get(ps.speaker_slug.lower())
        if row:
            return row, "resolved"
        # Slug we've never seen — means the MLA isn't in politicians yet,
        # or ingest-ns-mlas hasn't stamped them. Still report slug in
        # status so the operator can track these.
        return None, "slug_unknown"
    # Role-only (presiding officer). Defer to presiding_officer_resolver.
    if ps.speaker_role:
        return None, "role"
    # Fallback — surname/full-name fuzz (shouldn't trigger for NS; the
    # markup always supplies a slug on profile anchors). Included so
    # unexpected anchors still have a shot.
    if ps.full_name:
        hits = lookup.by_full_name.get(_norm(ps.full_name), [])
        if len(hits) == 1:
            return hits[0], "resolved"
        if len(hits) > 1:
            return None, "ambiguous"
    if ps.surname:
        hits = lookup.by_surname.get(_norm(ps.surname), [])
        if len(hits) == 1:
            return hits[0], "resolved"
        if len(hits) > 1:
            return None, "ambiguous"
    return None, "unresolved"


# ── Upsert ──────────────────────────────────────────────────────────

@dataclass
class IngestStats:
    sittings_scanned: int = 0
    speeches_seen: int = 0
    speeches_inserted: int = 0
    speeches_updated: int = 0
    speeches_resolved: int = 0
    speeches_role_only: int = 0
    speeches_slug_unknown: int = 0
    speeches_ambiguous: int = 0
    speeches_unresolved: int = 0
    parse_errors: int = 0
    skipped_empty: int = 0


async def _upsert_speech(
    db: Database, *, session_id: str, ref: SittingRef,
    sitting_date: date, parsed: parse_mod.ParsedSpeech,
    politician: Optional[dict], confidence: float,
    page_html: Optional[str],
) -> str:
    if not parsed.text.strip():
        return "skipped"
    politician_id = politician["id"] if politician else None
    party = politician["party"] if politician else None
    constituency = politician["constituency_name"] if politician else None

    raw_payload = {
        "ns_hansard": {
            "sitting_date": sitting_date.isoformat(),
            "parliament": parsed.raw.get("parliament"),
            "session": parsed.raw.get("session"),
            "sitting_slug": parsed.raw.get("sitting_slug"),
            "speaker_slug": parsed.speaker_slug,
            "href": parsed.raw.get("href"),
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
            $1, $2, 'provincial', 'NS',
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
    lookup = await load_ns_speaker_lookup(db)

    async with httpx.AsyncClient(
        timeout=REQUEST_TIMEOUT, headers=HEADERS, follow_redirects=True,
    ) as client:
        if one_off_url:
            try:
                meta = parse_mod.parse_url_meta(one_off_url)
                href_guess = one_off_url.replace(BASE_URL, "")
                refs = [SittingRef(
                    href=href_guess,
                    url=one_off_url,
                    sitting_date=meta.sitting_date_from_url,
                )]
            except ValueError as exc:
                log.error("ns_hansard: bad --url %s: %s", one_off_url, exc)
                return stats
        else:
            refs = await discover_sitting_refs(
                client, parliament=parliament, session=session,
            )
            if limit_sittings:
                # Sample from the newest end when capped — matches QC/MB
                # convention.
                refs = refs[-limit_sittings:]

        log.info(
            "ns_hansard: processing %d sittings (parliament=%d session=%d)",
            len(refs), parliament, session,
        )

        for ref in refs:
            if limit_speeches and (
                stats.speeches_inserted + stats.speeches_updated
            ) >= limit_speeches:
                break
            stats.sittings_scanned += 1
            try:
                r = await _get_with_retry(client, ref.url)
                r.raise_for_status()
                page_html = r.text
            except Exception as exc:
                log.warning("ns_hansard: sitting %s: fetch failed: %s", ref.url, exc)
                continue

            try:
                result = parse_mod.extract_speeches(page_html, ref.url)
            except Exception as exc:
                log.warning("ns_hansard: sitting %s: parse failed: %s", ref.url, exc)
                stats.parse_errors += 1
                continue

            if since and result.sitting_date < since:
                await asyncio.sleep(REQUEST_DELAY_SECONDS)
                continue
            if until and result.sitting_date > until:
                await asyncio.sleep(REQUEST_DELAY_SECONDS)
                continue

            if len(result.speeches) < 3:
                log.warning(
                    "ns_hansard: sitting %s: only %d speeches parsed — skipping",
                    ref.url, len(result.speeches),
                )
                stats.parse_errors += 1
                await asyncio.sleep(REQUEST_DELAY_SECONDS)
                continue

            log.info(
                "ns_hansard: sitting %s date=%s → %d speeches",
                ref.href, result.sitting_date, len(result.speeches),
            )

            for ps in result.speeches:
                if limit_speeches and (
                    stats.speeches_inserted + stats.speeches_updated
                ) >= limit_speeches:
                    break
                stats.speeches_seen += 1

                politician, status = _resolve_speech(lookup, ps)
                if status == "resolved":
                    stats.speeches_resolved += 1
                    confidence = 1.0
                elif status == "role":
                    stats.speeches_role_only += 1
                    confidence = 0.5
                elif status == "slug_unknown":
                    stats.speeches_slug_unknown += 1
                    confidence = 0.0
                elif status == "ambiguous":
                    stats.speeches_ambiguous += 1
                    confidence = 0.0
                else:
                    stats.speeches_unresolved += 1
                    confidence = 0.0

                outcome = await _upsert_speech(
                    db,
                    session_id=session_id,
                    ref=ref,
                    sitting_date=result.sitting_date,
                    parsed=ps,
                    politician=politician,
                    confidence=confidence,
                    page_html=page_html,
                )
                if outcome == "inserted":
                    stats.speeches_inserted += 1
                elif outcome == "updated":
                    stats.speeches_updated += 1
                elif outcome == "skipped":
                    stats.skipped_empty += 1

            await asyncio.sleep(REQUEST_DELAY_SECONDS)

    # Sync denormalised politician_id onto chunks so /search joins stay
    # consistent when we re-resolve speakers after more slugs land.
    await db.execute(
        """
        UPDATE speech_chunks sc
           SET politician_id = s.politician_id
          FROM speeches s
         WHERE sc.speech_id = s.id
           AND s.level = 'provincial'
           AND s.province_territory = 'NS'
           AND s.source_system = $1
           AND sc.politician_id IS DISTINCT FROM s.politician_id
        """,
        SOURCE_SYSTEM,
    )

    log.info(
        "ns_hansard done: %d sittings, %d speeches "
        "(inserted=%d updated=%d skipped=%d parse_errors=%d) "
        "resolved=%d role=%d slug_unknown=%d ambiguous=%d unresolved=%d",
        stats.sittings_scanned, stats.speeches_seen,
        stats.speeches_inserted, stats.speeches_updated,
        stats.skipped_empty, stats.parse_errors,
        stats.speeches_resolved, stats.speeches_role_only,
        stats.speeches_slug_unknown, stats.speeches_ambiguous,
        stats.speeches_unresolved,
    )
    return stats


# ── Post-pass resolver ──────────────────────────────────────────────

@dataclass
class ResolveStats:
    speeches_scanned: int = 0
    speeches_updated: int = 0
    still_unresolved: int = 0


async def resolve_ns_speakers(
    db: Database, *, limit: Optional[int] = None,
) -> ResolveStats:
    """Re-resolve politician_id on NS Hansard speeches with NULL politician_id.

    Call after running ``ingest-ns-mlas`` to pick up speeches whose
    anchor slug now resolves to a politicians row. Idempotent.
    """
    stats = ResolveStats()
    lookup = await load_ns_speaker_lookup(db)

    query = """
        SELECT s.id::text AS id,
               s.raw->'ns_hansard'->>'speaker_slug' AS speaker_slug,
               s.raw->'ns_hansard'->>'full_name'    AS full_name,
               s.raw->'ns_hansard'->>'surname'      AS surname
          FROM speeches s
         WHERE s.level = 'provincial'
           AND s.province_territory = 'NS'
           AND s.source_system = $1
           AND s.politician_id IS NULL
    """
    if limit:
        query += f" LIMIT {int(limit)}"

    rows = await db.fetch(query, SOURCE_SYSTEM)
    for r in rows:
        stats.speeches_scanned += 1
        row: Optional[dict] = None
        slug = r["speaker_slug"]
        if slug:
            row = lookup.by_slug.get(slug.lower())
        if not row and r["full_name"]:
            hits = lookup.by_full_name.get(_norm(r["full_name"]), [])
            if len(hits) == 1:
                row = hits[0]
        if not row and r["surname"]:
            hits = lookup.by_surname.get(_norm(r["surname"]), [])
            if len(hits) == 1:
                row = hits[0]
        if row:
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
        "resolve_ns_speakers: scanned=%d updated=%d still_unresolved=%d",
        stats.speeches_scanned, stats.speeches_updated, stats.still_unresolved,
    )
    return stats
