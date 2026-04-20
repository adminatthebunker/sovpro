"""British Columbia Hansard ingester — LIMS HDMS HTML → `speeches` table.

BC is the first HTML-scrape provincial Hansard pipeline (federal consumes
openparliament.ca JSON; AB parses PDFs). Both Blues (draft) and Final
variants are served by the same LIMS HDMS file server:

  Discovery:  GET https://lims.leg.bc.ca/hdms/debates/{parl}{sess}
              → JSON list of every sitting with Blues filename + Final
                redirect URL (when published) + timing metadata.
  Blues:      GET https://lims.leg.bc.ca/hdms/file/Debates/{parl}{sess}/
                     {YYYYMMDD}{am|pm}-House-Blues.htm
  Final:      GET https://lims.leg.bc.ca/hdms/file/Debates/{parl}{sess}/
                     {YYYYMMDD}{am|pm}-Hansard-n{NNN}.html

Both variants share a rich semantic markup (`SpeakerBegins`, `Time-Stamp`,
`Proceedings-Group`, etc.). `bc_hansard_parse.extract_speeches` handles
both in one pass.

## Upsert key — canonical URL strategy

`speeches` has `UNIQUE NULLS NOT DISTINCT (source_system, source_url, sequence)`.
For a given sitting the Blues and Final HTML are two snapshots of the same
underlying content. To let Final *replace* Blues in place (the user's
"Both, with replacement" choice) we use a synthesized canonical URL as
`source_url`:

    hansard-bc.canonical/Debates/{parl}{sess}/{YYYYMMDD}{am|pm}-Hansard.html

Real URLs live in `speeches.raw` as `blues_url` / `final_url` / `variant`.
This is the non-obvious design choice — a reviewer will rightly ask why
`source_url` isn't the real HTTP URL. Answer: because both Blues and Final
need to map to the same row, and the real URLs differ (Final has issue
number `-n{NNN}`).

## Resolution

`politicians.lims_member_id` is the integer key. `bc_bills.enrich_bc_member_ids`
populates it before the bills pipeline runs; the Hansard pipeline reads
it via `load_bc_speaker_lookup`. Presiding-officer rows ("The Speaker",
"Deputy Speaker") are left `politician_id=NULL` in v1 — a follow-up pass
resolves them via `politician_terms.office='Speaker'` within sitting dates.

## Scope

v1: current session (43rd Parliament, 2nd Session). Historical backfill
is safe to run anytime — the JSON discovery endpoint serves every session
LIMS has indexed.
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
from . import bc_hansard_parse as parse_mod

log = logging.getLogger(__name__)

SOURCE_SYSTEM = "hansard-bc"
GRAPHQL_URL = "https://lims.leg.bc.ca/graphql"
DEBATES_INDEX_URL = "https://lims.leg.bc.ca/hdms/debates/{parl}{sess}"
HDMS_FILE_URL = "https://lims.leg.bc.ca/hdms/file/Debates/{parl}{sess}/{filename}"

# Canonical URL template — stable across Blues/Final for the same sitting.
# Fictitious hostname makes it unambiguous this is NOT a clickable link.
CANONICAL_URL = (
    "https://hansard-bc.canonical/Debates/{parl}{sess}/{YYYYMMDD}{half}-Hansard.html"
)

REQUEST_TIMEOUT = 60
REQUEST_DELAY_SECONDS = 1.5  # Polite to lims.leg.bc.ca

HEADERS = {
    "User-Agent": "SovereignWatch/1.0 (civic-transparency; +admin@thebunkerops.ca)",
    "Accept": "text/html,application/xhtml+xml,application/json",
    "Origin": "https://dyn.leg.bc.ca",
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
RETRY_BACKOFF_SECONDS = (2, 4, 8, 16, 32)
RETRY_ON_STATUS = (500, 502, 503, 504)


async def _get_with_retry(client: httpx.AsyncClient, url: str) -> httpx.Response:
    last_exc: Optional[BaseException] = None
    for attempt, delay in enumerate((0,) + RETRY_BACKOFF_SECONDS):
        if delay:
            log.warning(
                "bc_hansard retry %d/%d after %ds — last error: %s",
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
    assert last_exc is not None
    raise last_exc


# ── Parliament+session slug helpers ─────────────────────────────────

_ORDINAL_SUFFIX = {1: "st", 2: "nd", 3: "rd"}


def _parl_slug(parliament: int) -> str:
    """43 → '43rd', 42 → '42nd'."""
    last = parliament % 10 if parliament % 100 not in (11, 12, 13) else 0
    return f"{parliament}{_ORDINAL_SUFFIX.get(last, 'th')}"


def _sess_slug(session: int) -> str:
    """2 → '2nd', 1 → '1st'."""
    last = session % 10 if session % 100 not in (11, 12, 13) else 0
    return f"{session}{_ORDINAL_SUFFIX.get(last, 'th')}"


def _session_path(parliament: int, session: int) -> str:
    """'43rd2nd' for (43, 2)."""
    return f"{_parl_slug(parliament)}{_sess_slug(session)}"


# ── Discovery ───────────────────────────────────────────────────────

@dataclass
class SittingRef:
    sitting_date: date
    half: str                      # 'am' | 'pm'
    parliament: int
    session: int
    blues_filename: str            # e.g. '20260415pm-House-Blues.htm'
    final_filename: Optional[str]  # e.g. '20260218pm-Hansard-n118.html'
    issue_number: Optional[int]
    debate_type: str               # 'House' | 'Section A' | 'Section C' | ...
    published: bool                # Final available

    @property
    def best_url(self) -> str:
        """Final if published+available, else Blues."""
        filename = (
            self.final_filename
            if (self.published and self.final_filename)
            else self.blues_filename
        )
        return HDMS_FILE_URL.format(
            parl=_parl_slug(self.parliament),
            sess=_sess_slug(self.session),
            filename=filename,
        )

    @property
    def canonical_url(self) -> str:
        return CANONICAL_URL.format(
            parl=_parl_slug(self.parliament),
            sess=_sess_slug(self.session),
            YYYYMMDD=self.sitting_date.strftime("%Y%m%d"),
            half=self.half,
        )


_REDIRECT_FINAL_RE = re.compile(
    r"(?P<filename>\d{8}(?:am|pm)-Hansard-(?:n\d+|v\d+n\d+)\.html?)",
    re.IGNORECASE,
)


def _parse_debate_index_entry(node: dict, parliament: int, session: int) -> Optional[SittingRef]:
    """Translate one debate-index node into a SittingRef.

    Only emits rows for `House` debates in v1 — Section A/C committee
    transcripts are a future workstream.

    Two filename shapes in the wild:
      Blues: `{YYYYMMDD}{am|pm}-House-Blues.htm` (P40-S4 onward, transitional)
      Final: `{YYYYMMDD}{am|pm}-Hansard-n{NNN}.html` (43rd Parl era)
             `{YYYYMMDD}{am|pm}-Hansard-v{VOL}n{NNN}.htm` (pre-43rd Parl)
    """
    filename = (node.get("fileName") or "").strip()
    if not filename.lower().endswith((".htm", ".html")):
        return None
    try:
        meta = parse_mod.parse_url_meta(f"/{filename}")
    except ValueError:
        # Committee transcripts (e.g. 20260226pm-CommitteeA-Blues.htm) and
        # other non-House files don't match the House filename pattern.
        return None

    attrs_nodes = (node.get("debateAttributes") or {}).get("nodes") or []
    if not attrs_nodes:
        return None
    a = attrs_nodes[0]
    # `debateType` is populated for modern sessions. Older (pre-P40)
    # sessions leave it null — assume House unless committeeA/C links
    # indicate otherwise.
    dt_obj = a.get("debateType")
    if dt_obj is None:
        if a.get("committeeALink") or a.get("committeeCLink"):
            return None
        debate_type = "House"
    else:
        debate_type = (dt_obj.get("name") or "House").strip()
        if debate_type.lower() != "house":
            return None

    published = bool(node.get("published"))
    issue_number = a.get("issueNumber") or None
    if isinstance(issue_number, int) and issue_number == 0:
        issue_number = None

    blues_filename: Optional[str] = None
    final_filename: Optional[str] = None

    if meta.variant == "blues":
        # Modern path: filename IS the Blues; Final (if any) is in redirect.
        blues_filename = filename
        redirect = (a.get("redirectLink") or "").strip()
        if redirect:
            m = _REDIRECT_FINAL_RE.search(redirect)
            if m:
                final_filename = m.group("filename")
    else:
        # Historical path: filename IS the Final, no Blues exists.
        final_filename = filename

    return SittingRef(
        sitting_date=meta.sitting_date,
        half=meta.half,
        parliament=parliament,
        session=session,
        blues_filename=blues_filename or filename,  # fallback for display/raw
        final_filename=final_filename,
        issue_number=issue_number or meta.issue,
        debate_type=debate_type,
        published=published or (meta.variant == "final"),
    )


async def discover_sitting_refs(
    client: httpx.AsyncClient, *, parliament: int, session: int,
) -> list[SittingRef]:
    """Fetch the HDMS debate-index JSON for a session and return SittingRefs.

    The index lists BOTH the Blues and the Final for sittings where Final
    is published — two entries for the same (date, half). We dedupe by
    canonical URL and prefer the entry that has a real Final filename
    (which lets `best_url` fetch the authoritative version directly).
    """
    url = DEBATES_INDEX_URL.format(parl=_parl_slug(parliament), sess=_sess_slug(session))
    r = await _get_with_retry(client, url)
    r.raise_for_status()
    data = r.json()
    nodes = (data.get("allHansardFileAttributes") or {}).get("nodes") or []

    by_canonical: dict[str, SittingRef] = {}
    for node in nodes:
        ref = _parse_debate_index_entry(node, parliament, session)
        if not ref:
            continue
        key = ref.canonical_url
        existing = by_canonical.get(key)
        if existing is None:
            by_canonical[key] = ref
            continue
        # Prefer whichever ref has a known Final filename. Keep issue
        # number from either (prefer non-None).
        if ref.final_filename and not existing.final_filename:
            merged = ref
        else:
            merged = existing
        merged.issue_number = merged.issue_number or existing.issue_number or ref.issue_number
        if existing.blues_filename and not merged.blues_filename.endswith("-House-Blues.htm"):
            # keep real Blues filename when available (historical sessions
            # won't have one)
            merged.blues_filename = existing.blues_filename if existing.blues_filename.endswith("-House-Blues.htm") else merged.blues_filename
        merged.published = merged.published or existing.published
        by_canonical[key] = merged

    refs = list(by_canonical.values())
    refs.sort(key=lambda r: (r.sitting_date, r.half))
    log.info(
        "bc_hansard discover: %d sittings (from %d raw index entries) for %d-%d",
        len(refs), len(nodes), parliament, session,
    )
    return refs


# ── Sessions ────────────────────────────────────────────────────────

_SESSIONS_QUERY = """
query GetSession($number: Int!) {
  allSessions(filter: {number: {equalTo: $number}}) {
    nodes { id number parliamentId startDate endDate }
  }
}
"""


async def ensure_session(
    db: Database, *, parliament: int, session: int,
) -> str:
    """Return legislative_sessions.id for BC (parliament, session).

    BC bills pipeline already upserts the session row; this function is
    idempotent and also fills in start/end dates from LIMS GraphQL when
    they're missing (defensive for standalone Hansard runs).
    """
    row = await db.fetchrow(
        """
        INSERT INTO legislative_sessions
            (level, province_territory, parliament_number, session_number,
             name, source_system, source_url)
        VALUES ('provincial', 'BC', $1, $2, $3, 'lims-bc', $4)
        ON CONFLICT (level, province_territory, parliament_number, session_number)
        DO UPDATE SET updated_at = now()
        RETURNING id
        """,
        parliament, session,
        f"{_parl_slug(parliament)} Parliament, {_sess_slug(session)} Session",
        DEBATES_INDEX_URL.format(parl=_parl_slug(parliament), sess=_sess_slug(session)),
    )
    return str(row["id"])


# ── Speaker → politician resolution ─────────────────────────────────

_WS_RE = re.compile(r"\s+")


def _norm(s: str) -> str:
    """Case/accent/punct-normalised form for dictionary lookups."""
    if not s:
        return ""
    t = unicodedata.normalize("NFKD", s)
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = t.lower()
    t = re.sub(r"[^\w\s-]", " ", t)
    return _WS_RE.sub(" ", t).strip()


_HONORIFIC_RE = re.compile(
    r"^(?:hon\.?|mr\.?|mrs\.?|ms\.?|miss|madam|madame|dr\.?|"
    r"member|premier|minister)\s+",
    re.IGNORECASE,
)


def _strip_honorifics(s: str) -> str:
    prev = None
    out = (s or "").strip()
    while out != prev:
        prev = out
        out = _HONORIFIC_RE.sub("", out).strip()
    return out


# Presiding-officer names — never resolve to a politician in this pass.
# Parliament → sitting-Speaker name. Fallback for sessions whose Final
# HTML lacks a structured Speaker header (P40–P42 Final format). The
# Blues variant carries the name in <h3 class="heading-right speaker">
# when available, but we can't always fetch Blues. This map is small,
# stable, and covers everything our parser will encounter.
#
# Sources: BC Legislature historical records.
BC_PARLIAMENT_SPEAKER = {
    43: "Raj Chouhan",           # 2024-present (also 2022-2024)
    42: "Raj Chouhan",           # 2020-2024
    41: "Darryl Plecas",         # 2017-2020 (also briefly Linda Reid early-term)
    40: "Linda Reid",            # 2013-2017
    39: "Bill Barisoff",         # 2009-2013
    38: "Bill Barisoff",         # 2005-2009
}


# Presiding-officer role labels — speaker_name_raw values that aren't a
# specific person. Pre-P43 Hansard uses "Mr./Madam/Madame Speaker" rather
# than "The Speaker", so both variants belong here. `_strip_honorifics`
# removes leading "Mr./Madam/etc." before lookup, so e.g.
# "Mr. Speaker" → "speaker" and must match below.
PRESIDING_ROLE_NAMES = {
    "speaker",              # after stripping Mr./Madam/Madame/etc.
    "the speaker",
    "deputy speaker",
    "assistant deputy speaker",
    "the chair",
    "deputy chair",
    "the deputy chair",
    "the acting chair",
    "assistant deputy chair",
    "clerk of the house",   # ceremonial role, not an MLA
    "clerk",
}


@dataclass
class SpeakerLookup:
    """Indexed BC MLAs for speaker-line resolution.

    Speaker-name shapes in BC Hansard by era:
      P43+:     "Hon. David Eby" / "Peter Milobar"     (full first name)
      P42-:     "Hon. K. Conroy" / "P. Milobar"        (first-initial + last)
      Any:      "Hon Chan" / "The Speaker"             (surname only / role)

    Four indexes cover these cases:
      by_full_name      — "david eby" / "peter milobar" → politicians
      by_initial_last   — "p milobar" / "k conroy" → politicians (P42- style)
      by_surname        — "milobar" alone → politicians (ambiguous when common)
    """
    by_full_name: dict[str, list[dict]] = field(default_factory=dict)
    by_initial_last: dict[str, list[dict]] = field(default_factory=dict)
    by_surname: dict[str, list[dict]] = field(default_factory=dict)

    def resolve(
        self,
        speaker_name_raw: str,
        *,
        sitting_speaker_name: Optional[str] = None,
    ) -> tuple[Optional[dict], str]:
        """Returns (politician_row_or_None, status).

        Status: 'resolved' | 'presiding' | 'ambiguous' | 'unresolved' | 'role'.
        'presiding' = role-only attribution resolved via sitting_speaker_name.
        """
        if not speaker_name_raw:
            return None, "unresolved"

        cleaned = _strip_honorifics(speaker_name_raw)
        low = cleaned.lower().strip()
        if low in PRESIDING_ROLE_NAMES:
            # "Speaker" / "The Speaker" / "Mr. Speaker" attributions
            # resolve to the sitting's Speaker (extracted from the HTML
            # header). Deputy Speaker / Chair aren't identified in the
            # HTML, so they remain role-only.
            if low in ("speaker", "the speaker") and sitting_speaker_name:
                key = _norm(sitting_speaker_name)
                hits = self.by_full_name.get(key)
                if hits and len(hits) == 1:
                    return hits[0], "presiding"
            return None, "role"

        # "Hon. Laanas / Tamara Davidson" → try both halves
        candidates = [cleaned]
        if "/" in cleaned:
            candidates.extend(part.strip() for part in cleaned.split("/") if part.strip())

        for c in candidates:
            key = _norm(c)
            if not key:
                continue
            hits = self.by_full_name.get(key)
            if hits and len(hits) == 1:
                return hits[0], "resolved"
            if hits and len(hits) > 1:
                return None, "ambiguous"

        # Initial-last pass (pre-P43 Hansard style: "P. Milobar", "K. Conroy",
        # "M. de Jong"). The by_initial_last index is keyed on
        # "{initial} {last_token_of_surname}" (see load_bc_speaker_lookup),
        # so compound surnames that normalise to 3+ tokens ("m de jong")
        # must be reduced to "m jong" for the lookup to hit.
        for c in candidates:
            key = _norm(c)
            if not key or " " not in key:
                continue
            tokens = key.split()
            if len(tokens) >= 2 and len(tokens[0]) == 1:
                lookup_key = f"{tokens[0]} {tokens[-1]}"
                hits = self.by_initial_last.get(lookup_key)
                if hits and len(hits) == 1:
                    return hits[0], "resolved"
                if hits and len(hits) > 1:
                    return None, "ambiguous"

        # Surname-only pass (e.g. "Hon Chan" → "chan")
        for c in candidates:
            key = _norm(c)
            if not key:
                continue
            tokens = key.split()
            if len(tokens) == 1:
                hits = self.by_surname.get(tokens[0])
                if hits and len(hits) == 1:
                    return hits[0], "resolved"
                if hits and len(hits) > 1:
                    return None, "ambiguous"
                continue
            # Try last-token-only as fallback
            hits = self.by_surname.get(tokens[-1])
            if hits and len(hits) == 1:
                return hits[0], "resolved"
            if hits and len(hits) > 1:
                return None, "ambiguous"

        return None, "unresolved"


async def load_bc_speaker_lookup(db: Database) -> SpeakerLookup:
    # Include ALL BC MLAs past and present — historical Hansard sessions
    # need to resolve retired names. A strict is_active=true filter would
    # miss (a) presiding officers LIMS marks inactive, and (b) MLAs who
    # served pre-2024. Ambiguous lookups (two MLAs with the same name or
    # initial+last) return no match and are handled by the post-pass.
    rows = await db.fetch(
        """
        SELECT id::text           AS id,
               name, first_name, last_name,
               lims_member_id
          FROM politicians
         WHERE level = 'provincial'
           AND province_territory = 'BC'
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
        first = _norm(r["first_name"] or "")
        if last:
            # Surname-only: each token of compound surname plus the whole
            # surname (handles "Calahoo Stonehouse" etc.).
            for tok in {last, last.split()[-1]}:
                lookup.by_surname.setdefault(tok, []).append(dict(r))
            # Initial+last: "p milobar" keyed to Peter Milobar. Uses the
            # first letter of first_name + surname-last-token. P42 and
            # earlier Hansard use this attribution style.
            if first:
                initial = first[0]
                lookup.by_initial_last.setdefault(
                    f"{initial} {last.split()[-1]}", []
                ).append(dict(r))

    # Dedupe within each bucket. Collapse by lims_member_id when present
    # — our politicians table occasionally has two rows for the same MLA
    # (e.g. accent vs. no-accent variants of the same name, both sharing
    # lims_member_id). Treating them as one resolves the name without
    # tripping the ambiguity branch.
    for idx in (lookup.by_full_name, lookup.by_initial_last, lookup.by_surname):
        for k, lst in idx.items():
            seen_ids: set[str] = set()
            seen_lims: set[int] = set()
            dedup: list[dict] = []
            for p in lst:
                lims_id = p.get("lims_member_id")
                if p["id"] in seen_ids:
                    continue
                if lims_id is not None and lims_id in seen_lims:
                    continue
                seen_ids.add(p["id"])
                if lims_id is not None:
                    seen_lims.add(lims_id)
                dedup.append(p)
            idx[k] = dedup

    log.info(
        "bc_hansard: loaded %d MLAs (unique_full=%d unique_initial_last=%d "
        "unique_surname=%d ambig_surname=%d)",
        len(rows),
        sum(1 for v in lookup.by_full_name.values() if len(v) == 1),
        sum(1 for v in lookup.by_initial_last.values() if len(v) == 1),
        sum(1 for v in lookup.by_surname.values() if len(v) == 1),
        sum(1 for v in lookup.by_surname.values() if len(v) > 1),
    )
    return lookup


