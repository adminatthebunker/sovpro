"""Nova Scotia Hansard HTML parser — sitting HTML → ParsedSpeech list.

The Nova Scotia Legislature publishes daily Hansard transcripts at

    https://nslegislature.ca/legislative-business/hansard-debates/
        assembly-{N}-session-{M}/house_{DDmmmYY}

as a single HTML page rendered from a Drupal "hansard" content type.
The shape is strongly regular:

  * Every speaker turn is a ``<p>`` whose first two children are
    ``<a name="{slug}-{NNNN}"></a>`` (the navigable anchor target)
    and ``<a href="/members/profiles/{slug}" class="hsd_mla" title="View
    Profile">NAME</a>`` (the speaker link). After the optional
    Previous/Next navigation links comes the ` : ` separator and the
    first paragraph of speech text.

  * Continuation content (subsequent paragraphs, ``<blockquote>``
    passages for tabled petitions, lists) follows as plain block
    elements with no ``class="hsd_mla"`` anchor. Everything up to the
    next speaker anchor is part of the current turn.

  * The presiding officer appears as ``<a href="/members/speaker/"
    class="hsd_mla">THE SPEAKER</a>`` — note the href is ``/members/
    speaker/``, not ``/members/profiles/<slug>``. This marks a
    role-only turn resolved later via ``presiding_officer_resolver``.

  * The sitting date lives in the ``<title>`` tag — e.g.
    "Nova Scotia Legislature - Hansard - Assembly 65, Session 1 -
    Thursday, April 9, 2026".

  * Honorifics appear inside the anchor text, always in ALL CAPS:
    "HON. DEREK MOMBOURQUETTE", "DEREK MOMBOURQUETTE", "MADAM SPEAKER".

Pure-offline: no network, no DB.
"""
from __future__ import annotations

import hashlib
import html as html_mod
import logging
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime, time, timezone
from typing import Optional
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)

HALIFAX_TZ = ZoneInfo("America/Halifax")

SPEAKER_ROLE_HREF = "/members/speaker"


# ── URL parsing ─────────────────────────────────────────────────────
# /legislative-business/hansard-debates/assembly-{N}-session-{M}/house_{YYmonDD}
#   — e.g. house_26apr09 = 2026-04-09 (year, month abbr, day).
_URL_META_RE = re.compile(
    r"/hansard-debates/assembly-(?P<parliament>\d+)-session-(?P<session>\d+)/"
    r"house_(?P<yy>\d{2})(?P<mon>[a-z]{3})(?P<dd>\d{2})",
    re.IGNORECASE,
)


@dataclass
class UrlMeta:
    parliament: int
    session: int
    sitting_slug: str         # e.g. "house_26apr09"
    sitting_date_from_url: Optional[date]


_MONTH_ABBR_TO_INT = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}


def parse_url_meta(url: str) -> UrlMeta:
    """Extract parliament/session/date from the sitting URL.

    NS encodes the date as ``house_YYmonDD`` — two-digit year, three-
    letter month abbreviation, two-digit day. Example: ``house_26apr09``
    = 2026-04-09. YY<70 resolves to 2000+YY defensively, though only
    2000+ dates appear in the Drupal system.
    """
    m = _URL_META_RE.search(url)
    if not m:
        raise ValueError(f"not a recognized NS Hansard URL: {url}")
    parliament = int(m.group("parliament"))
    session = int(m.group("session"))
    yy = int(m.group("yy"))
    mon_abbr = m.group("mon").lower()
    day = int(m.group("dd"))
    year = 2000 + yy if yy < 70 else 1900 + yy
    mon = _MONTH_ABBR_TO_INT.get(mon_abbr)
    sitting_date: Optional[date] = None
    if mon:
        try:
            sitting_date = date(year, mon, day)
        except ValueError:
            sitting_date = None
    slug = f"house_{m.group('yy')}{mon_abbr}{m.group('dd')}"
    return UrlMeta(
        parliament=parliament,
        session=session,
        sitting_slug=slug,
        sitting_date_from_url=sitting_date,
    )


# ── HTML helpers ────────────────────────────────────────────────────
_TAG_RE = re.compile(r"<[^>]+>", re.DOTALL)
_WS_RE = re.compile(r"\s+")
_NBSP_RE = re.compile(r"&nbsp;|\xa0")


def _decode_entities(s: str) -> str:
    return html_mod.unescape(_NBSP_RE.sub(" ", s))


def _strip_tags(s: str) -> str:
    cleaned = _TAG_RE.sub("", s)
    return _WS_RE.sub(" ", _decode_entities(cleaned)).strip()


