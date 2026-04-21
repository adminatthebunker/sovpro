"""Quebec Hansard ingester — Journal des débats → `speeches` table.

Mirrors BC Hansard's structure with two QC-specific wrinkles:

1. **Discovery is ASP.NET WebForms paginated.** The Journal des débats
   listing lives at `/fr/travaux-parlementaires/journaux-debats/` and
   ships a 5-per-page default with `__VIEWSTATE` / `__EVENTVALIDATION`
   hidden fields. We POST back with the dropdown's 100-per-page value
   plus the session filter (`ddlSessionLegislature=<id>`) and then walk
   `lkbPageN` postbacks until the listing runs out of new URLs. The
   session-select IDs are internal (e.g. 1617 = 43-2) and mapped via
   a small dict built from the dropdown options — we rescrape the
   dropdown on first hit so new sessions auto-register.

2. **Bilingual — French primary.** QC publishes both `/fr/…` and
   `/en/…` but only French is reliably served (English variants 500
   on several sittings we probed). `language='fr'` is pinned on every
   speech row.

## Speaker resolution

`politicians.qc_assnat_id` (integer) carries 124/124 of current-session
MNAs — no name-fuzz for active members. Historical MNAs are absent so
P42 / P43-1 backfills will resolve less cleanly; v1 scopes to the
current session (43-2) where the roster is complete.

Attribution shapes from the parser:
  - Person: `honorific + surname` ("M. Ciccone", "Mme Charest")
  - Role + person: `role + paren_surname` ("La Vice-Présidente (Mme Soucy)")
  - Pure role: `role` only ("Le Président", "Le Premier ministre")

The `Le Président` / `La Présidente` role is resolved in a post-pass
via `presiding_officer_resolver.py` using a date-ranged QC roster.
Everything else resolves by (honorific, surname) → qc_assnat_id FK.

## Upsert key

Same pattern as BC/AB: `UNIQUE NULLS NOT DISTINCT (source_system,
source_url, sequence)`. `source_url` is the real transcript URL
(no canonical-URL gymnastics — QC doesn't have Blues/Final twin files).
`source_system = 'hansard-qc'`.
"""
from __future__ import annotations

import asyncio
import logging
import re
import unicodedata
import urllib.parse
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional

import httpx
import orjson

from ..db import Database
from . import qc_hansard_parse as parse_mod

log = logging.getLogger(__name__)

SOURCE_SYSTEM = "hansard-qc"
LISTING_URL = "https://www.assnat.qc.ca/fr/travaux-parlementaires/journaux-debats/"
TRANSCRIPT_URL_TEMPLATE = (
    "https://www.assnat.qc.ca/fr/travaux-parlementaires/"
    "assemblee-nationale/{parl}-{sess}/journal-debats/{date}/{doc}.html"
)
# Wayback CDX API — fallback discovery for historical sessions.
# The assnat.qc.ca ASP.NET search form returns HTTP 500 for every session
# except the current one (server-side bug, reproducible from multiple IPs),
# so historical sessions need an alternative discovery path. Wayback has
# indexed most transcript URLs; once we have the URL we fetch the
# transcript straight from the origin (which *does* serve historical
# content just fine — only the search form is broken).
WAYBACK_CDX_URL = (
    "https://web.archive.org/cdx/search/cdx"
    "?url=assnat.qc.ca%2Ffr%2Ftravaux-parlementaires%2Fassemblee-nationale%2F"
    "{parl}-{sess}%2Fjournal-debats%2F*"
    "&output=txt&collapse=urlkey"
    "&filter=statuscode:200&filter=mimetype:text/html"
    "&limit=10000"
)

REQUEST_TIMEOUT = 60
REQUEST_DELAY_SECONDS = 1.5  # Polite to assnat.qc.ca

HEADERS = {
    "User-Agent": "SovereignWatch/1.0 (civic-transparency; +admin@thebunkerops.ca)",
    "Accept": "text/html,application/xhtml+xml,application/xml",
    "Accept-Language": "fr-CA,fr;q=0.9,en;q=0.7",
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
                "qc_hansard retry %d/%d after %ds — last error: %s",
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


async def _post_with_retry(
    client: httpx.AsyncClient, url: str, data: dict[str, str],
) -> httpx.Response:
    last_exc: Optional[BaseException] = None
    for attempt, delay in enumerate((0,) + RETRY_BACKOFF_SECONDS):
        if delay:
            log.warning(
                "qc_hansard POST retry %d/%d after %ds — last error: %s",
                attempt, len(RETRY_BACKOFF_SECONDS), delay, last_exc,
            )
            await asyncio.sleep(delay)
        try:
            r = await client.post(
                url,
                content=urllib.parse.urlencode(data).encode(),
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Referer": url,
                },
            )
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


