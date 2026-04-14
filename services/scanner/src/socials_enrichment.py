"""Socials enrichment from external sources (Team B backfill).

Phase 5 landed `politician_socials` with only 79 rows because ourcommons.ca
and assembly.ab.ca render their contact cards via JavaScript, which the
HTML-regex discovery pass couldn't see. This module backfills handles from
three additional sources:

  * Wikidata — SPARQL for every sitting Canadian federal/provincial/
    territorial legislator + their social properties (P2002/P2013/P2003/
    P2397/P4033/P7085/P6634/P12361).
  * openparliament.ca — MP JSON detail pages surface `other_info.twitter`
    (and sometimes a personal `web_site`) for most current federal MPs.
  * canada.masto.host — best-effort Mastodon lookup by candidate handle
    variations derived from each politician's name.

Every discovered (platform, url) pair is funneled through
`socials.upsert_social()`, so canonicalisation + `social_added` change
logging stays consistent with the Phase 5 normaliser.

Public API
----------
  enrich_from_wikidata(db, *, level=None)     -> int
  enrich_from_openparl(db)                     -> int
  enrich_mastodon_candidates(db)               -> int
  enrich_all_socials(db)                       -> None  (runs all three)

All three are re-entrant and skip politicians whose name+level can't be
matched back to our `politicians` table.
"""
from __future__ import annotations

import asyncio
import logging
import re
import unicodedata
from collections import Counter
from typing import Any, Iterable, Optional

import httpx
import orjson
from rich.console import Console

from .db import Database
from .socials import upsert_social

log = logging.getLogger(__name__)
console = Console()


# Identify ourselves to rate-limiting intermediaries. Wikidata's SPARQL
# service in particular asks every client to provide a project URL + contact.
ENRICH_USER_AGENT = (
    "CanadianPoliticalData-SocialsEnrichment/1.0 "
    "(+https://canadianpoliticaldata.ca; admin@thebunkerops.ca)"
)


# ── Wikidata ──────────────────────────────────────────────────────────────

WIKIDATA_SPARQL = "https://query.wikidata.org/sparql"

# Position-held (P39) item IDs for every sitting Canadian legislator role.
# Verified via wbsearchentities / manual lookup on 2026-04-13.
#   federal        : member of the House of Commons of Canada
#   ontario        : member of the Ontario Provincial Parliament (MPP)
#   alberta        : member of Alberta Legislative Assembly
#   bc             : member of the Legislative Assembly of British Columbia
#   quebec         : Member of the National Assembly of Quebec
#   manitoba       : member of the Legislative Assembly of Manitoba
#   saskatchewan   : Member of the Legislative Assembly of Saskatchewan
#   nova_scotia    : member of the Nova Scotia House of Assembly
#   new_brunswick  : member of the Legislative Assembly of New Brunswick
#   pei            : member of the Legislative Assembly of Prince Edward Island
#   nl             : member of the Newfoundland and Labrador House of Assembly
#   yukon          : member of the Yukon Legislative Assembly
#   nwt            : Member of the Legislative Assembly of the Northwest Territories
#   nunavut        : Member of the Legislative Assembly of Nunavut
WIKIDATA_POSITIONS: dict[str, dict[str, Optional[str]]] = {
    "federal":        {"qid": "Q15964890", "level": "federal",    "province": None},
    "ontario":        {"qid": "Q3305347",  "level": "provincial", "province": "ON"},
    "alberta":        {"qid": "Q15964815", "level": "provincial", "province": "AB"},
    "bc":             {"qid": "Q19004821", "level": "provincial", "province": "BC"},
    "quebec":         {"qid": "Q3305338",  "level": "provincial", "province": "QC"},
    "manitoba":       {"qid": "Q19007867", "level": "provincial", "province": "MB"},
    "saskatchewan":   {"qid": "Q18675661", "level": "provincial", "province": "SK"},
    "nova_scotia":    {"qid": "Q18239264", "level": "provincial", "province": "NS"},
    "new_brunswick":  {"qid": "Q18984329", "level": "provincial", "province": "NB"},
    "pei":            {"qid": "Q21010685", "level": "provincial", "province": "PE"},
    "nl":             {"qid": "Q19403853", "level": "provincial", "province": "NL"},
    "yukon":          {"qid": "Q18608478", "level": "provincial", "province": "YT"},
    "nwt":            {"qid": "Q45308871", "level": "provincial", "province": "NT"},
    "nunavut":        {"qid": "Q45308607", "level": "provincial", "province": "NU"},
}


