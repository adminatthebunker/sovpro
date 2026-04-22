"""Manitoba Hansard ingester — Word-exported HTML → ``speeches`` table.

Structural near-clone of the Quebec Hansard pipeline with English-
specific adaptations. See ``mb_hansard_parse`` for the speaker-turn
extractor; this module owns discovery, DB upsert, and speaker
resolution.

## Discovery

The session index at

    https://www.gov.mb.ca/legislature/hansard/{leg}_{sess}/{leg}_{sess}.html

contains a table of links shaped like
``<a href="vol_NN[letter]/summary[_letter].html">HTML</a>``. For each
sitting volume we construct the transcript URL directly —
``vol_NN[letter]/hNN[letter].html`` — rather than walking the summary
page, because the pattern is deterministic and saves one HTTP round-
trip per sitting. The sitting date lives in the transcript's
``<title>`` tag ("…, Nov 18, 2025") and is extracted by the parser.

## Speaker resolution

``politicians.mb_assembly_slug`` (surname slug) is the canonical MB
identifier — 56/56 seated MLAs have it stamped via
``ingest-mb-mlas``. Resolution order per parsed speech:

  1. ``full_name`` match (e.g. "Hon. Anita R. Neville" → lookup by
     normalized full name).
  2. ``surname`` match via slug candidates ("Dela Cruz" → try
     "delacruz" before "cruz").
  3. ``speaker_role`` match — deferred to
     ``presiding_officer_resolver`` for "The Speaker" / "The Deputy
     Speaker" (date-ranged term lookup).

## Upsert key

``UNIQUE NULLS NOT DISTINCT (source_system, source_url, sequence)``.
``source_system='hansard-mb'``. ``source_url`` is the transcript URL
(one per sitting); ``sequence`` is 1-indexed position within the
sitting.

Full-page HTML is stored on the ``sequence=1`` row only — same
write-amplification avoidance as QC/BC/AB Hansard (see comment in
_upsert_speech).
"""
from __future__ import annotations

import asyncio
import logging
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

import httpx
import orjson

from ..db import Database
from . import mb_hansard_parse as parse_mod

log = logging.getLogger(__name__)

SOURCE_SYSTEM = "hansard-mb"
SESSION_INDEX_URL = (
    "https://www.gov.mb.ca/legislature/hansard/{leg}_{sess}/{leg}_{sess}.html"
)

REQUEST_TIMEOUT = 60
REQUEST_DELAY_SECONDS = 1.0  # Polite to gov.mb.ca

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

@dataclass
class SittingRef:
    volume: str              # "01", "41a", …
    url: str                 # Transcript URL

_VOL_HREF_RE = re.compile(
    # Tolerates fragments/queries after .html — index anchors are
    # typically href="vol_01/summary.html#html".
    r'href="vol_(?P<vol>\d+[a-z]?)/summary(?:_[a-z])?\.html(?:[#?][^"]*)?"',
    re.IGNORECASE,
)

# Build a transcript URL from a volume key: "vol_01" → "vol_01/h01.html".
def _transcript_url(base: str, vol: str) -> str:
    return f"{base}vol_{vol}/h{vol}.html"


async def discover_sitting_refs(
    client: httpx.AsyncClient, *, parliament: int, session: int,
) -> list[SittingRef]:
    leg = f"{parliament}{_ordinal_suffix(parliament)}"
    sess = f"{session}{_ordinal_suffix(session)}"
    index_url = SESSION_INDEX_URL.format(leg=leg, sess=sess)
    r = await _get_with_retry(client, index_url)
    r.raise_for_status()
    html = r.text
    seen: set[str] = set()
    refs: list[SittingRef] = []
    base = index_url.rsplit("/", 1)[0] + "/"
    for m in _VOL_HREF_RE.finditer(html):
        vol = m.group("vol").lower()
        if vol in seen:
            continue
        seen.add(vol)
        refs.append(SittingRef(volume=vol, url=_transcript_url(base, vol)))
    # Preserve source ordering (index lists sittings chronologically,
    # newest-first in recent months; we sort by volume ascending to
    # match the numeric day order).
    refs.sort(key=lambda x: _vol_sort_key(x.volume))
    return refs


