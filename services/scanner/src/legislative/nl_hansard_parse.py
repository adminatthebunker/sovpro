"""Newfoundland & Labrador Hansard HTML parser — era-branching.

Speaker-turn extractor for:

    /HouseBusiness/Hansard/ga{GA}session{S}/{YY}-{MM}-{DD}.htm[l]

Two distinct HTML eras coexist in the corpus; the parser sniffs which
one a given transcript is and dispatches accordingly.

## Modern era — Word-exported HTML (GA 45+, 2004-ish onwards)

Clean ``<p class="MsoNormal">`` paragraphs. Speaker turns open with a
``<strong>`` wrapping an inner ``<span>``:

    <p class="MsoNormal"><strong><span style="...">SPEAKER (Lane): </span>
    </strong><span style="...">Order, please!</span></p>

The paragraph ends in a period; the next `<p class="MsoNormal">` is
either another speaker turn or a continuation paragraph for the
current turn (plain ``<span>`` with no ``<strong>`` prefix).

## Legacy era — FrontPage 3.0 export (GA 44 and earlier HTML)

Malformed markup: FrontPage leaves a dangling ``<b>`` at the end of
one paragraph and its closing ``</b>`` mid-text of the next. So a
legacy speaker turn looks like:

    <p>&nbsp;<b></p>
    <p>MR. SPEAKER (Snow): </b>Order, please!</p>
    <p>MR. MATTHEWS:</b> Thank you, Mr. Speaker.</p>

The load-bearing signature is ``NAME:</b>`` mid-paragraph. We match
that without caring about the dangling opening tag (if any).

## Speaker attribution shapes

Both eras share the same vocabulary; only the tag wrapper differs.

    S. O'LEARY:              → first-initial + surname (modern norm)
    MR. MATTHEWS:            → title + surname (legacy norm)
    PREMIER WAKEHAM:         → title + surname
    MINISTER OF FINANCE:     → role-only
    SPEAKER:                 → presiding role
    SPEAKER (Lane):          → presiding + parens-name disambiguator
    MR. SPEAKER (Snow):      → title + role + parens-name (legacy)
    SOME HON. MEMBERS:       → group chant / applause (speech_type='group')
    AN HON. MEMBER:          → anonymous member
    CLERK:                   → table officer

No riding, no party is ever carried inline — this is why NL speaker
resolution cannot use the riding disambiguation trick other
jurisdictions rely on.

## Sitting-date extraction

Three sources, tried in order:
  1. URL filename — ``{YY}-{MM}-{DD}`` is authoritative when the URL
     is well-formed.
  2. ``<title>`` tag — modern: ``April 21``; legacy: ``Hansard - November 17, 1999``.
  3. First header paragraph — ``April 21, 2026 HOUSE OF ASSEMBLY PROCEEDINGS``.

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

# NL is on Newfoundland Time (UTC-3:30 / -2:30 DST).
NL_TZ = ZoneInfo("America/St_Johns")

# NL afternoon sittings start at 13:30; Blues often appear same-evening.
_DEFAULT_SITTING_TIME = time(13, 30)


# ── URL parsing ─────────────────────────────────────────────────────
# /HouseBusiness/Hansard/ga{GA}session{S}/{YY}-{MM}-{DD}[{Label}].htm[l]
_URL_META_RE = re.compile(
    r"/Hansard/ga(?P<ga>\d+)session(?P<session>\d+)/"
    r"(?P<yy>\d{2})-(?P<mm>\d{2})-(?P<dd>\d{2})"
    r"(?P<label>[A-Za-z][A-Za-z0-9]*)?"
    r"\.html?$",
    re.IGNORECASE,
)


@dataclass
class UrlMeta:
    ga: int
    session: int
    sitting_date: date
    label: Optional[str]  # e.g. "SwearingIn", "ElectionofSpeaker"; None for regular sittings


def _yy_to_year(yy: int) -> int:
    # Hansard HTML era starts GA 34 (1970); FrontPage era 1999+. Anything
    # yy in (70, 99) → 1900s; yy in (00, 69) → 2000s. Gives us headroom
    # through 2069.
    return 1900 + yy if yy >= 70 else 2000 + yy


def parse_url_meta(url: str) -> UrlMeta:
    m = _URL_META_RE.search(url)
    if not m:
        raise ValueError(f"not a recognized NL Hansard URL: {url}")
    yy = int(m.group("yy"))
    mm = int(m.group("mm"))
    dd = int(m.group("dd"))
    return UrlMeta(
        ga=int(m.group("ga")),
        session=int(m.group("session")),
        sitting_date=date(_yy_to_year(yy), mm, dd),
        label=m.group("label") or None,
    )


# ── HTML helpers ────────────────────────────────────────────────────
_TAG_RE = re.compile(r"<[^>]+>", re.DOTALL)
_WS_RE = re.compile(r"\s+")
_NBSP_RE = re.compile(r"&nbsp;|\xa0")


def _decode_entities(s: str) -> str:
    return html_mod.unescape(_NBSP_RE.sub(" ", s))


def _strip_tags(s: str) -> str:
    cleaned = _TAG_RE.sub("", s or "")
    return _WS_RE.sub(" ", _decode_entities(cleaned)).strip()


def _norm(s: str) -> str:
    if not s:
        return ""
    t = unicodedata.normalize("NFKD", s.replace(" ", " "))
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = t.lower()
    t = re.sub(r"[^\w\s-]", " ", t)
    return _WS_RE.sub(" ", t).strip()


# ── Era detection ───────────────────────────────────────────────────
# Modern Word-export transcripts use `class="MsoNormal"` on every
# speech paragraph. Legacy FrontPage transcripts don't use that class
# at all. Sniff once per document.
_MSONORMAL_RE = re.compile(r'class\s*=\s*"?MsoNormal"?', re.IGNORECASE)


def detect_era(html: str) -> str:
    """Return ``"modern"`` or ``"legacy"``."""
    # At least three MsoNormal paragraphs = modern. A single match
    # could be a stray link ref; three is solid.
    hits = len(_MSONORMAL_RE.findall(html, re.IGNORECASE) if False
               else _MSONORMAL_RE.findall(html))
    return "modern" if hits >= 3 else "legacy"


# ── Shared speaker-attribution parsing ──────────────────────────────
# Main/paren split: "SPEAKER (Lane)" / "MR. SPEAKER (Snow)" /
# "MR. JOYCE (Bay of Islands)". NL never uses the paren for a role;
# always a surname or riding.
_PAREN_SPLIT_RE = re.compile(r"^(?P<main>[^()]+?)\s*\((?P<paren>[^()]+)\)\s*$")

# Group / anonymous markers that resolve to speech_type='group' with
# NULL politician_id. NL uses "SOME HON. MEMBERS" (with period) more
# often than "SOME HONOURABLE MEMBERS", so allow an optional period.
_GROUP_RE = re.compile(
    r"^(an|some|several)\s+hon(?:\.|ourable)?\s+members?$",
    re.IGNORECASE,
)

# Presiding / officer roles. The bare "SPEAKER:" form is resolved by
# the presiding_officer_resolver (date-ranged Speaker-term lookup).
_ROLE_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"^(?:mr\.?\s+)?speaker\s*$"),          "The Speaker"),
    (re.compile(r"^madam\s+speaker\s*$"),               "The Speaker"),
    (re.compile(r"^the\s+speaker\s*$"),                 "The Speaker"),
    (re.compile(r"^deputy\s+speaker\s*$"),              "The Deputy Speaker"),
    (re.compile(r"^(?:mr\.?\s+)?chair(?:person)?\s*$"), "The Chair"),
    (re.compile(r"^deputy\s+chair(?:person)?\s*$"),     "The Deputy Chair"),
    (re.compile(r"^clerk\s*$"),                         "The Clerk"),
    (re.compile(r"^sergeant[-\s]at[-\s]arms\s*$"),      "The Sergeant-at-Arms"),
]

# Honorific/title prefixes that sit in front of a surname. Kept as a
# list rather than an alternation to preserve longest-match order.
# Titles require whitespace after — ``\s+(.*)`` forces the optional
# period in ``\.?`` to be consumed by the title group rather than
# leaking into the rest ("MR. J. BYRNE" must yield rest="J. BYRNE",
# not ". J. BYRNE").
_TITLE_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"^(hon\.?\s+the\s+premier)\s+(.*)$", re.IGNORECASE), "Hon. the Premier"),
    (re.compile(r"^(premier)\s+(.*)$",                re.IGNORECASE), "Premier"),
    (re.compile(r"^(hon\.?\s+minister)\s+(.*)$",      re.IGNORECASE), "Hon. Minister"),
    (re.compile(r"^(minister)\s+(.*)$",               re.IGNORECASE), "Minister"),
    (re.compile(r"^(hon\.?\s+mr\.?)\s+(.*)$",         re.IGNORECASE), "Hon. Mr."),
    (re.compile(r"^(hon\.?\s+mrs\.?)\s+(.*)$",        re.IGNORECASE), "Hon. Mrs."),
    (re.compile(r"^(hon\.?\s+ms\.?)\s+(.*)$",         re.IGNORECASE), "Hon. Ms."),
    (re.compile(r"^(hon\.?)\s+(.*)$",                 re.IGNORECASE), "Hon."),
    (re.compile(r"^(mr\.?)\s+(.*)$",                  re.IGNORECASE), "Mr."),
    (re.compile(r"^(mrs\.?)\s+(.*)$",                 re.IGNORECASE), "Mrs."),
    (re.compile(r"^(ms\.?)\s+(.*)$",                  re.IGNORECASE), "Ms."),
    (re.compile(r"^(dr\.?)\s+(.*)$",                  re.IGNORECASE), "Dr."),
]

# Initial-plus-surname pattern — modern NL's compact attribution.
# Matches "S. O'LEARY" / "J. HOGAN" / "B. DAVIS".
_INITIAL_SURNAME_RE = re.compile(
    r"^(?P<init>[A-Z])\.\s+(?P<surname>[A-Z][A-Za-z'’\-]+(?:\s+[A-Z][A-Za-z'’\-]+)*)$"
)

_TRAILING_COLON_RE = re.compile(r"[:\s ]+$")


@dataclass
class ParsedAttribution:
    raw: str                            # as extracted from HTML, trailing colon stripped
    role: Optional[str] = None          # canonical role ("The Speaker", …) or None
    honorific: Optional[str] = None     # "Hon.", "Mr.", "Premier", …
    first_initial: Optional[str] = None # "S." for modern "S. O'LEARY"
    surname: Optional[str] = None       # "O'Leary", "Matthews", …
    paren: Optional[str] = None         # "Lane", "Snow", "Bay of Islands" — surname or riding
    is_group: bool = False              # "SOME HON. MEMBERS"


def _clean_attribution(raw: str) -> str:
    """Strip tags + entities + trailing colon from a raw speaker-label fragment."""
    text = _decode_entities(_TAG_RE.sub("", raw or ""))
    text = _WS_RE.sub(" ", text).strip()
    return _TRAILING_COLON_RE.sub("", text).strip()


def _match_role(text: str) -> Optional[str]:
    n = _norm(text)
    for pat, canonical in _ROLE_PATTERNS:
        if pat.match(n):
            return canonical
    return None


def _titlecase_surname(s: str) -> str:
    # Preserve internal uppercase after apostrophes: O'LEARY → O'Leary, not O'leary.
    def _tok(t: str) -> str:
        if not t:
            return t
        parts = re.split(r"(['’\-])", t)
        return "".join(
            p.capitalize() if i % 2 == 0 else p
            for i, p in enumerate(parts)
        )
    return " ".join(_tok(w) for w in s.split())


def parse_attribution(raw: str) -> ParsedAttribution:
    cleaned = _clean_attribution(raw)
    attr = ParsedAttribution(raw=cleaned)
    if not cleaned:
        return attr

    # Group / anonymous markers.
    if _GROUP_RE.match(cleaned):
        attr.is_group = True
        return attr

    # Split off parens ("SPEAKER (Lane)", "MR. SPEAKER (Snow)",
    # "MR. JOYCE (Bay of Islands)").
    m_paren = _PAREN_SPLIT_RE.match(cleaned)
    main = m_paren.group("main").strip() if m_paren else cleaned
    paren = m_paren.group("paren").strip() if m_paren else None
    if paren:
        attr.paren = paren

    # Role detection on main component first — "SPEAKER" / "THE SPEAKER"
    # / "MR. SPEAKER" / "DEPUTY SPEAKER".
    role = _match_role(main)
    if role:
        attr.role = role
        return attr

    # Initial + surname — "S. O'LEARY".
    m_init = _INITIAL_SURNAME_RE.match(main)
    if m_init:
        attr.first_initial = m_init.group("init") + "."
        attr.surname = _titlecase_surname(m_init.group("surname"))
        return attr

    # Title + rest — "MR. MATTHEWS", "MR. J. BYRNE", "HON. MINISTER OF FINANCE",
    # "PREMIER WAKEHAM".
    for pat, canonical in _TITLE_PATTERNS:
        m_t = pat.match(main)
        if m_t:
            attr.honorific = canonical
            rest = m_t.group(2).strip()
            if not rest:
                # Title-only attribution (e.g. "HON. MINISTER:") — leave
                # surname unset; paren (if any) is often the actual name.
                return attr
            # The rest might itself be a role ("MINISTER OF FINANCE"),
            # an initial+surname ("J. BYRNE" under "MR."), or a bare
            # surname ("MATTHEWS"). Role first, then initial+surname,
            # then fallback.
            inner_role = _match_role(rest)
            if inner_role:
                attr.role = inner_role
                return attr
            m_init_rest = _INITIAL_SURNAME_RE.match(rest)
            if m_init_rest:
                attr.first_initial = m_init_rest.group("init") + "."
                attr.surname = _titlecase_surname(m_init_rest.group("surname"))
                return attr
            # Heuristic: if rest starts with a portfolio preposition
            # ("MINISTER OF FINANCE"), treat as role-only.
            tokens = rest.split()
            if len(tokens) >= 2 and tokens[0].lower() in {"of", "for", "to", "the"}:
                attr.role = canonical + " " + rest
                return attr
            attr.surname = _titlecase_surname(tokens[-1])
            return attr

    # Fallback: single-token attribution — treat as surname.
    tokens = main.split()
    if tokens:
        attr.surname = _titlecase_surname(tokens[-1])
    return attr


# ── Output dataclass ────────────────────────────────────────────────
@dataclass
class ParsedSpeech:
    sequence: int
    speaker_name_raw: str
    speaker_role: Optional[str]
    honorific: Optional[str]
    first_initial: Optional[str]
    surname: Optional[str]
    paren: Optional[str]
    is_group: bool
    speech_type: str             # "floor" or "group"
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
    return datetime.combine(sitting_date, t, tzinfo=NL_TZ).astimezone(timezone.utc)


# ── Sitting-date fallbacks (URL is primary) ─────────────────────────
_TITLE_DATE_RE = re.compile(
    r"<title>[^<]*?(?P<mon>\w+)\s+(?P<day>\d{1,2}),\s+(?P<year>\d{4})[^<]*</title>",
    re.IGNORECASE,
)
_HEADER_DATE_RE = re.compile(
    r"(?P<mon>January|February|March|April|May|June|July|August|September|October|November|December)\s+"
    r"(?P<day>\d{1,2}),\s+(?P<year>\d{4})",
    re.IGNORECASE,
)
_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7,
    "aug": 8, "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
}


def extract_sitting_date_from_html(html: str) -> Optional[date]:
    for rx in (_TITLE_DATE_RE, _HEADER_DATE_RE):
        m = rx.search(html)
        if not m:
            continue
        mon = _MONTHS.get(m.group("mon").lower())
        if not mon:
            continue
        try:
            return date(int(m.group("year")), mon, int(m.group("day")))
        except ValueError:
            continue
    return None


# ── Partial-edited marker (UI-level signal) ─────────────────────────
_PARTIAL_MARKER_RE = re.compile(
    r"PARTIALLY\s+EDITED\s+transcript",
    re.IGNORECASE,
)


def detect_partial(html: str) -> bool:
    return bool(_PARTIAL_MARKER_RE.search(html or ""))


# ── Paragraph walker ────────────────────────────────────────────────
_P_RE = re.compile(r"<p\b(?P<attrs>[^>]*)>(?P<body>.*?)</p>",
                   re.DOTALL | re.IGNORECASE)

# Modern speaker-line opener: a paragraph whose first non-whitespace
# child is <strong>...</strong>. The trailing ":" is checked after
# stripping inner tags.
_MODERN_OPEN_RE = re.compile(
    r"^\s*<strong\b[^>]*>(?P<name>.*?)</strong>(?P<tail>.*)$",
    re.DOTALL | re.IGNORECASE,
)

# Legacy speaker-line: "NAME:</b> rest". We don't require an opening
# <b> because FrontPage leaves it on the prior paragraph.
_LEGACY_OPEN_RE = re.compile(
    r"^\s*(?:&nbsp;|\s)*(?:<b\b[^>]*>)?(?P<name>[^<]{1,160}?):\s*</b>(?P<tail>.*)$",
    re.DOTALL | re.IGNORECASE,
)

# Heading detection:
#   - Modern: <p class="MsoNormal" align="center" ...><strong><u>Statements by Members</u></strong></p>
#   - Legacy: <p ALIGN="CENTER">Statements by Ministers</u></p>
_CENTER_ATTR_RE = re.compile(r'align\s*=\s*"?center"?', re.IGNORECASE)
_HEADING_INNER_RE = re.compile(
    r"<u\b[^>]*>(?P<body>.*?)</u>",
    re.IGNORECASE | re.DOTALL,
)


def _is_heading(attrs: str, inner_html: str) -> tuple[bool, Optional[str]]:
    if not _CENTER_ATTR_RE.search(attrs or ""):
        return False, None
    m = _HEADING_INNER_RE.search(inner_html or "")
    if not m:
        # Some legacy centered paragraphs have a dangling `</u>` with
        # no opener — fall back to full-text body.
        body = _strip_tags(inner_html)
        if body and len(body) < 80 and ":" not in body:
            return True, body
        return False, None
    body = _strip_tags(m.group("body"))
    if not body or len(body) > 120 or body.endswith(":"):
        return False, None
    return True, body


def _is_speaker_candidate(name_plain: str) -> bool:
    """Filter out non-speaker <strong> blocks (intro notices, volume
    headers, inline emphasis).

    NL Hansard speaker lines are:
      - ALL-CAPS ("S. O'LEARY:", "SPEAKER:", "PREMIER WAKEHAM:"),
      - short (< 80 chars before the colon),
      - structurally parseable by parse_attribution into something
        meaningful (role / surname / group).

    Intro boilerplate like "Please be advised that this is a PARTIALLY
    EDITED transcript..." fails the all-caps check; volume headers like
    "April 21, 2026 HOUSE OF ASSEMBLY PROCEEDINGS Vol. LI No. 16" fail
    the colon check; "This can be accessed at:" fails the all-caps check.
    """
    n = (name_plain or "").strip()
    if not n.endswith(":"):
        return False
    body = n[:-1].strip()
    if not body or len(body) > 80:
        return False
    # All-caps dominance — tolerate honorific periods, apostrophes,
    # parens, digits (though digits are rare in real speaker lines).
    letters = [c for c in body if c.isalpha()]
    if not letters:
        return False
    upper_ratio = sum(1 for c in letters if c.isupper()) / len(letters)
    if upper_ratio < 0.8:
        return False
    # Structurally parseable as a known attribution shape.
    attr = parse_attribution(body)
    return bool(
        attr.role or attr.surname or attr.is_group or attr.honorific
    )


# ── Main extractor ──────────────────────────────────────────────────
@dataclass
class ParseResult:
    url: str
    url_meta: UrlMeta
    sitting_date: date
    era: str
    partial: bool
    speeches: list[ParsedSpeech]
    section_hits: dict[str, int]


def _dispatch_open(era: str, inner: str) -> Optional[tuple[str, str]]:
    """Return (raw_name_with_colon, tail_html) if this paragraph opens a
    speaker turn; None otherwise.
    """
    if era == "modern":
        m = _MODERN_OPEN_RE.match(inner)
        if not m:
            return None
        name_plain = _strip_tags(m.group("name"))
        # Modern <strong> blocks frequently wrap non-speaker content
        # (volume headers, partial-edited notices, inline emphasis that
        # happens to trail with ":"). Gate on an all-caps + attribution
        # structural check.
        if not _is_speaker_candidate(name_plain):
            return None
        return name_plain, m.group("tail") or ""
    # Legacy.
    m = _LEGACY_OPEN_RE.match(inner)
    if not m:
        return None
    name_plain = _strip_tags(m.group("name")) + ":"
    if not _is_speaker_candidate(name_plain):
        return None
    return name_plain, m.group("tail") or ""


def extract_speeches(html_text: str, url: str) -> ParseResult:
    meta = parse_url_meta(url)
    era = detect_era(html_text)
    partial = detect_partial(html_text)
    sitting_date = (
        meta.sitting_date or extract_sitting_date_from_html(html_text) or date(1970, 1, 1)
    )

    speeches: list[ParsedSpeech] = []
    section_hits: dict[str, int] = {}

    current_section: Optional[str] = None
    current_time = _DEFAULT_SITTING_TIME

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
        speech_type = "group" if turn_attr.is_group else "floor"
        speech = ParsedSpeech(
            sequence=len(speeches) + 1,
            speaker_name_raw=turn_name_raw,
            speaker_role=turn_attr.role,
            honorific=turn_attr.honorific,
            first_initial=turn_attr.first_initial,
            surname=turn_attr.surname,
            paren=turn_attr.paren,
            is_group=turn_attr.is_group,
            speech_type=speech_type,
            spoken_at=spoken_at,
            text=text,
            language="en",
            content_hash=_content_hash(text),
            raw={
                "ga":           meta.ga,
                "session":      meta.session,
                "section":      turn_section,
                "url":          url,
                "era":          era,
                "sitting_time": turn_time.strftime("%H:%M"),
                "paren":        turn_attr.paren,
            },
        )
        speeches.append(speech)
        turn_attr = None
        turn_name_raw = None
        turn_body_parts = []
        turn_section = None

    for pm in _P_RE.finditer(html_text):
        attrs = pm.group("attrs") or ""
        inner = pm.group("body") or ""

        # Section heading (centered + underlined).
        is_head, head_text = _is_heading(attrs, inner)
        if is_head and head_text:
            flush_turn()
            current_section = head_text
            section_hits[head_text] = section_hits.get(head_text, 0) + 1
            continue

        # Speaker-line opens a turn.
        dispatched = _dispatch_open(era, inner)
        if dispatched is not None:
            raw_name_plain, tail_html = dispatched
            flush_turn()
            attr = parse_attribution(raw_name_plain)
            if not attr.raw:
                continue
            turn_attr = attr
            turn_name_raw = raw_name_plain
            turn_body_parts = []
            tail_text = _strip_tags(tail_html)
            if tail_text:
                turn_body_parts.append(tail_text)
            turn_section = current_section
            turn_time = current_time
            continue

        # Continuation paragraph — attach to open turn.
        body_text = _strip_tags(inner)
        if body_text and turn_attr is not None:
            turn_body_parts.append(body_text)

    flush_turn()

    return ParseResult(
        url=url,
        url_meta=meta,
        sitting_date=sitting_date,
        era=era,
        partial=partial,
        speeches=speeches,
        section_hits=section_hits,
    )