def _norm(s: str) -> str:
    if not s:
        return ""
    t = unicodedata.normalize("NFKD", s.replace(" ", " "))
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = t.lower()
    t = re.sub(r"[^\w\s-]", " ", t)
    return _WS_RE.sub(" ", t).strip()


# ── Sitting-date extraction ─────────────────────────────────────────
# "Nova Scotia Legislature - Hansard - Assembly 65, Session 1 -
#  Thursday, April 9, 2026"
_TITLE_DATE_RE = re.compile(
    r"<title[^>]*>[^<]*?"
    r"(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),\s+"
    r"(?P<mon>\w+)\s+(?P<day>\d{1,2}),\s+(?P<year>\d{4})"
    r"[^<]*</title>",
    re.IGNORECASE,
)
_MONTHS_LONG = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7,
    "aug": 8, "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
}


def extract_sitting_date(html: str) -> Optional[date]:
    m = _TITLE_DATE_RE.search(html)
    if m:
        mon = _MONTHS_LONG.get(m.group("mon").lower())
        if mon:
            try:
                return date(int(m.group("year")), mon, int(m.group("day")))
            except ValueError:
                pass
    return None


# ── Speech-turn extraction ──────────────────────────────────────────
# A <p> opens a new turn iff it contains <a href="/members/profiles/slug">
# or <a href="/members/speaker/"> after an optional <a name="..."></a>
# waypoint. We DO NOT require class="hsd_mla" — profile anchors carry
# that class but speaker anchors do not. The <p> prefix is what
# distinguishes body speech turns from TOC entries (which sit inside
# <td class="indent10"><div class="leader"><span class="leader">…).
_TURN_OPENER_RE = re.compile(
    r"<p\b[^>]*>"                                     # opening <p>
    # Optional anchor target. In session 65-1 the name anchor self-closes
    # (``<a name="x"></a>``); in 63-3 and earlier it stays open
    # (``<a name="x">`` with no ``</a>`` before the href anchor).
    r"\s*(?:<a\s+name=\"[^\"]+\"[^>]*>\s*(?:</a>\s*)?)?"
    r"<a\b[^>]*\bhref=\"(?P<href>/members/"
    r"(?:profiles/[^\"]+|speaker/?))\""              # /members/profiles/slug OR /members/speaker
    r"[^>]*>(?P<name>[^<]+)</a>",
    re.IGNORECASE | re.DOTALL,
)


# Markers that sit right after the spoken transcript ends. We stop the
# final turn here so written-questions section, sidebar nav, filter
# form, and footer don't get folded into the last speech. NS sittings
# end with a Speaker "…House now stands adjourned" turn; the block
# after that is either [The House rose at HH:MM p.m.] + written
# questions, or the aside/footer chrome directly.
_BODY_END_RE = re.compile(
    r"<p\b[^>]*class=\"hsd_center\"[^>]*>\s*<b[^>]*>\s*NOTICE[^<]*QUESTIONS"
    r"|\[The House\s+rose\s+at\b"
    r"|<aside\b"
    r"|<div[^>]*class=\"[^\"]*filter-intro[^\"]*\""
    r"|<footer\b",
    re.IGNORECASE,
)


def _find_body_slice(html: str) -> tuple[int, int]:
    """Narrow the HTML so the last turn doesn't slurp the page footer.

    TOC entries live inside ``<td class="indent10">`` wrappers at the
    top of the page and lack ``<p>`` parents, so the turn regex
    naturally skips them. We only need to cap the END of the body —
    typically the filter-intro panel (search-older-Hansard form) or
    the page footer, whichever comes first.
    """
    end = len(html)
    m_end = _BODY_END_RE.search(html)
    if m_end:
        end = m_end.start()
    return 0, end


# ── Attribution parsing ─────────────────────────────────────────────
# Anchor text examples (always ALL CAPS in the transcript body):
#   "DEREK MOMBOURQUETTE"
#   "HON. DEREK MOMBOURQUETTE"
#   "HON. BRENDAN MAGUIRE"         ← slug is "brendan-o.-maguire"; link text drops middle
#   "THE SPEAKER"
#   "MADAM SPEAKER"
#   "MR. SPEAKER"
_HONORIFIC_RE = re.compile(
    r"^(?P<hon>hon\.|hon|honourable|mr\.|mrs\.|ms\.|miss\.?|dr\.?|madam|sir)\s+"
    r"(?P<rest>.+)$",
    re.IGNORECASE,
)