# Wikidata social-property -> (platform_hint, url formatter)
# The SPARQL query returns bare handles (without a leading '@'); we wrap
# each into a canonical URL so socials.upsert_social() / canonicalize() can
# normalize it the same way it does for Open North payloads.
WIKIDATA_SOCIAL_PROPS: tuple[tuple[str, str, str], ...] = (
    # (var_name, platform_hint, url_template)
    ("twitter",   "twitter",   "https://twitter.com/{value}"),
    ("facebook",  "facebook",  "https://www.facebook.com/{value}"),
    ("instagram", "instagram", "https://www.instagram.com/{value}"),
    ("youtube",   "youtube",   "https://www.youtube.com/channel/{value}"),
    ("tiktok",    "tiktok",    "https://www.tiktok.com/@{value}"),
    ("linkedin",  "linkedin",  "https://www.linkedin.com/in/{value}"),
    ("mastodon",  "mastodon",  "_mastodon_"),   # special handling
    ("bluesky",   "bluesky",   "https://bsky.app/profile/{value}"),
)


def _build_wikidata_sparql(qids: Iterable[str]) -> str:
    """Build the SPARQL pulling every current legislator with any social."""
    values = " ".join(f"wd:{q}" for q in qids)
    return f"""
SELECT DISTINCT ?person ?personLabel ?posLabel
  ?twitter ?facebook ?instagram ?youtube ?tiktok ?linkedin ?mastodon ?bluesky
WHERE {{
  VALUES ?pos {{ {values} }}
  ?person p:P39 ?ps .
  ?ps ps:P39 ?pos .
  FILTER NOT EXISTS {{ ?ps pq:P582 ?end }}
  FILTER EXISTS {{
      {{ ?person wdt:P2002 [] }} UNION {{ ?person wdt:P2013 [] }}
    UNION {{ ?person wdt:P2003 [] }} UNION {{ ?person wdt:P2397 [] }}
    UNION {{ ?person wdt:P7085 [] }} UNION {{ ?person wdt:P6634 [] }}
    UNION {{ ?person wdt:P4033 [] }} UNION {{ ?person wdt:P12361 [] }}
  }}
  OPTIONAL {{ ?person wdt:P2002 ?twitter . }}
  OPTIONAL {{ ?person wdt:P2013 ?facebook . }}
  OPTIONAL {{ ?person wdt:P2003 ?instagram . }}
  OPTIONAL {{ ?person wdt:P2397 ?youtube . }}
  OPTIONAL {{ ?person wdt:P7085 ?tiktok . }}
  OPTIONAL {{ ?person wdt:P6634 ?linkedin . }}
  OPTIONAL {{ ?person wdt:P4033 ?mastodon . }}
  OPTIONAL {{ ?person wdt:P12361 ?bluesky . }}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en". }}
}}
"""


def _mastodon_url_from_address(addr: str) -> Optional[str]:
    """'user@infosec.exchange' -> 'https://infosec.exchange/@user'."""
    addr = addr.strip().lstrip("@")
    if "@" not in addr:
        return None
    user, _, host = addr.partition("@")
    user = user.strip()
    host = host.strip().lower()
    if not user or not host:
        return None
    return f"https://{host}/@{user}"


def _normalize_name(name: str) -> str:
    """Lower-cased, punctuation-stripped, accent-folded name key.

    Wikidata stores names with diacritics; our DB usually matches. But
    Wikidata may omit middle names or reorder French-preposition names,
    so we reduce both sides to ``unicode-normalized ascii lower word list``
    and key on the sorted-unique-token tuple. That lets "Joël Lightbound"
    and "Joel Lightbound" collide while still preserving distinctness
    between e.g. "Mark Carney" and "Mark Carney Jr."
    """
    if not name:
        return ""
    decomposed = unicodedata.normalize("NFKD", name)
    ascii_only = "".join(c for c in decomposed if not unicodedata.combining(c))
    cleaned = re.sub(r"[^A-Za-z\s'\-]", " ", ascii_only).lower()
    tokens = [t for t in re.split(r"[\s'\-]+", cleaned) if t]
    # We key on the whole token sequence; middle-initials are dropped.
    tokens = [t for t in tokens if len(t) > 1]
    return " ".join(tokens)


