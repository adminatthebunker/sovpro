"""Manitoba Hansard — Word 97 HTML-export parser.

Sister module to ``mb_hansard_parse`` (which handles the modern
MsoNormal / Word 2007+ export used by legs 39+). This one handles
the Word 97 export used by **legislatures 37 and 38** (1999-10 →
2007-05), which covers roughly 8 years of history that the modern
parser can't see.

## Markup shape

Word 97's HTML export is structurally different from MsoNormal:

  * Uppercase tags throughout (``<HTML>``, ``<BODY>``, ``<B>``,
    ``<P>``) — no ``class=MsoNormal``, no CSS-driven layout.
  * The ``<B>`` wrapper for speaker attributions opens BEFORE the
    ``<P>`` and closes after the colon, so the speaker's bold run
    spans the paragraph boundary:

        <B><P ALIGN="JUSTIFY">Hon. Greg Selinger (Minister of
        Finance): </B>Mr. Speaker, I am pleased to table the
        following reports…</P>

  * Continuation paragraphs for the same speaker have no bold
    lead-in:

        <P ALIGN="JUSTIFY">&#9;I am also pleased to table…</P>

  * Section headers + procedural labels use ``<P ALIGN="CENTER">``:

        <P ALIGN="CENTER">ROUTINE PROCEEDINGS</P>
        <P ALIGN="CENTER">Tuesday, November 30, 1999</P>
        <I><P ALIGN="CENTER">PRAYERS</P></I>

  * Section anchors sometimes appear as ``<A NAME="oq"></A>`` inside
    centered headers ("ORAL QUESTION PERIOD"). Useful for tagging
    speech_type but not required.

  * **No timestamps.** Modern MB has ``<b>*</b>(13:40)`` markers per
    turn; W97 only gives "The House met at 1:30 p.m." as the opening
    header. All turns get the sitting-date midnight + a default
    13:30 start-time wall-clock.

  * Encoding is windows-1252. The ingester already forces
    ``r.encoding = "windows-1252"`` on per-sitting fetches (see
    mb_hansard._process_one_sitting), so this module receives
    properly-decoded text.

## Output

Same ``ParseResult`` shape as mb_hansard_parse so downstream code
(``_upsert_speech`` in mb_hansard.py, the speaker resolver, the
chunker) doesn't care which era it's processing.

## Dispatch

``extract_speeches`` in ``mb_hansard_parse`` detects W97 markup via
the generator-meta-tag heuristic and delegates here for matching
sittings. Callers should not import this module directly.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from typing import Optional

from . import mb_hansard_parse as modern
from .mb_hansard_parse import (
    ParseResult,
    ParsedSpeech,
    UrlMeta,
    _content_hash,
    _decode_entities,
    _localise,
    _strip_tags,
    parse_attribution,
    parse_url_meta,
)

# ── Format detection ─────────────────────────────────────────────────

_W97_GENERATOR_RE = re.compile(
    r'CONTENT="(?:[^"]*)Microsoft Word 97(?:[^"]*)"',
    re.IGNORECASE,
)
# Fallback: bare <BODY> with no CSS class, no <head><style>@font-face
_UPPERCASE_BODY_RE = re.compile(r'<BODY\b', re.IGNORECASE)
_MSO_NORMAL_RE = re.compile(r'MsoNormal', re.IGNORECASE)


def is_word97(html_text: str) -> bool:
    """Heuristic — true iff this looks like Word 97 HTML export.

    Word 97: generator meta tag says "Microsoft Word 97" AND no
    MsoNormal class anywhere in the document.
    Modern (2007+): has "Microsoft Word 11" or later generator, OR
    uses MsoNormal extensively.
    """
    if _MSO_NORMAL_RE.search(html_text):
        return False
    return bool(_W97_GENERATOR_RE.search(html_text))


# ── Sitting-date extraction ─────────────────────────────────────────

# Word 97 sittings lead with:
#   <P ALIGN="CENTER">Tuesday, November 30, 1999</P>
# The dow is optional ("November 30, 1999" also valid in a few).
_W97_HEADER_DATE_RE = re.compile(
    r"(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),?\s+"
    r"(?P<mon>January|February|March|April|May|June|July|"
    r"August|September|October|November|December)\s+"
    r"(?P<day>\d{1,2}),\s+(?P<year>\d{4})",
    re.IGNORECASE,
)

_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
}


def extract_sitting_date_w97(html: str) -> Optional[date]:
    """Scan the first ~20 KB of body for the day-of-week header.

    W97 sittings have bare <TITLE>Vol</TITLE> (not a date), so the
    title-regex in mb_hansard_parse can't help. The dow header is
    one of the first few centered paragraphs.
    """
    # Strip tags + collapse whitespace, but cap the window — the header
    # is always near the top and scanning the full file risks matching
    # an in-debate date ("fiscal year ending March 31, 2006").
    stripped = re.sub(r"<[^>]+>", " ", html[:20000])
    stripped = re.sub(r"\s+", " ", stripped)
    m = _W97_HEADER_DATE_RE.search(stripped)
    if not m:
        return None
    mon = _MONTHS.get(m.group("mon").lower())
    if not mon:
        return None
    try:
        return date(int(m.group("year")), mon, int(m.group("day")))
    except ValueError:
        return None


# ── Section-header detection ────────────────────────────────────────

# CENTER-aligned paragraph content. Captures the inner text so we can
# set speech_type hints when we see "ORAL QUESTION PERIOD", etc.
_CENTER_P_RE = re.compile(
    r'<P\s+ALIGN="CENTER"[^>]*>(.*?)</P>',
    re.IGNORECASE | re.DOTALL,
)

_SECTION_CANONICAL = {
    # key: a lowercase-trimmed substring of the header → canonical section
    "oral question": "Oral Questions",
    "tabling of reports": "Tabling of Reports",
    "introduction of guests": "Introduction of Guests",
    "members' statements": "Members' Statements",
    "members statements": "Members' Statements",
    "routine proceedings": "Routine Proceedings",
    "orders of the day": "Orders of the Day",
    "committee of supply": "Committee of Supply",
    "government business": "Government Business",
    "house business": "House Business",
    "ministerial statements": "Ministerial Statements",
    "government motion": "Government Motion",
    "petitions": "Petitions",
    "private members' business": "Private Members' Business",
    "private members business": "Private Members' Business",
    "second readings": "Second Readings",
    "third readings": "Third Readings",
    "point of order": "Point of Order",
}


def _classify_section(header_text: str) -> Optional[str]:
    norm = re.sub(r"\s+", " ", header_text).strip().lower()
    for needle, canon in _SECTION_CANONICAL.items():
        if needle in norm:
            return canon
    return None


# ── Speaker turn extraction ─────────────────────────────────────────

# Matches the full speaker turn from `<B>` open through to the next
# `<B>` open or EOF. Captures:
#   attr_raw: the bold run content (Name + optional Role + colon)
#   body_html: everything AFTER </B> up to the next <B> / EOF
#
# We tolerate the `<B>` sometimes wrapping across <P> boundaries and
# various attribute orders on <P>.
_TURN_RE = re.compile(
    r"""
    <B>\s*                              # open bold
    (?:</?P[^>]*>\s*)*                  # optional <P> open(s) before the name
    (?P<attr_raw>[^<]+?):\s*            # Name + (Role) + ":" + trailing space
    (?:\s*<[^>]+>\s*)*?                 # optional inline tags between name and </B>
    </B>                                # close bold
    (?P<body_html>.*?)                  # body up to next turn
    (?=<B>\s*(?:</?P[^>]*>\s*)*[A-Z(][^<]*?:\s*|\Z)
    """,
    re.IGNORECASE | re.DOTALL | re.VERBOSE,
)

# Looser attribution check — W97 occasionally has stray bolds around
# non-speaker content (italicized emphasis inside body). We treat a
# <B> run as a speaker turn iff:
#   1. the inner text ends with ":"
#   2. the content before ":" is "looks like a person / role"
_SPEAKER_LIKE_RE = re.compile(
    r"""^\s*
    (?:Hon\.?|Madam|Mr\.?|Mrs\.?|Ms\.?|Miss|Dr\.?|Some|An)
    \s+
    [A-Z]""",
    re.VERBOSE,
)


def _looks_like_speaker_attr(attr_raw: str) -> bool:
    cleaned = re.sub(r"\s+", " ", attr_raw).strip()
    if not cleaned:
        return False
    if len(cleaned) > 200:
        return False  # Almost certainly body text captured by mistake
    if _SPEAKER_LIKE_RE.match(cleaned):
        return True
    # A bare "Name:" without honorific (continuation turns, e.g.
    # "Mr. Doer:" or "Mr. Filmon:") still start with Mr./Mrs./Ms.
    # so the above regex catches them. Speaker-role lines like
    # "Mr. Speaker:" / "Madam Speaker:" / "Madam Chairperson:"
    # match too.
    return False


# Body cleaning: strip tags, decode entities, normalize whitespace
_BODY_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _clean_body(body_html: str) -> str:
    # Centered paragraphs embedded in the body (procedural headers
    # that interrupt a speech) are stripped along with other tags;
    # that's acceptable because the section tracker below watches
    # centered headers separately.
    text = _BODY_TAG_RE.sub(" ", body_html)
    text = _decode_entities(text)
    text = _WS_RE.sub(" ", text).strip()
    return text


# Default sitting time — W97 era has no inline timestamps, so every
# speech gets the sitting-date at 13:30 local. "The House met at …"
# opening line could be parsed for the actual start time but that's
# one value per sitting, not per speech, so the precision gain is
# tiny. Match the modern parser's default.
_DEFAULT_START_TIME = time(13, 30)


# ── Main extractor ──────────────────────────────────────────────────


def extract_speeches_w97(html_text: str, url: str) -> ParseResult:
    """W97 equivalent of mb_hansard_parse.extract_speeches."""
    meta = parse_url_meta(url)
    sitting_date = extract_sitting_date_w97(html_text) or date(1970, 1, 1)

    # First pass: find all centered headers with their byte offsets,
    # so we can annotate each speech with the last-seen section.
    section_spans: list[tuple[int, str]] = []
    for m in _CENTER_P_RE.finditer(html_text):
        canon = _classify_section(_strip_tags(m.group(1)))
        if canon:
            section_spans.append((m.start(), canon))
    section_spans.sort()

    def section_at(pos: int) -> Optional[str]:
        # Binary search would be cleaner; linear is fine at <100 sections.
        last = None
        for span_pos, canon in section_spans:
            if span_pos > pos:
                break
            last = canon
        return last

    speeches: list[ParsedSpeech] = []
    section_hits: dict[str, int] = {}

    for m in _TURN_RE.finditer(html_text):
        attr_raw = m.group("attr_raw")
        if not _looks_like_speaker_attr(attr_raw):
            continue

        attr = parse_attribution(attr_raw)
        body = _clean_body(m.group("body_html"))
        if not body:
            # Speaker opened with no subsequent body text — happens
            # for procedural "Mr. Speaker: The honourable member for
            # X." stubs where the next turn immediately follows.
            # Still record the turn if there's any text after stripping.
            continue

        section = section_at(m.start())
        if section:
            section_hits[section] = section_hits.get(section, 0) + 1

        spoken_at = _localise(sitting_date, _DEFAULT_START_TIME)
        speech = ParsedSpeech(
            sequence=len(speeches) + 1,
            speaker_name_raw=re.sub(r"\s+", " ", attr_raw).strip(),
            speaker_role=attr.role,
            honorific=attr.honorific,
            surname=attr.surname,
            full_name=attr.full_name,
            paren_role=attr.paren_role,
            speech_type=_speech_type(attr, section),
            spoken_at=spoken_at,
            text=body,
            language="en",
            content_hash=_content_hash(body),
            raw={
                "volume": meta.volume,
                "html_id": meta.html_id,
                "section": section,
                "url": url,
                "sitting_time": _DEFAULT_START_TIME.strftime("%H:%M"),
                "era": "w97",
            },
        )
        speeches.append(speech)

    return ParseResult(
        url=url,
        url_meta=meta,
        sitting_date=sitting_date,
        speeches=speeches,
        section_hits=section_hits,
    )


def _speech_type(attr, section: Optional[str]) -> str:
    # Group acknowledgments: "Some Honourable Members:", "An
    # Honourable Member:" — the raw attribution gives us this
    # directly via parse_attribution's honorific/surname fields.
    raw = f"{attr.honorific or ''} {attr.surname or ''}".lower()
    if "honourable members" in raw or "honourable member" in raw:
        return "group"
    return "floor"