# Role patterns (case-insensitive after lower()). Presiding officer is
# the only role-only attribution we expect in NS.
_ROLE_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"^the\s+speaker$"),           "The Speaker"),
    (re.compile(r"^madam\s+speaker$"),         "The Speaker"),
    (re.compile(r"^madame\s+speaker$"),        "The Speaker"),
    (re.compile(r"^mister\s+speaker$"),        "The Speaker"),
    (re.compile(r"^mr\.?\s+speaker$"),         "The Speaker"),
    (re.compile(r"^the\s+deputy\s+speaker$"),  "The Deputy Speaker"),
    (re.compile(r"^the\s+chair(?:man|person|woman)?$"), "The Chair"),
    (re.compile(r"^the\s+clerk$"),             "The Clerk"),
    (re.compile(r"^the\s+sergeant[-\s]at[-\s]arms$"), "The Sergeant-at-Arms"),
]


@dataclass
class ParsedAttribution:
    raw: str
    role: Optional[str]
    honorific: Optional[str]
    surname: Optional[str]
    given_names: Optional[str]        # first + middle names, title-cased
    full_name: Optional[str]          # "Derek Mombourquette" (title-cased)


def _title_case_person(text: str) -> str:
    """Title-case an ALL-CAPS person name, preserving common suffixes.

    "DEREK MOMBOURQUETTE" → "Derek Mombourquette"
    "SMITH-MCCROSSIN"    → "Smith-Mccrossin"
    """
    out_parts: list[str] = []
    for word in text.split():
        parts = word.split("-")
        parts = [p.capitalize() for p in parts]
        out_parts.append("-".join(parts))
    return " ".join(out_parts)


def parse_attribution(raw: str, href_slug: Optional[str]) -> ParsedAttribution:
    """Decompose the anchor text into honorific / role / name pieces.

    `href_slug` is the /members/profiles/<slug> value (or None for
    /members/speaker/). When the href points to a member profile, we
    always return a person attribution even if the role pattern matches
    (unlikely — the NS markup reserves /members/speaker/ for the role).
    """
    cleaned = _WS_RE.sub(" ", raw).strip()
    lower = cleaned.lower()

    # Role-only attribution (THE SPEAKER, MADAM SPEAKER, …). Only
    # activate when the href explicitly points at /members/speaker/.
    if href_slug is None:
        for pat, canonical in _ROLE_PATTERNS:
            if pat.match(lower):
                return ParsedAttribution(
                    raw=cleaned, role=canonical, honorific=None,
                    surname=None, given_names=None, full_name=None,
                )
        # Unknown role-only string; still treat as role and stash raw.
        return ParsedAttribution(
            raw=cleaned, role=cleaned, honorific=None,
            surname=None, given_names=None, full_name=None,
        )

    # Person attribution — strip honorific, split on whitespace.
    honorific: Optional[str] = None
    m_hon = _HONORIFIC_RE.match(cleaned)
    if m_hon:
        honorific = m_hon.group("hon").title()
        rest = m_hon.group("rest").strip()
    else:
        rest = cleaned
    # Title-case the person name.
    pretty = _title_case_person(rest)
    tokens = pretty.split()
    if not tokens:
        return ParsedAttribution(
            raw=cleaned, role=None, honorific=honorific,
            surname=None, given_names=None, full_name=None,
        )
    surname = tokens[-1]
    given = " ".join(tokens[:-1]) if len(tokens) > 1 else ""
    return ParsedAttribution(
        raw=cleaned,
        role=None,
        honorific=honorific,
        surname=surname,
        given_names=given or None,
        full_name=pretty,
    )


# ── Output dataclass ────────────────────────────────────────────────
@dataclass
class ParsedSpeech:
    sequence: int
    speaker_name_raw: str
    speaker_role: Optional[str]
    speaker_slug: Optional[str]         # /members/profiles/<slug> → <slug>; None for role-only
    honorific: Optional[str]
    surname: Optional[str]
    full_name: Optional[str]
    speech_type: str
    spoken_at: datetime                 # UTC
    text: str
    language: str
    content_hash: str
    raw: dict = field(default_factory=dict)

    @property
    def word_count(self) -> int:
        return len(self.text.split())


def _content_hash(text: str) -> str:
    normalised = unicodedata.normalize("NFKC", text).strip().lower()
    normalised = _WS_RE.sub(" ", normalised)
    return hashlib.sha256(normalised.encode("utf-8")).hexdigest()


def _localise(sitting_date: date, t: time) -> datetime:
    return datetime.combine(sitting_date, t, tzinfo=HALIFAX_TZ).astimezone(timezone.utc)