# ── Discovery ───────────────────────────────────────────────────────

# ASP.NET hidden fields needed on every postback.
_VS_RE = re.compile(r'id="__VIEWSTATE"\s+value="([^"]*)"')
_VSGEN_RE = re.compile(r'id="__VIEWSTATEGENERATOR"\s+value="([^"]*)"')
_EV_RE = re.compile(r'id="__EVENTVALIDATION"\s+value="([^"]*)"')

# Transcript link on the listing page.
_TRANSCRIPT_HREF_RE = re.compile(
    r'/fr/travaux-parlementaires/assemblee-nationale/'
    r'(?P<parl>\d+)-(?P<sess>\d+)/journal-debats/'
    r'(?P<date>\d{8})/(?P<doc>\d+)\.html'
)

# Session selector dropdown option -> "43e législature, 2e session (...)".
# Build a (parl, sess) -> select_value map by scraping these.
_SESSION_OPTION_RE = re.compile(
    r'<option[^>]+value="(?P<val>\d+)"[^>]*>'
    r'\s*(?P<parl>\d+)e\s+l[ée]gislature,\s*(?P<sess>\d+)(?:re|e)\s+session',
    re.IGNORECASE,
)

# Form control names (ASP.NET WebForms cruft)
_CTRL_SESSION_DDL = "ctl00$ColCentre$ContenuColonneGauche$ddlSessionLegislature"
_CTRL_DEBAT_TYPE = "ctl00$ColCentre$ContenuColonneGauche$rblOptionTypeDebat"
_CTRL_PAGE_SIZE = "ctl00$ColCentre$ContenuColonneGauche$PaginationHaut$ddlNombreParPage"
_CTRL_SEARCH_BTN = "ctl00$ColCentre$ContenuColonneGauche$btnRecherche"
_CTRL_PAGE_N_FMT = "ctl00$ColCentre$ContenuColonneGauche$PaginationHaut$lkbPage{n}"
_CTRL_PAGE_NEXT = "ctl00$ColCentre$ContenuColonneGauche$PaginationHaut$lkbPageSuivante"


@dataclass
class SittingRef:
    sitting_date: date
    parliament: int
    session: int
    document_id: str
    url: str

    @classmethod
    def from_href_match(cls, m: re.Match) -> "SittingRef":
        d = datetime.strptime(m.group("date"), "%Y%m%d").date()
        return cls(
            sitting_date=d,
            parliament=int(m.group("parl")),
            session=int(m.group("sess")),
            document_id=m.group("doc"),
            url=TRANSCRIPT_URL_TEMPLATE.format(
                parl=m.group("parl"),
                sess=m.group("sess"),
                date=m.group("date"),
                doc=m.group("doc"),
            ),
        )


def _extract_viewstate(html: str) -> tuple[str, str, str]:
    vs = _VS_RE.search(html)
    vsg = _VSGEN_RE.search(html)
    ev = _EV_RE.search(html)
    if not (vs and vsg and ev):
        raise RuntimeError(
            "qc_hansard discovery: missing ASP.NET hidden fields — "
            "page markup may have changed"
        )
    return vs.group(1), vsg.group(1), ev.group(1)


def _extract_session_map(html: str) -> dict[tuple[int, int], str]:
    """Parse dropdown options into {(parl, session): dropdown_value}.

    The listing serves HTML entities (`&#233;`) in option text, so unescape
    before regex-matching — pattern matches on literal `é`.
    """
    import html as _html
    decoded = _html.unescape(html)
    out: dict[tuple[int, int], str] = {}
    for m in _SESSION_OPTION_RE.finditer(decoded):
        key = (int(m.group("parl")), int(m.group("sess")))
        out[key] = m.group("val")
    return out


def _extract_transcript_refs(html: str) -> list[SittingRef]:
    seen: set[tuple[str, str]] = set()
    out: list[SittingRef] = []
    for m in _TRANSCRIPT_HREF_RE.finditer(html):
        key = (m.group("date"), m.group("doc"))
        if key in seen:
            continue
        seen.add(key)
        out.append(SittingRef.from_href_match(m))
    return out


