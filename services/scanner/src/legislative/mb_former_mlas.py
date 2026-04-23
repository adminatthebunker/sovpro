"""MB historical MLA roster backfill.

The MB Legislature publishes every MLA who has ever served on two
Word-exported static HTML pages:

    https://www.gov.mb.ca/legislature/members/mla_bio_living.html
    https://www.gov.mb.ca/legislature/members/mla_bio_deceased.html

Each MLA is represented by:

  * one ``<strong>LASTNAME, Firstname</strong>`` tag introducing the
    entry (sometimes nested inside ``<p>``), then
  * one or more ``<strong>Month DD, YYYY - Month DD, YYYY</strong>``
    term-range tags that follow consecutively (the end date can be
    the word ``present`` for sitting members on the living page).

There is no per-MLA numeric ID and no slug in a URL we can scrape;
the keying column is the ``mb_assembly_slug`` (``lastname-firstname``
lowercased, accent-stripped) that the current-roster ingester
``mb_mlas`` also uses. Collisions on the slug are rare over 150
years of Manitoba politics but do happen (``smith-john``,
``campbell-colin``) — when they do, we disambiguate against the
existing slug's date range and suffix a new entry with ``-2``, ``-3``,
etc. Every collision is logged.

Idempotency: ``politicians`` upserts on ``mb_assembly_slug``;
``politician_terms`` upserts on ``(politician_id, office,
started_at)``. A full re-run is a no-op.

Why this matters: the current-roster ingester only knows 56 sitting
MLAs. Once MB Hansard is backfilled past session 43-3 (2023+) into
earlier legislatures, name-based speaker resolution collapses
without historical MLAs in ``politicians``. This ingester is the
prerequisite.
"""
from __future__ import annotations

import asyncio
import html as html_lib
import logging
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from typing import Iterator, Optional

import httpx

from ..db import Database

log = logging.getLogger(__name__)

URL_LIVING = "https://www.gov.mb.ca/legislature/members/mla_bio_living.html"
URL_DECEASED = "https://www.gov.mb.ca/legislature/members/mla_bio_deceased.html"

HEADERS = {
    "User-Agent": "CanadianPoliticalDataBot/1.0 (+https://canadianpoliticaldata.ca)",
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "en-CA,en;q=0.9",
}
REQUEST_TIMEOUT = 60
REQUEST_DELAY_SECONDS = 1.0


# ── Regexes ─────────────────────────────────────────────────────────

