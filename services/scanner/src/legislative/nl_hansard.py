"""Newfoundland & Labrador Hansard ingester — HTML → ``speeches`` table.

Structural sibling of ``mb_hansard.py`` with NL-specific adaptations:

  * Session discovery from ``/HouseBusiness/Hansard/ga{GA}session{S}/`` —
    the calendar page lists one date-named ``.htm[l]`` per sitting day.
  * Era-branching parser — modern Word-export vs legacy FrontPage
    (see ``nl_hansard_parse`` docstring).
  * Speaker resolution via ``(first_initial, surname)`` against
    date-windowed ``politician_terms``. NL has **no canonical MHA id**
    on assembly.nl.ca, so slug-based lookup (the MB pattern) is not an
    option — name matching is the only path.

## Upsert key

``UNIQUE NULLS NOT DISTINCT (source_system, source_url, sequence)``.
``source_system='hansard-nl'``. One row per speaker turn; ``sequence``
is 1-indexed position within the sitting.

Full-page HTML is stored on the ``sequence=1`` row only, matching the
QC/BC/AB/MB pattern (write-amplification avoidance).

## Partial vs edited transcripts

NL serves the preliminary "blues" transcript at the canonical URL and
replaces it in place once editing completes. The parser flags this via
``ParseResult.partial``. We record this in ``raw.nl_hansard.partial``
so a downstream re-run can see whether the content has stabilised. The
``ON CONFLICT DO UPDATE`` clause refreshes text/confidence on every
run; once the edited version ships, the next ingest picks it up.
"""
from __future__ import annotations

import asyncio
import logging
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional

import httpx
import orjson

from ..db import Database
from . import nl_hansard_parse as parse_mod

log = logging.getLogger(__name__)

SOURCE_SYSTEM = "hansard-nl"
BASE = "https://www.assembly.nl.ca"
SESSION_INDEX_URL = BASE + "/HouseBusiness/Hansard/ga{ga}session{session}/"

REQUEST_TIMEOUT = 60
REQUEST_DELAY_SECONDS = 1.0  # Be polite to assembly.nl.ca