# Wayback CDX transcript-URL regex — matches valid daily-transcript shape only.
_CDX_TRANSCRIPT_RE = re.compile(
    r"/assemblee-nationale/(?P<parl>\d+)-(?P<sess>\d+)/"
    r"journal-debats/(?P<date>\d{8})/(?P<doc>\d+)\.html",
    re.IGNORECASE,
)


async def discover_via_wayback(
    client: httpx.AsyncClient, *, parliament: int, session: int,
) -> list[SittingRef]:
    """Fallback discovery for historical sessions via Wayback Machine CDX.

    The origin server serves historical transcripts fine — only the
    listing/search form is broken. Wayback's CDX API reliably returns
    the set of URLs it has indexed for our URL pattern, which is a
    near-complete list of every sitting the assembly published.

    For each CDX row we pluck (date, doc_id), dedupe, and return
    SittingRefs pointed at the origin URL. The ingest orchestrator then
    fetches each transcript straight from assnat.qc.ca.
    """
    url = WAYBACK_CDX_URL.format(parl=parliament, sess=session)
    log.info("qc_hansard wayback CDX: querying %d-%d", parliament, session)
    r = await _get_with_retry(client, url)
    r.raise_for_status()

    seen: set[tuple[str, str]] = set()
    out: list[SittingRef] = []
    for line in r.text.splitlines():
        m = _CDX_TRANSCRIPT_RE.search(line)
        if not m:
            continue
        if int(m.group("parl")) != parliament or int(m.group("sess")) != session:
            continue
        key = (m.group("date"), m.group("doc"))
        if key in seen:
            continue
        seen.add(key)
        out.append(SittingRef(
            sitting_date=datetime.strptime(m.group("date"), "%Y%m%d").date(),
            parliament=parliament,
            session=session,
            document_id=m.group("doc"),
            url=TRANSCRIPT_URL_TEMPLATE.format(
                parl=parliament, sess=session,
                date=m.group("date"), doc=m.group("doc"),
            ),
        ))
    out.sort(key=lambda r: r.sitting_date)
    log.info(
        "qc_hansard wayback CDX: %d unique transcripts for %d-%d",
        len(out), parliament, session,
    )
    return out


async def discover_sitting_refs(
    client: httpx.AsyncClient, *, parliament: int, session: int,
) -> list[SittingRef]:
    """Paginate the Journal des débats listing and collect all refs for (parliament, session).

    Strategy:
      1. GET the listing to harvest ViewState + session-dropdown map.
      2. POST with ddlSessionLegislature=<id>, debate type = 1 (Assemblée nationale
         only — skip committees for v1), 100 per page, click Rechercher.
      3. Walk lkbPageSuivante postbacks until no new refs appear.

    If the form returns HTTP 500 (historical sessions trigger a server-side
    bug) or produces zero refs, we transparently fall back to the Wayback
    Machine CDX API (`discover_via_wayback`) which has indexed most
    transcript URLs historically. Once we have URLs from Wayback, fetches
    go straight to the origin — Wayback is a URL-discovery crutch, not a
    content mirror.
    """
    try:
        refs = await _discover_via_form(
            client, parliament=parliament, session=session,
        )
        if refs:
            return refs
        log.info(
            "qc_hansard form returned 0 refs for %d-%d — falling back to Wayback",
            parliament, session,
        )
    except Exception as exc:
        log.info(
            "qc_hansard form discovery failed for %d-%d (%s) — falling back to Wayback",
            parliament, session, exc,
        )
    return await discover_via_wayback(client, parliament=parliament, session=session)


