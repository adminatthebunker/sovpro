"""Ontario Hansard HTML parser — sitting transcript HTML → ParsedSpeech list.

The Ontario Legislative Assembly publishes daily Hansard transcripts at

    https://www.ola.org/en/legislative-business/house-documents/
        parliament-{P}/session-{S}/{YYYY-MM-DD}/hansard

as Drupal nodes of type ``hansard_document``. The transcript body lives
inside ``body.value`` on the JSON serialization (``?_format=json``), but
the body itself is HTML — the same HTML you'd get from rendering the
page. This parser takes that HTML body string and returns a list of
ParsedSpeech rows.

## Markup shape

Every speaker turn is a ``<p>`` with class ``speakerStart`` whose first
inner element is a ``<strong>`` carrying the speaker attribution,
terminated by a colon. The speech body follows the closing ``</strong>``
up to the closing ``</p>``::

    <p class="speakerStart"><span id="para49"/><strong>Hon. Edith Dumont (Lieutenant Governor):</strong> Pray be seated.</p>

The ``<span id="paraN"/>`` is a navigation anchor — Drupal serializes it
as a self-closing element. Parser tolerates either present or absent.

Procedural / stage-direction notes use ``<p class="procedure">`` and are
**skipped** — they're things like "Her Honour was then pleased to retire"
that don't belong as speeches.

## Attribution shapes

ON has more variety than NS because there are no per-speaker slug
anchors — the prose carries the full attribution:

  * ``Hon. Stephen Crawford:`` — minister / government member
  * ``Mr. Steve Clark:`` / ``Ms. Laurie Scott:`` / ``Mrs. X:`` / ``Madam Y:``
  * ``The Speaker (Hon. Donna Skelly):`` — presiding officer with the
    actual speaker's name in parens. Parser extracts the parens content
    as ``parens_name`` and prefers it for resolution.
  * ``The Acting Speaker (Mr. Smith):`` — same pattern, different role.
  * ``The Deputy Speaker (Mr. Smith):`` — same pattern, different role.
  * ``The Clerk of the Assembly (Mr. Trevor Day):`` — clerk + name.
  * ``The Speaker:`` (no parens) — bare role; defer to
    presiding-officer resolver via the SPEAKER_ROSTER.

## Sitting date

Comes from the parent JSON node's ``field_date`` (passed in by the
orchestrator) — exact and authoritative. We don't try to scrape it from
the body HTML the way NS does (NS's title carries it; ON's body doesn't).

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

TORONTO_TZ = ZoneInfo("America/Toronto")


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
    t = unicodedata.normalize("NFKD", s.replace(" ", " "))
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = t.lower()
    t = re.sub(r"[^\w\s-]", " ", t)
    return _WS_RE.sub(" ", t).strip()


# ── Speaker turn detection ──────────────────────────────────────────
# Match <p class="speakerStart"...><strong>{attr}:</strong>{body}</p>.
# - The optional <span id="paraN"/> nav-anchor sits between the <p>
#   open and the <strong>. We tolerate it (or any other inline tag)
#   in the head slot.
# - speakerStart is the canonical class. Confirmed in probe 8 against
#   2025-04-14 sitting.
_TURN_OPENER_RE = re.compile(
    r"<p\b[^>]*class=\"[^\"]*\bspeakerStart\b[^\"]*\"[^>]*>"  # <p class="...speakerStart...">
    r"\s*(?:<[^>]+>\s*)*"                                     # optional inline tags (e.g. <span/>)
    r"<strong\b[^>]*>(?P<attr_inner>.*?)</strong>"            # <strong>ATTR</strong> (lazy)
    r"(?P<body>.*?)"                                          # body up to closing </p>
    r"</p>",
    re.IGNORECASE | re.DOTALL,
)


# ── Attribution parsing ─────────────────────────────────────────────
# Honorific opener — case-insensitive, followed by a name.
# MPP (Member of Provincial Parliament) is ON-specific and used as a
# bare prefix on some attributions, e.g. "MPP Lisa Gretzky:".
_HONORIFIC_RE = re.compile(
    r"^(?P<hon>hon\.|hon|honourable|mr\.|mrs\.|ms\.|miss\.?|dr\.?|madam|sir|mpp)\s+"
    r"(?P<rest>.+)$",
    re.IGNORECASE,
)

# Role patterns — match against the OUTER attribution after parens
# stripped. ON publishes "The Speaker", "The Acting Speaker",
# "The Deputy Speaker" plus ad-hoc clerk / officer roles, AND the
# legacy "Madam Speaker" / "Mr. Speaker" forms used in older transcripts.
_ROLE_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"^the\s+speaker$"),                      "The Speaker"),
    (re.compile(r"^madam\s+speaker$"),                    "The Speaker"),
    (re.compile(r"^madame\s+speaker$"),                   "The Speaker"),
    (re.compile(r"^mister\s+speaker$"),                   "The Speaker"),
    (re.compile(r"^mr\.?\s+speaker$"),                    "The Speaker"),
    (re.compile(r"^the\s+acting\s+speaker$"),             "The Acting Speaker"),
    (re.compile(r"^the\s+deputy\s+speaker$"),             "The Deputy Speaker"),
    (re.compile(r"^the\s+chair$"),                        "The Chair"),
    (re.compile(r"^the\s+clerk(?:\s+of\s+the\s+assembly)?$"), "The Clerk"),
    (re.compile(r"^the\s+sergeant[-\s]at[-\s]arms$"),     "The Sergeant-at-Arms"),
    # Fall-through in caller: anything starting with "The " is a role we
    # don't specifically recognise but is still a role-only attribution.
]

# Attribution carrying an inline "(name)" — e.g. "The Speaker (Hon. Donna Skelly)".
# We capture the parens content for parens_name extraction.
_ROLE_WITH_PARENS_RE = re.compile(
    r"^(?P<role>.+?)\s*\((?P<parens>[^)]+)\)\s*$",
)


@dataclass
class ParsedAttribution:
    """Decomposed speaker attribution.

    Two mutually-coherent shapes:
      - Person attribution: ``role`` is None, ``full_name`` is set.
      - Role attribution: ``role`` is set; ``full_name`` may also be set
        if there was a parens-name (e.g. "The Speaker (Hon. Donna Skelly)").
    """
    raw: str                          # original attribution as published
    role: Optional[str]               # canonical role (e.g. "The Speaker") or None
    parens_inner_raw: Optional[str]   # raw parens content if any (e.g. "Hon. Donna Skelly")
    honorific: Optional[str]          # parsed honorific (Hon./Mr./Ms./Mrs./Madam/Dr./Sir)
    surname: Optional[str]
    given_names: Optional[str]
    full_name: Optional[str]          # title-cased "First Last" (after honorific strip)


def _title_case_person(text: str) -> str:
    """Title-case a person name, preserving hyphenated surnames.

    "stephen crawford" → "Stephen Crawford"
    "smith-jones"      → "Smith-Jones"
    """
    out_parts: list[str] = []
    for word in text.split():
        parts = word.split("-")
        parts = [p.capitalize() for p in parts]
        out_parts.append("-".join(parts))
    return " ".join(out_parts)


def _decompose_person(name_text: str) -> tuple[Optional[str], Optional[str], Optional[str], Optional[str]]:
    """Return (honorific, given_names, surname, full_name) from a name string.

    Strips a leading honorific if present, then title-cases the rest and
    splits the trailing word as surname.
    """
    cleaned = _WS_RE.sub(" ", name_text).strip()
    honorific: Optional[str] = None
    m_hon = _HONORIFIC_RE.match(cleaned)
    if m_hon:
        honorific = m_hon.group("hon").title()
        rest = m_hon.group("rest").strip()
    else:
        rest = cleaned
    pretty = _title_case_person(rest)
    tokens = pretty.split()
    if not tokens:
        return honorific, None, None, None
    surname = tokens[-1]
    given = " ".join(tokens[:-1]) if len(tokens) > 1 else None
    return honorific, given, surname, pretty


def parse_attribution(raw_attr: str) -> ParsedAttribution:
    """Decompose a Hansard attribution string.

    Handles:
      - Plain honorific names: "Hon. Stephen Crawford" → person.
      - Role + parens: "The Speaker (Hon. Donna Skelly)" → role + person
        (the parens person is the actual speaker; role is metadata).
      - Bare roles: "The Speaker" → role only.
      - Bare names without honorific (rare): "Steve Clark" → person.
    """
    # Strip the trailing ":" if present (parser sometimes catches it),
    # plus any HTML entities the regex might have left behind.
    cleaned = _decode_entities(raw_attr).rstrip(":").strip()
    cleaned = _WS_RE.sub(" ", cleaned)

    # Try role-with-parens first: "The Speaker (Hon. Donna Skelly)".
    # We only enter this path when the OUTER part is a role ("The X").
    # Otherwise the outer is the actual speaker (e.g.
    # "Hon. Edith Dumont (Lieutenant Governor)") and the parens is just
    # metadata — handled by the plain person path below.
    m_parens = _ROLE_WITH_PARENS_RE.match(cleaned)
    if m_parens and m_parens.group("role").strip().lower().startswith("the "):
        role_raw = m_parens.group("role").strip()
        parens_inner = m_parens.group("parens").strip()
        role_lower = role_raw.lower()
        canonical_role: Optional[str] = role_raw  # default: keep raw "The X"
        for pat, can in _ROLE_PATTERNS:
            if pat.match(role_lower):
                canonical_role = can
                break
        # Decompose the parens person — that's the actual speaker.
        hon, given, surname, full = _decompose_person(parens_inner)
        return ParsedAttribution(
            raw=cleaned,
            role=canonical_role,
            parens_inner_raw=parens_inner,
            honorific=hon,
            surname=surname,
            given_names=given,
            full_name=full,
        )

    # No role-with-parens match. Could be a bare role, a person, or a
    # person with metadata parens like "Hon. Edith Dumont (Lieutenant Governor)".

    # If parens are present and the OUTER is a person (not a role),
    # strip the parens for person decomposition but keep the inner as
    # parens_inner_raw metadata on the result.
    parens_inner_meta: Optional[str] = None
    person_text = cleaned
    if m_parens:
        parens_inner_meta = m_parens.group("parens").strip()
        person_text = m_parens.group("role").strip()

    lower = person_text.lower()
    for pat, can in _ROLE_PATTERNS:
        if pat.match(lower):
            return ParsedAttribution(
                raw=cleaned, role=can,
                parens_inner_raw=parens_inner_meta,
                honorific=None, surname=None, given_names=None, full_name=None,
            )
    # Bare "The X" we don't recognise — treat as role.
    if lower.startswith("the "):
        return ParsedAttribution(
            raw=cleaned, role=person_text,
            parens_inner_raw=parens_inner_meta,
            honorific=None, surname=None, given_names=None, full_name=None,
        )

    # Plain person attribution (with parens metadata stripped if present).
    hon, given, surname, full = _decompose_person(person_text)
    return ParsedAttribution(
        raw=cleaned, role=None,
        parens_inner_raw=parens_inner_meta,
        honorific=hon, surname=surname, given_names=given, full_name=full,
    )


# ── Output dataclass ────────────────────────────────────────────────
@dataclass
class ParsedSpeech:
    sequence: int
    speaker_name_raw: str               # original attribution string (no trailing colon)
    speaker_role: Optional[str]         # "The Speaker" / "The Acting Speaker" / None
    parens_name: Optional[str]          # raw parens content for role attributions
    honorific: Optional[str]
    surname: Optional[str]
    full_name: Optional[str]            # title-cased "First Last" (after honorific strip)
    speech_type: str                    # "floor"
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


# ── Language detection ──────────────────────────────────────────────
# ON Hansard publishes a single bilingual transcript. ~3% of turns are
# in French (francophone MPPs like France Gélinas, Guy Bourgouin).
# We tag each speech with its primary language so search can filter by
# language and embeddings stay honest about content language.
#
# Heuristic: a small set of high-frequency French stopwords. If a turn's
# text hits >= 2 distinct stopwords, it's French. False positives for
# English turns that quote a French phrase are acceptable (rare).
_FR_STOPWORDS = (
    " le ", " la ", " les ", " des ", " une ", " un ", " du ",
    " que ", " qui ", " pour ", " avec ", " sur ", " ce ", " ces ",
    " est ", " sont ", " être ", " mais ", " merci ",
    " madame ", " monsieur ", " député", " gouvernement ",
)


def _detect_language(text: str) -> str:
    """Return 'fr' if the text reads as French, else 'en'."""
    haystack = " " + text.lower() + " "
    hits = sum(1 for w in _FR_STOPWORDS if w in haystack)
    return "fr" if hits >= 2 else "en"


def _localise(sitting_date: date, t: time) -> datetime:
    return datetime.combine(sitting_date, t, tzinfo=TORONTO_TZ).astimezone(timezone.utc)


# Default sitting time when we have no per-speech timestamp. ON sittings
# often start at 09:00 (morning) or 13:00 (afternoon); we pick 09:00 as
# a deterministic fallback. Only the date is semantically load-bearing
# for search filters — exact time is captured at-ingest if present.
_DEFAULT_START_TIME = time(9, 0)


# ── Main extractor ──────────────────────────────────────────────────
@dataclass
class ParseResult:
    url: str
    sitting_date: date
    speeches: list[ParsedSpeech]


def extract_speeches(
    body_html: str, *, sitting_url: str, sitting_date: date,
) -> ParseResult:
    """Parse the body HTML of an ON Hansard sitting into ParsedSpeech list.

    `body_html` is the inner HTML of the transcript body — typically
    obtained from the JSON node's ``body.value`` field. `sitting_date`
    comes from the parent JSON node's ``field_date``.
    """
    spoken_at = _localise(sitting_date, _DEFAULT_START_TIME)
    speeches: list[ParsedSpeech] = []

    for m in _TURN_OPENER_RE.finditer(body_html):
        attr_inner = m.group("attr_inner")
        body_raw = m.group("body")

        # The <strong> may contain inline tags (e.g. <span id=Pxxxx/>).
        # Strip them to get the bare attribution string.
        attr_text = _strip_tags(attr_inner)
        if not attr_text:
            continue
        # Drop a trailing colon that lives inside <strong>...</strong>
        # (most ON markup includes the colon inside the strong tag).
        attr_text = attr_text.rstrip(":").strip()
        if not attr_text:
            continue

        attr = parse_attribution(attr_text)

        # Body extraction: drop leading whitespace + optional ":" that
        # sometimes sits OUTSIDE the </strong> instead of inside it.
        body_clean = re.sub(r"^\s*:\s*", "", body_raw, count=1)
        # Strip nav anchors that point to in-page paragraph IDs.
        body_clean = re.sub(
            r"<a\b[^>]*\bhref=\"#[A-Za-z0-9_]+\"[^>]*>[^<]*</a>",
            "",
            body_clean,
            flags=re.IGNORECASE,
        )
        # Preserve paragraph boundaries before tag-stripping.
        body_clean = re.sub(
            r"</(?:p|blockquote|div|li|tr|h[1-6])\s*>",
            "\n",
            body_clean,
            flags=re.IGNORECASE,
        )
        text = _strip_tags(body_clean)
        text_paras = [p.strip() for p in text.split("\n")]
        text = "\n\n".join(p for p in text_paras if p)
        if not text:
            continue

        speech = ParsedSpeech(
            sequence=len(speeches) + 1,
            speaker_name_raw=attr.raw,
            speaker_role=attr.role,
            parens_name=attr.parens_inner_raw,
            honorific=attr.honorific,
            surname=attr.surname,
            full_name=attr.full_name,
            speech_type="floor",
            spoken_at=spoken_at,
            text=text,
            language=_detect_language(text),
            content_hash=_content_hash(text),
            raw={
                "url": sitting_url,
                "sitting_date": sitting_date.isoformat(),
                "role": attr.role,
                "parens_inner_raw": attr.parens_inner_raw,
                "honorific": attr.honorific,
                "surname": attr.surname,
                "full_name": attr.full_name,
            },
        )
        speeches.append(speech)

    return ParseResult(
        url=sitting_url,
        sitting_date=sitting_date,
        speeches=speeches,
    )