def _vol_sort_key(vol: str) -> tuple[int, str]:
    m = re.match(r"(\d+)([a-z]?)", vol)
    if not m:
        return (10_000, vol)
    return (int(m.group(1)), m.group(2))


def _ordinal_suffix(n: int) -> str:
    if 10 <= n % 100 <= 20:
        return "th"
    return {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")


# ── Session upsert ─────────────────────────────────────────────────

async def ensure_session(
    db: Database, *, parliament: int, session: int,
) -> str:
    row = await db.fetchrow(
        """
        INSERT INTO legislative_sessions
            (level, province_territory, parliament_number, session_number,
             name, source_system, source_url)
        VALUES ('provincial', 'MB', $1, $2, $3, $4, $5)
        ON CONFLICT (level, province_territory, parliament_number, session_number)
        DO UPDATE SET updated_at = now()
        RETURNING id
        """,
        parliament, session,
        f"{parliament}{_ordinal_suffix(parliament)} Legislature, "
        f"{session}{_ordinal_suffix(session)} Session",
        SOURCE_SYSTEM,
        SESSION_INDEX_URL.format(
            leg=f"{parliament}{_ordinal_suffix(parliament)}",
            sess=f"{session}{_ordinal_suffix(session)}",
        ),
    )
    return str(row["id"])


# ── Speaker resolution ──────────────────────────────────────────────

_WS_RE = re.compile(r"\s+")


def _norm(s: str) -> str:
    if not s:
        return ""
    t = unicodedata.normalize("NFKD", s.replace("\u00a0", " "))
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = t.lower()
    t = re.sub(r"[^\w\s-]", " ", t)
    return _WS_RE.sub(" ", t).strip()


def _slug_candidates(text: str) -> list[str]:
    """Generate slug candidates for compound-surname resolution.

    "Dela Cruz" → ["delacruz", "dela-cruz", "cruz"]
    "Kinew"     → ["kinew"]
    """
    tokens = [t for t in _norm(text).split() if t]
    if not tokens:
        return []
    out: list[str] = []
    if len(tokens) >= 2:
        out.append("".join(tokens))
        out.append("-".join(tokens))
    out.append(tokens[-1])
    return list(dict.fromkeys(out))


@dataclass
class SpeakerLookup:
    by_full_name: dict[str, list[dict]] = field(default_factory=dict)
    by_surname: dict[str, list[dict]] = field(default_factory=dict)
    by_slug: dict[str, dict] = field(default_factory=dict)

    def resolve_by_full_name(self, name: str) -> tuple[Optional[dict], str]:
        key = _norm(name)
        if not key:
            return None, "unresolved"
        hits = self.by_full_name.get(key)
        if hits and len(hits) == 1:
            return hits[0], "resolved"
        if hits and len(hits) > 1:
            return None, "ambiguous"
        # Fall back to slug-joined form ("dela cruz" → "delacruz").
        for cand in _slug_candidates(name):
            hit = self.by_slug.get(cand)
            if hit:
                return hit, "resolved"
        return None, "unresolved"

    def resolve_by_surname(self, surname: str) -> tuple[Optional[dict], str]:
        if not surname:
            return None, "unresolved"
        # Try each slug candidate first (slug is unique per MLA).
        for cand in _slug_candidates(surname):
            hit = self.by_slug.get(cand)
            if hit:
                return hit, "resolved"
        # Fall back to normalised surname match.
        key = _norm(surname)
        if not key:
            return None, "unresolved"
        hits = self.by_surname.get(key)
        if hits and len(hits) == 1:
            return hits[0], "resolved"
        if hits and len(hits) > 1:
            return None, "ambiguous"
        tokens = key.split()
        if len(tokens) > 1:
            hits = self.by_surname.get(tokens[-1])
            if hits and len(hits) == 1:
                return hits[0], "resolved"
            if hits and len(hits) > 1:
                return None, "ambiguous"
        return None, "unresolved"


async def load_mb_speaker_lookup(db: Database) -> SpeakerLookup:
    rows = await db.fetch(
        """
        SELECT id::text AS id, name, first_name, last_name, mb_assembly_slug
          FROM politicians
         WHERE level = 'provincial'
           AND province_territory = 'MB'
        """
    )
    lookup = SpeakerLookup()
    for r in rows:
        full = _norm(r["name"] or "")
        if full:
            lookup.by_full_name.setdefault(full, []).append(dict(r))
        fl = _norm(f"{r['first_name'] or ''} {r['last_name'] or ''}")
        if fl and fl != full:
            lookup.by_full_name.setdefault(fl, []).append(dict(r))
        last = _norm(r["last_name"] or "")
        if last:
            lookup.by_surname.setdefault(last, []).append(dict(r))
            # Last-token index for compound surnames.
            tokens = last.split()
            if len(tokens) > 1:
                lookup.by_surname.setdefault(tokens[-1], []).append(dict(r))
        if r["mb_assembly_slug"]:
            lookup.by_slug[r["mb_assembly_slug"]] = dict(r)
    log.info(
        "mb_hansard: loaded %d MLAs (slugs=%d unique_surname=%d ambig_surname=%d)",
        len(rows), len(lookup.by_slug),
        sum(1 for v in lookup.by_surname.values() if len(v) == 1),
        sum(1 for v in lookup.by_surname.values() if len(v) > 1),
    )
    return lookup


def _resolve_speech(
    lookup: SpeakerLookup, ps: parse_mod.ParsedSpeech,
) -> tuple[Optional[dict], str]:
    # Prefer full-name match when the attribution carries a first name
    # (throne speech, ministerial introductions).
    if ps.full_name:
        pol, status = lookup.resolve_by_full_name(ps.full_name)
        if pol:
            return pol, "resolved"
        if status == "ambiguous":
            return None, "ambiguous"
    if ps.surname:
        pol, status = lookup.resolve_by_surname(ps.surname)
        if pol:
            return pol, "resolved"
        if status == "ambiguous":
            return None, "ambiguous"
    if ps.speaker_role:
        return None, "role"
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
    speeches_ambiguous: int = 0
    speeches_unresolved: int = 0
    parse_errors: int = 0
    skipped_empty: int = 0


async def _upsert_speech(
    db: Database, *, session_id: str, ref: SittingRef, sitting_date: date,
    parsed: parse_mod.ParsedSpeech, politician: Optional[dict],
    confidence: float, page_html: str,
) -> str:
    if not parsed.text.strip():
        return "skipped"
    politician_id = politician["id"] if politician else None

    raw_payload = {
        "mb_hansard": {
            "sitting_date": sitting_date.isoformat(),
            "volume": parsed.raw.get("volume"),
            "html_id": parsed.raw.get("html_id"),
            "section": parsed.raw.get("section"),
            "honorific": parsed.honorific,
            "surname": parsed.surname,
            "full_name": parsed.full_name,
            "paren_role": parsed.paren_role,
            "sitting_time": parsed.raw.get("sitting_time"),
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
            $1, $2, 'provincial', 'MB',
            $3, $4, NULL, NULL,
            $5, $6, $7, $8, $9,
            $10, $11,
            $12, $13, NULL,
            $14::jsonb, $15, $16
        )
        ON CONFLICT (source_system, source_url, sequence)
        DO UPDATE SET
            politician_id = EXCLUDED.politician_id,
            speaker_name_raw = EXCLUDED.speaker_name_raw,
            speaker_role = EXCLUDED.speaker_role,
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
        # Store the full transcript HTML only on the sequence=1 row —
        # mirrors the QC/BC/AB pattern to avoid 200× write amplification
        # per sitting.
        page_html if parsed.sequence == 1 else None,
        parsed.content_hash,
    )
    return "inserted" if result and result["inserted"] else "updated"


# ── Orchestrator ────────────────────────────────────────────────────

async def ingest(
    db: Database, *,
    parliament: int, session: int,
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
    lookup = await load_mb_speaker_lookup(db)

    async with httpx.AsyncClient(
        timeout=REQUEST_TIMEOUT, headers=HEADERS, follow_redirects=True,
    ) as client:
        if one_off_url:
            meta = parse_mod.parse_url_meta(one_off_url)
            refs = [SittingRef(volume=meta.volume, url=one_off_url)]
        else:
            refs = await discover_sitting_refs(
                client, parliament=parliament, session=session,
            )
            if limit_sittings:
                refs = refs[-limit_sittings:]

        log.info(
            "mb_hansard: processing %d sittings (parliament=%d session=%d)",
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
                # Word-exported Hansard HTML declares <meta charset=
                # windows-1252>, but MB's server doesn't set Content-
                # Type charset so httpx falls back to UTF-8 and mojibakes
                # every "é", non-breaking space, etc. Force cp1252.
                r.encoding = "windows-1252"
                page_html = r.text
            except Exception as exc:
                log.warning("sitting %s: fetch failed: %s", ref.url, exc)
                continue

            try:
                result = parse_mod.extract_speeches(page_html, ref.url)
            except Exception as exc:
                log.warning("sitting %s: parse failed: %s", ref.url, exc)
                stats.parse_errors += 1
                continue

            if since and result.sitting_date < since:
                continue
            if until and result.sitting_date > until:
                continue

            if len(result.speeches) < 3:
                log.warning(
                    "sitting %s: only %d speeches parsed — skipping",
                    ref.url, len(result.speeches),
                )
                stats.parse_errors += 1
                continue

            log.info(
                "sitting vol=%s date=%s → %d speeches",
                ref.volume, result.sitting_date, len(result.speeches),
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

    # Sync denormalised politician_id onto chunks.
    await db.execute(
        """
        UPDATE speech_chunks sc
           SET politician_id = s.politician_id
          FROM speeches s
         WHERE sc.speech_id = s.id
           AND s.level = 'provincial'
           AND s.province_territory = 'MB'
           AND s.source_system = $1
           AND sc.politician_id IS DISTINCT FROM s.politician_id
        """,
        SOURCE_SYSTEM,
    )

    log.info(
        "mb_hansard done: %d sittings, %d speeches "
        "(inserted=%d updated=%d skipped=%d parse_errors=%d) "
        "resolved=%d role=%d ambiguous=%d unresolved=%d",
        stats.sittings_scanned,
        stats.speeches_seen,
        stats.speeches_inserted,
        stats.speeches_updated,
        stats.skipped_empty,
        stats.parse_errors,
        stats.speeches_resolved,
        stats.speeches_role_only,
        stats.speeches_ambiguous,
        stats.speeches_unresolved,
    )
    return stats


# ── Post-pass resolver ──────────────────────────────────────────────

@dataclass
class ResolveStats:
    speeches_scanned: int = 0
    speeches_updated: int = 0
    still_unresolved: int = 0


async def resolve_mb_speakers(
    db: Database, *, limit: Optional[int] = None,
) -> ResolveStats:
    """Re-resolve politician_id on MB Hansard speeches with NULL politician_id.

    Run after adding more MB MLAs (historical backfill) or after fixing
    a parser bug.
    """
    stats = ResolveStats()
    lookup = await load_mb_speaker_lookup(db)

    query = """
        SELECT s.id::text AS id,
               s.speaker_name_raw,
               s.speaker_role,
               s.raw->'mb_hansard'->>'surname'   AS surname,
               s.raw->'mb_hansard'->>'full_name' AS full_name
          FROM speeches s
         WHERE s.level = 'provincial'
           AND s.province_territory = 'MB'
           AND s.source_system = $1
           AND s.politician_id IS NULL
    """
    if limit:
        query += f" LIMIT {int(limit)}"

    rows = await db.fetch(query, SOURCE_SYSTEM)
    for r in rows:
        stats.speeches_scanned += 1
        politician = None
        if r["full_name"]:
            pol, _ = lookup.resolve_by_full_name(r["full_name"])
            if pol:
                politician = pol
        if not politician and r["surname"]:
            pol, _ = lookup.resolve_by_surname(r["surname"])
            if pol:
                politician = pol
        if politician:
            await db.execute(
                """
                UPDATE speeches
                   SET politician_id = $1::uuid,
                       confidence    = GREATEST(confidence, 0.9),
                       updated_at    = now()
                 WHERE id = $2::uuid
                """,
                politician["id"], r["id"],
            )
            await db.execute(
                """
                UPDATE speech_chunks
                   SET politician_id = $1::uuid
                 WHERE speech_id = $2::uuid
                   AND politician_id IS DISTINCT FROM $1::uuid
                """,
                politician["id"], r["id"],
            )
            stats.speeches_updated += 1
        else:
            stats.still_unresolved += 1

    log.info(
        "resolve_mb_speakers: scanned=%d updated=%d still_unresolved=%d",
        stats.speeches_scanned, stats.speeches_updated, stats.still_unresolved,
    )
    return stats