async def _discover_via_form(
    client: httpx.AsyncClient, *, parliament: int, session: int,
) -> list[SittingRef]:
    # Initial GET
    r = await _get_with_retry(client, LISTING_URL)
    r.raise_for_status()
    html = r.text

    session_map = _extract_session_map(html)
    key = (parliament, session)
    if key not in session_map:
        raise RuntimeError(
            f"qc_hansard discovery: session {parliament}-{session} not in "
            f"dropdown (known: {sorted(session_map.keys())[:10]}…)"
        )
    session_ddl_val = session_map[key]
    log.info(
        "qc_hansard: session %d-%d → ddl value %s",
        parliament, session, session_ddl_val,
    )

    # First filtered page: POST with session filter + 100 per page.
    vs, vsg, ev = _extract_viewstate(html)
    form: dict[str, str] = {
        "__EVENTTARGET": _CTRL_SEARCH_BTN,
        "__EVENTARGUMENT": "",
        "__VIEWSTATE": vs,
        "__VIEWSTATEGENERATOR": vsg,
        "__EVENTVALIDATION": ev,
        _CTRL_SESSION_DDL: session_ddl_val,
        _CTRL_DEBAT_TYPE: "1",   # Assemblée nationale only
        _CTRL_PAGE_SIZE: "100",
        _CTRL_SEARCH_BTN: "Rechercher",
    }
    # Submitting via btnRecherche click requires sending that button's value too;
    # treat it as a standard submit (no __EVENTTARGET) to match browser behaviour.
    form.pop("__EVENTTARGET")
    r = await _post_with_retry(client, LISTING_URL, form)
    r.raise_for_status()

    all_refs: dict[tuple[str, str], SittingRef] = {}
    page_num = 1
    while True:
        refs = _extract_transcript_refs(r.text)
        added = 0
        for ref in refs:
            k = (ref.document_id, ref.sitting_date.isoformat())
            if ref.parliament != parliament or ref.session != session:
                continue
            if k in all_refs:
                continue
            all_refs[k] = ref
            added += 1
        log.info("qc_hansard discover: page %d → %d new refs (total %d)",
                 page_num, added, len(all_refs))
        # If this page had no new in-scope refs, stop.
        if added == 0 and page_num > 1:
            break

        # Walk to the next page via lkbPageSuivante.
        try:
            vs, vsg, ev = _extract_viewstate(r.text)
        except RuntimeError:
            log.warning("qc_hansard discover: no ViewState on page %d — stopping",
                        page_num)
            break
        form_next: dict[str, str] = {
            "__EVENTTARGET": _CTRL_PAGE_NEXT,
            "__EVENTARGUMENT": "",
            "__VIEWSTATE": vs,
            "__VIEWSTATEGENERATOR": vsg,
            "__EVENTVALIDATION": ev,
            _CTRL_SESSION_DDL: session_ddl_val,
            _CTRL_DEBAT_TYPE: "1",
            _CTRL_PAGE_SIZE: "100",
        }
        r = await _post_with_retry(client, LISTING_URL, form_next)
        r.raise_for_status()
        page_num += 1
        if page_num > 30:
            # Defensive cap — no session has ever had >3000 sittings.
            log.warning("qc_hansard discover: hit page cap at %d", page_num)
            break
        await asyncio.sleep(REQUEST_DELAY_SECONDS)

    refs_list = list(all_refs.values())
    refs_list.sort(key=lambda r: r.sitting_date)
    log.info(
        "qc_hansard discover: %d sittings for %d-%d",
        len(refs_list), parliament, session,
    )
    return refs_list


# ── Sessions ────────────────────────────────────────────────────────

async def ensure_session(
    db: Database, *, parliament: int, session: int,
) -> str:
    """Return legislative_sessions.id for QC (parliament, session).

    QC bills pipeline already upserts the session row (`qc_bills.py`), so
    this is a no-op fetch in practice — idempotent anyway.
    """
    row = await db.fetchrow(
        """
        INSERT INTO legislative_sessions
            (level, province_territory, parliament_number, session_number,
             name, source_system, source_url)
        VALUES ('provincial', 'QC', $1, $2, $3, $4, $5)
        ON CONFLICT (level, province_territory, parliament_number, session_number)
        DO UPDATE SET updated_at = now()
        RETURNING id
        """,
        parliament, session,
        f"{parliament}e Législature, {'1re' if session == 1 else f'{session}e'} Session",
        SOURCE_SYSTEM,
        LISTING_URL,
    )
    return str(row["id"])


# ── Speaker resolution ──────────────────────────────────────────────

_WS_RE = re.compile(r"\s+")


def _norm(s: str) -> str:
    """Case/accent/punct-normalised form for dictionary lookups."""
    if not s:
        return ""
    t = unicodedata.normalize("NFKD", s.replace("\u00a0", " "))
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = t.lower()
    t = re.sub(r"[^\w\s-]", " ", t)
    return _WS_RE.sub(" ", t).strip()