HEADERS = {
    "User-Agent": "SovereignWatch/1.0 (civic-transparency; +admin@thebunkerops.ca)",
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


# Catch-all 404 signature — assembly.nl.ca returns 200 for unmapped
# URLs with a styled error page. Content-compare to distinguish real
# sitting-day transcripts from the error template.
_CATCHALL_404_SIG = "Sorry, this page could not be found"


def _is_catchall_404(html: str) -> bool:
    return _CATCHALL_404_SIG in (html or "")


# ── Discovery ───────────────────────────────────────────────────────

@dataclass
class SittingRef:
    sitting_date: date       # Extracted from filename
    url: str                 # Full transcript URL
    label: Optional[str]     # "SwearingIn" / "ElectionofSpeaker" / None


# Session-index href pattern. The calendar page emits relative URLs like
# ``26-04-21.htm`` or ``25-11-03ElectionofSpeaker.htm``. We accept both
# extensions and optional name-suffix labels.
_SITTING_HREF_RE = re.compile(
    r'href="(?P<yy>\d{2})-(?P<mm>\d{2})-(?P<dd>\d{2})(?P<label>[A-Za-z][A-Za-z0-9]*)?\.(?P<ext>html?)"',
    re.IGNORECASE,
)


def _yy_to_year(yy: int) -> int:
    return 1900 + yy if yy >= 70 else 2000 + yy


async def discover_sitting_refs(
    client: httpx.AsyncClient, *, ga: int, session: int,
) -> list[SittingRef]:
    index_url = SESSION_INDEX_URL.format(ga=ga, session=session)
    r = await _get_with_retry(client, index_url)
    r.raise_for_status()
    html = r.text
    if _is_catchall_404(html):
        raise ValueError(
            f"NL Hansard session index missing (catch-all 404 template): {index_url}"
        )
    seen: set[str] = set()
    refs: list[SittingRef] = []
    for m in _SITTING_HREF_RE.finditer(html):
        filename = (
            f"{m.group('yy')}-{m.group('mm')}-{m.group('dd')}"
            f"{m.group('label') or ''}.{m.group('ext').lower()}"
        )
        if filename in seen:
            continue
        seen.add(filename)
        try:
            sitting_date = date(
                _yy_to_year(int(m.group("yy"))),
                int(m.group("mm")),
                int(m.group("dd")),
            )
        except ValueError:
            continue
        refs.append(SittingRef(
            sitting_date=sitting_date,
            url=index_url + filename,
            label=m.group("label"),
        ))
    refs.sort(key=lambda x: (x.sitting_date, x.label or ""))
    log.info(
        "nl_hansard discovery: ga=%d s=%d → %d sittings (%s … %s)",
        ga, session, len(refs),
        refs[0].sitting_date if refs else "-",
        refs[-1].sitting_date if refs else "-",
    )
    return refs


# ── Session upsert ─────────────────────────────────────────────────

def _ordinal_suffix(n: int) -> str:
    if 10 <= n % 100 <= 20:
        return "th"
    return {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")


async def ensure_session(
    db: Database, *, ga: int, session: int,
) -> str:
    row = await db.fetchrow(
        """
        INSERT INTO legislative_sessions
            (level, province_territory, parliament_number, session_number,
             name, source_system, source_url)
        VALUES ('provincial', 'NL', $1, $2, $3, $4, $5)
        ON CONFLICT (level, province_territory, parliament_number, session_number)
        DO UPDATE SET
            name          = COALESCE(legislative_sessions.name, EXCLUDED.name),
            source_system = COALESCE(legislative_sessions.source_system, EXCLUDED.source_system),
            source_url    = COALESCE(legislative_sessions.source_url, EXCLUDED.source_url),
            updated_at    = now()
        RETURNING id
        """,
        ga, session,
        f"{ga}{_ordinal_suffix(ga)} General Assembly, "
        f"{session}{_ordinal_suffix(session)} Session",
        SOURCE_SYSTEM,
        SESSION_INDEX_URL.format(ga=ga, session=session),
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


def _surname_slug(s: str) -> str:
    """Collapse a surname to a lowercase slug for lookup.

    "O'Leary" → "oleary"; "St. John"/"St John" → "stjohn"; "Dela Cruz"
    → "delacruz"; single-token names pass through unchanged.
    """
    n = _norm(s)
    return "".join(n.split()).replace("-", "")


@dataclass
class _PolTerm:
    politician_id: str
    started: Optional[date]
    ended: Optional[date]


@dataclass
class SpeakerLookup:
    # (first_initial_letter_lower, surname_slug) → list of term-qualified candidates
    by_initial_surname: dict[tuple[str, str], list[_PolTerm]] = field(default_factory=dict)
    by_surname: dict[str, list[_PolTerm]] = field(default_factory=dict)
    # For debugging / stats
    loaded_politicians: int = 0

    def _in_window(self, cand: _PolTerm, d: Optional[date]) -> bool:
        if d is None:
            return True
        # Currently-serving MHAs (ended_at IS NULL) get a soft
        # started_at: the Open North ingest stamps started_at = ingest
        # time, not the actual term start, so any strict comparison
        # would exclude legitimate speeches that happened before the
        # Open North snapshot. Only enforce started_at on *closed*
        # terms (ended_at IS NOT NULL) — those reflect a real tenure
        # window.
        if cand.ended is None:
            return True
        if cand.started is not None and d < cand.started:
            return False
        if d > cand.ended:
            return False
        return True

    def _filter_unique_politicians(
        self, cands: list[_PolTerm], d: Optional[date],
    ) -> list[str]:
        """Return the distinct politician_ids whose term windows
        include ``d``. Collapses multiple term rows per politician
        (Speaker + MHA, or 2019–2021 MHA + 2025– MHA) to a single id —
        so a politician with two open-ended rows (a common Open North
        + presiding-officer-seed artefact) doesn't read as ambiguous.
        """
        seen: set[str] = set()
        for c in cands:
            if c.politician_id in seen:
                continue
            if not self._in_window(c, d):
                continue
            seen.add(c.politician_id)
        return list(seen)

    def _distinct_politician_ids(self, cands: list[_PolTerm]) -> set[str]:
        return {c.politician_id for c in cands}

    def resolve(
        self, *, first_initial: Optional[str], surname: Optional[str],
        sitting_date: Optional[date],
    ) -> tuple[Optional[str], str]:
        """Return (politician_id, status) where status is one of
        'resolved' / 'ambiguous' / 'unresolved'.

        The date-windowing check is only used to *disambiguate* between
        multiple politicians sharing a lookup key — it's skipped when a
        key already maps to a single politician. This matters because
        the politician_terms table carries data-quality issues we can't
        fix from this pipeline: Open North stamps ``started_at = now()``
        instead of actual term start, and the presiding-officer seed
        sometimes collides with prior "MP" term rows (e.g., Tom Osborne
        carries both a 2015–2017 Speaker term and a 2025– "MP" term, so
        a 2023 sitting falls outside both windows even though he's the
        only NL Osborne). Trusting a unique (initial, surname) match
        sidesteps all of that.
        """
        if not surname:
            return None, "unresolved"
        sur_slug = _surname_slug(surname)
        if not sur_slug:
            return None, "unresolved"

        # Primary: initial + surname.
        if first_initial:
            init_letter = first_initial.rstrip(".").lower()
            raw = self.by_initial_surname.get((init_letter, sur_slug), [])
            pids = self._distinct_politician_ids(raw)
            if len(pids) == 1:
                return next(iter(pids)), "resolved"
            if len(pids) > 1:
                # Genuine collision — date-window to pick one.
                narrowed = self._filter_unique_politicians(raw, sitting_date)
                if len(narrowed) == 1:
                    return narrowed[0], "resolved"
                return None, "ambiguous"

        # Fallback: surname alone.
        raw = self.by_surname.get(sur_slug, [])
        pids = self._distinct_politician_ids(raw)
        if len(pids) == 1:
            return next(iter(pids)), "resolved"
        if len(pids) > 1:
            narrowed = self._filter_unique_politicians(raw, sitting_date)
            if len(narrowed) == 1:
                return narrowed[0], "resolved"
            return None, "ambiguous"
        return None, "unresolved"


async def load_nl_speaker_lookup(db: Database) -> SpeakerLookup:
    """Build (initial, surname) + surname lookups from NL politicians
    and their term windows.

    Politicians with multiple terms produce multiple entries (one per
    term). The resolver picks by date-window filter.
    """
    rows = await db.fetch(
        """
        SELECT p.id::text AS id,
               p.first_name,
               p.last_name,
               pt.started_at::date AS started,
               pt.ended_at::date   AS ended
          FROM politicians p
          JOIN politician_terms pt ON pt.politician_id = p.id
         WHERE pt.level = 'provincial'
           AND pt.province_territory = 'NL'
        """
    )
    lookup = SpeakerLookup()
    seen_pols: set[str] = set()
    for r in rows:
        pid = r["id"]
        seen_pols.add(pid)
        last = (r["last_name"] or "").strip()
        if not last:
            continue
        sur_slug = _surname_slug(last)
        if not sur_slug:
            continue
        term = _PolTerm(
            politician_id=pid,
            started=r["started"],
            ended=r["ended"],
        )
        lookup.by_surname.setdefault(sur_slug, []).append(term)
        first = (r["first_name"] or "").strip()
        if first:
            init_letter = first[0].lower()
            lookup.by_initial_surname.setdefault(
                (init_letter, sur_slug), []
            ).append(term)
    lookup.loaded_politicians = len(seen_pols)
    log.info(
        "nl_hansard: speaker lookup loaded — politicians=%d "
        "unique_surnames=%d initial+surname_keys=%d",
        lookup.loaded_politicians,
        len(lookup.by_surname),
        len(lookup.by_initial_surname),
    )
    return lookup


# ── Upsert ──────────────────────────────────────────────────────────

@dataclass
class IngestStats:
    sittings_scanned: int = 0
    sittings_skipped_404: int = 0
    speeches_seen: int = 0
    speeches_inserted: int = 0
    speeches_updated: int = 0
    speeches_resolved: int = 0
    speeches_role_only: int = 0
    speeches_group: int = 0
    speeches_ambiguous: int = 0
    speeches_unresolved: int = 0
    parse_errors: int = 0
    skipped_empty: int = 0


async def _upsert_speech(
    db: Database, *, session_id: str, ref: SittingRef,
    parsed: parse_mod.ParsedSpeech, politician_id: Optional[str],
    confidence: float, partial: bool, page_html: str,
) -> str:
    if not parsed.text.strip():
        return "skipped"

    raw_payload = {
        "nl_hansard": {
            "sitting_date": ref.sitting_date.isoformat(),
            "label":         ref.label,
            "section":       parsed.raw.get("section"),
            "era":           parsed.raw.get("era"),
            "honorific":     parsed.honorific,
            "first_initial": parsed.first_initial,
            "surname":       parsed.surname,
            "paren":         parsed.paren,
            "is_group":      parsed.is_group,
            "partial":       partial,
            "sitting_time":  parsed.raw.get("sitting_time"),
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
            $1, $2, 'provincial', 'NL',
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
        # Store full transcript HTML on sequence=1 only.
        page_html if parsed.sequence == 1 else None,
        parsed.content_hash,
    )
    return "inserted" if result and result["inserted"] else "updated"


# ── Orchestrator ────────────────────────────────────────────────────

async def ingest(
    db: Database, *,
    ga: int, session: int,
    since: Optional[date] = None,
    until: Optional[date] = None,
    limit_sittings: Optional[int] = None,
    limit_speeches: Optional[int] = None,
    one_off_url: Optional[str] = None,
) -> IngestStats:
    stats = IngestStats()
    session_id = await ensure_session(db, ga=ga, session=session)
    lookup = await load_nl_speaker_lookup(db)

    async with httpx.AsyncClient(
        timeout=REQUEST_TIMEOUT, headers=HEADERS, follow_redirects=True,
    ) as client:
        if one_off_url:
            meta = parse_mod.parse_url_meta(one_off_url)
            refs = [SittingRef(
                sitting_date=meta.sitting_date,
                url=one_off_url,
                label=meta.label,
            )]
        else:
            refs = await discover_sitting_refs(client, ga=ga, session=session)
            if limit_sittings:
                refs = refs[-limit_sittings:]

        log.info(
            "nl_hansard: processing %d sittings (ga=%d session=%d)",
            len(refs), ga, session,
        )

        for ref in refs:
            if limit_speeches and (
                stats.speeches_inserted + stats.speeches_updated
            ) >= limit_speeches:
                break
            if since and ref.sitting_date < since:
                continue
            if until and ref.sitting_date > until:
                continue
            stats.sittings_scanned += 1

            try:
                r = await _get_with_retry(client, ref.url)
                r.raise_for_status()
                # Both Hansard HTML eras declare UTF-8; httpx picks it
                # up from the meta tag if the server omits the charset
                # (assembly.nl.ca does omit). Force UTF-8 to be safe.
                r.encoding = "utf-8"
                page_html = r.text
            except Exception as exc:
                log.warning("sitting %s: fetch failed: %s", ref.url, exc)
                continue

            if _is_catchall_404(page_html):
                log.warning("sitting %s: catch-all 404 — skipping", ref.url)
                stats.sittings_skipped_404 += 1
                continue

            try:
                result = parse_mod.extract_speeches(page_html, ref.url)
            except Exception as exc:
                log.warning("sitting %s: parse failed: %s", ref.url, exc)
                stats.parse_errors += 1
                continue

            if len(result.speeches) < 3:
                log.warning(
                    "sitting %s: only %d speeches parsed — skipping",
                    ref.url, len(result.speeches),
                )
                stats.parse_errors += 1
                continue

            log.info(
                "sitting %s (era=%s partial=%s) → %d speeches",
                ref.sitting_date, result.era, result.partial,
                len(result.speeches),
            )

            for ps in result.speeches:
                if limit_speeches and (
                    stats.speeches_inserted + stats.speeches_updated
                ) >= limit_speeches:
                    break
                stats.speeches_seen += 1

                politician_id: Optional[str] = None
                confidence = 0.0
                if ps.is_group:
                    stats.speeches_group += 1
                    confidence = 1.0  # group marker is unambiguous-as-a-group
                elif ps.speaker_role:
                    # Presiding role — resolved in post-pass via
                    # resolve-presiding-speakers.
                    stats.speeches_role_only += 1
                    confidence = 0.5
                else:
                    pid, status = lookup.resolve(
                        first_initial=ps.first_initial,
                        surname=ps.surname,
                        sitting_date=result.sitting_date,
                    )
                    if status == "resolved":
                        politician_id = pid
                        stats.speeches_resolved += 1
                        confidence = 1.0
                    elif status == "ambiguous":
                        stats.speeches_ambiguous += 1
                        confidence = 0.5
                    else:
                        stats.speeches_unresolved += 1
                        confidence = 0.0

                outcome = await _upsert_speech(
                    db,
                    session_id=session_id,
                    ref=ref,
                    parsed=ps,
                    politician_id=politician_id,
                    confidence=confidence,
                    partial=result.partial,
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
           AND s.province_territory = 'NL'
           AND s.source_system = $1
           AND sc.politician_id IS DISTINCT FROM s.politician_id
        """,
        SOURCE_SYSTEM,
    )

    log.info(
        "nl_hansard done: %d sittings (skipped_404=%d), %d speeches "
        "(inserted=%d updated=%d skipped_empty=%d parse_errors=%d) "
        "resolved=%d group=%d role=%d ambiguous=%d unresolved=%d",
        stats.sittings_scanned,
        stats.sittings_skipped_404,
        stats.speeches_seen,
        stats.speeches_inserted,
        stats.speeches_updated,
        stats.skipped_empty,
        stats.parse_errors,
        stats.speeches_resolved,
        stats.speeches_group,
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


async def resolve_nl_speakers(
    db: Database, *, limit: Optional[int] = None,
) -> ResolveStats:
    """Re-resolve politician_id on NL Hansard speeches with NULL politician_id.

    Run after adding more NL MHAs (historical backfill) or after fixing
    a parser edge case. Role-only rows (The Speaker) are covered by
    ``resolve-presiding-speakers --province NL`` — this pass only
    touches name-bearing rows (those with `surname` in raw).
    """
    stats = ResolveStats()
    lookup = await load_nl_speaker_lookup(db)

    query = """
        SELECT s.id::text AS id,
               s.spoken_at::date AS spoken_date,
               s.raw->'nl_hansard'->>'first_initial' AS first_initial,
               s.raw->'nl_hansard'->>'surname'       AS surname,
               s.raw->'nl_hansard'->>'is_group'      AS is_group
          FROM speeches s
         WHERE s.level = 'provincial'
           AND s.province_territory = 'NL'
           AND s.source_system = $1
           AND s.politician_id IS NULL
           AND (s.raw->'nl_hansard'->>'is_group') IS DISTINCT FROM 'true'
           AND (s.speaker_role IS NULL OR s.speaker_role = '')
    """
    if limit:
        query += f" LIMIT {int(limit)}"

    rows = await db.fetch(query, SOURCE_SYSTEM)
    for r in rows:
        stats.speeches_scanned += 1
        pid, status = lookup.resolve(
            first_initial=r["first_initial"],
            surname=r["surname"],
            sitting_date=r["spoken_date"],
        )
        if status == "resolved" and pid:
            await db.execute(
                """
                UPDATE speeches
                   SET politician_id = $1::uuid,
                       confidence    = GREATEST(confidence, 0.9),
                       updated_at    = now()
                 WHERE id = $2::uuid
                """,
                pid, r["id"],
            )
            await db.execute(
                """
                UPDATE speech_chunks
                   SET politician_id = $1::uuid
                 WHERE speech_id = $2::uuid
                   AND politician_id IS DISTINCT FROM $1::uuid
                """,
                pid, r["id"],
            )
            stats.speeches_updated += 1
        else:
            stats.still_unresolved += 1

    log.info(
        "resolve_nl_speakers: scanned=%d updated=%d still_unresolved=%d",
        stats.speeches_scanned, stats.speeches_updated, stats.still_unresolved,
    )
    return stats