async def _load_politician_index(
    db: Database,
    *,
    level: Optional[str] = None,
) -> dict[tuple[str, str], str]:
    """Return {(level, name_key): politician_id} for active politicians.

    When two distinct politicians share a name_key within a level, we
    keep the first and skip ambiguous matches; they'll be reported during
    enrichment.
    """
    where = "WHERE is_active = true"
    args: list[Any] = []
    if level is not None:
        where += " AND level = $1"
        args.append(level)
    rows = await db.fetch(
        f"SELECT id, name, level, province_territory FROM politicians {where}",
        *args,
    )
    idx: dict[tuple[str, str], str] = {}
    dupes: set[tuple[str, str]] = set()
    for r in rows:
        key = (r["level"], _normalize_name(r["name"]))
        if not key[1]:
            continue
        if key in idx:
            dupes.add(key)
            continue
        idx[key] = str(r["id"])
    if dupes:
        log.info("ambiguous name_keys skipped during indexing: %d", len(dupes))
    return idx


async def enrich_from_wikidata(
    db: Database,
    *,
    level: Optional[str] = None,
) -> int:
    """Pull social handles for every Canadian legislator on Wikidata.

    Matches Wikidata person -> local politician by level + normalised name.
    Returns the number of (politician, platform, handle) rows inserted or
    updated via upsert_social().
    """
    # Decide which positions to query based on the `level` filter.
    if level == "federal":
        active = {k: v for k, v in WIKIDATA_POSITIONS.items() if v["level"] == "federal"}
    elif level == "provincial":
        active = {k: v for k, v in WIKIDATA_POSITIONS.items() if v["level"] == "provincial"}
    else:
        active = WIKIDATA_POSITIONS

    qid_to_level = {v["qid"]: v["level"] for v in active.values()}
    qids = list(qid_to_level.keys())
    if not qids:
        return 0

    sparql = _build_wikidata_sparql(qids)

    console.print(
        f"[cyan]Querying Wikidata SPARQL for {len(qids)} position items "
        f"(level filter={level or 'all'})…[/cyan]"
    )

    async with httpx.AsyncClient(
        timeout=httpx.Timeout(120.0, connect=10.0),
        headers={
            "User-Agent": ENRICH_USER_AGENT,
            "Accept": "application/sparql-results+json",
        },
    ) as client:
        try:
            resp = await client.get(WIKIDATA_SPARQL, params={"query": sparql})
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPError as exc:
            console.print(f"[red]Wikidata SPARQL request failed: {exc}[/red]")
            return 0

    bindings = data.get("results", {}).get("bindings", [])
    console.print(f"[cyan]Wikidata returned {len(bindings)} person rows[/cyan]")
    if not bindings:
        return 0

    # Build a {qid: level} we need for matching, plus load the politician
    # index scoped to the relevant levels.
    idx = await _load_politician_index(db, level=level)
    console.print(
        f"[cyan]Indexed {len(idx)} active politicians "
        f"(level filter={level or 'all'})[/cyan]"
    )

    # Collapse multiple (person, pos) SPARQL rows into one per (person, level)
    # so we don't double-upsert the same handle.
    persons: dict[str, dict[str, Any]] = {}
    skipped_unmatched = 0
    ambiguous_handles = Counter()

    for b in bindings:
        person_uri = b.get("person", {}).get("value", "")
        name = b.get("personLabel", {}).get("value", "")
        pos_label = b.get("posLabel", {}).get("value", "")
        # Map the position label back to our level — "Legislative Assembly"
        # etc. all correspond to provincial; only the House of Commons is
        # federal. Cheaper than resolving the Q-id again.
        row_level = "federal" if "House of Commons" in pos_label else "provincial"

        name_key = _normalize_name(name)
        if not name_key:
            continue
        match_id = idx.get((row_level, name_key))
        if match_id is None:
            skipped_unmatched += 1
            continue

        slot = persons.setdefault(person_uri, {
            "politician_id": match_id,
            "name": name,
            "handles": {},   # {(platform, handle): url}
        })

        for var, platform_hint, url_tmpl in WIKIDATA_SOCIAL_PROPS:
            val = b.get(var, {}).get("value")
            if not val:
                continue
            if platform_hint == "mastodon":
                url = _mastodon_url_from_address(val)
                if url is None:
                    continue
            else:
                url = url_tmpl.format(value=val)
            slot["handles"].setdefault((platform_hint, val), url)

    # Detect persons with a suspiciously large number of distinct handles
    # (a classic Wikidata disambiguation-bug symptom).
    for uri, slot in persons.items():
        n = len(slot["handles"])
        if n >= 10:
            log.warning(
                "Wikidata %s (%s) has %d distinct handles — "
                "possible disambiguation/merge issue", uri, slot["name"], n,
            )

    # Upsert — Wikidata SPARQL replies are sent serially (we already have
    # the payload); database writes themselves are cheap. Do them one-at-a-
    # time to keep the log ordered.
    inserted = 0
    counts: Counter[str] = Counter()
    per_person_counts = Counter()
    for uri, slot in persons.items():
        pid = slot["politician_id"]
        for (platform_hint, _handle), url in slot["handles"].items():
            try:
                canon = await upsert_social(db, pid, platform_hint, url)
            except Exception as exc:
                log.warning("wikidata upsert failed for %s %s: %s", pid, url, exc)
                continue
            if canon is None:
                counts["other"] += 1
                continue
            counts[canon.platform] += 1
            per_person_counts[pid] += 1
            inserted += 1

    console.print(
        f"[green]✓ Wikidata enrichment: matched {len(persons)} persons, "
        f"upserted {inserted} rows, unmatched={skipped_unmatched}[/green]"
    )
    if counts:
        for plat, n in counts.most_common():
            console.print(f"    {plat:<10} {n}")

    # Anomaly flagging: any politician receiving >= 10 socials in a single run
    # deserves a human look (likely a Wikidata merge error or a very
    # chronically-online MP).
    big = [(pid, n) for pid, n in per_person_counts.items() if n >= 10]
    if big:
        console.print(
            f"[yellow]⚠ {len(big)} politicians got 10+ handles this run — "
            "investigate for Wikidata disambiguation bugs:[/yellow]"
        )
        for pid, n in sorted(big, key=lambda x: -x[1])[:5]:
            row = await db.fetchrow("SELECT name FROM politicians WHERE id = $1", pid)
            console.print(f"    {row['name'] if row else pid}: {n} handles")

    return inserted


