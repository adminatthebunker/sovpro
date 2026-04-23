"""Manitoba Hansard HTML parser — hNN.html → ParsedSpeech list.

The Legislative Assembly of Manitoba publishes daily transcripts as
Microsoft-Word-exported HTML at:

    /legislature/hansard/{leg}_{sess}/vol_{NN[letter]}/h{NN[letter]}.html

The file is encoded in windows-1252 (httpx decodes based on the
server-provided charset). The body is littered with Word-specific
markup — ``<p class=MsoNormal>``, ``<span lang=EN-US>``, ``<o:p>``
sentinels, conditional comments — but the speaker-turn pattern is
straightforward:

  * Each turn is a ``<p class=MsoNormal...>`` whose first non-whitespace
    child is ``<b>Name:</b>`` (trailing colon is the load-bearing bit
    that distinguishes a speaker line from a centered-bold heading
    like "Patient-Focused Health Care").
  * Continuation paragraphs inside the turn are plain ``<p>`` with no
    ``<b>`` prefix.
  * Timestamps appear as their own standalone paragraphs:
    ``<b>*</b> (13:40)``. They update the "current spoken time" for
    subsequent speeches but don't themselves create a turn.
  * Section headings use ``class=MsoHeading8`` OR a centered bold
    paragraph with no trailing colon.

Attribution shapes observed:

  Mr. Kinew                           → honorific + surname
  Mrs. Cook                           → honorific + surname
  Ms. Altomare                        → honorific + surname
  Hon. Min. Sala                      → honorific + role-prefix + surname
  Hon. Min. Fontaine                  → same
  Hon. Mr. Wiebe                      → same
  Hon. Anita R. Neville (Lieutenant Governor of the Province of Manitoba)
                                       → honorific + full name + role-in-parens
  The Speaker                         → role (presiding)
  The Deputy Speaker                  → role (presiding)
  Madam Speaker                       → role (presiding)
  MLA Sala                            → ambiguous — title "MLA" used for
                                         certain ministers; treat like
                                         honorific + surname
  An Honourable Member / Some Honourable Members → group / anonymous

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

WINNIPEG_TZ = ZoneInfo("America/Winnipeg")


# ── URL parsing ─────────────────────────────────────────────────────
# /legislature/hansard/{leg}_{sess}/vol_{NN[letter]}/h{NN[letter]}.html
# where {leg} is like "43rd" and {sess} is "3rd".
_URL_META_RE = re.compile(
    r"/hansard/(?P<leg>\d+\w+)_(?P<sess>\d+\w+)/"
    r"vol_(?P<vol>\d+[a-z]?)/h(?P<htid>\d+[a-z]?)\.html",
    re.IGNORECASE,
)


@dataclass
class UrlMeta:
    parliament: int          # numeric form of "43rd" → 43
    session: int             # numeric form of "3rd" → 3
    volume: str              # "01", "41a", …
    html_id: str             # "01", "41a", …


def _ordinal_to_int(s: str) -> int:
    m = re.match(r"(\d+)", s)
    if not m:
        raise ValueError(f"not an ordinal: {s}")
    return int(m.group(1))


def parse_url_meta(url: str) -> UrlMeta:
    m = _URL_META_RE.search(url)
    if not m:
        raise ValueError(f"not a recognized MB Hansard URL: {url}")
    return UrlMeta(
        parliament=_ordinal_to_int(m.group("leg")),
        session=_ordinal_to_int(m.group("sess")),
        volume=m.group("vol").lower(),
        html_id=m.group("htid").lower(),
    )


# ── HTML helpers ────────────────────────────────────────────────────
_TAG_RE = re.compile(r"<[^>]+>", re.DOTALL)
_WS_RE = re.compile(r"\s+")
# Word inserts <o:p>...</o:p> sentinels we must strip before tag-stripping.
_OP_SENTINEL_RE = re.compile(r"<o:p[^>]*>.*?</o:p>", re.IGNORECASE | re.DOTALL)
# Bold runs may contain nested <span>, <a>, etc.; we only want the text.
_NBSP_RE = re.compile(r"&nbsp;|\xa0")


def _decode_entities(s: str) -> str:
    return html_mod.unescape(_NBSP_RE.sub(" ", s))


def _strip_tags(s: str) -> str:
    cleaned = _OP_SENTINEL_RE.sub("", s)
    cleaned = _TAG_RE.sub("", cleaned)
    return _WS_RE.sub(" ", _decode_entities(cleaned)).strip()


def _norm(s: str) -> str:
    if not s:
        return ""
    t = unicodedata.normalize("NFKD", s.replace("\u00a0", " "))
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = t.lower()
    t = re.sub(r"[^\w\s-]", " ", t)
    return _WS_RE.sub(" ", t).strip()


# ── Paragraph + speaker regex ───────────────────────────────────────
# Each <p ...>...</p> block; DOTALL because paragraphs span multiple
# source-HTML lines.
_P_RE = re.compile(r"<p\b(?P<attrs>[^>]*)>(?P<body>.*?)</p>", re.DOTALL | re.IGNORECASE)

# A paragraph opens a speaker turn iff its first non-whitespace child
# is <b>…</b> AND the content inside the <b> ends with a colon (after
# entity decode + whitespace collapse).
_P_OPEN_B_RE = re.compile(
    r"^\s*<b\b[^>]*>(?P<name>.*?)</b>(?P<tail>.*)$",
    re.DOTALL,
)

# Timestamp marker: <b>*</b> (HH:MM) — sometimes with variations on
# the asterisk glyph (•, ◊) but always a (HH:MM) tail.
_TIMESTAMP_RE = re.compile(
    r"^\s*<b\b[^>]*>\s*[*•◊]+\s*</b>\s*\(\s*(?P<h>\d{1,2})\s*:\s*(?P<m>\d{2})\s*\)",
    re.DOTALL | re.IGNORECASE,
)

# Main-body bounds. We don't need an anchor — Word's `<body>` runs
# from the opening boilerplate to the footer boilerplate; the parser
# just walks paragraphs and the scoring disambiguates.

# Heading: entire <p> body is a bold block with no trailing colon, OR
# the paragraph has class=MsoHeading<N>.
_HEADING_ONLY_RE = re.compile(
    r"^\s*<b\b[^>]*>(?P<body>.*?)</b>\s*$",
    re.DOTALL | re.IGNORECASE,
)
_HEADING_CLASS_RE = re.compile(r"MsoHeading\d+", re.IGNORECASE)


def _is_heading(paragraph_attrs: str, inner_html: str) -> tuple[bool, Optional[str]]:
    # MsoHeading<N> paragraphs are always headings.
    if _HEADING_CLASS_RE.search(paragraph_attrs or ""):
        body = _strip_tags(inner_html)
        return (True, body) if body else (False, None)
    # Otherwise look for pure-bold body without trailing colon.
    m = _HEADING_ONLY_RE.match(inner_html.strip())
    if not m:
        return False, None
    body = _strip_tags(m.group("body"))
    if not body or ":" in body[-2:] or body.startswith("*"):
        return False, None
    return True, body


# ── Speaker attribution ─────────────────────────────────────────────
_TRAILING_COLON_RE = re.compile(r"[:\s\u00a0]+$")


def _looks_like_speaker_name(name_text: str) -> bool:
    cleaned = _decode_entities(_TAG_RE.sub("", name_text))
    return bool(re.search(r":\s*$", cleaned))


def _clean_speaker(name_text: str) -> str:
    text = _decode_entities(_TAG_RE.sub("", name_text))
    text = _WS_RE.sub(" ", text).strip()
    return _TRAILING_COLON_RE.sub("", text).strip()


# Role patterns (English, accent-agnostic after _norm).
_ROLE_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # Presiding officers — single-person-at-a-time, resolved via
    # date-ranged terms in the presiding_officer_resolver.
    (re.compile(r"^the\s+speaker$"),                       "The Speaker"),
    (re.compile(r"^madam\s+speaker$"),                     "The Speaker"),
    (re.compile(r"^mister\s+speaker$"),                    "The Speaker"),
    (re.compile(r"^the\s+deputy\s+speaker$"),              "The Deputy Speaker"),
    (re.compile(r"^the\s+acting\s+speaker.*$"),            "The Acting Speaker"),
    (re.compile(r"^the\s+chairperson.*$"),                 "The Chairperson"),
    (re.compile(r"^the\s+chair.*$"),                       "The Chair"),
    # Presiding officer of Committee of Supply / Committee of the Whole.
    (re.compile(r"^the\s+deputy\s+chair.*$"),              "The Deputy Chair"),
    # Head of government — either person resolution (by paren) or role-only.
    (re.compile(r"^the\s+premier\s*$"),                    "The Premier"),
    (re.compile(r"^the\s+hon(?:ourable)?\s+premier\s*$"),  "The Premier"),
    # Cabinet / leadership role-only attributions.
    (re.compile(r"^the\s+minister\s+of\b.*$"),             "The Minister"),
    (re.compile(r"^the\s+minister\s+responsible\b.*$"),    "The Minister"),
    (re.compile(r"^the\s+attorney\s+general.*$"),          "The Attorney General"),
    (re.compile(r"^the\s+government\s+house\s+leader.*$"), "The Government House Leader"),
    (re.compile(r"^the\s+official\s+opposition.*$"),       "The Official Opposition Leader"),
    (re.compile(r"^the\s+clerk\s*$"),                      "The Clerk"),
    (re.compile(r"^the\s+sergeant[-\s]at[-\s]arms\s*$"),   "The Sergeant-at-Arms"),
    # Anonymous / group.
    (re.compile(r"^(?:an|some|several)\s+hon(?:ourable)?\s+members?$"),
                                                           "Honourable Members"),
    (re.compile(r"^voices?$"),                             "Voices"),
]

# Main/paren split: "Hon. Anita R. Neville (Lieutenant Governor of...)"
_PAREN_SPLIT_RE = re.compile(r"^(?P<main>[^()]+?)\s*\((?P<paren>[^()]+)\)\s*$")

# Honorifics at the start of a person attribution. "Hon. Min." is a
# MB-specific compound for cabinet ministers — fold into a single
# honorific unit so surname extraction works.
_HONORIFIC_RE = re.compile(
    r"^(?P<hon>"
    r"Hon\.\s+Min\.|Hon\.\s+Mr\.|Hon\.\s+Mrs\.|Hon\.\s+Ms\.|Hon\.\s+Madam|"
    r"Hon\.|Honourable|"
    r"Mr\.|Mrs\.|Ms\.|Miss\.?|Dr\.?|Madam|MLA"
    r")\s+(?P<rest>.+)$",
    re.IGNORECASE,
)


@dataclass
class ParsedAttribution:
    raw: str
    role: Optional[str]
    honorific: Optional[str]
    surname: Optional[str]
    # Full first+middle+last name when the source spells it out
    # (throne-speech speaker line: "Hon. Anita R. Neville").
    full_name: Optional[str] = None
    paren_role: Optional[str] = None         # "Lieutenant Governor of..." / "Attorney General"


def _match_role(text: str) -> Optional[str]:
    norm = _norm(text)
    for pat, canonical in _ROLE_PATTERNS:
        if pat.match(norm):
            return canonical
    return None


def _split_honorific(text: str) -> tuple[Optional[str], Optional[str]]:
    m = _HONORIFIC_RE.match(text.strip())
    if not m:
        return None, None
    hon_raw = m.group("hon")
    rest = m.group("rest").strip()
    # Title-case the honorific canonical form.
    hon = " ".join(w.capitalize() for w in hon_raw.split())
    # Fold "Hon. Min." into "Hon. Min." (already correct);
    # "Hon. Mr." → "Hon. Mr." etc. Leave casing as-is for consistency.
    return hon, rest


def parse_attribution(raw: str) -> ParsedAttribution:
    cleaned = _clean_speaker(raw)
    m_paren = _PAREN_SPLIT_RE.match(cleaned)
    main = m_paren.group("main").strip() if m_paren else cleaned
    paren = m_paren.group("paren").strip() if m_paren else None

    attr = ParsedAttribution(raw=cleaned, role=None, honorific=None, surname=None)

    # Role detection on the main component (e.g. "The Speaker").
    role = _match_role(main)
    if role:
        attr.role = role
        if paren:
            # Role + parenthetical person — not common in MB but we
            # preserve the paren for potential downstream use.
            attr.paren_role = paren
        return attr

    # Person attribution — honorific + surname (or full name) on main.
    hon, rest = _split_honorific(main)
    if hon and rest:
        attr.honorific = hon
        # "rest" may be "Kinew" (surname only) or "Anita R. Neville"
        # (multi-token full name). Surname is the last token; everything
        # else is first / middle names.
        tokens = rest.split()
        attr.surname = tokens[-1] if tokens else None
        if len(tokens) > 1:
            attr.full_name = rest
        if paren:
            paren_role = _match_role(paren)
            if paren_role:
                attr.paren_role = paren_role
            else:
                attr.paren_role = paren
        return attr

    # Fallback: single-token attribution (rare — surname-only).
    attr.surname = main
    return attr


# ── Output dataclass ────────────────────────────────────────────────
@dataclass
class ParsedSpeech:
    sequence: int
    speaker_name_raw: str
    speaker_role: Optional[str]
    honorific: Optional[str]
    surname: Optional[str]
    full_name: Optional[str]
    paren_role: Optional[str]
    speech_type: str
    spoken_at: datetime          # UTC
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
    return datetime.combine(sitting_date, t, tzinfo=WINNIPEG_TZ).astimezone(timezone.utc)


# ── Sitting-date extraction from transcript title ───────────────────
# e.g. "3rd Session - 43rd Legislature, Vol. 1, Nov 18, 2025"
_TITLE_DATE_RE = re.compile(
    r"<title>[^<]*?,\s*(?P<mon>\w+)\s+(?P<day>\d{1,2}),\s+(?P<year>\d{4})\s*</title>",
    re.IGNORECASE,
)
_MONTHS_LONG = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7,
    "aug": 8, "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
}
# Fallback: look in the first bold paragraph for "Tuesday, November 18, 2025".
_HEADER_DATE_RE = re.compile(
    r"(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),?\s+"
    r"(?P<mon>\w+)\s+(?P<day>\d{1,2}),\s+(?P<year>\d{4})",
    re.IGNORECASE,
)


def extract_sitting_date(html: str) -> Optional[date]:
    # 1. Modern sittings carry the date in <title>, e.g.
    #    "3rd Session - 43rd Legislature, Vol. 1, Nov 18, 2025".
    m = _TITLE_DATE_RE.search(html)
    if m:
        mon = _MONTHS_LONG.get(m.group("mon").lower())
        if mon:
            try:
                return date(int(m.group("year")), mon, int(m.group("day")))
            except ValueError:
                pass
    # 2. Historical "letter"-variant volumes (vol_NN[a-z]) have a
    #    bare <title>VOL</title>. The sitting-date header appears
    #    in the body but is wrapped in tags ("<b><span>Thursday,
    #    </span></b><b><span>April 24, 2008</span></b>"), so regex
    #    against raw HTML misses it. Strip tags + collapse
    #    whitespace, then look for the first
    #    "Day-of-week, Month DD, YYYY" in the plaintext.
    #    No byte limit — early sittings often have ~30 KB of CSS
    #    font-face definitions before any body content, pushing the
    #    header date past a small window. Full-page strip is cheap.
    stripped = re.sub(r"<[^>]+>", " ", html)
    stripped = re.sub(r"\s+", " ", stripped)
    m = _HEADER_DATE_RE.search(stripped)
    if m:
        mon = _MONTHS_LONG.get(m.group("mon").lower())
        if mon:
            try:
                return date(int(m.group("year")), mon, int(m.group("day")))
            except ValueError:
                pass
    return None


# ── Main extractor ──────────────────────────────────────────────────
@dataclass
class ParseResult:
    url: str
    url_meta: UrlMeta
    sitting_date: date
    speeches: list[ParsedSpeech]
    section_hits: dict[str, int]


# Default sitting time when no <b>*</b> (HH:MM) marker has been seen
# yet. MB afternoon sittings start at 13:30; throne speech mornings
# start at 10:00. We pick 13:30 as a middle-ground fallback.
_DEFAULT_START_TIME = time(13, 30)


def extract_speeches(html_text: str, url: str) -> ParseResult:
    meta = parse_url_meta(url)
    sitting_date = extract_sitting_date(html_text) or date(1970, 1, 1)
    speeches: list[ParsedSpeech] = []
    section_hits: dict[str, int] = {}

    current_time = _DEFAULT_START_TIME
    current_section: Optional[str] = None

    turn_attr: Optional[ParsedAttribution] = None
    turn_name_raw: Optional[str] = None
    turn_body_parts: list[str] = []
    turn_section: Optional[str] = None
    turn_time: time = current_time

    def flush_turn() -> None:
        nonlocal turn_attr, turn_name_raw, turn_body_parts, turn_section, turn_time
        if turn_attr is None or not turn_name_raw:
            turn_attr = None
            turn_name_raw = None
            turn_body_parts = []
            turn_section = None
            return
        text = "\n\n".join(p for p in turn_body_parts if p).strip()
        if not text:
            turn_attr = None
            turn_name_raw = None
            turn_body_parts = []
            turn_section = None
            return
        spoken_at = _localise(sitting_date, turn_time)
        speech = ParsedSpeech(
            sequence=len(speeches) + 1,
            speaker_name_raw=turn_name_raw,
            speaker_role=turn_attr.role,
            honorific=turn_attr.honorific,
            surname=turn_attr.surname,
            full_name=turn_attr.full_name,
            paren_role=turn_attr.paren_role,
            speech_type="floor",
            spoken_at=spoken_at,
            text=text,
            language="en",
            content_hash=_content_hash(text),
            raw={
                "volume": meta.volume,
                "html_id": meta.html_id,
                "section": turn_section,
                "url": url,
                "sitting_time": turn_time.strftime("%H:%M"),
            },
        )
        speeches.append(speech)
        turn_attr = None
        turn_name_raw = None
        turn_body_parts = []
        turn_section = None

    for pm in _P_RE.finditer(html_text):
        attrs = pm.group("attrs") or ""
        inner = pm.group("body")

        # Timestamp marker — <b>*</b> (HH:MM). Updates current_time,
        # does not open a turn. Must be checked BEFORE speaker check
        # so the "*" isn't mistaken for a speaker.
        mt = _TIMESTAMP_RE.match(inner)
        if mt:
            try:
                hh = int(mt.group("h"))
                mm = int(mt.group("m"))
                if 0 <= hh < 24 and 0 <= mm < 60:
                    current_time = time(hh, mm)
            except ValueError:
                pass
            continue

        # Section heading — MsoHeadingN class or pure-bold-no-colon.
        is_head, head_text = _is_heading(attrs, inner)
        if is_head and head_text:
            flush_turn()
            current_section = head_text
            section_hits[head_text] = section_hits.get(head_text, 0) + 1
            continue

        # Speaker line opens a turn.
        m_speaker = _P_OPEN_B_RE.match(inner.strip())
        if m_speaker and _looks_like_speaker_name(m_speaker.group("name")):
            flush_turn()
            raw_name = _clean_speaker(m_speaker.group("name"))
            if not raw_name:
                continue
            attr = parse_attribution(raw_name)
            tail_text = _strip_tags(m_speaker.group("tail"))
            turn_attr = attr
            turn_name_raw = raw_name
            turn_body_parts = [tail_text] if tail_text else []
            turn_section = current_section
            turn_time = current_time
            continue

        # Continuation paragraph — attached to the open turn.
        body_text = _strip_tags(inner)
        if body_text and turn_attr is not None:
            turn_body_parts.append(body_text)

    flush_turn()

    return ParseResult(
        url=url,
        url_meta=meta,
        sitting_date=sitting_date,
        speeches=speeches,
        section_hits=section_hits,
    )
