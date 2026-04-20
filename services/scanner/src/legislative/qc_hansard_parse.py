"""Quebec Hansard HTML parser — Journal des débats → ParsedSpeech list.

The Assemblée nationale publishes daily transcripts at a predictable URL:

    /fr/travaux-parlementaires/assemblee-nationale/{parl}-{sess}
        /journal-debats/{YYYYMMDD}/{doc_id}.html

The markup is refreshingly simple compared to BC's multi-class HDMS: every
speaker turn is a `<p>` whose *first* non-whitespace child is `<b>Name : </b>`,
followed by the speech text inline. Continuation paragraphs are plain
`<p style="text-align: justify">...</p>` without a `<b>` prefix — no
`SpeakerContinues` marker class. Section headings use centered bold that
does *not* end with a colon; that's the one-bit signal we use to distinguish
a speaker line from a heading.

## Speaker-attribution shapes observed

  M. Ciccone                      → honorific + surname
  Mme Charest                     → honorific + surname
  La Présidente                   → role (Speaker)
  Le Président                    → role (Speaker)
  La Vice-Présidente              → role (Deputy Speaker)
  La Vice-Présidente (Mme Soucy)  → role + parenthetical person
  M. Legault (chef du gouvernement) → person + parenthetical role
  Le Premier ministre             → role
  Le ministre de la Santé         → role
  Des voix / Une voix             → group / anonymous

Non-breaking space (`\\xa0`) appears between honorific and surname and
before the trailing colon. Newlines inside the `<b>…</b>` body are common.

## Output

`ParsedSpeech` — one per speaker turn. Headings, editorial comments
(`(Applaudissements)`, `(Suspension de la séance)`), and vote tallies
flow into the preceding speaker's body text (matching BC's convention).

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
from typing import Iterator, Optional
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)

MONTREAL_TZ = ZoneInfo("America/Montreal")


# ── URL parsing ─────────────────────────────────────────────────────
# /fr/travaux-parlementaires/assemblee-nationale/{parl}-{sess}/journal-debats/{YYYYMMDD}/{doc_id}.html
_URL_META_RE = re.compile(
    r"/assemblee-nationale/(?P<parl>\d+)-(?P<sess>\d+)/"
    r"journal-debats/(?P<date>\d{8})/(?P<doc>\d+)\.html",
    re.IGNORECASE,
)


@dataclass
class UrlMeta:
    sitting_date: date
    parliament: int
    session: int
    document_id: str


def parse_url_meta(url: str) -> UrlMeta:
    m = _URL_META_RE.search(url)
    if not m:
        raise ValueError(f"not a recognized QC Hansard URL: {url}")
    return UrlMeta(
        sitting_date=datetime.strptime(m.group("date"), "%Y%m%d").date(),
        parliament=int(m.group("parl")),
        session=int(m.group("sess")),
        document_id=m.group("doc"),
    )


# ── HTML helpers ────────────────────────────────────────────────────
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _strip_tags(s: str) -> str:
    return _WS_RE.sub(" ", html_mod.unescape(_TAG_RE.sub("", s))).strip()


def _norm(s: str) -> str:
    """Lowercase, strip accents, reduce punctuation, collapse whitespace."""
    if not s:
        return ""
    t = unicodedata.normalize("NFKD", s.replace("\u00a0", " "))
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = t.lower()
    t = re.sub(r"[^\w\s-]", " ", t)
    return _WS_RE.sub(" ", t).strip()


# ── Paragraph iterator ──────────────────────────────────────────────
# Matches <p> with optional attributes; captures body (possibly multi-line).
_P_RE = re.compile(r"<p\b(?P<attrs>[^>]*)>(?P<body>.*?)</p>", re.DOTALL | re.IGNORECASE)
# Main-content scope — QC wraps the actual Hansard in #ctl00_ColCentre_ContenuColonneGauche,
# but a simpler heuristic works: the article body sits between the "Journal des débats" h2
# and the "Document(s) associé(s)" h2. Use those as start/end anchors.
_SCOPE_START_RE = re.compile(r'<h2[^>]*>\s*Journal des d[ée]bats\s*</h2>', re.IGNORECASE)
_SCOPE_END_RE = re.compile(
    r'<h2[^>]*>\s*Document\(s\)\s+associ[ée]\(s\)', re.IGNORECASE,
)


def _scoped_body(html_text: str) -> str:
    """Return the slice of HTML between the content header and the trailing doc list.

    Falls back to the whole document when anchors aren't found — some older
    transcripts may not carry the "Journal des débats" h2."""
    start = _SCOPE_START_RE.search(html_text)
    if not start:
        return html_text
    tail = html_text[start.end():]
    end = _SCOPE_END_RE.search(tail)
    if end:
        return tail[: end.start()]
    return tail


# ── Speaker parsing ─────────────────────────────────────────────────
# A paragraph opens a speaker turn iff its *first* child is <b>…</b> AND the
# content inside the <b> ends with a colon (optionally preceded by NBSP/ws).
# This disambiguates speaker lines from section headings that are also bold.
_P_OPEN_B_RE = re.compile(
    r"^\s*<b\b[^>]*>(?P<name>.*?)</b>(?P<tail>.*)$",
    re.DOTALL,
)
_TRAILING_COLON_RE = re.compile(r"[:\s\u00a0]+$")


def _looks_like_speaker_name(name_text: str) -> bool:
    """True if the <b>…</b> content ends with a colon (speaker), else heading."""
    cleaned = html_mod.unescape(_TAG_RE.sub("", name_text))
    cleaned = cleaned.replace("\u00a0", " ")
    return bool(re.search(r":\s*$", cleaned))


def _clean_speaker(name_text: str) -> str:
    """Strip tags, NBSPs, and trailing colon from the <b>…</b> content."""
    text = html_mod.unescape(_TAG_RE.sub("", name_text))
    text = text.replace("\u00a0", " ")
    text = _WS_RE.sub(" ", text).strip()
    return _TRAILING_COLON_RE.sub("", text).strip()


# Role patterns (case-insensitive, accent-agnostic after _norm).
#   le president / la presidente                          → 'Le Président'
#   le vice-president / la vice-presidente / le 1er vp    → 'Le Vice-Président'
#   le premier ministre / la premiere ministre            → 'Le Premier ministre'
#   le ministre …                                          → 'Le Ministre'
#   le leader … / la leader …                              → 'Le Leader'
#   le whip … / la whip …                                  → 'Le Whip'
#   le chef …                                              → 'Le Chef'
#   des voix / une voix                                    → group role
_ROLE_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # Presiding officers — single-person-at-a-time, resolved via date-ranged terms.
    (re.compile(r"^(?:le|la)\s+president(?:e)?$"),                    "Le Président"),
    (re.compile(r"^(?:le|la)\s+vice[-\s]president(?:e)?$"),            "Le Vice-Président"),
    (re.compile(r"^(?:le|la)\s+(?:1er|1re|premier|premiere)\s+"
                r"vice[-\s]president(?:e)?$"),                         "Le Vice-Président"),
    (re.compile(r"^(?:le|la)\s+deuxieme\s+vice[-\s]president(?:e)?$"), "Le Vice-Président"),
    (re.compile(r"^(?:le|la)\s+troisieme\s+vice[-\s]president(?:e)?$"),"Le Vice-Président"),
    # Chair of Committee of the Whole — analogous to Committee Chair in AB/BC.
    (re.compile(r"^(?:le|la)\s+president(?:e)?\s+de\s+commission"),    "Le Président de commission"),
    # Head of government — either person resolution (by paren) or role-only.
    (re.compile(r"^(?:le|la)\s+(?:premier|premiere)\s+ministre.*$"),   "Le Premier ministre"),
    # Cabinet / leadership — role-only attributions.
    (re.compile(r"^(?:le|la)\s+ministre\b.*$"),                        "Le Ministre"),
    (re.compile(r"^(?:le|la)\s+leader\b.*$"),                          "Le Leader"),
    (re.compile(r"^(?:le|la)\s+whip\b.*$"),                            "Le Whip"),
    (re.compile(r"^(?:le|la)\s+chef\b.*$"),                            "Le Chef"),
    # Secretary / Clerk roles — not MNAs.
    (re.compile(r"^(?:le|la)\s+secretaire\b.*$"),                      "Le Secrétaire"),
    # Anonymous / group.
    (re.compile(r"^des\s+voix$"),                                       "Des voix"),
    (re.compile(r"^une\s+voix$"),                                       "Une voix"),
]

# Main/paren split: "La Vice-Présidente (Mme Soucy)" or "M. Legault (chef du gouvernement)"
_PAREN_SPLIT_RE = re.compile(r"^(?P<main>[^()]+?)\s*\((?P<paren>[^()]+)\)\s*$")

# Honorifics at the start of a person attribution.
_HONORIFIC_RE = re.compile(
    r"^(?P<hon>M\.|Mme|Mlle|Dr\.?|Me)\s+(?P<rest>.+)$",
)


@dataclass
class ParsedAttribution:
    raw: str
    role: Optional[str]       # Canonical role string, e.g. "Le Président", "La Vice-Présidente"
    honorific: Optional[str]  # "M.", "Mme", …
    surname: Optional[str]    # e.g. "Charest", "de Sève" (spaces preserved)
    # Parenthetical name when the main form is a role, e.g.
    # "La Vice-Présidente (Mme Soucy)" → paren_honorific="Mme", paren_surname="Soucy"
    paren_honorific: Optional[str] = None
    paren_surname: Optional[str] = None
    # Constituency hint from a non-role parenthetical on a person attribution,
    # e.g. "M. Lévesque (Chapleau)" → constituency_hint="Chapleau". Load-bearing
    # for disambiguating MNAs who share a surname (Lévesque, Bélanger, Roy…).
    constituency_hint: Optional[str] = None


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
    # Normalise honorific casing
    hon = m.group("hon")
    if hon.lower() == "m.":
        hon = "M."
    elif hon.lower() == "mme":
        hon = "Mme"
    elif hon.lower() == "mlle":
        hon = "Mlle"
    elif hon.lower() in ("dr", "dr."):
        hon = "Dr."
    elif hon.lower() == "me":
        hon = "Me"
    return hon, m.group("rest").strip()


def parse_attribution(raw: str) -> ParsedAttribution:
    """Split a `<b>…</b>` speaker line into role / honorific / surname parts."""
    cleaned = _clean_speaker(raw)
    # Check for parenthetical first
    m_paren = _PAREN_SPLIT_RE.match(cleaned)
    main = m_paren.group("main").strip() if m_paren else cleaned
    paren = m_paren.group("paren").strip() if m_paren else None

    attr = ParsedAttribution(raw=cleaned, role=None, honorific=None, surname=None)

    # Role detection on the main component
    role = _match_role(main)
    if role:
        attr.role = role
        # Parenthetical inside a role attribution → person inside
        # ("La Vice-Présidente (Mme Soucy)")
        if paren:
            p_hon, p_rest = _split_honorific(paren)
            attr.paren_honorific = p_hon
            attr.paren_surname = p_rest.strip() if p_rest else None
        return attr

    # Person attribution — honorific + surname on the main
    hon, rest = _split_honorific(main)
    if hon and rest:
        attr.honorific = hon
        attr.surname = rest
        if paren:
            # Either: paren is a role ("chef du gouvernement") → set role,
            # or it's a riding name used to disambiguate shared surnames
            # ("M. Lévesque (Chapleau)") → record as constituency_hint.
            paren_role = _match_role(paren)
            if paren_role:
                attr.role = paren_role
            else:
                attr.constituency_hint = paren
        return attr

    # Fallback: single-token attribution (rare — surname-only, e.g. "Chouinard").
    # Treat as surname.
    attr.surname = main
    return attr


# ── Output dataclass ────────────────────────────────────────────────
@dataclass
class ParsedSpeech:
    sequence: int
    speaker_name_raw: str          # The full <b>…</b> text (minus trailing colon)
    speaker_role: Optional[str]    # Canonical role — "Le Président", "La Vice-Présidente", …
    honorific: Optional[str]
    surname: Optional[str]
    paren_honorific: Optional[str]
    paren_surname: Optional[str]
    constituency_hint: Optional[str]  # Riding name from disambiguating parens on person lines
    speech_type: str               # 'floor' (v1 — no section detection yet)
    spoken_at: datetime            # UTC
    text: str
    language: str
    source_anchor: Optional[str]
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
    return datetime.combine(sitting_date, t, tzinfo=MONTREAL_TZ).astimezone(timezone.utc)


# ── Section detection ───────────────────────────────────────────────
# Section headings in QC Hansard are centered `<p align="center">` / similar,
# containing a single `<b>HEADING</b>` with no trailing colon. Common headings:
#   Présence, Ouverture de la séance, Affaires courantes, Déclarations de députés,
#   Dépôt de documents, Dépôt de rapports de commissions, Questions et réponses
#   orales, Motions sans préavis, Avis touchant les travaux des commissions,
#   Affaires du jour, Projet de loi n° X — Adoption, …
#
# We use the latest-seen heading as `raw.section` metadata on every speech
# emitted until the next heading. The heuristic: a <p> whose body is a single
# <b>…</b> with no trailing colon is a heading.
_HEADING_ONLY_RE = re.compile(
    r"^\s*<b\b[^>]*>(?P<body>[^<]*(?:<(?!/?b\b)[^>]*>[^<]*)*)</b>\s*$",
    re.DOTALL,
)


def _is_heading(inner_html: str) -> tuple[bool, Optional[str]]:
    """True if the <p> body is just a bold heading (not a speaker). Returns (flag, heading_text)."""
    m = _HEADING_ONLY_RE.match(inner_html)
    if not m:
        return False, None
    body = _strip_tags(m.group("body"))
    if not body:
        return False, None
    # Still could be a speaker if it ended with a colon — but _HEADING_ONLY_RE
    # already matched and we stripped tags, so check body directly.
    if re.search(r":\s*$", body):
        return False, None
    return True, body


# ── Main extractor ──────────────────────────────────────────────────
@dataclass
class ParseResult:
    url: str
    url_meta: UrlMeta
    speeches: list[ParsedSpeech]
    section_hits: dict[str, int]


def _default_sitting_time(half: Optional[str] = None) -> time:
    # QC Assembly typically opens at 9:40 am, 1:40 pm, or 3:00 pm depending
    # on schedule. Without per-sitting time parsing, use a reasonable default.
    return time(10, 0)


def extract_speeches(html_text: str, url: str) -> ParseResult:
    """Parse one QC Journal des débats transcript into ParsedSpeech list."""
    meta = parse_url_meta(url)
    body = _scoped_body(html_text)
    speeches: list[ParsedSpeech] = []
    section_hits: dict[str, int] = {}

    current_section: Optional[str] = None

    # Open turn accumulator
    turn_attr: Optional[ParsedAttribution] = None
    turn_name_raw: Optional[str] = None
    turn_body_parts: list[str] = []
    turn_section: Optional[str] = None

    def flush_turn() -> None:
        nonlocal turn_attr, turn_name_raw, turn_body_parts, turn_section
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
        spoken_at = _localise(meta.sitting_date, _default_sitting_time())
        speech = ParsedSpeech(
            sequence=len(speeches) + 1,
            speaker_name_raw=turn_name_raw,
            speaker_role=turn_attr.role,
            honorific=turn_attr.honorific,
            surname=turn_attr.surname,
            paren_honorific=turn_attr.paren_honorific,
            paren_surname=turn_attr.paren_surname,
            constituency_hint=turn_attr.constituency_hint,
            speech_type="floor",
            spoken_at=spoken_at,
            text=text,
            language="fr",
            source_anchor=None,
            content_hash=_content_hash(text),
            raw={
                "document_id": meta.document_id,
                "section": turn_section,
                "url": url,
            },
        )
        speeches.append(speech)
        turn_attr = None
        turn_name_raw = None
        turn_body_parts = []
        turn_section = None

    for m in _P_RE.finditer(body):
        inner = m.group("body")

        # Heading detection — bold block, no trailing colon.
        is_head, head_text = _is_heading(inner)
        if is_head and head_text:
            flush_turn()
            current_section = head_text
            section_hits[head_text] = section_hits.get(head_text, 0) + 1
            continue

        # Speaker detection — opens with <b>Name :</b>.
        m_speaker = _P_OPEN_B_RE.match(inner)
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
            continue

        # Continuation paragraph — appended to the open turn.
        body_text = _strip_tags(inner)
        if body_text and turn_attr is not None:
            turn_body_parts.append(body_text)

    flush_turn()

    return ParseResult(
        url=url,
        url_meta=meta,
        speeches=speeches,
        section_hits=section_hits,
    )


# ── CLI harness for offline iteration ───────────────────────────────
if __name__ == "__main__":  # pragma: no cover
    import sys

    if len(sys.argv) < 2:
        print("usage: python -m src.legislative.qc_hansard_parse <fixture.html> [url]", file=sys.stderr)
        sys.exit(2)
    path = sys.argv[1]
    fake_url = sys.argv[2] if len(sys.argv) >= 3 else (
        "https://www.assnat.qc.ca/fr/travaux-parlementaires/assemblee-nationale/"
        "43-2/journal-debats/20260402/431789.html"
    )
    with open(path, encoding="utf-8") as f:
        raw = f.read()
    result = extract_speeches(raw, fake_url)
    print(
        f"url={result.url}\n"
        f"date={result.url_meta.sitting_date} parliament={result.url_meta.parliament} "
        f"session={result.url_meta.session} doc={result.url_meta.document_id}\n"
        f"speeches={len(result.speeches)}\n"
        f"sections={len(result.section_hits)}\n"
        "---"
    )
    for sp in result.speeches[:8]:
        preview = sp.text[:120].replace("\n", " ")
        print(
            f"[{sp.sequence:4d}] {sp.spoken_at:%H:%M} role={sp.speaker_role!r} "
            f"hon={sp.honorific!r} surname={sp.surname!r} "
            f"paren={sp.paren_surname!r} ({sp.word_count:>4} w) "
            f"{sp.speaker_name_raw!r}"
        )
        print(f"       {preview}…")
    print("...")
    for sp in result.speeches[-4:]:
        preview = sp.text[:120].replace("\n", " ")
        print(
            f"[{sp.sequence:4d}] role={sp.speaker_role!r} surname={sp.surname!r} "
            f"paren={sp.paren_surname!r} ({sp.word_count:>4} w) "
            f"{sp.speaker_name_raw!r}"
        )
        print(f"       {preview}…")
    print("\nSection hits:")
    for sect, n in sorted(result.section_hits.items(), key=lambda x: -x[1])[:10]:
        print(f"  {n:>3}  {sect}")