# ── openparliament.ca ─────────────────────────────────────────────────────

OPENPARL_BASE = "https://openparliament.ca"
OPENPARL_CONCURRENCY = 3


async def _list_openparl_politicians(client: httpx.AsyncClient) -> list[dict[str, Any]]:
    """Walk the paginated /politicians/ listing.

    Each page yields objects with `name` + `url` (the slug path). We only
    need the URL → detail lookup, but `name` helps us disambiguate.
    """
    out: list[dict[str, Any]] = []
    next_url = "/politicians/?format=json&limit=500"
    while next_url:
        resp = await client.get(OPENPARL_BASE + next_url)
        resp.raise_for_status()
        data = resp.json()
        out.extend(data.get("objects", []))
        pagination = data.get("pagination", {}) or {}
        next_url = pagination.get("next_url")
    return out


async def enrich_from_openparl(db: Database) -> int:
    """Fetch openparliament.ca detail pages for federal MPs missing socials."""

    # Build a name -> politician_id map for federal MPs (active only).
    rows = await db.fetch(
        """
        SELECT id, name FROM politicians
         WHERE is_active = true AND level = 'federal'
        """
    )
    by_name: dict[str, str] = {}
    for r in rows:
        key = _normalize_name(r["name"])
        if key and key not in by_name:
            by_name[key] = str(r["id"])

    if not by_name:
        console.print("[yellow]No active federal politicians to enrich[/yellow]")
        return 0

    sem = asyncio.Semaphore(OPENPARL_CONCURRENCY)
    inserted = 0
    counts: Counter[str] = Counter()
    matched = 0
    unmatched = 0

    async with httpx.AsyncClient(
        timeout=httpx.Timeout(30.0, connect=10.0),
        headers={
            "User-Agent": ENRICH_USER_AGENT,
            "Accept": "application/json",
        },
        follow_redirects=True,
    ) as client:
        console.print("[cyan]Listing openparliament.ca MPs…[/cyan]")
        try:
            listing = await _list_openparl_politicians(client)
        except httpx.HTTPError as exc:
            console.print(f"[red]openparliament listing failed: {exc}[/red]")
            return 0
        console.print(f"[cyan]  {len(listing)} MPs listed[/cyan]")

        async def handle_one(entry: dict[str, Any]) -> int:
            nonlocal matched, unmatched
            name = entry.get("name", "")
            slug_url = entry.get("url", "")
            if not name or not slug_url:
                return 0

            key = _normalize_name(name)
            pid = by_name.get(key)
            if pid is None:
                unmatched += 1
                return 0

            async with sem:
                detail_url = OPENPARL_BASE + slug_url + "?format=json"
                try:
                    resp = await client.get(detail_url)
                    if resp.status_code == 404:
                        return 0
                    resp.raise_for_status()
                    detail = resp.json()
                except httpx.HTTPError as exc:
                    log.debug("openparl detail failed for %s: %s", slug_url, exc)
                    return 0

            matched += 1
            n_inserted_here = 0

            other = detail.get("other_info", {}) or {}
            # openparliament stores list-of-values. Typical keys:
            #   twitter:   ['SomeHandle']
            #   facebook:  (rare, usually URL)
            for raw_key, platform_hint, url_tmpl in (
                ("twitter",   "twitter",   "https://twitter.com/{value}"),
                ("facebook",  "facebook",  "https://www.facebook.com/{value}"),
                ("instagram", "instagram", "https://www.instagram.com/{value}"),
                ("youtube",   "youtube",   "https://www.youtube.com/{value}"),
            ):
                values = other.get(raw_key)
                if not values:
                    continue
                if isinstance(values, str):
                    values = [values]
                for v in values:
                    if not v:
                        continue
                    # If the stored value looks like a URL, use it directly.
                    if v.startswith("http://") or v.startswith("https://") or "/" in v:
                        url = v
                    else:
                        url = url_tmpl.format(value=v)
                    try:
                        canon = await upsert_social(db, pid, platform_hint, url)
                    except Exception as exc:
                        log.warning("openparl upsert failed for %s %s: %s", pid, url, exc)
                        continue
                    if canon is not None:
                        counts[canon.platform] += 1
                        n_inserted_here += 1

            # `links` sometimes includes a Twitter / Facebook / Instagram URL.
            for link in detail.get("links") or []:
                url = (link or {}).get("url") or ""
                if not url:
                    continue
                # Skip the ourcommons.ca official page — not a social.
                if "ourcommons.ca" in url:
                    continue
                try:
                    canon = await upsert_social(db, pid, None, url)
                except Exception as exc:
                    log.warning("openparl link upsert failed for %s %s: %s", pid, url, exc)
                    continue
                if canon is not None:
                    counts[canon.platform] += 1
                    n_inserted_here += 1

            return n_inserted_here

        results = await asyncio.gather(*(handle_one(e) for e in listing))
        inserted = sum(results)

    console.print(
        f"[green]✓ openparliament enrichment: matched {matched} MPs, "
        f"upserted {inserted} rows (unmatched names: {unmatched})[/green]"
    )
    if counts:
        for plat, n in counts.most_common():
            console.print(f"    {plat:<10} {n}")
    return inserted