@dataclass
class SpeakerLookup:
    """Indexed QC MNAs for speaker-line resolution.

    QC Hansard attribution style is "honorific + surname" (M. / Mme +
    surname only — given names are *not* in the attribution). Most
    resolution is surname-only; for shared surnames QC disambiguates with
    a parenthetical riding name ("M. Lévesque (Chapleau)"). The lookup
    supports both paths.

    Indexes:
      by_full_name    — full "first last" / "name" strings
      by_surname      — single-token and compound-surname keys
      by_riding_surname — (surname, constituency_norm) → unique politician
    """
    by_full_name: dict[str, list[dict]] = field(default_factory=dict)
    by_surname: dict[str, list[dict]] = field(default_factory=dict)
    by_riding_surname: dict[tuple[str, str], dict] = field(default_factory=dict)

    def resolve_by_surname(
        self,
        surname: Optional[str],
        *,
        constituency_hint: Optional[str] = None,
    ) -> tuple[Optional[dict], str]:
        """Returns (politician_row_or_None, status).

        Status: 'resolved' | 'ambiguous' | 'unresolved'.

        Disambiguation order:
          1. (surname, constituency_hint) exact — always unique when hinted.
          2. Full surname token key.
          3. Last-token fallback for compound surnames.
        """
        if not surname:
            return None, "unresolved"
        key = _norm(surname)
        if not key:
            return None, "unresolved"

        # Riding-disambiguated lookup — always unique when both keys hit.
        if constituency_hint:
            hint_norm = _norm(constituency_hint)
            if hint_norm:
                candidate_keys = [(key, hint_norm)]
                tokens = key.split()
                if len(tokens) > 1:
                    candidate_keys.append((tokens[-1], hint_norm))
                for ck in candidate_keys:
                    hit = self.by_riding_surname.get(ck)
                    if hit:
                        return hit, "resolved"

        # Try the full surname first (handles "de Sève", "Jolin-Barrette",
        # "Boivin Roy").
        hits = self.by_surname.get(key)
        if hits and len(hits) == 1:
            return hits[0], "resolved"
        if hits and len(hits) > 1:
            # With a constituency hint present we already tried above,
            # so plain ambiguity here is genuine.
            return None, "ambiguous"

        # Last-token fallback for compound surnames stored under just the
        # last part ("de Sève" in the text but "Sève" in the DB).
        tokens = key.split()
        if len(tokens) > 1:
            hits = self.by_surname.get(tokens[-1])
            if hits and len(hits) == 1:
                return hits[0], "resolved"
            if hits and len(hits) > 1:
                return None, "ambiguous"
        return None, "unresolved"

    def resolve_by_full_name(self, name: str) -> tuple[Optional[dict], str]:
        key = _norm(name)
        if not key:
            return None, "unresolved"
        hits = self.by_full_name.get(key)
        if hits and len(hits) == 1:
            return hits[0], "resolved"
        if hits and len(hits) > 1:
            return None, "ambiguous"
        return None, "unresolved"


