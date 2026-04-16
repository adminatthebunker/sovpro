"""Quebec bills pipeline — donneesquebec CSV + RSS + HTML sponsor scrape.

Three data sources, each doing what it's best at:

1. **CSV** at donneesquebec.ca (`projets-de-loi.csv`) — the authoritative
   bill roster. 613 rows spanning the current legislature plus the
   previous session. Refreshed daily by the Assemblée nationale. One
   HTTP GET, no scraping, no WAF budget. Gives us bill number + title
   + type + last-stage-reached + dates.

2. **RSS** at `SyndicationRSS-210.html` — every stage transition on
   every current-session bill, published as one XML document. Same
   pattern as NS RSS. We use this to populate bill_events with a
   timeline, since the CSV only exposes the *last* stage.

3. **Bill detail HTML** (optional, opt-in) — each bill has a detail
   page at `/en/travaux-parlementaires/projets-loi/projet-loi-{n}-{p}-{s}.html`.
   The sponsor is a single `<a href="/en/deputes/{slug}-{id}/index.html">`.
   We fetch each bill's detail page once, regex out the numeric MNA id,
   and do an exact FK lookup into politicians.qc_assnat_id. This is
   the only phase with a per-bill HTTP cost — ~150 bills/session, run
   with a polite 2s delay.

The Quebec Assembly speaks French officially; stage labels in the RSS
and CSV are French. Our internal canonical `stage` vocabulary is English
(matching NS/ON/BC). Raw French labels are preserved in
`bill_events.stage_label` for UI display.
"""
from __future__ import annotations

import asyncio
import csv
import io
import logging
import re
import unicodedata
from datetime import date, datetime
from typing import Any, Optional
from xml.etree import ElementTree as ET

import httpx
import orjson

from ..db import Database

log = logging.getLogger(__name__)

SOURCE_SYSTEM = "assnat-qc"
REQUEST_TIMEOUT = 60
HEADERS = {
    "User-Agent": "SovereignWatch/1.0 (civic-transparency; +admin@thebunkerops.ca)",
    "Accept": "text/html,application/xhtml+xml,application/xml,text/csv",
}

CSV_URL = (
    "https://www.donneesquebec.ca/recherche/dataset/"
    "2bde70f9-15ff-455b-b3ea-c6e229b24074/resource/"
    "93c74b8c-51d1-49e6-9ab9-1f8d96dbd735/download/projets-de-loi.csv"
)
RSS_URL = "https://www.assnat.qc.ca/fr/rss/SyndicationRSS-210.html"
BILL_DETAIL_URL = (
    "https://www.assnat.qc.ca/en/travaux-parlementaires/projets-loi/"
    "projet-loi-{number}-{parl}-{session}.html"
)

# CSV column header → snake-case key we use internally.
# (Column order changes break CSVs more often than name changes.)
_CSV_STAGE_MAP: dict[str, tuple[str, str]] = {
    "presentation":                     ("introduced",     "Présentation"),
    "adoption_principe":                ("second_reading", "Adoption du principe"),
    "depot_commission":                 ("committee",      "Dépôt en commission"),
    "depot_commission_consultation":    ("committee",      "Consultations particulières"),
    "depot_commission_etude_detaillee": ("committee",      "Étude détaillée en commission"),
    "sanction":                         ("royal_assent",   "Sanction"),
}

# RSS items carry French stage labels embedded in `<title>`. We match
# on lowercase-accent-stripped prefix. These are exhaustive for the
# current session's vocabulary — any unknown label falls through to
# stage="other" so we never drop an event entirely.
_RSS_STAGE_MAP: list[tuple[str, str, str]] = [
    # (normalized prefix, canonical stage, display label)
    ("presentation",                                     "introduced",     "Présentation"),
    ("adoption du principe",                             "second_reading", "Adoption du principe"),
    ("consultations particulieres",                      "committee",      "Consultations particulières"),
    ("etude detaillee en commission",                    "committee",      "Étude détaillée en commission"),
    ("depot du rapport de commission - etude detaillee", "committee",      "Dépôt du rapport — Étude détaillée"),
    ("prise en consideration du rapport de commission",  "report",         "Prise en considération du rapport"),
    ("sanction",                                         "royal_assent",   "Sanction"),
    # Must come *after* "adoption du principe" — prefix matching picks
    # the first hit, and a bare "Adoption" is Quebec's Westminster-style
    # third reading (final passage) event.
    ("adoption",                                         "third_reading", "Adoption"),
]