# ── canada.masto.host lookup ──────────────────────────────────────────────

MASTO_HOST = "canada.masto.host"
MASTO_LOOKUP_URL = f"https://{MASTO_HOST}/api/v1/accounts/lookup"
MASTO_CONCURRENCY = 4
# Any valid Mastodon account receives a display_name that usually contains
# at least one of the politician's name tokens; require >= 50 % overlap so
# we don't attach random accounts that happen to match a common handle.
MASTO_DISPLAY_MIN_OVERLAP = 0.5


def _mastodon_candidate_handles(name: str) -> list[str]:
    """Yield a handful of guessed account names for a given full name.

    Example 'Marie-France Lalonde' ->
        ['MarieFranceLalonde', 'Marie_France_Lalonde',
         'mlalonde', 'lalonde', 'mariefrance']
    Duplicates eliminated; returns an ordered list.
    """
    if not name:
        return []
    cleaned = unicodedata.normalize("NFKD", name)
    cleaned = "".join(c for c in cleaned if not unicodedata.combining(c))
    tokens = [t for t in re.split(r"[\s'\-]+", cleaned) if t]
    if not tokens:
        return []

    first = tokens[0]
    last = tokens[-1]
    candidates = [
        "".join(tokens),
        "_".join(tokens),
        f"{first[0]}{last}".lower() if len(first) > 0 else None,
        last,
        f"{first}{last}",
        f"{first}_{last}",
    ]
    out: list[str] = []
    seen: set[str] = set()
    for c in candidates:
        if not c:
            continue
        c = c.strip()
        if len(c) < 3:
            continue
        low = c.lower()
        if low in seen:
            continue
        seen.add(low)
        out.append(c)
    return out[:5]  # hard cap; we don't need to hammer the server