# Default sitting time when we have no better signal. NS afternoon
# sittings start at 13:00; morning Committee-of-the-Whole at 10:00.
# We pick 13:00 as a deterministic fallback — only the date is
# semantically load-bearing for search filters.
_DEFAULT_START_TIME = time(13, 0)


# ── Main extractor ──────────────────────────────────────────────────
@dataclass
class ParseResult:
    url: str
    url_meta: UrlMeta
    sitting_date: date
    speeches: list[ParsedSpeech]


# Slug extraction from href="/members/profiles/<slug>".
_SLUG_FROM_HREF_RE = re.compile(
    r"/members/profiles/(?P<slug>[^/\"?#]+)", re.IGNORECASE,
)


def extract_speeches(html_text: str, url: str) -> ParseResult:
    """Parse a sitting's HTML into a list of ParsedSpeech.

    Walks the hsd_body region, finds each turn opener, and claims all
    content between one opener and the next as that turn's body.
    """
    meta = parse_url_meta(url)
    sitting_date = (
        extract_sitting_date(html_text)
        or meta.sitting_date_from_url
        or date(1970, 1, 1)
    )
    body_start, body_end = _find_body_slice(html_text)
    body_html = html_text[body_start:body_end]

    openers = list(_TURN_OPENER_RE.finditer(body_html))
    speeches: list[ParsedSpeech] = []
    spoken_at = _localise(sitting_date, _DEFAULT_START_TIME)

    for i, m in enumerate(openers):
        turn_end = openers[i + 1].start() if i + 1 < len(openers) else len(body_html)

        href = m.group("href")
        name_raw = _decode_entities(m.group("name")).strip()
        if not name_raw:
            continue
        slug_match = _SLUG_FROM_HREF_RE.search(href)
        speaker_slug = slug_match.group("slug") if slug_match else None

        attr = parse_attribution(name_raw, speaker_slug)

        # Everything after the speaker anchor (and any Next/Previous nav
        # anchors) is the speech body. We strip all <a> tags first (their
        # text content is either nav glyphs or speaker names, never body
        # text), then drop the remaining markup.
        body_after_name = body_html[m.end():turn_end]
        # Drop the leading " : " separator if present, along with any
        # further <a> navigation links (« / »).
        body_after_name = re.sub(
            r"^\s*(?:<a\b[^>]*>[^<]*</a>\s*)*(?::|&nbsp;|\s)*",
            "",
            body_after_name,
            count=1,
        )
        # Strip remaining inline nav anchors anywhere in the turn.
        body_clean = re.sub(
            r"<a\b[^>]*\btitle=\"(?:Previous|Next|Top)\"[^>]*>[^<]*</a>",
            "",
            body_after_name,
            flags=re.IGNORECASE,
        )
        # Strip page-number anchors (<a name=IPage1234></a> or
        # <a href="#HPage1234">1234</a>).
        body_clean = re.sub(
            r"<a\b[^>]*(?:name=\"?IPage\d+\"?|href=\"#[HI]Page\d+\")[^>]*>[^<]*</a>",
            "",
            body_clean,
            flags=re.IGNORECASE,
        )
        # Preserve paragraph boundaries by replacing block-close tags
        # with newlines before tag-stripping.
        body_clean = re.sub(
            r"</(?:p|blockquote|div|li|tr)\s*>",
            "\n",
            body_clean,
            flags=re.IGNORECASE,
        )
        text = _strip_tags(body_clean)
        # Collapse runs of whitespace within paragraphs (tag strip leaves
        # odd doubled spaces) while keeping paragraph boundaries.
        text_paras = [p.strip() for p in text.split("\n")]
        text = "\n\n".join(p for p in text_paras if p)

        if not text:
            continue

        speech = ParsedSpeech(
            sequence=len(speeches) + 1,
            speaker_name_raw=name_raw,
            speaker_role=attr.role,
            speaker_slug=speaker_slug,
            honorific=attr.honorific,
            surname=attr.surname,
            full_name=attr.full_name,
            speech_type="floor",
            spoken_at=spoken_at,
            text=text,
            language="en",
            content_hash=_content_hash(text),
            raw={
                "url": url,
                "sitting_date": sitting_date.isoformat(),
                "parliament": meta.parliament,
                "session": meta.session,
                "sitting_slug": meta.sitting_slug,
                "href": href,
                "slug": speaker_slug,
                "honorific": attr.honorific,
                "surname": attr.surname,
                "full_name": attr.full_name,
            },
        )
        speeches.append(speech)

    return ParseResult(
        url=url,
        url_meta=meta,
        sitting_date=sitting_date,
        speeches=speeches,
    )
