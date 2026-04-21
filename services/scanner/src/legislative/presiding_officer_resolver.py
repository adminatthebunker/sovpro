"""Resolve politician_id on presiding-officer (Speaker) turns in
provincial Hansard by date-ranged lookup into `politician_terms`.

Problem: rows like `speaker_role='The Speaker'` with `politician_id=NULL`
don't carry a name in the speaker line — only the role title. The actual
person holding the Speaker's chair on any given sitting day is knowable
from the Legislature's public records, but it's external data we have
to seed.

Approach:
  1. A small hand-curated roster (SPEAKER_ROSTER) lists every Speaker of
     the House for each jurisdiction with exact start/end dates. The
     roster is intentionally **data-only** — if a Speaker changes, we
     amend this file, re-run, and the backfill is idempotent.
  2. `ensure_speaker_politicians` inserts any roster name that's not
     already in `politicians` as a minimal row (level=provincial,
     is_active=false). Historical Speakers (retired, deceased) are
     otherwise absent from the current-roster-only politician tables.
  3. `ensure_speaker_terms` upserts rows into `politician_terms` with
     `office='Speaker'` and the roster's start/end dates. The `source`
     column is set to 'presiding_officer_seed' so re-runs can delete
     and re-insert cleanly (no unique constraint on politician_terms).
  4. `resolve_speakers` walks `speeches` WHERE `politician_id IS NULL`
     AND the role/name line indicates "The Speaker", joins against
     `politician_terms` by date range, and updates speeches +
     speech_chunks in one pass.

Scope: Tier 1 only (the Speaker). Deputy Speaker, Acting Speaker, and
Committee-of-the-Whole Chair are separate workstreams — the Speaker
role is single-person-at-a-time and fully date-determinable, which the
other roles are not.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from typing import Optional

from ..db import Database

log = logging.getLogger(__name__)


# ── Speaker rosters ─────────────────────────────────────────────────
#
# Dates sourced from Wikipedia + official Legislature pages. End date
# of one Speaker is the start date of the next — the brief gaps between
# sessions contain no Hansard speeches. `None` ended_at means still
# serving.
#
# Adding a new Speaker:
#   1. Append a tuple to the relevant list.
#   2. Update the previous Speaker's ended_at to the new one's start.
#   3. Re-run `resolve-presiding-speakers` — it's idempotent.

@dataclass(frozen=True)
class SpeakerTerm:
    full_name: str      # as it should appear in `politicians.name`
    first_name: str
    last_name: str
    started_at: date
    ended_at: Optional[date]


SPEAKER_ROSTER: dict[str, list[SpeakerTerm]] = {
    # Alberta: Speakers #11 through current (covers Hansard corpus
    # from 2000-02-17). Source: Wikipedia "Speaker of the Legislative
    # Assembly of Alberta" + assembly.ab.ca.
    "AB": [
        SpeakerTerm("Ken Kowalski",    "Ken",    "Kowalski",  date(1997, 4, 14), date(2012, 5, 23)),
        SpeakerTerm("Gene Zwozdesky",  "Gene",   "Zwozdesky", date(2012, 5, 23), date(2015, 6, 11)),
        SpeakerTerm("Bob Wanner",      "Bob",    "Wanner",    date(2015, 6, 11), date(2019, 5, 20)),
        SpeakerTerm("Nathan Cooper",   "Nathan", "Cooper",    date(2019, 5, 21), date(2025, 5, 13)),
        SpeakerTerm("Ric McIver",      "Ric",    "McIver",    date(2025, 5, 13), None),
    ],
    # British Columbia: Speakers #36 through current (covers Hansard
    # corpus from 2008-02-12). Source: Wikipedia "Speaker of the
    # Legislative Assembly of British Columbia" + 41st Parliament of BC.
    # The 41st Parliament had three Speakers: Thomson resigned after
    # one week (June 22–29, 2017); the chair sat vacant through summer
    # recess until Plecas was acclaimed September 8, 2017.
    "BC": [
        SpeakerTerm("Bill Barisoff",   "Bill",    "Barisoff", date(2005, 5, 17), date(2013, 5, 14)),
        SpeakerTerm("Linda Reid",      "Linda",   "Reid",     date(2013, 5, 14), date(2017, 6, 22)),
        SpeakerTerm("Steve Thomson",   "Steve",   "Thomson",  date(2017, 6, 22), date(2017, 6, 29)),
        SpeakerTerm("Darryl Plecas",   "Darryl",  "Plecas",   date(2017, 9,  8), date(2020, 12, 7)),
        SpeakerTerm("Raj Chouhan",     "Raj",     "Chouhan",  date(2020, 12, 7), None),
    ],
    # Quebec: Presidents of the Assemblée nationale. "Le Président" /
    # "La Présidente" is the QC equivalent of "The Speaker". Roster
    # covers current 43rd legislature plus historical sessions back to
    # the 38th (2007+) — the range Wayback CDX surfaces transcript URLs
    # for. Earlier sessions would need additional roster entries + a
    # historical MNA backfill to be worth resolving.
    # Source: Wikipedia "Président de l'Assemblée nationale du Québec"
    # + assnat.qc.ca historical records.
    "QC": [
        SpeakerTerm("Michel Bissonnet",  "Michel",   "Bissonnet", date(2003,  5, 13), date(2008,  4,  8)),
        SpeakerTerm("Yvon Vallières",    "Yvon",     "Vallières", date(2008,  4,  8), date(2011,  4,  5)),
        SpeakerTerm("Jacques Chagnon",   "Jacques",  "Chagnon",   date(2011,  4,  5), date(2018, 10,  1)),
        SpeakerTerm("François Paradis",  "François", "Paradis",   date(2018, 11, 28), date(2022, 11, 29)),
        SpeakerTerm("Nathalie Roy",      "Nathalie", "Roy",       date(2022, 11, 29), None),
    ],
    # Manitoba: covers the 43rd Legislature (2023-present) which is
    # where the Hansard corpus currently lives. Earlier Speakers
    # (Driedger, Reid, Hickes) will be added when we backfill
    # pre-2023 sittings — not needed for the current-session ingest.
    # Source: Wikipedia "Speaker of the Legislative Assembly of
    # Manitoba" + gov.mb.ca/legislature/members.
    "MB": [
        SpeakerTerm("Tom Lindsey",    "Tom",    "Lindsey",  date(2023, 11, 21), None),
    ],
}


SOURCE_TAG = "presiding_officer_seed"


# ── Seeding politicians + politician_terms ─────────────────────────

async def _find_politician_id(
    db: Database, *, province: str, first_name: str, last_name: str,
) -> Optional[str]:
    """Case-insensitive lookup by (first_name, last_name) within a province."""
    row = await db.fetchrow(
        """
        SELECT id::text AS id
          FROM politicians
         WHERE level = 'provincial'
           AND province_territory = $1
           AND lower(first_name) = lower($2)
           AND lower(last_name)  = lower($3)
         LIMIT 1
        """,
        province, first_name, last_name,
    )
    return row["id"] if row else None


async def _insert_minimal_politician(
    db: Database, *, province: str, term: SpeakerTerm,
) -> str:
    """Insert a retired Speaker as a minimal politicians row and return UUID.

    Matches the field set used by `scripts/bc-enrich-historical-mlas.py`:
    name + first_name + last_name + level + province_territory +
    is_active=false, with empty jsonb for social_urls/extras and a
    `source_id` tag so operators can trace origin.
    """
    row = await db.fetchrow(
        """
        INSERT INTO politicians (
            name, first_name, last_name,
            level, province_territory,
            is_active, social_urls, extras, source_id
        )
        VALUES ($1, $2, $3, 'provincial', $4,
                false, '{}'::jsonb, '{}'::jsonb, $5)
        RETURNING id::text AS id
        """,
        term.full_name, term.first_name, term.last_name,
        province,
        f"presiding-officer-seed:{province}:{term.last_name.lower()}",
    )
    log.info("inserted %s (%s) → politicians.%s", term.full_name, province, row["id"])
    return row["id"]


async def ensure_speaker_politicians(
    db: Database, province: str,
) -> dict[str, str]:
    """Ensure every Speaker in the roster exists in `politicians`.
    Returns {full_name: politician_id}.
    """
    roster = SPEAKER_ROSTER.get(province, [])
    out: dict[str, str] = {}
    inserted = 0
    for term in roster:
        pid = await _find_politician_id(
            db, province=province,
            first_name=term.first_name, last_name=term.last_name,
        )
        if pid is None:
            pid = await _insert_minimal_politician(db, province=province, term=term)
            inserted += 1
        out[term.full_name] = pid
    log.info(
        "ensure_speaker_politicians(%s): roster=%d inserted=%d",
        province, len(roster), inserted,
    )
    return out


async def ensure_speaker_terms(
    db: Database, province: str, *, name_to_id: dict[str, str],
) -> int:
    """Upsert Speaker-office rows into `politician_terms` for this province.

    Idempotent: deletes any rows with our source tag for this
    province+level+office='Speaker' first, then re-inserts. This avoids
    needing a unique constraint migration for a small, curated dataset.
    """
    await db.execute(
        """
        DELETE FROM politician_terms
         WHERE level = 'provincial'
           AND province_territory = $1
           AND office = 'Speaker'
           AND source = $2
        """,
        province, SOURCE_TAG,
    )
    roster = SPEAKER_ROSTER.get(province, [])
    inserted = 0
    for term in roster:
        pid = name_to_id[term.full_name]
        await db.execute(
            """
            INSERT INTO politician_terms (
                politician_id, office, level, province_territory,
                started_at, ended_at, source
            )
            VALUES ($1::uuid, 'Speaker', 'provincial', $2, $3, $4, $5)
            """,
            pid,
            province,
            term.started_at, term.ended_at,
            SOURCE_TAG,
        )
        inserted += 1
    log.info("ensure_speaker_terms(%s): %d rows", province, inserted)
    return inserted


# ── Resolution ──────────────────────────────────────────────────────

@dataclass
class ResolveStats:
    scanned: int = 0
    resolved: int = 0
    no_term_match: int = 0
    chunks_updated: int = 0


# Which `speeches.speaker_role` values indicate the presiding Speaker for
# each jurisdiction. Tier 1 only — Deputy Speaker / Chair are NOT covered.
# Parser modules must emit these exact strings; if you add a new
# jurisdiction, check which canonical role(s) its parser emits for the
# main Speaker chair and add them here.
_SPEAKER_ROLE_BY_PROVINCE: dict[str, tuple[str, ...]] = {
    "AB": ("The Speaker", "Speaker"),
    "BC": ("The Speaker", "Speaker"),
    # Quebec: Journal des débats labels the Speaker "Le Président" /
    # "La Présidente"; the qc_hansard parser normalises both to
    # "Le Président". "Le Vice-Président" (Deputy) is Tier 2 and
    # intentionally excluded.
    "QC": ("Le Président",),
    # Manitoba: the mb_hansard parser normalises "Madam Speaker",
    # "Mister Speaker", and "The Speaker" all to "The Speaker".
    "MB": ("The Speaker",),
}

# Back-compat default for any province without an explicit mapping.
_DEFAULT_SPEAKER_ROLE_VALUES: tuple[str, ...] = ("The Speaker", "Speaker")

# Speaker_name_raw fallbacks for rows where `speaker_role` is NULL but the
# raw attribution line clearly indicates the Speaker. AB Hansard occasionally
# stores "Mr. Speaker" directly in speaker_name_raw for older eras
# (~40 rows observed). These are added to the OR-match regardless of
# province — they're English-only and harmless on QC rows.
_SPEAKER_NAME_PATTERNS = (
    "Mr. Speaker", "Madam Speaker", "Madame Speaker",
    "The Speaker",
)


def _speaker_role_values(province: str) -> tuple[str, ...]:
    return _SPEAKER_ROLE_BY_PROVINCE.get(province, _DEFAULT_SPEAKER_ROLE_VALUES)


async def resolve_speakers(
    db: Database, province: str, *, limit: Optional[int] = None,
) -> ResolveStats:
    """Update speeches (and chunks) where speaker_role indicates 'The Speaker'
    and politician_id is NULL, by looking up the active Speaker term for
    the spoken_at date.

    Updates speech_chunks.politician_id as well so retrieval-side joins
    stay consistent (chunks created pre-resolution held the NULL copy).
    """
    stats = ResolveStats()

    where = """
        s.level = 'provincial'
        AND s.province_territory = $1
        AND s.politician_id IS NULL
        AND (
            s.speaker_role = ANY($2::text[])
            OR (
                (s.speaker_role IS NULL OR s.speaker_role = '')
                AND s.speaker_name_raw = ANY($3::text[])
            )
        )
    """
    sql = f"""
        SELECT s.id::text AS id,
               s.spoken_at::date AS spoken_date
          FROM speeches s
         WHERE {where}
    """
    params: list = [province, list(_speaker_role_values(province)), list(_SPEAKER_NAME_PATTERNS)]
    if limit is not None:
        sql += f" LIMIT {int(limit)}"

    rows = await db.fetch(sql, *params)
    stats.scanned = len(rows)

    # Load Speaker terms once. For Tier 1 there are <=10 rows per province.
    term_rows = await db.fetch(
        """
        SELECT politician_id::text AS politician_id,
               started_at::date    AS started_at,
               ended_at::date      AS ended_at
          FROM politician_terms
         WHERE level = 'provincial'
           AND province_territory = $1
           AND office = 'Speaker'
           AND source = $2
         ORDER BY started_at
        """,
        province, SOURCE_TAG,
    )

    def find_speaker_for(d: date) -> Optional[str]:
        for t in term_rows:
            started = t["started_at"]
            ended = t["ended_at"]
            if d >= started and (ended is None or d < ended):
                return t["politician_id"]
        return None

    # Bucket updates by politician_id for bulk updates.
    by_politician: dict[str, list[str]] = {}
    for r in rows:
        d = r["spoken_date"]
        if d is None:
            stats.no_term_match += 1
            continue
        pid = find_speaker_for(d)
        if pid is None:
            stats.no_term_match += 1
            continue
        by_politician.setdefault(pid, []).append(r["id"])

    # Flush in 5k-row batches — passing 100k+ UUIDs to ANY($1::uuid[]) in
    # a single statement times out asyncpg. Confidence 0.9 (below full-name
    # match's 1.0, above ambiguous surname's 0.5) — we're certain of the
    # date window but not of any per-speech semantic check.
    BATCH = 5000
    for pid, speech_ids in by_politician.items():
        for i in range(0, len(speech_ids), BATCH):
            batch = speech_ids[i : i + BATCH]
            await db.execute(
                """
                UPDATE speeches
                   SET politician_id = $1::uuid,
                       confidence    = GREATEST(confidence, 0.9),
                       updated_at    = now()
                 WHERE id = ANY($2::uuid[])
                   AND politician_id IS NULL
                """,
                pid, batch,
            )
            result = await db.execute(
                """
                UPDATE speech_chunks
                   SET politician_id = $1::uuid
                 WHERE speech_id = ANY($2::uuid[])
                   AND politician_id IS DISTINCT FROM $1::uuid
                """,
                pid, batch,
            )
            stats.resolved += len(batch)
            # asyncpg returns a command tag like "UPDATE 123" — parse the count.
            try:
                stats.chunks_updated += int(result.split()[-1])
            except (ValueError, AttributeError):
                pass

    # Final reconcile: catch any speech_chunks whose politician_id drifted
    # from the parent speech. This guards against timeout-aborted prior
    # runs where the speech UPDATE committed but the matching chunk
    # UPDATE never did — on re-run, the speech is no longer NULL so it
    # falls out of `rows` above, and the chunk desync persists. One
    # targeted sweep closes the loop regardless of run history.
    reconcile = await db.execute(
        """
        UPDATE speech_chunks sc
           SET politician_id = s.politician_id
          FROM speeches s
         WHERE sc.speech_id = s.id
           AND s.level = 'provincial'
           AND s.province_territory = $1
           AND s.speaker_role = ANY($2::text[])
           AND s.politician_id IS NOT NULL
           AND sc.politician_id IS DISTINCT FROM s.politician_id
        """,
        province, list(_speaker_role_values(province)),
    )
    try:
        stats.chunks_updated += int(reconcile.split()[-1])
    except (ValueError, AttributeError):
        pass

    log.info(
        "resolve_speakers(%s): scanned=%d resolved=%d no_term_match=%d chunks_updated=%d",
        province, stats.scanned, stats.resolved, stats.no_term_match, stats.chunks_updated,
    )
    return stats


async def seed_and_resolve(
    db: Database, province: str, *, limit: Optional[int] = None,
) -> dict:
    """End-to-end convenience: ensure politicians + terms, then resolve.

    Idempotent. Safe to re-run after adding a new Speaker row to
    SPEAKER_ROSTER — the backfill picks up the change.
    """
    name_to_id = await ensure_speaker_politicians(db, province)
    terms_count = await ensure_speaker_terms(db, province, name_to_id=name_to_id)
    stats = await resolve_speakers(db, province, limit=limit)
    return {
        "province": province,
        "roster": len(name_to_id),
        "terms": terms_count,
        "scanned": stats.scanned,
        "resolved": stats.resolved,
        "no_term_match": stats.no_term_match,
        "chunks_updated": stats.chunks_updated,
    }