async def _mastodon_lookup(
    client: httpx.AsyncClient,
    handle: str,
) -> Optional[dict[str, Any]]:
    """Single canada.masto.host lookup. Returns account dict or None."""
    try:
        resp = await client.get(MASTO_LOOKUP_URL, params={"acct": handle})
    except httpx.HTTPError:
        return None
    if resp.status_code == 404:
        return None
    if resp.status_code != 200:
        return None
    try:
        data = resp.json()
    except ValueError:
        return None
    if not isinstance(data, dict) or "error" in data or not data.get("id"):
        return None
    return data


async def enrich_mastodon_candidates(db: Database) -> int:
    """Probe canada.masto.host for plausible handles of our politicians."""
    rows = await db.fetch(
        """
        SELECT p.id, p.name
          FROM politicians p
          LEFT JOIN politician_socials ps
            ON ps.politician_id = p.id AND ps.platform = 'mastodon'
         WHERE p.is_active = true AND ps.id IS NULL
        """
    )
    if not rows:
        console.print("[yellow]No politicians missing a Mastodon handle[/yellow]")
        return 0
    console.print(
        f"[cyan]Checking canada.masto.host for {len(rows)} politicians…[/cyan]"
    )

    sem = asyncio.Semaphore(MASTO_CONCURRENCY)
    inserted = 0
    checked_candidates = 0
    found_accounts = 0

    async with httpx.AsyncClient(
        timeout=httpx.Timeout(15.0, connect=8.0),
        headers={
            "User-Agent": ENRICH_USER_AGENT,
            "Accept": "application/json",
        },
        follow_redirects=True,
    ) as client:

        async def try_one(row) -> int:
            nonlocal checked_candidates, found_accounts
            pid = str(row["id"])
            name = row["name"] or ""
            name_tokens = {
                t.lower()
                for t in _normalize_name(name).split()
                if len(t) >= 3
            }
            if not name_tokens:
                return 0

            inserted_here = 0
            for handle in _mastodon_candidate_handles(name):
                async with sem:
                    checked_candidates += 1
                    account = await _mastodon_lookup(client, handle)
                    # polite pacing
                    await asyncio.sleep(0.05)
                if not account:
                    continue

                # Confirmation: does the display_name actually look like
                # this politician? If not, we refuse to attach the handle.
                disp = (account.get("display_name") or "").lower()
                disp_tokens = {
                    t for t in re.split(r"[^a-z0-9]+", disp) if len(t) >= 3
                }
                overlap = (
                    len(name_tokens & disp_tokens) / max(1, len(name_tokens))
                )
                if overlap < MASTO_DISPLAY_MIN_OVERLAP:
                    continue

                found_accounts += 1
                username = account.get("username") or handle
                url = account.get("url") or f"https://{MASTO_HOST}/@{username}"
                try:
                    canon = await upsert_social(db, pid, "mastodon", url)
                except Exception as exc:
                    log.warning("mastodon upsert failed for %s: %s", pid, exc)
                    continue
                if canon is not None:
                    inserted_here += 1
                    # One confirmed account is enough — don't keep probing.
                    break
            return inserted_here

        results = await asyncio.gather(*(try_one(r) for r in rows))
        inserted = sum(results)

    console.print(
        f"[green]✓ Mastodon enrichment: probed {checked_candidates} candidate "
        f"handles, matched {found_accounts} accounts, upserted {inserted}[/green]"
    )
    return inserted


# ── Orchestrator ──────────────────────────────────────────────────────────

async def enrich_all_socials(db: Database) -> None:
    """Run wikidata → openparl → mastodon in that order."""
    total_wiki = await enrich_from_wikidata(db)
    total_parl = await enrich_from_openparl(db)
    total_masto = await enrich_mastodon_candidates(db)
    console.print(
        f"[bold green]Enrichment complete — "
        f"wikidata={total_wiki} openparl={total_parl} mastodon={total_masto}"
        f"[/bold green]"
    )