# ── Upsert ──────────────────────────────────────────────────────────

@dataclass
class IngestStats:
    sittings_scanned: int = 0
    speeches_seen: int = 0
    speeches_inserted: int = 0
    speeches_updated: int = 0
    speeches_resolved: int = 0         # Full-name / surname match
    speeches_presiding: int = 0        # "The Speaker" → sitting speaker
    speeches_role_only: int = 0        # Deputy Speaker / Chair, no person
    speeches_ambiguous: int = 0
    speeches_unresolved: int = 0
    parse_errors: int = 0
    skipped_empty: int = 0


async def _upsert_speech(
    db: Database,
    *,
    session_id: str,
    ref: SittingRef,
    parsed: parse_mod.ParsedSpeech,
    politician: Optional[dict],
    confidence: float,
    page_html: str,
    real_url: str,
    sitting_speaker_name: Optional[str] = None,
) -> str:
    """Insert/update one speech. Returns 'inserted' | 'updated' | 'skipped'."""
    if not parsed.text.strip():
        return "skipped"

    politician_id = politician["id"] if politician else None

    raw_payload = {
        "bc_hansard": {
            "sitting_date": ref.sitting_date.isoformat(),
            "half": ref.half,
            "parliament": ref.parliament,
            "session": ref.session,
            "issue_number": ref.issue_number,
            "debate_type": ref.debate_type,
            "variant": parsed.raw.get("variant"),
            "section": parsed.raw.get("section"),
            "subject": parsed.raw.get("subject"),
            "blues_url": HDMS_FILE_URL.format(
                parl=_parl_slug(ref.parliament),
                sess=_sess_slug(ref.session),
                filename=ref.blues_filename,
            ),
            "final_url": (
                HDMS_FILE_URL.format(
                    parl=_parl_slug(ref.parliament),
                    sess=_sess_slug(ref.session),
                    filename=ref.final_filename,
                ) if ref.final_filename else None
            ),
            "fetched_url": real_url,
            "sitting_speaker": sitting_speaker_name,
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
            $1, $2, 'provincial', 'BC',
            $3, $4, NULL, NULL,
            $5, $6, $7, $8, $9,
            $10, $11,
            $12, $13, $14,
            $15::jsonb, $16, $17
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
            source_anchor = EXCLUDED.source_anchor,
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
        ref.canonical_url,
        parsed.source_anchor,
        raw_json,
        page_html,  # same HTML stored on every row for this sitting (see plan note)
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
    """Fetch + parse + upsert BC Hansard for one parliament+session.

    Args:
        parliament: BC Parliament number (e.g. 43).
        session: Session within the parliament (e.g. 2).
        since / until: optional inclusive sitting-date window.
        limit_sittings: cap on sittings processed.
        limit_speeches: cap on total speeches inserted/updated.
        one_off_url: if given, bypass discovery and process this single
            URL. Used by the smoke-test flag on the CLI.
    """
    stats = IngestStats()
    session_id = await ensure_session(db, parliament=parliament, session=session)
    lookup = await load_bc_speaker_lookup(db)

    async with httpx.AsyncClient(
        timeout=REQUEST_TIMEOUT, headers=HEADERS, follow_redirects=True,
    ) as client:
        if one_off_url:
            refs = [_synthesize_ref_from_url(one_off_url, parliament, session)]
        else:
            refs = await discover_sitting_refs(client, parliament=parliament, session=session)

            if since:
                refs = [r for r in refs if r.sitting_date >= since]
            if until:
                refs = [r for r in refs if r.sitting_date <= until]
            if limit_sittings:
                refs = refs[-limit_sittings:]

        log.info(
            "bc_hansard: processing %d sittings (parliament=%d session=%d)",
            len(refs), parliament, session,
        )

        for ref in refs:
            if limit_speeches and (stats.speeches_inserted + stats.speeches_updated) >= limit_speeches:
                break
            stats.sittings_scanned += 1
            url = ref.best_url
            log.info(
                "sitting %s %s → %s",
                ref.sitting_date, ref.half, url,
            )
            try:
                r = await _get_with_retry(client, url)
                r.raise_for_status()
                page_html = r.text
            except Exception as exc:
                log.warning("sitting %s: fetch failed: %s", url, exc)
                continue

            try:
                result = parse_mod.extract_speeches(page_html, url)
            except Exception as exc:
                log.warning("sitting %s: parse failed: %s", url, exc)
                stats.parse_errors += 1
                continue

            # Fallback: if the HTML didn't carry a sitting-Speaker element
            # (P42+ Final format strips it out), use the per-parliament
            # default from BC_PARLIAMENT_SPEAKER.
            if not result.sitting_speaker_name:
                result.sitting_speaker_name = BC_PARLIAMENT_SPEAKER.get(ref.parliament)

            if len(result.speeches) < 3:
                # Defensive: parsed almost nothing — likely markup drift
                # or a truly-empty file. Abort this sitting loudly.
                log.warning(
                    "sitting %s: only %d speeches parsed — skipping. sections=%s",
                    url, len(result.speeches), result.section_hits,
                )
                stats.parse_errors += 1
                continue
            if len(result.speeches) < 10:
                # Short sittings are legitimate for Throne Speech days,
                # prorogation, recess-return ceremonies, etc. Ingest but
                # flag for review.
                log.warning(
                    "sitting %s: short sitting (%d speeches). variant=%s sections=%s",
                    url, len(result.speeches), result.url_meta.variant, list(result.section_hits)[:5],
                )

            log.info(
                "  parsed %d speeches (variant=%s, sections=%d)",
                len(result.speeches), result.url_meta.variant, len(result.section_hits),
            )

            for ps in result.speeches:
                if limit_speeches and (stats.speeches_inserted + stats.speeches_updated) >= limit_speeches:
                    break
                stats.speeches_seen += 1

                politician, status = lookup.resolve(
                    ps.speaker_name_raw,
                    sitting_speaker_name=result.sitting_speaker_name,
                )
                if status == "resolved":
                    stats.speeches_resolved += 1
                    confidence = 1.0
                elif status == "presiding":
                    stats.speeches_presiding += 1
                    confidence = 0.9
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
                    parsed=ps,
                    politician=politician,
                    confidence=confidence,
                    page_html=page_html,
                    real_url=url,
                    sitting_speaker_name=result.sitting_speaker_name,
                )
                if outcome == "inserted":
                    stats.speeches_inserted += 1
                elif outcome == "updated":
                    stats.speeches_updated += 1
                elif outcome == "skipped":
                    stats.skipped_empty += 1

            await asyncio.sleep(REQUEST_DELAY_SECONDS)

    # Sync denormalised politician_id / confidence onto chunks. Chunks
    # created before this run were built from speeches.politician_id at
    # chunk time; if ingest updated the parent row (e.g. via Blues→Final
    # replacement or a widened resolver pass), chunks would otherwise
    # drift. One UPDATE keeps them consistent.
    await db.execute(
        """
        UPDATE speech_chunks sc
           SET politician_id = s.politician_id
          FROM speeches s
         WHERE sc.speech_id = s.id
           AND s.level = 'provincial'
           AND s.province_territory = 'BC'
           AND s.source_system = $1
           AND sc.politician_id IS DISTINCT FROM s.politician_id
        """,
        SOURCE_SYSTEM,
    )

    log.info(
        "bc_hansard done: %d sittings, %d speeches "
        "(inserted=%d updated=%d skipped_empty=%d parse_errors=%d) "
        "resolved=%d presiding=%d role=%d ambiguous=%d unresolved=%d",
        stats.sittings_scanned,
        stats.speeches_seen,
        stats.speeches_inserted,
        stats.speeches_updated,
        stats.skipped_empty,
        stats.parse_errors,
        stats.speeches_resolved,
        stats.speeches_presiding,
        stats.speeches_role_only,
        stats.speeches_ambiguous,
        stats.speeches_unresolved,
    )
    return stats


def _synthesize_ref_from_url(url: str, parliament: int, session: int) -> SittingRef:
    """Build a SittingRef from a bare URL for smoke-testing.

    Used by the --url CLI flag. The synthesized ref lacks final_filename
    when the URL is a Blues, but that's fine for one-off ingest.
    """
    meta = parse_mod.parse_url_meta(url)
    filename = url.rsplit("/", 1)[-1]
    return SittingRef(
        sitting_date=meta.sitting_date,
        half=meta.half,
        parliament=parliament,
        session=session,
        blues_filename=filename if meta.variant == "blues" else f"{meta.sitting_date:%Y%m%d}{meta.half}-House-Blues.htm",
        final_filename=filename if meta.variant == "final" else None,
        issue_number=meta.issue,
        debate_type="House",
        published=(meta.variant == "final"),
    )


# ── Post-pass resolver ──────────────────────────────────────────────

@dataclass
class ResolveStats:
    speeches_scanned: int = 0
    speeches_updated: int = 0
    still_unresolved: int = 0


async def resolve_bc_speakers(
    db: Database, *, limit: Optional[int] = None,
) -> ResolveStats:
    """Re-resolve politician_id on BC Hansard speeches with NULL politician_id.

    Run after adding more BC MLAs to the politicians table, or after
    fixing a name-normalisation bug that left speeches unresolved.
    """
    stats = ResolveStats()
    lookup = await load_bc_speaker_lookup(db)

    query = """
        SELECT s.id::text AS id,
               s.speaker_name_raw,
               s.confidence,
               s.speaker_role,
               COALESCE(
                   s.raw->'bc_hansard'->>'sitting_speaker',
                   -- Fallback for pre-fallback-code rows: use the session's
                   -- parliament number to look up BC_PARLIAMENT_SPEAKER
                   NULL
               ) AS sitting_speaker,
               ls.parliament_number AS parliament_number
          FROM speeches s
          JOIN legislative_sessions ls ON ls.id = s.session_id
         WHERE s.level = 'provincial'
           AND s.province_territory = 'BC'
           AND s.source_system = $1
           AND s.politician_id IS NULL
    """
    params: list = [SOURCE_SYSTEM]
    if limit:
        query += " LIMIT $2"
        params.append(limit)

    rows = await db.fetch(query, *params)
    for r in rows:
        stats.speeches_scanned += 1
        sitting_speaker = r["sitting_speaker"] or BC_PARLIAMENT_SPEAKER.get(
            r["parliament_number"]
        )
        politician, status = lookup.resolve(
            r["speaker_name_raw"],
            sitting_speaker_name=sitting_speaker,
        )
        if politician and status in ("resolved", "presiding"):
            new_conf = 1.0 if status == "resolved" else 0.9
            await db.execute(
                """
                UPDATE speeches
                   SET politician_id = $1,
                       confidence = $2,
                       updated_at = now()
                 WHERE id = $3::uuid
                """,
                politician["id"], new_conf, r["id"],
            )
            # Denormalised copy on speech_chunks stays in sync with the
            # parent speech. Chunks created pre-resolution have NULL here.
            await db.execute(
                """
                UPDATE speech_chunks
                   SET politician_id = $1
                 WHERE speech_id = $2::uuid
                   AND politician_id IS DISTINCT FROM $1
                """,
                politician["id"], r["id"],
            )
            stats.speeches_updated += 1
        else:
            stats.still_unresolved += 1

    log.info(
        "resolve_bc_speakers: scanned=%d updated=%d still_unresolved=%d",
        stats.speeches_scanned, stats.speeches_updated, stats.still_unresolved,
    )
    return stats