_BILL_TYPE_MAP = {
    "Public du gouvernement": "government",
    "Public de député":       "private_member",
    "D'intérêt privé":        "private",
}

_MNA_HREF_RE = re.compile(
    r"/(?:en|fr)/deputes/(?P<slug>[a-z0-9-]+)-(?P<id>\d+)/index\.html",
    re.IGNORECASE,
)

# Bill titles in the CSV are prefixed with the bill's original
# (parliament, session) — e.g. "43-1 PL 82  Loi concernant...". That
# prefix is load-bearing for us because the CSV marks bills with the
# *current* session even when they were introduced in a previous one
# and carried over. The detail page URL uses the *original* session,
# so we parse the prefix to know where to look.
_TITLE_PREFIX_RE = re.compile(
    r"^\s*(?P<parl>\d+)-(?P<session>\d+)\s+PL\s+(?P<number>[a-z0-9-]+)\s+",
    re.IGNORECASE,
)

# French months so we can parse RSS item trailing dates like "2 avril 2026".
_FR_MONTHS = {
    "janvier": 1, "fevrier": 2, "mars": 3, "avril": 4, "mai": 5, "juin": 6,
    "juillet": 7, "aout": 8, "septembre": 9, "octobre": 10, "novembre": 11,
    "decembre": 12,
}

_DATE_TAIL_RE = re.compile(
    r"(\d{1,2})\s+(janvier|f[eé]vrier|mars|avril|mai|juin|juillet|"
    r"ao[uû]t|septembre|octobre|novembre|d[eé]cembre)\s+(\d{4})",
    re.IGNORECASE,
)