_STRONG_RE = re.compile(r"<strong[^>]*>(.*?)</strong>", re.IGNORECASE | re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")
_TR_RE = re.compile(r"<tr\b[^>]*>(.*?)</tr>", re.IGNORECASE | re.DOTALL)

# LASTNAME, First ...  where first may carry nickname parens, initials,
# and a trailing honorific suffix after another comma (Q.C., D.C., etc.).
_NAME_RE = re.compile(
    r"""^
    (?P<last>[A-ZÀ-Þ][A-ZÀ-Þ'\-.\s]*?)   # SURNAME (uppercase, spaces/hyphens/periods OK)
    ,\s+
    (?P<first>[A-Za-zÀ-ÿ0-9'\-.\s]+?(?:\([^)]+\))?[A-Za-zÀ-ÿ0-9'\-.\s]*)  # First (middle) (nick)
    (?:,\s*[A-Za-z.]+\.?)?                                    # optional suffix: ", Q.C." / ", M.D."
    \s*$""",
    re.VERBOSE,
)

_MONTH = (
    r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|"
    r"Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sept?(?:ember)?|"
    r"Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\.?"
)
# Date forms we accept:
#   "October 18, 2023"    (day + year)
#   "March 1900"          (month + year, no day — pre-1900 rows)
#   "Nov. 19, 1908"       (abbreviated month + period)
_DATE_FULL_RE = rf"{_MONTH}\s+\d{{1,2}}[,\xa0\s]+\d{{4}}"
_DATE_YEAR_RE = rf"{_MONTH}\s+\d{{4}}"
_DATE_TOKEN = rf"(?:{_DATE_FULL_RE}|{_DATE_YEAR_RE})"

# "(resigned|died|deceased|appointed) " prefix on end dates — strip, keep the date.
_TERM_RE = re.compile(
    rf"""^\s*
    (?P<start>{_DATE_TOKEN})
    \s*[-‐-―]\s*
    (?:(?:resigned|died|deceased|appointed|defeated|retired)\s+)?
    (?P<end>{_DATE_TOKEN}|[Pp]resent)
    \s*$""",
    re.VERBOSE,
)

# Narrative-event markers used on the living page (and occasionally
# the deceased page) for MLAs whose terms aren't expressed as
# <strong>DATE-DATE</strong> blocks. Examples:
#   "Elected g.e. October 3, 2023"
#   "Re-elected g.e. April 11, 2023"
#   "Appointed August 4, 2023"
#   "Not a candidate g.e. October 3, 2023"
#   "Resigned March 24, 2025 to run in Federal Election"
#   "Died January 15, 2025"
#   "Defeated g.e. April 11, 2023"
#   "Retired April 11, 2023"
_EVENT_START_RE = re.compile(
    rf"(?:(?:Re[-\s]?)?[Ee]lected|[Aa]ppointed|[Ss]worn\s+in)\s+(?:g\.?\s*e\.?\s+)?"
    rf"(?P<date>{_DATE_TOKEN})",
)
_EVENT_END_RE = re.compile(
    rf"(?:[Nn]ot\s+a\s+candidate|[Rr]esigned|[Rr]etired|[Dd]efeated|[Dd]ied|"
    rf"[Dd]eceased|[Pp]assed\s+away)\s+(?:g\.?\s*e\.?\s+)?(?:on\s+)?"
    rf"(?P<date>{_DATE_TOKEN})",
)

_MONTH_TO_NUM = {
    m.lower(): i for i, m in enumerate(
        ["january","february","march","april","may","june",
         "july","august","september","october","november","december"], start=1
    )
}
_MONTH_ABBREV = {
    "jan":1,"feb":2,"mar":3,"apr":4,"may":5,"jun":6,"jul":7,
    "aug":8,"sept":9,"sep":9,"oct":10,"nov":11,"dec":12,
}


# ── Data shapes ─────────────────────────────────────────────────────


@dataclass
class Term:
    started_at: date
    ended_at: Optional[date]  # None = "present"


@dataclass
class ParsedMla:
    last_name: str
    first_name: str
    terms: list[Term] = field(default_factory=list)
    source_page: str = ""  # "living" | "deceased"

    @property
    def mb_assembly_slug(self) -> str:
        return _make_slug(self.last_name, self.first_name)


@dataclass
class Stats:
    pages_fetched: int = 0
    names_seen: int = 0
    terms_parsed: int = 0
    terms_skipped_malformed: int = 0
    politicians_inserted: int = 0
    politicians_updated: int = 0
    slug_collisions: int = 0
    terms_inserted: int = 0
    terms_skipped_existing: int = 0


# ── Helpers ─────────────────────────────────────────────────────────


def _decode_and_strip(s: str) -> str:
    """Strip inner HTML tags, decode entities, collapse whitespace."""
    s = _TAG_RE.sub(" ", s)
    s = html_lib.unescape(s)
    s = s.replace("\xa0", " ")
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def _strip_accents(s: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", s)
        if unicodedata.category(c) != "Mn"
    )


def _make_slug(last: str, first: str) -> str:
    """MB convention: ``lastname-firstname`` lowercased, accent-stripped,
    compound-surname joined (``van der berg`` → ``vanderberg``), nickname
    parens dropped.
    """
    # Drop nickname parens from the first name: "Joseph (Bud)" → "Joseph"
    first_clean = re.sub(r"\([^)]+\)", " ", first)
    # Drop any stray periods/commas
    first_clean = re.sub(r"[.,]", " ", first_clean)
    # Take only the first token of the first name (matches current-roster
    # convention — compound first names converge on the common form).
    first_token = first_clean.strip().split()[0] if first_clean.strip() else ""
    # Surname: keep order but remove spaces + punctuation.
    last_clean = re.sub(r"[.,]", " ", last).strip()
    last_joined = re.sub(r"\s+", "", last_clean)  # "Van Der Berg" → "VanDerBerg"
    return _strip_accents(f"{last_joined}-{first_token}".lower()).replace("'", "")


def _parse_date(token: str) -> Optional[date]:
    """Parse 'October 18, 2023', 'Oct. 18, 2023', 'October 2023' → date.

    Missing-day falls back to the 1st of the month. Malformed input
    returns None.
    """
    token = token.strip().replace("\xa0", " ")
    token = re.sub(r"\s+", " ", token)
    # Try full form first.
    m = re.match(
        rf"^(?P<month>\w+)\.?\s+(?P<day>\d{{1,2}})(?:,)?\s+(?P<year>\d{{4}})$",
        token,
    )
    day = 1
    year = None
    month_name = None
    if m:
        month_name = m.group("month").lower().rstrip(".")
        day = int(m.group("day"))
        year = int(m.group("year"))
    else:
        m = re.match(rf"^(?P<month>\w+)\.?\s+(?P<year>\d{{4}})$", token)
        if m:
            month_name = m.group("month").lower().rstrip(".")
            year = int(m.group("year"))
    if not month_name or year is None:
        return None
    month = _MONTH_TO_NUM.get(month_name) or _MONTH_ABBREV.get(month_name[:4]) or _MONTH_ABBREV.get(month_name[:3])
    if not month:
        return None
    try:
        return date(year, month, day)
    except ValueError:
        # Bad day for month — fall back to the 1st.
        try:
            return date(year, month, 1)
        except ValueError:
            return None


# ── Parser ──────────────────────────────────────────────────────────


def _extract_strong_terms(row_html: str) -> list[Term]:
    """Pick up all ``<strong>Month DD, YYYY - Month DD, YYYY</strong>`` blocks
    within one table row (deceased-style format).
    """
    terms: list[Term] = []
    for raw in _STRONG_RE.findall(row_html):
        text = _decode_and_strip(raw)
        if not text:
            continue
        m = _TERM_RE.match(text)
        if not m:
            continue
        start_dt = _parse_date(m.group("start"))
        if start_dt is None:
            continue
        end_raw = m.group("end")
        end_dt: Optional[date] = None
        if end_raw.lower() != "present":
            end_dt = _parse_date(end_raw)
            if end_dt is None:
                continue
        terms.append(Term(started_at=start_dt, ended_at=end_dt))
    return terms


def _extract_narrative_terms(row_text: str) -> list[Term]:
    """Pair Elected/Re-elected/Appointed with Not-a-candidate/Resigned/Died
    within one MLA's row (living-style format).

    Strategy: collect (position, kind, date) triples, sort by position,
    walk left-to-right pairing each 'start' event with the next 'end'
    event. A trailing 'start' with no matching 'end' becomes an
    open-ended term (current MLA).
    """
    events: list[tuple[int, str, date]] = []
    for m in _EVENT_START_RE.finditer(row_text):
        d = _parse_date(m.group("date"))
        if d:
            events.append((m.start(), "start", d))
    for m in _EVENT_END_RE.finditer(row_text):
        d = _parse_date(m.group("date"))
        if d:
            events.append((m.start(), "end", d))
    events.sort(key=lambda t: t[0])

    terms: list[Term] = []
    open_start: Optional[date] = None
    for _pos, kind, d in events:
        if kind == "start":
            if open_start is not None:
                # Two starts in a row (e.g. Elected + Re-elected with no
                # interleaved end). Treat the intervening election as
                # the end of the previous term / start of the new one.
                terms.append(Term(started_at=open_start, ended_at=d))
            open_start = d
        else:  # "end"
            if open_start is None:
                continue  # dangling end event; ignore
            terms.append(Term(started_at=open_start, ended_at=d))
            open_start = None
    if open_start is not None:
        # Still-seated MLA — open-ended term.
        terms.append(Term(started_at=open_start, ended_at=None))
    return terms


def _find_name_in_row(row_html: str) -> Optional[tuple[str, str]]:
    """Return (last, first) of the first name-shaped <strong> in a row, or None."""
    for raw in _STRONG_RE.findall(row_html):
        text = _decode_and_strip(raw)
        if not text:
            continue
        m = _NAME_RE.match(text)
        if not m:
            continue
        surname_token = m.group("last").split()[0] if m.group("last") else ""
        if surname_token.upper() in (
            "NAME", "DATE", "ELECTION", "PORTFOLIO", "CONSTITUENCY",
            "THE", "MINISTER", "HON",
        ):
            continue
        return m.group("last").strip().title(), m.group("first").strip()
    return None


def _parse_page(html_text: str, source_page: str) -> Iterator[ParsedMla]:
    """Yield ParsedMla instances from one page's HTML.

    Walks ``<tr>`` rows. Per row:
      1. First ``<strong>`` whose content is name-shaped becomes the
         MLA's identity.
      2. Strong-tag term ranges in the same row (deceased-style) are
         collected. If any are found, they win.
      3. Otherwise the row's decoded text is scanned for narrative
         election events (living-style: Elected/Re-elected/Resigned/
         Not-a-candidate/etc.) and paired into terms.
    """
    plain_row = _TAG_RE  # alias for readability
    for row_match in _TR_RE.finditer(html_text):
        row_html = row_match.group(1)
        name = _find_name_in_row(row_html)
        if name is None:
            continue
        last, first = name
        strong_terms = _extract_strong_terms(row_html)
        if strong_terms:
            terms = strong_terms
        else:
            # Decode to plain text (tags stripped) for narrative
            # pattern matching — regex is anchored on word tokens so
            # tag-stripping can't create false positives.
            row_text = _decode_and_strip(row_html)
            terms = _extract_narrative_terms(row_text)
        yield ParsedMla(
            last_name=last, first_name=first, terms=terms,
            source_page=source_page,
        )


async def _fetch(client: httpx.AsyncClient, url: str) -> str:
    r = await client.get(url, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    return r.text


# ── Top-level ingest ────────────────────────────────────────────────


async def ingest_mb_former_mlas(
    db: Database,
    *,
    include_living: bool = True,
    include_deceased: bool = True,
    delay: float = REQUEST_DELAY_SECONDS,
) -> Stats:
    stats = Stats()
    if not (include_living or include_deceased):
        return stats

    parsed: list[ParsedMla] = []
    async with httpx.AsyncClient(headers=HEADERS, follow_redirects=True) as client:
        if include_deceased:
            html_text = await _fetch(client, URL_DECEASED)
            stats.pages_fetched += 1
            n_before = len(parsed)
            parsed.extend(_parse_page(html_text, "deceased"))
            log.info("mb_former_mlas: deceased page parsed: %d MLAs", len(parsed) - n_before)
            if include_living:
                await asyncio.sleep(delay)
        if include_living:
            html_text = await _fetch(client, URL_LIVING)
            stats.pages_fetched += 1
            n_before = len(parsed)
            parsed.extend(_parse_page(html_text, "living"))
            log.info("mb_former_mlas: living page parsed: %d MLAs", len(parsed) - n_before)

    stats.names_seen = len(parsed)
    for p in parsed:
        stats.terms_parsed += len(p.terms)

    # ── Upsert politicians ──────────────────────────────────────────
    # Strategy:
    #   1. Name-match against existing MB politicians first. If the
    #      MLA on the bio page matches an existing row by normalized
    #      (first_name, last_name), attach terms to that row — don't
    #      create a duplicate. This handles current MLAs who appear
    #      on the "living" page and already have slugs set by the
    #      mb_mlas current-roster ingester ("byram" → Jodie Byram).
    #   2. Otherwise INSERT a new row keyed on mb_assembly_slug in
    #      the "lastname-firstname" shape (distinguishable from the
    #      current-roster surname-only slugs). Within-batch
    #      collisions get "-2", "-3" suffixes and log.
    seen_slugs: set[str] = set()
    for p in parsed:
        if not p.last_name or not p.first_name:
            continue

        norm_first = _strip_accents(p.first_name.split()[0]).lower()
        norm_last = _strip_accents(p.last_name).lower()
        existing = await db.fetchrow(
            """
            SELECT id FROM politicians
             WHERE province_territory='MB' AND level='provincial'
               AND lower(unaccent(split_part(first_name, ' ', 1))) = $1
               AND lower(unaccent(last_name)) = $2
             LIMIT 1
            """,
            norm_first, norm_last,
        )

        if existing is not None:
            pol_id = existing["id"]
            stats.politicians_updated += 1
        else:
            base_slug = p.mb_assembly_slug
            if not base_slug:
                continue
            slug = base_slug
            dupe_n = 1
            while slug in seen_slugs:
                dupe_n += 1
                slug = f"{base_slug}-{dupe_n}"
                stats.slug_collisions += 1
            seen_slugs.add(slug)

            # On living page: is_active = True iff most recent term is open-ended.
            is_active = False
            if p.source_page == "living" and p.terms:
                latest = max(p.terms, key=lambda t: t.started_at)
                is_active = latest.ended_at is None

            full_name = f"{p.first_name} {p.last_name}".strip()
            source_id = f"manitoba-assembly:former-mlas:{slug}"

            row = await db.fetchrow(
                """
                INSERT INTO politicians
                    (name, first_name, last_name, level, province_territory,
                     mb_assembly_slug, is_active, source_id)
                VALUES ($1, $2, $3, 'provincial', 'MB', $4, $5, $6)
                ON CONFLICT (mb_assembly_slug) WHERE mb_assembly_slug IS NOT NULL
                DO UPDATE SET
                    name       = COALESCE(NULLIF(politicians.name, ''),       EXCLUDED.name),
                    first_name = COALESCE(NULLIF(politicians.first_name, ''), EXCLUDED.first_name),
                    last_name  = COALESCE(NULLIF(politicians.last_name, ''),  EXCLUDED.last_name),
                    updated_at = now()
                RETURNING id, (xmax = 0) AS inserted
                """,
                full_name, p.first_name, p.last_name, slug, is_active, source_id,
            )
            pol_id = row["id"]
            if row["inserted"]:
                stats.politicians_inserted += 1
            else:
                stats.politicians_updated += 1

        # ── Upsert terms for this politician ────────────────────────
        for term in p.terms:
            existing = await db.fetchrow(
                """
                SELECT 1 FROM politician_terms
                 WHERE politician_id = $1 AND office = 'MLA'
                   AND started_at = $2
                """,
                pol_id, term.started_at,
            )
            if existing is not None:
                stats.terms_skipped_existing += 1
                continue
            await db.execute(
                """
                INSERT INTO politician_terms
                    (politician_id, office, level, province_territory,
                     started_at, ended_at, source)
                VALUES ($1, 'MLA', 'provincial', 'MB', $2, $3,
                        'assembly.mb.ca:former-mlas')
                """,
                pol_id, term.started_at, term.ended_at,
            )
            stats.terms_inserted += 1

    log.info(
        "mb_former_mlas: pages=%d names=%d terms_parsed=%d "
        "inserted=%d updated=%d slug_collisions=%d "
        "terms_inserted=%d terms_skipped=%d",
        stats.pages_fetched, stats.names_seen, stats.terms_parsed,
        stats.politicians_inserted, stats.politicians_updated,
        stats.slug_collisions,
        stats.terms_inserted, stats.terms_skipped_existing,
    )
    return stats
