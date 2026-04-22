r"""New Brunswick Hansard ingester — bilingual PDF → `speeches` table.

Unlike federal (openparliament JSON API) and close to Alberta's recipe
(PDF-only), NB Hansard is published as **bilingual PDFs**: English in the
left column, French translation in the right. The listing page at
``/en/house-business/hansard/{L}/{S}`` is plain HTML; each sitting has
one PDF.

## Upstream shape

* Listing URL: ``https://www.legnb.ca/en/house-business/hansard/{L}/{S}``
* PDF URL (listing href): ``/content/house_business\{L}\{S}\hansard\{seq} {YYYY-MM-DD}{b|bil}.pdf``
  - The **literal backslash** is a Windows-path artefact on the site's
    static-content layer; the host accepts ``%5C`` so we URL-encode when
    fetching.
  - The filename suffix is inconsistent — older sittings use ``b.pdf``,
    newer ones use ``bil.pdf``. We match both.
  - The date in the filename is authoritative; we don't try to parse the
    PDF's cover page.
* Digital coverage: 58/3 (2016-2017) onwards (~10 sessions). Earlier
  sessions return "There are no hansard transcripts for the selected
  legislative session."

## PDF → text

Poppler's ``pdftotext`` default (reading-order) mode is used. The PDF
is two-column (EN|FR) but pdftotext's reading-order heuristic produces
alternating English/French paragraphs at the text level, which is good
enough for downstream retrieval over a multilingual embedding model
(Qwen3-Embedding-0.6B).

## Speaker-line convention

NB Hansard's convention: the English speaker line appears FIRST for each
turn, immediately followed by the French equivalent ("L'hon. Mme Holt :"
style — note the Gallic space before the colon). To avoid emitting one
semantic speech as two rows, we match **English-only** speaker patterns;
French labels become ordinary body text. Line shapes observed across
2016–2026 sittings:

    "Hon. Susan Holt:"             → honorific + first + last name
    "Hon. Ms. Holt:"               → honorific + gender title + surname
    "Mr. Coon:"                    → honorific + surname
    "Ms Mitton:"                   → "Ms" (no period)
    "Mrs. Johnson:"                → "Mrs."
    "Member Arseneau:"             → rare; follows AB style
    "Mr. Speaker:"                 → role-only, resolved by presiding-officer
    "Madam Speaker:"               → role-only
    "The Speaker:"                 → rare alternative role form
    "Her Honour:"                  → Lt. Governor (ceremonial)

The comma-lookahead form sometimes appears on the first speaker line of
an adjourned-debate continuation:
    "Hon. Ms. Holt, resuming the adjourned debate on Motion 24:"

We handle this by allowing optional content between the name and the
terminal colon, bounded by the same line.

## Resolution strategy

NB published NO numeric MLA id — per convention #1, we fall back to
name-based matching against ``politicians`` (level='provincial',
province_territory='NB'). The ``Hon.`` honorific and gender-title
variants (``Hon. Ms.``) are stripped for matching. Single-token matches
first against last_name; if ambiguous, try first+last full name.

Role-only lines ("Mr. Speaker") get ``politician_id = NULL`` at ingest
and are backfilled by ``presiding_officer_resolver`` via the NB Speaker
roster + date ranges (SPEAKER_ROSTER["NB"]).

## Idempotency

``UNIQUE NULLS NOT DISTINCT (source_system, source_url, sequence)`` on
``speeches``. Re-parsing the same sitting updates mutable columns in
place.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime, time, timezone
from typing import Optional

import httpx
import orjson

from ..db import Database

log = logging.getLogger(__name__)

SOURCE_SYSTEM = "legnb-hansard"
BASE = "https://www.legnb.ca"
LISTING_URL = BASE + "/en/house-business/hansard/{legislature}/{session}"
REQUEST_TIMEOUT = 120
PDF_FETCH_DELAY_SECONDS = 1.5

HEADERS = {
    "User-Agent": "SovereignWatchBot/1.0 (+https://canadianpoliticaldata.ca; civic-transparency)",
    "Accept": "text/html,application/xhtml+xml,application/pdf",
}

# ── Retry shim ─────────────────────────────────────────────────────
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
                "nb_hansard retry %d/%d after %ds — last error: %s",
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


# ── Listing page ─────────────────────────────────────────────────────
# Sitting links look like:
#   /content/house_business\61\2\hansard\32 2026-03-27b.pdf
# or
#   /content/house_business\61\2\hansard\32 2026-03-27bil.pdf
# The space between sequence and date is a literal ASCII space in the
# href; HTML entity-decoding is not needed. The backslash is the
# Windows-style separator served verbatim by the site.
_LISTING_PDF_RE = re.compile(
    r'href="(?P<raw_href>/content/house_business\\(?P<leg>\d+)\\'
    r'(?P<sess>\d+)\\hansard\\'
    r'(?P<seq>\d+)\s+(?P<date>\d{4}-\d{2}-\d{2})(?P<suffix>b|bil)\.pdf)"',
    re.IGNORECASE,
)


def _encode_pdf_href(raw_href: str) -> str:
    """Convert a listing href's literal backslashes + space into the
    percent-encoded form the legnb.ca static layer accepts."""
    return BASE + raw_href.replace("\\", "%5C").replace(" ", "%20")


@dataclass
class SittingRef:
    raw_href: str
    url: str
    sitting_date: date
    legislature: int
    session: int
    sequence: int  # sitting number within session


async def fetch_transcript_index(
    client: httpx.AsyncClient, *, legislature: int, session: int
) -> list[SittingRef]:
    """Scrape the HTML listing for one (legislature, session) and return
    a list of SittingRef in whatever order the page uses. An empty list
    is the normal response for sessions with no digital Hansard (e.g.
    Leg 58/1 and earlier)."""
    url = LISTING_URL.format(legislature=legislature, session=session)
    r = await _get_with_retry(client, url)
    r.raise_for_status()

    seen: set[str] = set()
    out: list[SittingRef] = []
    for m in _LISTING_PDF_RE.finditer(r.text):
        raw_href = m.group("raw_href")
        if raw_href in seen:
            continue
        seen.add(raw_href)
        out.append(SittingRef(
            raw_href=raw_href,
            url=_encode_pdf_href(raw_href),
            sitting_date=datetime.strptime(m.group("date"), "%Y-%m-%d").date(),
            legislature=int(m.group("leg")),
            session=int(m.group("sess")),
            sequence=int(m.group("seq")),
        ))
    log.info(
        "nb_hansard listing: %d sittings for leg=%d session=%d",
        len(out), legislature, session,
    )
    return out


# ── PDF → text ───────────────────────────────────────────────────────

def _pdftotext(pdf_bytes: bytes) -> str:
    from .pdf_utils import pdftotext as _p
    return _p(pdf_bytes, layout=False)


async def fetch_pdf_and_extract(
    client: httpx.AsyncClient, url: str
) -> str:
    r = await _get_with_retry(client, url)
    r.raise_for_status()
    if not r.content or len(r.content) < 500:
        raise RuntimeError(
            f"pdf at {url} is empty or truncated ({len(r.content)} bytes)"
        )
    return _pdftotext(r.content)


# ── Text → speeches ─────────────────────────────────────────────────

# English role speakers. "Mr. Speaker" and "Madam Speaker" are the
# NB canonical forms; "The Speaker" and "The Deputy Speaker" also
# appear. "The Chair" / "The Deputy Chair" are Committee of the
# Whole markers. Her Honour is the Lt. Governor — we capture it as a
# role so surrounding ceremonial text is not orphaned.
_ROLE_SPEAKER_RE = re.compile(
    r"^(?P<role>"
    r"Mr\.\s+Speaker"
    r"|Madam\s+Speaker"
    r"|Madame\s+Speaker"
    r"|Mr\.\s+Chair(?:person|man)?"
    r"|Madam\s+Chair(?:person|woman)?"
    r"|Madame\s+Chair(?:person|woman)?"
    r"|The\s+Deputy\s+Speaker"
    r"|The\s+Speaker"
    r"|The\s+Deputy\s+Chair"
    r"|The\s+Chair(?:person|man)?"
    r"|Her\s+Honour"
    r"|His\s+Honour"
    r"|Hon\.\s+Members"
    r"|Some\s+Hon\.\s+Members"
    r"|Some\s+Members"
    r"):\s+(?P<body>.+)$",
    re.IGNORECASE | re.DOTALL,
)

# English person speaker. NB lines take several shapes:
#   "Hon. Susan Holt:"              — Premier / minister: full name
#   "Hon. Ms. Holt:"                — honorific + gender + surname
#   "Mr. Coon:"                     — backbencher surname only
#   "Ms Mitton:"                    — "Ms" with no period
#   "Mrs. Petrovic:"                — "Mrs."
#   "Member Arseneau:"              — rare, mirrors AB convention
# The name-portion may itself contain honorific inserts ("Hon. Mr.",
# "Hon. Ms.") so we allow up to 4 name-like tokens before the terminal
# colon. A small chunk of contextual text (e.g. "resuming the
# adjourned debate on Motion 24") may sit between name and colon,
# bounded to 80 chars so we don't swallow paragraphs that happen to
# end in a colon.
_NAME_TOKEN = r"[\wÀ-ſ'’\-\.]+"
_PERSON_SPEAKER_RE = re.compile(
    r"^(?P<honorific>Hon\.|Mr\.|Mrs\.|Ms|Dr\.|Member)\s+"
    r"(?P<rest>" + _NAME_TOKEN + r"(?:\s+" + _NAME_TOKEN + r"){0,4})"
    r"(?P<context>,\s[^:]{0,200})?:\s+(?P<body>.+)$",
    re.IGNORECASE | re.DOTALL,
)

# French speaker labels — used to suppress duplicate speech emission.
# We do NOT create a new speech from these; instead we treat them as
# body text of the English speech that precedes them (NB's convention
# is English first).
_FR_SPEAKER_RE = re.compile(
    r"^(?:L[’']hon\.|M\.|Mme|Le\s+président|La\s+présidente"
    r"|Son\s+Honneur|Le\s+Vice-président|La\s+Vice-présidente)"
    r"[\s ].+?\s:\s",
    re.IGNORECASE,
)

_PAGE_NUMBER_RE = re.compile(r"^\d{1,5}$")
_DATE_FOOTER_RE = re.compile(
    r"^[A-Z][a-z]+\s+\d{1,2}(?:,\s+\d{4}|\s+[a-zéû]+)?$"
)
_TIMESTAMP_RE = re.compile(r"^\d{1,2}:\d{2}$")
_BRACKETED_RE = re.compile(r"^[\(\[][^\]\)]+[\)\]]$")

_RUNNING_TITLES = (
    "legislative assembly",
    "province of new brunswick",
    "assemblée législative",
    "province du nouveau-brunswick",
    "journal of debates",
    "journal des débats",
    "(hansard)",
    "contents",
    "table des matières",
    "list of members by constituency",
    "liste des parlementaires par circonscription",
    "cabinet ministers / le cabinet",
)


def _is_noise_line(stripped: str) -> bool:
    lower = stripped.lower()
    if _PAGE_NUMBER_RE.match(stripped):
        return True
    if _TIMESTAMP_RE.match(stripped):
        return True
    if _BRACKETED_RE.match(stripped) and len(stripped) < 120:
        # Short bracketed asides are procedural inserts.
        return True
    if _DATE_FOOTER_RE.match(stripped) and len(stripped) < 32:
        return True
    for t in _RUNNING_TITLES:
        if t in lower and len(stripped) < 80:
            return True
    return False


@dataclass
class ParsedSpeech:
    sequence: int
    speaker_name_raw: str
    speaker_role: Optional[str]
    honorific: Optional[str]
    surname: Optional[str]
    full_name: Optional[str]      # when the PDF carries first+last
    body: str


_WS_RE = re.compile(r"\s+")


def _normalize_ws(s: str) -> str:
    return _WS_RE.sub(" ", s.strip())


def _split_name_tokens(rest: str) -> tuple[Optional[str], Optional[str]]:
    """Given the rest of a person speaker line (after honorific), split
    into (full_name, surname). NB names may include gender titles
    (``Ms.``, ``Mr.``) as the first token — strip those so the surname
    comes through cleanly.

    Examples:
      "Susan Holt"             → ("Susan Holt", "Holt")
      "Ms. Holt"               → (None, "Holt")
      "Coon"                   → (None, "Coon")
      "Jean-Claude D'Amours"   → ("Jean-Claude D'Amours", "D'Amours")
      "Mary E. Wilson"         → ("Mary E. Wilson", "Wilson")
      "Rob McKee, K.C."        → ("Rob McKee", "McKee")      # trim post-nominal
    """
    rest = rest.strip()
    if not rest:
        return None, None
    # Trim trailing post-nominals (K.C., Q.C., P.C., c.r., etc.)
    rest = re.sub(
        r",?\s*(?:K\.?C\.?|Q\.?C\.?|P\.?C\.?|c\.?r\.?|C\.?P\.?)\.?$",
        "",
        rest,
        flags=re.IGNORECASE,
    ).strip()

    tokens = rest.split()
    # Strip gender honorific if it's the first token
    if tokens and tokens[0].lower().rstrip(".") in ("ms", "mr", "mrs", "mme"):
        tokens = tokens[1:]
    if not tokens:
        return None, None
    if len(tokens) == 1:
        return None, tokens[0]
    return " ".join(tokens), tokens[-1]


def _paragraphs(raw: str) -> list[str]:
    """Split pdftotext output into logical paragraphs, joining each
    paragraph's physical lines with spaces. Blank-line separated.
    Noise lines (page numbers, timestamps, running titles, bracketed
    asides) are skipped at the line level before paragraph assembly.
    """
    paras: list[list[str]] = [[]]
    for raw_line in raw.splitlines():
        stripped = raw_line.strip()
        if not stripped:
            if paras[-1]:
                paras.append([])
            continue
        if _is_noise_line(stripped):
            continue
        paras[-1].append(stripped)
    out: list[str] = []
    for p in paras:
        if not p:
            continue
        joined = _SOFT_HYPHEN_RE.sub("", " ".join(p))
        joined = _normalize_ws(joined)
        if joined:
            out.append(joined)
    return out


def extract_speeches_from_text(raw: str) -> list[ParsedSpeech]:
    """Paragraph-level parse of bilingual NB Hansard.

    NB's long speaker attributions ("Hon. Ms. Holt, resuming the
    adjourned debate on Motion 24:") wrap across multiple physical
    lines, so line-by-line matching misses them. We operate on
    paragraphs (blank-line separated blocks, joined into one logical
    line each), which collapses the wrap.

    Only English speaker lines trigger new speeches. Paragraphs
    starting with a French speaker label ("L'hon.", "Mme", "Le
    président", "Des voix", etc.) are translation/response text of the
    preceding English turn and become body lines. Any paragraph that
    isn't a speaker line appends to the current body.
    """
    paras = _paragraphs(raw)
    out: list[ParsedSpeech] = []
    cur: Optional[ParsedSpeech] = None
    cur_body: list[str] = []
    seen_first_speaker = False

    def _finalize():
        nonlocal cur, cur_body
        if cur is not None and cur_body:
            cur.body = "\n\n".join(cur_body).strip()
            if cur.body:
                out.append(cur)
        cur = None
        cur_body = []

    for para in paras:
        m_role = _ROLE_SPEAKER_RE.match(para)
        m_person = _PERSON_SPEAKER_RE.match(para) if not m_role else None

        if m_role:
            _finalize()
            role = _normalize_ws(m_role.group("role"))
            body_start = _normalize_ws(m_role.group("body") or "")
            cur = ParsedSpeech(
                sequence=len(out) + 1,
                speaker_name_raw=role,
                speaker_role=role,
                honorific=None,
                surname=None,
                full_name=None,
                body="",
            )
            if body_start:
                cur_body.append(body_start)
            seen_first_speaker = True
            continue

        if m_person:
            _finalize()
            honorific = _normalize_ws(m_person.group("honorific"))
            rest = _normalize_ws(m_person.group("rest"))
            full_name, surname = _split_name_tokens(rest)
            speaker_name_raw = f"{honorific} {rest}".strip()
            body_start = _normalize_ws(m_person.group("body") or "")
            cur = ParsedSpeech(
                sequence=len(out) + 1,
                speaker_name_raw=speaker_name_raw,
                speaker_role=None,
                honorific=honorific,
                surname=surname,
                full_name=full_name,
                body="",
            )
            if body_start:
                cur_body.append(body_start)
            seen_first_speaker = True
            continue

        # French speaker lines are NOT new speeches — they're the
        # translation of the preceding English turn. Append to body.
        if _FR_SPEAKER_RE.match(para):
            if cur is not None:
                cur_body.append(para)
            continue

        # Continuation paragraph — append to current body. Drop pre-
        # first-speaker front-matter silently.
        if cur is not None:
            cur_body.append(para)
        elif not seen_first_speaker:
            continue

    _finalize()
    return out


_SOFT_HYPHEN_RE = re.compile(r"-\n(?=[a-zéèêâûôîïç])")


def _join_body(lines: list[str]) -> str:
    if not lines:
        return ""
    paragraphs: list[list[str]] = [[]]
    for line in lines:
        if line == "":
            if paragraphs[-1]:
                paragraphs.append([])
        else:
            paragraphs[-1].append(line)
    rendered: list[str] = []
    for para in paragraphs:
        if not para:
            continue
        joined = " ".join(para)
        joined = _SOFT_HYPHEN_RE.sub("", joined)
        joined = _normalize_ws(joined)
        if joined:
            rendered.append(joined)
    return "\n\n".join(rendered)


# ── Name → politician ───────────────────────────────────────────────


def _norm(s: str) -> str:
    if not s:
        return ""
    t = unicodedata.normalize("NFKD", s)
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = t.lower()
    t = re.sub(r"[^\w\s\-]", " ", t)
    return _WS_RE.sub(" ", t).strip()


@dataclass
class SpeakerLookup:
    by_full_name: dict[str, list[dict]] = field(default_factory=dict)
    by_first_last: dict[str, list[dict]] = field(default_factory=dict)
    by_surname: dict[str, list[dict]] = field(default_factory=dict)

    def resolve(
        self, *, full_name: Optional[str], surname: Optional[str],
    ) -> tuple[Optional[dict], str]:
        """Returns (politician_row_or_None, status).

        Status is 'resolved' | 'ambiguous' | 'unresolved'.
        """
        if full_name:
            key = _norm(full_name)
            cands = self.by_full_name.get(key)
            if cands and len(cands) == 1:
                return cands[0], "resolved"
            if cands and len(cands) > 1:
                return None, "ambiguous"
            if " " in key:
                tokens = key.split()
                fl = f"{tokens[0]} {tokens[-1]}"
                cands = self.by_first_last.get(fl)
                if cands and len(cands) == 1:
                    return cands[0], "resolved"
                if cands and len(cands) > 1:
                    return None, "ambiguous"

        if surname:
            key = _norm(surname)
            cands = self.by_surname.get(key)
            if cands and len(cands) == 1:
                return cands[0], "resolved"
            if cands and len(cands) > 1:
                return None, "ambiguous"

        return None, "unresolved"


async def load_speaker_lookup(db: Database) -> SpeakerLookup:
    """Load NB MLAs indexed by full name, first+last, and surname.
    Includes inactive politicians since historical Hansard needs former
    MLAs' names to resolve."""
    rows = await db.fetch(
        """
        SELECT id::text AS id, name, first_name, last_name
          FROM politicians
         WHERE level = 'provincial'
           AND province_territory = 'NB'
        """
    )
    lookup = SpeakerLookup()
    for r in rows:
        full_name = _norm(r["name"] or "")
        if full_name:
            lookup.by_full_name.setdefault(full_name, []).append(dict(r))
        first = _norm(r["first_name"] or "")
        last = _norm(r["last_name"] or "")
        if first and last:
            first_tok = first.split()[0]
            last_tok = last.split()[-1]
            lookup.by_first_last.setdefault(
                f"{first_tok} {last_tok}", []
            ).append(dict(r))
        if last:
            last_tok = last.split()[-1]
            lookup.by_surname.setdefault(last_tok, []).append(dict(r))
            if " " in last:
                lookup.by_surname.setdefault(last, []).append(dict(r))

    for idx in (lookup.by_full_name, lookup.by_first_last, lookup.by_surname):
        for k, lst in idx.items():
            seen = set()
            deduped = []
            for p in lst:
                if p["id"] in seen:
                    continue
                seen.add(p["id"])
                deduped.append(p)
            idx[k] = deduped

    log.info(
        "nb_hansard: loaded %d NB politicians "
        "(unique_full=%d unique_first_last=%d unique_surname=%d "
        "ambig_surname=%d)",
        len(rows),
        sum(1 for v in lookup.by_full_name.values() if len(v) == 1),
        sum(1 for v in lookup.by_first_last.values() if len(v) == 1),
        sum(1 for v in lookup.by_surname.values() if len(v) == 1),
        sum(1 for v in lookup.by_surname.values() if len(v) > 1),
    )
    return lookup


# ── Sessions ────────────────────────────────────────────────────────

async def ensure_session(
    db: Database, *, legislature: int, session: int,
) -> str:
    row = await db.fetchrow(
        """
        INSERT INTO legislative_sessions (
            level, province_territory, parliament_number, session_number,
            name, source_system, source_url
        )
        VALUES ('provincial', 'NB', $1, $2, $3, $4, $5)
        ON CONFLICT (level, province_territory, parliament_number, session_number)
        DO UPDATE SET
            name          = COALESCE(legislative_sessions.name, EXCLUDED.name),
            source_system = COALESCE(legislative_sessions.source_system, EXCLUDED.source_system),
            source_url    = COALESCE(legislative_sessions.source_url, EXCLUDED.source_url),
            updated_at    = now()
        RETURNING id
        """,
        legislature, session,
        f"{legislature}th Legislature, Session {session}",
        SOURCE_SYSTEM,
        LISTING_URL.format(legislature=legislature, session=session),
    )
    return str(row["id"])


# ── Upsert ──────────────────────────────────────────────────────────

def _content_hash(text: str) -> str:
    t = unicodedata.normalize("NFKC", text)
    t = _WS_RE.sub(" ", t).strip().lower()
    return hashlib.sha256(t.encode("utf-8")).hexdigest()


@dataclass
class IngestStats:
    sittings_scanned: int = 0
    sittings_skipped_empty: int = 0
    speeches_seen: int = 0
    speeches_inserted: int = 0
    speeches_updated: int = 0
    speeches_resolved: int = 0
    speeches_role_only: int = 0
    speeches_ambiguous: int = 0
    speeches_unresolved: int = 0
    skipped_empty: int = 0


async def _upsert_speech(
    db: Database,
    *,
    session_id: str,
    sitting: SittingRef,
    parsed: ParsedSpeech,
    politician: Optional[dict],
) -> str:
    if not parsed.body.strip():
        return "skipped"

    spoken_at = datetime.combine(
        sitting.sitting_date, time(9, 0)
    ).replace(tzinfo=timezone.utc)

    politician_id = politician["id"] if politician else None

    raw_payload = {
        "nb_hansard": {
            "sitting_date": sitting.sitting_date.isoformat(),
            "sitting_sequence": sitting.sequence,
            "legislature": sitting.legislature,
            "session": sitting.session,
            "honorific": parsed.honorific,
            "surname": parsed.surname,
            "full_name": parsed.full_name,
            "raw_href": sitting.raw_href,
        }
    }
    raw_json = orjson.dumps(raw_payload).decode("utf-8")
    ch = _content_hash(parsed.body)

    # Language: primarily English (speaker lines are English). Body
    # includes interleaved FR translations from the bilingual PDF, but
    # the language tag follows the primary speaker attribution.
    result = await db.fetchrow(
        """
        INSERT INTO speeches (
            session_id, politician_id, level, province_territory,
            speaker_name_raw, speaker_role, party_at_time, constituency_at_time,
            confidence, speech_type, spoken_at, sequence, language,
            text, word_count,
            source_system, source_url, source_anchor,
            raw, content_hash
        ) VALUES (
            $1, $2, 'provincial', 'NB',
            $3, $4, NULL, NULL,
            $5, 'floor', $6, $7, 'en',
            $8, $9,
            $10, $11, $12,
            $13::jsonb, $14
        )
        ON CONFLICT (source_system, source_url, sequence)
        DO UPDATE SET
            politician_id = EXCLUDED.politician_id,
            speaker_name_raw = EXCLUDED.speaker_name_raw,
            speaker_role = EXCLUDED.speaker_role,
            text = EXCLUDED.text,
            word_count = EXCLUDED.word_count,
            raw = EXCLUDED.raw,
            content_hash = EXCLUDED.content_hash,
            updated_at = now()
        RETURNING (xmax = 0) AS inserted
        """,
        session_id,
        politician_id,
        parsed.speaker_name_raw,
        parsed.speaker_role,
        1.0 if politician_id else (0.5 if parsed.speaker_role else 0.3),
        spoken_at,
        parsed.sequence,
        parsed.body,
        len(parsed.body.split()),
        SOURCE_SYSTEM,
        sitting.url,
        f"sitting={sitting.sitting_date.isoformat()};seq={parsed.sequence}",
        raw_json,
        ch,
    )
    return "inserted" if result and result["inserted"] else "updated"


# ── Orchestrator ────────────────────────────────────────────────────

async def ingest(
    db: Database,
    *,
    legislature: int,
    session: int,
    since: Optional[date] = None,
    until: Optional[date] = None,
    limit_sittings: Optional[int] = None,
    limit_speeches: Optional[int] = None,
) -> IngestStats:
    stats = IngestStats()
    session_id = await ensure_session(db, legislature=legislature, session=session)
    lookup = await load_speaker_lookup(db)

    async with httpx.AsyncClient(
        timeout=REQUEST_TIMEOUT, headers=HEADERS, follow_redirects=True
    ) as client:
        sittings = await fetch_transcript_index(
            client, legislature=legislature, session=session,
        )

        if since:
            sittings = [s for s in sittings if s.sitting_date >= since]
        if until:
            sittings = [s for s in sittings if s.sitting_date <= until]

        # Oldest-first to make incremental resumption intuitive.
        sittings.sort(key=lambda s: (s.sitting_date, s.sequence))

        if limit_sittings:
            sittings = sittings[-limit_sittings:]

        log.info(
            "nb_hansard: processing %d sittings (leg=%d session=%d)",
            len(sittings), legislature, session,
        )

        for sitting in sittings:
            if (limit_speeches
                    and stats.speeches_inserted + stats.speeches_updated >= limit_speeches):
                break
            stats.sittings_scanned += 1
            log.info(
                "sitting %s seq=%d → %s",
                sitting.sitting_date, sitting.sequence, sitting.url,
            )
            try:
                text = await fetch_pdf_and_extract(client, sitting.url)
            except Exception as exc:
                log.warning("sitting %s: fetch/parse failed: %s", sitting.url, exc)
                continue

            parsed_list = extract_speeches_from_text(text)
            log.info("  parsed %d speeches", len(parsed_list))
            if not parsed_list:
                stats.sittings_skipped_empty += 1

            for ps in parsed_list:
                if (limit_speeches
                        and stats.speeches_inserted + stats.speeches_updated >= limit_speeches):
                    break
                stats.speeches_seen += 1

                politician: Optional[dict] = None
                if ps.speaker_role is not None:
                    stats.speeches_role_only += 1
                else:
                    politician, status = lookup.resolve(
                        full_name=ps.full_name, surname=ps.surname,
                    )
                    if status == "resolved":
                        stats.speeches_resolved += 1
                    elif status == "ambiguous":
                        stats.speeches_ambiguous += 1
                    else:
                        stats.speeches_unresolved += 1

                outcome = await _upsert_speech(
                    db,
                    session_id=session_id,
                    sitting=sitting,
                    parsed=ps,
                    politician=politician,
                )
                if outcome == "inserted":
                    stats.speeches_inserted += 1
                elif outcome == "updated":
                    stats.speeches_updated += 1
                elif outcome == "skipped":
                    stats.skipped_empty += 1

            await asyncio.sleep(PDF_FETCH_DELAY_SECONDS)

    log.info(
        "nb_hansard done: %d sittings (%d empty), %d speeches "
        "(inserted=%d updated=%d skipped_empty=%d) "
        "resolved=%d role_only=%d ambiguous=%d unresolved=%d",
        stats.sittings_scanned,
        stats.sittings_skipped_empty,
        stats.speeches_seen,
        stats.speeches_inserted,
        stats.speeches_updated,
        stats.skipped_empty,
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


async def resolve_nb_speakers(
    db: Database, *, limit: Optional[int] = None,
) -> ResolveStats:
    """Re-resolve politician_id on NB Hansard speeches with NULL
    politician_id. Run after expanding the NB MLA roster (historical
    backfill) or after fixing name normalisation."""
    stats = ResolveStats()
    lookup = await load_speaker_lookup(db)

    query = """
        SELECT s.id::text AS id,
               s.speaker_name_raw,
               s.speaker_role,
               s.raw->'nb_hansard'->>'surname'   AS surname,
               s.raw->'nb_hansard'->>'full_name' AS full_name
          FROM speeches s
         WHERE s.level = 'provincial'
           AND s.province_territory = 'NB'
           AND s.source_system = $1
           AND s.politician_id IS NULL
           AND (s.speaker_role IS NULL OR s.speaker_role = '')
    """
    if limit:
        query += f" LIMIT {int(limit)}"

    rows = await db.fetch(query, SOURCE_SYSTEM)
    for r in rows:
        stats.speeches_scanned += 1
        politician, _ = lookup.resolve(
            full_name=r["full_name"], surname=r["surname"],
        )
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
        "resolve_nb_speakers: scanned=%d updated=%d still_unresolved=%d",
        stats.speeches_scanned, stats.speeches_updated, stats.still_unresolved,
    )
    return stats


async def ingest_all_sessions_in_legislature(
    db: Database,
    *,
    legislature: int,
    since: Optional[date] = None,
    until: Optional[date] = None,
    limit_sittings: Optional[int] = None,
    limit_speeches: Optional[int] = None,
) -> IngestStats:
    """Iterate S=1..6 within a legislature; run ingest() for each
    non-empty session. Returns aggregated stats."""
    agg = IngestStats()
    for S in range(1, 7):
        async with httpx.AsyncClient(
            timeout=REQUEST_TIMEOUT, headers=HEADERS, follow_redirects=True
        ) as client:
            sittings = await fetch_transcript_index(
                client, legislature=legislature, session=S,
            )
        if not sittings:
            continue
        s = await ingest(
            db,
            legislature=legislature, session=S,
            since=since, until=until,
            limit_sittings=limit_sittings, limit_speeches=limit_speeches,
        )
        for f in (
            "sittings_scanned", "sittings_skipped_empty",
            "speeches_seen", "speeches_inserted", "speeches_updated",
            "speeches_resolved", "speeches_role_only",
            "speeches_ambiguous", "speeches_unresolved", "skipped_empty",
        ):
            setattr(agg, f, getattr(agg, f) + getattr(s, f))
    return agg