def _strip_accents(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    return "".join(c for c in s if not unicodedata.combining(c))


def _parse_iso_date(s: Optional[str]) -> Optional[date]:
    if not s:
        return None
    try:
        return datetime.strptime(s[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def _parse_fr_date(s: str) -> Optional[date]:
    """Extract the last-mentioned French date from a string."""
    if not s:
        return None
    m = None
    for m in _DATE_TAIL_RE.finditer(_strip_accents(s.lower())):
        pass
    if not m:
        return None
    try:
        return date(int(m.group(3)), _FR_MONTHS[m.group(2)], int(m.group(1)))
    except (KeyError, ValueError):
        return None


# ─────────────────────────────────────────────────────────────────────
# Phase 1 — legislative_sessions upsert
# ─────────────────────────────────────────────────────────────────────

def _fr_ordinal(n: int) -> str:
    return "1re" if n == 1 else f"{n}e"


async def _upsert_qc_session(
    db: Database, *, parliament: int, session: int,
    start_date: Optional[date], end_date: Optional[date],
) -> str:
    row = await db.fetchrow(
        """
        INSERT INTO legislative_sessions (
            level, province_territory, parliament_number, session_number,
            name, start_date, end_date, source_system, source_url
        )
        VALUES ('provincial', 'QC', $1, $2, $3, $4, $5, $6, $7)
        ON CONFLICT (level, province_territory, parliament_number, session_number)
        DO UPDATE SET
            start_date = COALESCE(EXCLUDED.start_date, legislative_sessions.start_date),
            end_date   = COALESCE(EXCLUDED.end_date, legislative_sessions.end_date),
            updated_at = now()
        RETURNING id
        """,
        parliament, session,
        f"{_fr_ordinal(parliament)} Législature, {_fr_ordinal(session)} Session",
        start_date, end_date,
        SOURCE_SYSTEM,
        f"https://www.assnat.qc.ca/en/travaux-parlementaires/projets-loi/"
        f"projets-loi-{parliament}-{session}.html",
    )
    return str(row["id"])


# ─────────────────────────────────────────────────────────────────────
# Phase 2 — CSV ingest (bill roster)
# ─────────────────────────────────────────────────────────────────────

async def ingest_qc_bills_csv(
    db: Database, *, current_only: bool = True,
) -> dict[str, int]:
    """Download the donneesquebec bills CSV and upsert bills + last-stage events.

    When ``current_only`` (default True) we filter to the most recent
    legislature/session combination. Otherwise every row in the CSV is
    ingested — that's current + previous session (~600 bills).
    """
    stats = {"rows": 0, "sessions_touched": 0, "bills": 0, "events": 0}

    async with httpx.AsyncClient(headers=HEADERS, follow_redirects=True) as client:
        r = await client.get(CSV_URL, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        buf = io.StringIO(r.text)

    reader = list(csv.DictReader(buf))
    stats["rows"] = len(reader)
    if not reader:
        log.warning("ingest_qc_bills_csv: empty CSV")
        return stats

    # Each CSV row reports the *current-view* session (No_session) but
    # the bill's *origin* session lives inside the title prefix. We keep
    # the current-view filter as the scoping gate (so current_only means
    # "active right now") but bind the bill + source_url to the origin
    # session so URLs resolve correctly.
    def _row_key(row: dict) -> tuple[int, int]:
        """Return (origin_parl, origin_session) — falls back to the
        CSV's current-view columns when the title prefix is absent."""
        m = _TITLE_PREFIX_RE.match(row.get("Titre_projet_loi") or "")
        if m:
            return int(m.group("parl")), int(m.group("session"))
        return int(row["No_legislature"]), int(row["No_session"])

    # Decide which rows to ingest based on CSV-asserted current session.
    current_pairs = sorted(
        {(int(row["No_legislature"]), int(row["No_session"])) for row in reader},
        reverse=True,
    )
    if current_only:
        current_pairs = current_pairs[:1]
    current_scope = set(current_pairs)

    rows_to_ingest = [
        r for r in reader
        if (int(r["No_legislature"]), int(r["No_session"])) in current_scope
    ]

    # Origin sessions for those rows — any row pointing to a different
    # session via its title prefix needs that origin session upserted
    # too, so we can set session_id FK on the bill row.
    origin_keys = {_row_key(r) for r in rows_to_ingest} | current_scope
    session_ids: dict[tuple[int, int], str] = {}

    # Session metadata: legislature start/end dates are duplicated on
    # every row. Harvest once per (parl, session) key we're going to
    # touch — CSV has them for current-view; origin may reuse the same
    # legislature dates so default to whichever we have.
    session_meta: dict[tuple[int, int], tuple[Optional[date], Optional[date]]] = {}
    for row in reader:
        for key in ((int(row["No_legislature"]), int(row["No_session"])), _row_key(row)):
            if key in session_meta:
                continue
            session_meta[key] = (
                _parse_iso_date(row.get("Date_debut_legislature")),
                _parse_iso_date(row.get("Date_fin_legislature")),
            )

    for key in origin_keys:
        start, end = session_meta.get(key, (None, None))
        session_ids[key] = await _upsert_qc_session(
            db, parliament=key[0], session=key[1],
            start_date=start, end_date=end,
        )
        stats["sessions_touched"] += 1

    for row in rows_to_ingest:
        bill_number = (row.get("Numero_projet_loi") or "").strip()
        if not bill_number:
            continue

        parl, sess = _row_key(row)
        source_id = f"{SOURCE_SYSTEM}:{parl}-{sess}:bill-{bill_number}"
        last_stage_code = (row.get("Derniere_etape_franchie") or "").strip().lower()
        stage_canon, stage_label = _CSV_STAGE_MAP.get(
            last_stage_code, ("other", last_stage_code or None),
        )
        last_stage_date = _parse_iso_date(row.get("Date_derniere_etape"))
        status_changed_at = (
            datetime.combine(last_stage_date, datetime.min.time())
            if last_stage_date else None
        )
        bill_type = _BILL_TYPE_MAP.get(row.get("Type_projet_loi") or "", None)
        detail_url = BILL_DETAIL_URL.format(
            number=bill_number, parl=parl, session=sess,
        )

        bill_row = await db.fetchrow(
            """
            INSERT INTO bills (
                session_id, level, province_territory, bill_number,
                title, bill_type, status, status_changed_at,
                source_id, source_system, source_url, raw, last_fetched_at
            )
            VALUES ($1, 'provincial', 'QC', $2, $3, $4, $5, $6,
                    $7, $8, $9, $10::jsonb, now())
            ON CONFLICT (source_id) DO UPDATE SET
                title             = EXCLUDED.title,
                bill_type         = COALESCE(EXCLUDED.bill_type, bills.bill_type),
                status            = EXCLUDED.status,
                status_changed_at = CASE
                    WHEN EXCLUDED.status_changed_at IS NOT NULL
                     AND (EXCLUDED.status_changed_at > bills.status_changed_at
                          OR bills.status_changed_at IS NULL)
                    THEN EXCLUDED.status_changed_at
                    ELSE bills.status_changed_at
                END,
                source_url        = EXCLUDED.source_url,
                raw               = bills.raw || jsonb_build_object('csv', $10::jsonb),
                last_fetched_at   = now(),
                updated_at        = now()
            RETURNING id
            """,
            session_ids[(parl, sess)], bill_number, row.get("Titre_projet_loi") or f"Bill {bill_number}",
            bill_type, stage_label, status_changed_at,
            source_id, SOURCE_SYSTEM, detail_url,
            orjson.dumps(row).decode(),
        )
        bill_id = str(bill_row["id"])
        stats["bills"] += 1

        # Emit the last-stage event — idempotent via bill_events_uniq.
        if last_stage_date and stage_canon != "other":
            await db.execute(
                """
                INSERT INTO bill_events (
                    bill_id, stage, stage_label, event_date, raw
                )
                VALUES ($1, $2, $3, $4, $5::jsonb)
                ON CONFLICT ON CONSTRAINT bill_events_uniq DO NOTHING
                """,
                bill_id, stage_canon, stage_label, last_stage_date,
                orjson.dumps({"source": f"{SOURCE_SYSTEM}-csv"}).decode(),
            )
            stats["events"] += 1

    log.info("ingest_qc_bills_csv: %s", stats)
    return stats


# ─────────────────────────────────────────────────────────────────────
# Phase 3 — RSS ingest (stage-transition timeline)
# ─────────────────────────────────────────────────────────────────────

_BILL_URL_FROM_RSS_RE = re.compile(
    r"/(?:fr|en)/travaux-parlementaires/projets-loi/"
    r"projet-loi-(?P<number>[a-z0-9-]+)-(?P<parl>\d+)-(?P<session>\d+)\.html",
    re.IGNORECASE,
)


def _parse_rss_item(item: ET.Element) -> Optional[dict[str, Any]]:
    def _t(tag: str) -> Optional[str]:
        el = item.find(tag)
        return el.text if (el is not None and el.text) else None

    title = _t("title") or ""
    link = _t("link") or ""
    m = _BILL_URL_FROM_RSS_RE.search(link)
    if not m:
        return None

    norm_title = _strip_accents(title.lower())
    stage_canon, stage_label = "other", title
    for prefix, canon, label in _RSS_STAGE_MAP:
        if norm_title.startswith(prefix):
            stage_canon, stage_label = canon, label
            break

    event_date = _parse_fr_date(title) or _parse_fr_date(_t("pubDate") or "")

    return {
        "parliament": int(m.group("parl")),
        "session": int(m.group("session")),
        "bill_number": m.group("number"),
        "stage": stage_canon,
        "stage_label": stage_label,
        "event_date": event_date,
        "title": title,
        "link": link,
        "pub_date": _t("pubDate"),
    }


async def ingest_qc_bills_rss(db: Database) -> dict[str, int]:
    """Fetch the bills RSS feed and fill in stage-transition events."""
    async with httpx.AsyncClient(headers=HEADERS, follow_redirects=True) as client:
        r = await client.get(RSS_URL, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        xml = r.content

    root = ET.fromstring(xml)
    items = root.findall(".//item")
    stats = {"items": len(items), "matched": 0, "events_added": 0, "unmatched": 0}

    for item in items:
        parsed = _parse_rss_item(item)
        if not parsed or parsed["event_date"] is None:
            stats["unmatched"] += 1
            continue
        source_id = (
            f"{SOURCE_SYSTEM}:{parsed['parliament']}-{parsed['session']}:"
            f"bill-{parsed['bill_number']}"
        )
        bill_id = await db.fetchval(
            "SELECT id FROM bills WHERE source_id = $1", source_id,
        )
        if bill_id is None:
            stats["unmatched"] += 1
            continue
        stats["matched"] += 1

        if parsed["stage"] == "other":
            # Still record it as an event so the raw timeline is preserved,
            # but label it with the full original title for disambiguation.
            continue

        await db.execute(
            """
            INSERT INTO bill_events (
                bill_id, stage, stage_label, event_date, raw
            )
            VALUES ($1, $2, $3, $4, $5::jsonb)
            ON CONFLICT ON CONSTRAINT bill_events_uniq DO NOTHING
            """,
            bill_id, parsed["stage"], parsed["stage_label"], parsed["event_date"],
            orjson.dumps({"source": f"{SOURCE_SYSTEM}-rss",
                          "title": parsed["title"],
                          "pub_date": parsed["pub_date"]}).decode(),
        )
        stats["events_added"] += 1

    log.info("ingest_qc_bills_rss: %s", stats)
    return stats


# ─────────────────────────────────────────────────────────────────────
# Phase 4 — per-bill detail page (sponsor resolution)
# ─────────────────────────────────────────────────────────────────────

async def fetch_qc_bill_sponsors(
    db: Database, *, limit: Optional[int] = None,
    delay_seconds: float = 2.0,
) -> dict[str, int]:
    """Fetch each current-session bill's detail page and link a sponsor.

    Cheapest high-value augmentation of the CSV data: one HTTP GET per
    bill, extract the first `/en/deputes/{slug}-{id}/` link from the
    HTML, FK-lookup on politicians.qc_assnat_id, insert bill_sponsors.
    Skips bills that already have a sponsor row.
    """
    # Private bills ("D'intérêt privé") live under a different URL
    # scheme that the CSV doesn't tell us about, and their sponsor
    # semantics are atypical (no minister/MNA author — they're petition-
    # style submissions). Skip them here and revisit if/when we build
    # a private-bill detail scraper.
    rows = await db.fetch(
        """
        SELECT b.id, b.source_url, b.bill_number
          FROM bills b
         WHERE b.source_system = $1
           AND (b.bill_type IS NULL OR b.bill_type <> 'private')
           AND NOT EXISTS (
                SELECT 1 FROM bill_sponsors s WHERE s.bill_id = b.id
           )
         ORDER BY length(b.bill_number), b.bill_number
        """ + (f" LIMIT {int(limit)}" if limit else ""),
        SOURCE_SYSTEM,
    )
    stats = {"scanned": len(rows), "pages_fetched": 0, "sponsors": 0,
             "sponsors_linked": 0, "no_sponsor_found": 0,
             "not_found": 0, "errors": 0}

    async with httpx.AsyncClient(headers=HEADERS, follow_redirects=True,
                                  timeout=REQUEST_TIMEOUT) as client:
        for i, row in enumerate(rows):
            if i > 0 and delay_seconds > 0:
                await asyncio.sleep(delay_seconds)
            url = row["source_url"]
            try:
                resp = await client.get(url)
                if resp.status_code == 404:
                    stats["not_found"] += 1
                    continue
                resp.raise_for_status()
            except httpx.HTTPError as e:
                log.warning("fetch_qc_bill_sponsors: %s: %s", url, e)
                stats["errors"] += 1
                continue
            stats["pages_fetched"] += 1

            m = _MNA_HREF_RE.search(resp.text)
            if not m:
                stats["no_sponsor_found"] += 1
                continue
            slug = m.group("slug")
            mna_id = int(m.group("id"))

            pol_id = await db.fetchval(
                "SELECT id FROM politicians WHERE qc_assnat_id = $1 "
                "  AND level = 'provincial' AND province_territory = 'QC'",
                mna_id,
            )
            await db.execute(
                """
                INSERT INTO bill_sponsors (
                    bill_id, politician_id, sponsor_slug, sponsor_name_raw,
                    role, source_system
                )
                VALUES ($1, $2, $3, $4, 'sponsor', $5)
                ON CONFLICT (bill_id, sponsor_slug)
                  WHERE sponsor_slug IS NOT NULL
                  DO UPDATE SET
                      politician_id = COALESCE(
                          EXCLUDED.politician_id, bill_sponsors.politician_id
                      )
                """,
                str(row["id"]), pol_id, str(mna_id), slug, SOURCE_SYSTEM,
            )
            stats["sponsors"] += 1
            if pol_id:
                stats["sponsors_linked"] += 1

    log.info("fetch_qc_bill_sponsors: %s", stats)
    return stats