async def load_qc_speaker_lookup(db: Database) -> SpeakerLookup:
    """Build the SpeakerLookup from politicians.

    Scope: all QC provincial politicians (past + present). 124/124 of
    current MNAs have qc_assnat_id populated — new sittings resolve
    via surname; past-session attributions degrade gracefully when a
    retired MNA isn't in the table.
    """
    rows = await db.fetch(
        """
        SELECT id::text           AS id,
               name, first_name, last_name, constituency_name,
               qc_assnat_id
          FROM politicians
         WHERE level = 'provincial'
           AND province_territory = 'QC'
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
        last_tokens = last.split() if last else []
        # Derive surname keys from (a) the DB last_name itself, (b) its
        # last token for compound cases like "de Sève", and (c) the tail
        # of the `name` field when first_name absorbs part of a compound
        # surname ("Karine Boivin Roy" → first_name="Karine Boivin",
        # last_name="Roy"). The name-tail path unique-keys Boivin Roy,
        # Jolin-Barrette, Pouliot-Morrison, etc.
        surname_keys: set[str] = set()
        if last:
            surname_keys.add(last)
            if last_tokens:
                surname_keys.add(last_tokens[-1])
        if full:
            name_tokens = full.split()
            if len(name_tokens) > 2:
                # Anything after the first token is potential surname.
                surname_keys.add(" ".join(name_tokens[1:]))
        for tok in surname_keys:
            lookup.by_surname.setdefault(tok, []).append(dict(r))
        # Riding-disambiguated index: (surname, constituency) → single politician.
        riding_norm = _norm(r["constituency_name"] or "")
        if riding_norm and surname_keys:
            for tok in surname_keys:
                lookup.by_riding_surname[(tok, riding_norm)] = dict(r)

    # Dedupe within each bucket by qc_assnat_id (and fallback by id).
    for idx in (lookup.by_full_name, lookup.by_surname):
        for k, lst in idx.items():
            seen_ids: set[str] = set()
            seen_assnat: set[int] = set()
            dedup: list[dict] = []
            for p in lst:
                qa = p.get("qc_assnat_id")
                if p["id"] in seen_ids:
                    continue
                if qa is not None and qa in seen_assnat:
                    continue
                seen_ids.add(p["id"])
                if qa is not None:
                    seen_assnat.add(qa)
                dedup.append(p)
            idx[k] = dedup

    log.info(
        "qc_hansard: loaded %d MNAs (unique_surname=%d ambig_surname=%d)",
        len(rows),
        sum(1 for v in lookup.by_surname.values() if len(v) == 1),
        sum(1 for v in lookup.by_surname.values() if len(v) > 1),
    )
    return lookup


def _resolve_speech(
    lookup: SpeakerLookup, ps: parse_mod.ParsedSpeech,
) -> tuple[Optional[dict], str]:
    """Walk the parsed speaker attribution through the lookup.

    Order:
      1. Parenthetical person (role + `(Mme Surname)`) — highest signal.
      2. Direct person attribution (honorific + surname) with optional
         constituency hint from a non-role parenthetical.
      3. Role-only (no match here — presiding-officer resolver handles later).
    """
    if ps.paren_surname:
        pol, status = lookup.resolve_by_surname(ps.paren_surname)
        if pol:
            return pol, "resolved_paren"
        if status == "ambiguous":
            return None, "ambiguous"
    if ps.surname:
        pol, status = lookup.resolve_by_surname(
            ps.surname, constituency_hint=ps.constituency_hint,
        )
        if pol:
            return pol, "resolved"
        if status == "ambiguous":
            return None, "ambiguous"
    if ps.speaker_role:
        return None, "role"
    return None, "unresolved"


# ── Upsert ──────────────────────────────────────────────────────────

@dataclass
class IngestStats:
    sittings_scanned: int = 0
    speeches_seen: int = 0
    speeches_inserted: int = 0
    speeches_updated: int = 0
    speeches_resolved: int = 0
    speeches_role_only: int = 0
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
) -> str:
    """Insert/update one speech. Returns 'inserted' | 'updated' | 'skipped'."""
    if not parsed.text.strip():
        return "skipped"
    politician_id = politician["id"] if politician else None

    raw_payload = {
        "qc_hansard": {
            "sitting_date": ref.sitting_date.isoformat(),
            "parliament": ref.parliament,
            "session": ref.session,
            "document_id": ref.document_id,
            "section": parsed.raw.get("section"),
            "honorific": parsed.honorific,
            "surname": parsed.surname,
            "paren_honorific": parsed.paren_honorific,
            "paren_surname": parsed.paren_surname,
            "constituency_hint": parsed.constituency_hint,
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
            $1, $2, 'provincial', 'QC',
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
        ref.url,
        parsed.source_anchor,
        raw_json,
        # Store the full transcript HTML only on the *first* row of each
        # sitting. Earlier versions stored it on every row (200 × 500 KB
        # = ~100 MB write per sitting), which made TOAST + WAL the dominant
        # cost of ingest (~10 speeches/sec). One copy per sitting keeps
        # re-parse possible without 200× write amplification.
        page_html if parsed.sequence == 1 else None,
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
    """Fetch + parse + upsert QC Hansard for one parliament+session."""
    stats = IngestStats()
    session_id = await ensure_session(db, parliament=parliament, session=session)
    lookup = await load_qc_speaker_lookup(db)

    async with httpx.AsyncClient(
        timeout=REQUEST_TIMEOUT, headers=HEADERS, follow_redirects=True,
    ) as client:
        if one_off_url:
            meta = parse_mod.parse_url_meta(one_off_url)
            refs = [SittingRef(
                sitting_date=meta.sitting_date,
                parliament=meta.parliament,
                session=meta.session,
                document_id=meta.document_id,
                url=one_off_url,
            )]
        else:
            refs = await discover_sitting_refs(
                client, parliament=parliament, session=session,
            )
            if since:
                refs = [r for r in refs if r.sitting_date >= since]
            if until:
                refs = [r for r in refs if r.sitting_date <= until]
            if limit_sittings:
                refs = refs[-limit_sittings:]

        log.info(
            "qc_hansard: processing %d sittings (parliament=%d session=%d)",
            len(refs), parliament, session,
        )

        for ref in refs:
            if limit_speeches and (
                stats.speeches_inserted + stats.speeches_updated
            ) >= limit_speeches:
                break
            stats.sittings_scanned += 1
            log.info("sitting %s → %s", ref.sitting_date, ref.url)
            try:
                r = await _get_with_retry(client, ref.url)
                r.raise_for_status()
                page_html = r.text
            except Exception as exc:
                log.warning("sitting %s: fetch failed: %s", ref.url, exc)
                continue

            try:
                result = parse_mod.extract_speeches(page_html, ref.url)
            except Exception as exc:
                log.warning("sitting %s: parse failed: %s", ref.url, exc)
                stats.parse_errors += 1
                continue

            if len(result.speeches) < 3:
                log.warning(
                    "sitting %s: only %d speeches parsed — skipping",
                    ref.url, len(result.speeches),
                )
                stats.parse_errors += 1
                continue

            log.info("  parsed %d speeches", len(result.speeches))

            for ps in result.speeches:
                if limit_speeches and (
                    stats.speeches_inserted + stats.speeches_updated
                ) >= limit_speeches:
                    break
                stats.speeches_seen += 1

                politician, status = _resolve_speech(lookup, ps)
                if status in ("resolved", "resolved_paren"):
                    stats.speeches_resolved += 1
                    confidence = 1.0
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
                )
                if outcome == "inserted":
                    stats.speeches_inserted += 1
                elif outcome == "updated":
                    stats.speeches_updated += 1
                elif outcome == "skipped":
                    stats.skipped_empty += 1

            await asyncio.sleep(REQUEST_DELAY_SECONDS)

    # Sync denormalised politician_id onto chunks — matches BC / AB pattern.
    await db.execute(
        """
        UPDATE speech_chunks sc
           SET politician_id = s.politician_id
          FROM speeches s
         WHERE sc.speech_id = s.id
           AND s.level = 'provincial'
           AND s.province_territory = 'QC'
           AND s.source_system = $1
           AND sc.politician_id IS DISTINCT FROM s.politician_id
        """,
        SOURCE_SYSTEM,
    )

    log.info(
        "qc_hansard done: %d sittings, %d speeches "
        "(inserted=%d updated=%d skipped=%d parse_errors=%d) "
        "resolved=%d role=%d ambiguous=%d unresolved=%d",
        stats.sittings_scanned,
        stats.speeches_seen,
        stats.speeches_inserted,
        stats.speeches_updated,
        stats.skipped_empty,
        stats.parse_errors,
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


async def resolve_qc_speakers(
    db: Database, *, limit: Optional[int] = None,
) -> ResolveStats:
    """Re-resolve politician_id on QC Hansard speeches with NULL politician_id.

    Run after adding more QC MNAs (historical backfill), or after fixing
    a parser bug that left speeches unresolved.
    """
    stats = ResolveStats()
    lookup = await load_qc_speaker_lookup(db)

    query = """
        SELECT s.id::text AS id,
               s.speaker_name_raw,
               s.speaker_role,
               s.raw->'qc_hansard'->>'surname'            AS surname,
               s.raw->'qc_hansard'->>'paren_surname'      AS paren_surname,
               s.raw->'qc_hansard'->>'constituency_hint'  AS constituency_hint
          FROM speeches s
         WHERE s.level = 'provincial'
           AND s.province_territory = 'QC'
           AND s.source_system = $1
           AND s.politician_id IS NULL
    """
    params: list = [SOURCE_SYSTEM]
    if limit:
        query += f" LIMIT {int(limit)}"

    rows = await db.fetch(query, *params)
    for r in rows:
        stats.speeches_scanned += 1
        politician = None
        # Try paren first (role + person)
        if r["paren_surname"]:
            pol, _ = lookup.resolve_by_surname(r["paren_surname"])
            if pol:
                politician = pol
        if not politician and r["surname"]:
            pol, _ = lookup.resolve_by_surname(
                r["surname"], constituency_hint=r["constituency_hint"],
            )
            if pol:
                politician = pol
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
        "resolve_qc_speakers: scanned=%d updated=%d still_unresolved=%d",
        stats.speeches_scanned, stats.speeches_updated, stats.still_unresolved,
    )
    return stats
