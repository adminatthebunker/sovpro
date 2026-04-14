"""Ontario gap-filler — MPP personal-URL + socials enrichment.

Background
----------
Open North's ``/representatives/ontario-legislature/`` indexes all 124 sitting
MPPs but exposes only their ``ola.org`` institutional profile URL — no
personal / campaign sites. The previous ``ola.org`` scraping attempt
(``enrich.enrich_ontario_mpps``) returned 0 hits because
``/en/members/all/<slug>`` pages do not link outbound to a member's personal
site (verified 2026-04-13: a fresh fetch of ``doug-ford`` produced 5 "personal-
looking" URLs, none of which were member sites — all were boilerplate like
``https://ogp.me/ns#`` or Drupal-module references).

This gap-filler pushes past that by combining five open-web sources in order
of yield and reliability:

  1a. **Ontario Liberal caucus roster** (``ontarioliberal.ca/liberal-mpps``):
      clean anchor-text "Website" link per MPP. Covers ~14 OLP MPPs at ~100%.

  1b. **Ontario NDP caucus roster** (``ondpcaucus.com``):
      same shape — "Visit website" anchor next to each MPP's name/riding
      card. Covers the 27 sitting NDP MPPs at ~100%.

  2. **Wikipedia per-MPP articles** (MediaWiki ``parse`` API):
     a) validate each candidate article against Wikipedia categories
        containing "Ontario MPP" / "Ontario Legislative Assembly" / etc. OR a
        wikitext mention of "Legislative Assembly of Ontario" to reject
        disambiguation collisions (e.g. Jonathan Tsao → Toronto councillor,
        Steve Clark → Def Leppard guitarist);
     b) extract the personal URL from the infobox ``| website =`` field or
        the "External links" section, with aggressive host-filtering for
        news/social/institutional/party URLs.

  3. **DNS-probe fallback** using naming patterns observed in sources 1-2:
     ``{first}{last}mpp.ca`` → ``{first}{last}.ca`` → ``{last}mpp.ca`` →
     ``{first}{last}mpp.com`` → ``{first}{last}.com``. Each candidate is
     confirmed by requiring both the first-name and the last-name token to
     appear on the fetched HTML — prevents false positives like
     ``bailey.ca`` (a surname-squat domain) for Robert Bailey.

  4. **Wikidata SPARQL** (P39=Q3305347): collects ``P856`` websites plus
     social properties (P2002 / P2013 / P2003 / P2397 / P7085 / P6634 /
     P4033 / P12361). P856 values are often the party URL (e.g. Doug Ford
     → ontariopc.ca) so they are passed through the same is_personal()
     filter that rejects party boilerplate. Socials are upserted directly
     through ``socials.upsert_social()``.

Hit-rate context (roster of 123 active ON MPPs as of 2026-04-13):
  - Wikipedia-only path: ~35/123 (~28 %) personal URLs with 0 false positives
    after category validation.
  - Adding Liberal caucus: +14 (overlapping ~5 with Wikipedia) ≈ 40 total.
  - Adding DNS-probe: empirically yields another ~20-30 PC-heavy MPPs whose
    Wikipedia pages are stubs without External Links.

The implementation is idempotent: results are attached through
``shared.attach_website`` + ``shared.attach_socials``, both of which
ON CONFLICT DO NOTHING on insert and COALESCE-on-update for
``politicians.personal_url``. Safe to re-run any number of times.
"""
from __future__ import annotations

import asyncio
import logging
import re
import unicodedata
from typing import Optional

import httpx
from rich.console import Console
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
)

from ..db import Database
from .shared import BROWSER_UA, attach_socials, attach_website

log = logging.getLogger(__name__)
console = Console()


# ─────────────────────────────────────────────────────────────────────
# URL filters — shared across sources.
# ─────────────────────────────────────────────────────────────────────

# A "personal" URL is anything NOT on these hosts:
_BAD_HOST_SUBSTRINGS: tuple[str, ...] = (
    # Institutional / shared infrastructure
    "ola.org", "wikidata", "wikipedia", "wikimedia", "commons.",
    "parl.gc.ca", "ourcommons", ".gc.ca",
    "ontario.ca", "gov.on.ca", "elections.on.ca", "elections.ca",
    "toronto.ca", "ottawa.ca", "hamilton.ca", "mississauga.ca",
    # Party / caucus
    "ontariopc.ca", "ontariondp.ca", "ontarioliberal.ca", "onliberal.ca",
    "gpo.ca", "greenparty.on.ca", "liberal.ca", "ndp.ca",
    "onndp.ca", "ontario.liberal.ca", "conservative.ca",
    # Socials
    "facebook.com", "fb.com", "twitter.com", "x.com", "instagram.com",
    "youtube.com", "youtu.be", "tiktok.com", "linkedin.com",
    "threads.net", "bsky.app", "mastodon", "t.me", "whatsapp",
    # Archives / aggregators / media platforms
    "archive.org", "wayback", "openparliament.ca", "openpolitics.ca",
    "flickr.com", "vimeo.com", "issuu.com", "slideshare", "scribd",
    "soundcloud", "patreon", "gofundme", "actblue", "donorbox",
    # Famous false-positive from v1 (Steve Clark → Def Leppard guitarist)
    "defleppard.com",
)

_NEWS_SUBSTRINGS: tuple[str, ...] = (
    ".cbc.ca", "thestar.com", "theglobeandmail.com", "globalnews.ca",
    "nationalpost.com", "ctvnews.ca", "cp24.com", "theguardian.com",
    "nytimes.com", "washingtonpost.com", "macleans.ca", "toronto.com",
    "hamiltonspectator.com", "niagarafallsreview.ca", "thespec.com",
    "therecord.com", "theintelligencer.ca", "winnipegfreepress.com",
    "reuters.com", "apnews.com", "radio-canada.ca", "thelocal.ca",
    "ici.radio-canada.ca", "windsorstar.com", "ottawacitizen.com",
    "torontosun.com", "simcoe.com", "barrietoday.com",
    "orilliamatters.com", "baytoday.ca", "newmarkettoday.ca",
    "sudbury.com", "northernlife.ca", "kenoraonline.com",
    "thunderbay.ca", "thunderbaynewswatch.com", "welland.com",
    "niagarathisweek.com", "thecord.ca", "cjnews.com",
    "leadinginfluence.com", "newswire.ca", "prnewswire.com",
    "thechronicleherald.ca", "nugget.ca", "chathamdailynews.ca",
    "tvo.org", "stcatharinesstandard.ca", "thesudburystar.com",
    "thepeterboroughexaminer.com", "kingstonist.com",
    "stittsvillecentral.ca", "saultstar.com", "sooeveningnews.com",
    "brantfordexpositor.ca", "theweathernetwork", "lfpress.com",
    "doi.org", "jstor.org", "worldcat.org", "scholar.google",
    "books.google", "archive.today",
)


def _is_personal_url(u: str) -> bool:
    """Return True if the URL looks like an MPP's personal/constituency site."""
    if not u:
        return False
    low = u.lower().strip()
    if not low.startswith(("http://", "https://")):
        return False
    if low.endswith((".pdf", ".jpg", ".png", ".gif", ".mp4", ".mp3")):
        return False
    for bad in _BAD_HOST_SUBSTRINGS:
        if bad in low:
            return False
    for news in _NEWS_SUBSTRINGS:
        if news in low:
            return False
    return True


def _clean_url(u: str) -> str:
    for ch in ('"', ".", ",", ")", "]", "|", "}", "{", ">", "'", "`"):
        u = u.rstrip(ch)
    return u


# ─────────────────────────────────────────────────────────────────────
# Name normalisation (re-used across name→Wikidata and name→Wikipedia).
# ─────────────────────────────────────────────────────────────────────

def _norm_name(name: str) -> str:
    """Aggressive fold: accents stripped, lower-case, punctuation→space."""
    if not name:
        return ""
    n = unicodedata.normalize("NFKD", name)
    n = "".join(c for c in n if not unicodedata.combining(c))
    n = re.sub(r"[^a-z0-9\s]+", " ", n.lower())
    toks = [t for t in n.split() if len(t) >= 2]
    return " ".join(toks)


def _norm_loose(name: str) -> str:
    """Drop single-letter tokens (middle initials)."""
    return " ".join(t for t in _norm_name(name).split() if len(t) > 1)


def _split_first_last(name: str) -> tuple[Optional[str], Optional[str]]:
    """Return (first, last) from a name like "Mary-Margaret McMahon"."""
    base = re.sub(
        r"^\s*(?:Hon\.|Honourable|Premier|Speaker|Dr\.|Mr\.|Mrs\.|Ms\.)\s+",
        "",
        name,
        flags=re.IGNORECASE,
    )
    # Drop middle-initials: "Charmaine A. Williams" → "Charmaine Williams"
    base = re.sub(r"\s+[A-Z]\.\s+", " ", base)
    # Drop parenthetical nicknames: "Jennifer (Jennie) Stevens" → "Jennifer Stevens"
    base = re.sub(r"\s*\([^)]+\)\s*", " ", base).strip()
    parts = base.split()
    if not parts:
        return None, None
    if len(parts) == 1:
        return parts[0], None
    return parts[0], parts[-1]


def _all_tokens(name: str) -> list[str]:
    """Return the sequence of tokens after dropping honorifics + parentheticals.

    Used by the DNS probe to generate slug variants for multi-part surnames
    like ``Dawn Gallagher Murphy`` (three tokens). ``_split_first_last``
    collapses to first+last which misses patterns like
    ``dawngallaghermurphympp.ca``.
    """
    base = re.sub(
        r"^\s*(?:Hon\.|Honourable|Premier|Speaker|Dr\.|Mr\.|Mrs\.|Ms\.)\s+",
        "",
        name,
        flags=re.IGNORECASE,
    )
    base = re.sub(r"\s+[A-Z]\.\s+", " ", base)
    base = re.sub(r"\s*\([^)]+\)\s*", " ", base).strip()
    # Split on whitespace and hyphens so that "Wong-Tam" / "Kusendova-Bashta"
    # generate slug fragments for the full combined surname.
    raw = [t for t in re.split(r"\s+", base) if t]
    return raw


def _name_variants_for_wikipedia(name: str) -> list[str]:
    """Wikipedia title guesses for a raw DB name."""
    base = name.strip()
    variants: list[str] = [base]
    stripped = re.sub(r"\s+[A-Z]\.\s+", " ", base)
    if stripped != base:
        variants.append(stripped)
    noperiod = re.sub(r"\.", "", base)
    if noperiod != base:
        variants.append(noperiod)
    nopar = re.sub(r"\s*\([^)]+\)\s*", " ", base).strip()
    if nopar and nopar != base:
        variants.append(nopar)
    # Disambig suffixes — applied to every base variant
    extras: list[str] = []
    for entry in list(variants):
        for suffix in (
            " (politician)",
            " (Canadian politician)",
            " (Ontario politician)",
            " (Ontario MPP)",
        ):
            extras.append(entry + suffix)
    variants.extend(extras)
    # De-dupe preserving order
    seen: set[str] = set()
    out: list[str] = []
    for v in variants:
        k = v.replace("_", " ").strip()
        if k and k not in seen:
            seen.add(k)
            out.append(k)
    return out


# ─────────────────────────────────────────────────────────────────────
# Source 1 — Ontario Liberal caucus roster
# ─────────────────────────────────────────────────────────────────────

OLIBERAL_CAUCUS_URL = "https://ontarioliberal.ca/liberal-mpps"
ONDP_CAUCUS_URL = "https://www.ondpcaucus.com"

# On the OLP caucus page every MPP is rendered inside a `<div class="... mpp">`
# card whose heading is ``<h2>Name&nbsp; <span class="riding">…</span></h2>``
# and whose "Website" link carries class="social wb". We split the HTML on
# card boundaries, then extract (name, website) per card.
_OLIBERAL_CARD_SPLIT_RE = re.compile(
    r'<div[^>]*\bclass="[^"]*\bmpp\b[^"]*"[^>]*>',
    re.IGNORECASE,
)
_OLIBERAL_NAME_RE = re.compile(
    r'<h2[^>]*>\s*([^<&]+?)\s*(?:&nbsp;|<)',
    re.IGNORECASE,
)
_OLIBERAL_WEBSITE_RE = re.compile(
    r'<a[^>]+href=["\']([^"\']+)["\'][^>]*class="[^"]*\bwb\b[^"]*"',
    re.IGNORECASE,
)


async def _fetch_oliberal_caucus(
    client: httpx.AsyncClient,
) -> dict[str, str]:
    """Return {normalized_mpp_name: website_url} from the OLP caucus page."""
    try:
        r = await client.get(OLIBERAL_CAUCUS_URL, timeout=30.0)
        r.raise_for_status()
    except Exception as exc:
        log.warning("Ontario Liberal caucus fetch failed: %s", exc)
        return {}
    html = r.text

    out: dict[str, str] = {}
    for card in _OLIBERAL_CARD_SPLIT_RE.split(html)[1:]:
        nm = _OLIBERAL_NAME_RE.search(card)
        ws = _OLIBERAL_WEBSITE_RE.search(card)
        if not nm or not ws:
            continue
        raw_name = nm.group(1).replace("&nbsp;", " ").strip()
        url = _clean_url(ws.group(1).strip())
        if not _is_personal_url(url):
            continue
        key = _norm_loose(raw_name)
        if key and key not in out:
            out[key] = url
    return out


# The NDP caucus page lists each MPP inside a ``<div class="mpp">`` card.
# Card structure:
#     <div class="endorsement-title">
#       <h6>Riding</h6>
#       <h3>First <span class="lastname">Last</span></h3>
#       <a href="URL">Visit website</a>
#     </div>
# We scan the full HTML for "Visit website" anchors and associate each with
# the most recent preceding ``<h3>First …Last</h3>`` heading.
_ONDP_WEBSITE_ANCHOR_RE = re.compile(
    r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>\s*(?:Visit\s+website|Visit\s+Website|Website|website|Visit\s+Site)\s*</a>',
    re.IGNORECASE,
)
# Accept nested tags inside the heading (strip them later).
_ONDP_CARD_NAME_RE = re.compile(
    r'<h[234][^>]*>\s*((?:[^<]|<span[^>]*>[^<]*</span>)+?)\s*</h[234]>',
    re.IGNORECASE | re.DOTALL,
)


async def _fetch_ondp_caucus(
    client: httpx.AsyncClient,
) -> dict[str, str]:
    """Return {normalized_mpp_name: website_url} from the NDP caucus page."""
    try:
        r = await client.get(ONDP_CAUCUS_URL, timeout=30.0)
        r.raise_for_status()
    except Exception as exc:
        log.warning("Ontario NDP caucus fetch failed: %s", exc)
        return {}
    html = r.text

    # Collect every plausible "person heading" with its position.
    name_positions: list[tuple[int, str]] = []
    for nm in _ONDP_CARD_NAME_RE.finditer(html):
        raw = nm.group(1)
        # Strip nested tags and entity noise
        candidate = re.sub(r"<[^>]+>", "", raw)
        candidate = re.sub(r"&[a-z]+;", " ", candidate)
        candidate = re.sub(r"\s+", " ", candidate).strip()
        if len(candidate) < 3 or len(candidate.split()) > 5:
            continue
        low = candidate.lower()
        if any(t in low for t in (
            "loop", "newsletter", "stay", "caucus office", "contact us",
            "privacy", "accessibility", "sign up", "subscribe",
            "stay in the", "team on your side",
        )):
            continue
        name_positions.append((nm.start(), candidate))

    out: dict[str, str] = {}
    for anchor in _ONDP_WEBSITE_ANCHOR_RE.finditer(html):
        pos = anchor.start()
        url = _clean_url(anchor.group(1).strip())
        if not _is_personal_url(url):
            continue
        # Find the latest name heading before this anchor.
        name = None
        for npos, n in name_positions:
            if npos < pos:
                name = n
            else:
                break
        if not name:
            continue
        key = _norm_loose(name)
        if key and key not in out:
            out[key] = url
    return out


# ─────────────────────────────────────────────────────────────────────
# Source 2 — Wikipedia (per-MPP article scraping)
# ─────────────────────────────────────────────────────────────────────

WIKIPEDIA_API = "https://en.wikipedia.org/w/api.php"

_ONTARIO_CAT_KEYWORDS: tuple[str, ...] = (
    "ontario mpp",
    "ontario mpps",
    "members of the ontario provincial parliament",
    "ontario legislative assembly",
)

_WIKITEXT_ONTARIO_HINTS: tuple[str, ...] = (
    "ontario provincial parliament",
    "legislative assembly of ontario",
)


def _article_is_ontario_mpp(cats: list[str], wikitext: str) -> bool:
    for c in cats:
        low = c.lower()
        for kw in _ONTARIO_CAT_KEYWORDS:
            if kw in low:
                return True
    if wikitext:
        wl = wikitext.lower()
        for hint in _WIKITEXT_ONTARIO_HINTS:
            if hint in wl:
                return True
        if "member of provincial parliament" in wl and "ontario" in wl:
            return True
    return False


def _wiki_infobox_website(wikitext: str) -> Optional[str]:
    """Return the first personal-looking URL from the infobox |website= field."""
    m = re.search(
        r"(?im)^\s*\|\s*website\s*=\s*(.+?)(?=\n\s*\|\s*\w[\w \-]*=|\n\s*\}\}\s*$)",
        wikitext,
        re.DOTALL,
    )
    if not m:
        return None
    blob = m.group(1)
    for raw in re.findall(r"https?://[^\s\]\|\}\n<>]+", blob):
        u = _clean_url(raw)
        if _is_personal_url(u):
            return u
    return None


def _wiki_external_links_website(wikitext: str) -> Optional[str]:
    """Return the first personal-looking URL from the 'External links' section."""
    m = re.search(
        r"(?im)^==\s*External\s+links\s*==(.+?)(?=^==[^=]|\Z)",
        wikitext,
        re.DOTALL | re.MULTILINE,
    )
    if not m:
        return None
    block = m.group(1)
    # Prefer {{Official website|URL}} / {{Official URL|URL}} templates first.
    for c in re.findall(
        r"\{\{\s*[Oo]fficial\s+(?:URL|website)\s*\|([^}]+)\}\}", block,
    ):
        for raw in re.findall(r"https?://[^\s\|\}\n<>]+", c):
            u = _clean_url(raw)
            if _is_personal_url(u):
                return u
    # Fall back: first personal-looking URL anywhere in the section.
    for raw in re.findall(r"https?://[^\s\]\|\}\n<>]+", block):
        u = _clean_url(raw)
        if _is_personal_url(u):
            return u
    return None


async def _fetch_wiki_articles(
    client: httpx.AsyncClient,
    titles: list[str],
) -> dict[str, tuple[str, list[str]]]:
    """Batched MediaWiki query: {title: (wikitext, [categories])}."""
    out: dict[str, tuple[str, list[str]]] = {}
    BATCH = 20
    for i in range(0, len(titles), BATCH):
        chunk = titles[i:i + BATCH]
        params = {
            "action": "query",
            "prop": "revisions|categories",
            "rvprop": "content",
            "rvslots": "main",
            "cllimit": "max",
            "titles": "|".join(chunk),
            "format": "json",
            "redirects": 1,
            "formatversion": 2,
        }
        try:
            r = await client.get(WIKIPEDIA_API, params=params, timeout=45.0)
            r.raise_for_status()
            j = r.json()
        except Exception as exc:
            log.debug("wikipedia batch %d failed: %s", i, exc)
            await asyncio.sleep(2)
            continue
        for p in j.get("query", {}).get("pages", []) or []:
            if p.get("missing") or p.get("invalid"):
                continue
            revs = p.get("revisions") or []
            wt = (
                revs[0].get("slots", {}).get("main", {}).get("content")
                if revs else None
            )
            cats = [c.get("title", "") for c in (p.get("categories") or [])]
            t = (p.get("title") or "").replace(" ", "_")
            if t:
                out[t] = (wt or "", cats)
        await asyncio.sleep(0.2)
    return out


async def _wikipedia_lookup(
    client: httpx.AsyncClient,
    db_names: list[str],
) -> dict[str, str]:
    """Return {db_name: personal_url} discovered via English Wikipedia."""
    # Expand every DB name into variant titles and fetch them all in one batch.
    per_name: dict[str, list[str]] = {
        n: _name_variants_for_wikipedia(n) for n in db_names
    }
    all_titles: set[str] = set()
    for names in per_name.values():
        for n in names:
            all_titles.add(n.replace(" ", "_"))
    console.print(
        f"[cyan]  wikipedia: fetching {len(all_titles)} title variants…[/cyan]"
    )
    articles = await _fetch_wiki_articles(client, sorted(all_titles))

    out: dict[str, str] = {}
    for db_name in db_names:
        for v in per_name[db_name]:
            entry = articles.get(v.replace(" ", "_"))
            if entry is None:
                continue
            wt, cats = entry
            if not wt:
                continue
            if not _article_is_ontario_mpp(cats, wt):
                continue
            url = _wiki_infobox_website(wt) or _wiki_external_links_website(wt)
            if url:
                out[db_name] = url
                break
    return out


# ─────────────────────────────────────────────────────────────────────
# Source 3 — DNS / HTTP pattern probe
# ─────────────────────────────────────────────────────────────────────

def _slugify(token: str) -> str:
    s = unicodedata.normalize("NFKD", token or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", "", s.lower())


def _probe_candidates(first: str, last: str,
                      all_tokens: Optional[list[str]] = None) -> list[str]:
    """Generate plausible URL patterns for an MPP's personal site.

    The patterns below are ordered by empirical yield across the 95-MPP
    working set, mpp.ca first because ~60 % of active MPPs use that suffix.
    When ``all_tokens`` is supplied we also emit the full-name slug to
    cover middle-name cases like ``dawngallaghermurphympp.ca``.
    """
    f = _slugify(first)
    l = _slugify(last)
    if not (f and l):
        return []
    cands: list[str] = [
        f"https://{f}{l}mpp.ca",
        f"https://{f}{l}.ca",
        f"https://{l}mpp.ca",
        f"https://{f}{l}mpp.com",
        f"https://{f}{l}.com",
        f"https://{f}-{l}mpp.ca",
        f"https://mpp{f}{l}.ca",
        # "team<last>.ca" — Neil Lumsden etc.
        f"https://team{l}.ca",
        # Last-name-only patterns — covers fedeli.com (Victor), cuzzetto.com
        # (Rudy). Same MPP-keyword confirmation still catches false positives
        # (bailey.ca remains rejected).
        f"https://{l}.com",
        f"https://{l}.ca",
    ]
    # Full-name slug for middle-name MPPs.
    if all_tokens and len(all_tokens) >= 3:
        full = "".join(_slugify(t) for t in all_tokens)
        if full and full != f + l:
            cands.insert(0, f"https://{full}mpp.ca")
            cands.append(f"https://{full}.ca")
            cands.append(f"https://{full}.com")
            cands.append(f"https://{full}mpp.com")
    return cands


# Keywords that must appear on the page before we trust a probed URL as an
# MPP site. Requiring at least one of these kills false positives like
# ``chrisscott.ca`` (real-estate agent) and ``jeffburch.com`` → Northwestern
# Mutual (financial advisor) — both of which contain the MPP's name but no
# political context.
_MPP_CONFIRM_KEYWORDS: tuple[str, ...] = (
    "mpp",
    "member of provincial parliament",
    "legislative assembly",
    "queen's park",
    "queens park",
    "ontario pc",
    "progressive conservative party of ontario",
    "ontario ndp",
    "ontario new democratic",
    "ontario liberal",
    "ontario green",
    "provincial parliament",
    "constituency office",
    "ontario.ca",
    "ola.org",
)


# ─────────────────────────────────────────────────────────────────────
# Source 4 — Wikidata SPARQL (websites + socials)
# ─────────────────────────────────────────────────────────────────────

WIKIDATA_SPARQL = "https://query.wikidata.org/sparql"

# Wikidata P39 item for "member of the Ontario Provincial Parliament"
MPP_POSITION_QID = "Q3305347"

_SPARQL_QUERY = f"""
SELECT DISTINCT ?person ?personLabel ?website
  ?twitter ?facebook ?instagram ?youtube ?tiktok ?linkedin ?mastodon ?bluesky
WHERE {{
  ?person p:P39 ?ps .
  ?ps ps:P39 wd:{MPP_POSITION_QID} .
  OPTIONAL {{ ?person wdt:P856 ?website . }}
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

_WIKIDATA_SOCIAL_PROPS: tuple[tuple[str, str, str], ...] = (
    # (var_name, platform_hint, url_template)
    ("twitter",   "twitter",   "https://twitter.com/{value}"),
    ("facebook",  "facebook",  "https://www.facebook.com/{value}"),
    ("instagram", "instagram", "https://www.instagram.com/{value}"),
    ("youtube",   "youtube",   "https://www.youtube.com/channel/{value}"),
    ("tiktok",    "tiktok",    "https://www.tiktok.com/@{value}"),
    ("linkedin",  "linkedin",  "https://www.linkedin.com/in/{value}"),
    ("mastodon",  "mastodon",  "_mastodon_"),
    ("bluesky",   "bluesky",   "https://bsky.app/profile/{value}"),
)


def _mastodon_url_from_address(addr: str) -> Optional[str]:
    addr = addr.strip().lstrip("@")
    if "@" not in addr:
        return None
    user, _, host = addr.partition("@")
    if not (user and host):
        return None
    return f"https://{host.lower()}/@{user}"


async def _fetch_wikidata(
    client: httpx.AsyncClient,
) -> dict[str, dict]:
    """Return {wikidata_uri: {name, website?, socials: {platform: url}}}."""
    # Wikidata's SPARQL endpoint requires an explicit Accept header to
    # return JSON; otherwise it serves the interactive HTML UI and .json()
    # blows up. Mirrors socials_enrichment.enrich_from_wikidata().
    try:
        r = await client.get(
            WIKIDATA_SPARQL,
            params={"query": _SPARQL_QUERY},
            headers={"Accept": "application/sparql-results+json"},
            timeout=120.0,
        )
        r.raise_for_status()
        data = r.json()
    except Exception as exc:
        log.warning("Wikidata SPARQL failed: %s", exc)
        return {}

    persons: dict[str, dict] = {}
    for b in data.get("results", {}).get("bindings", []) or []:
        uri = b.get("person", {}).get("value", "")
        if not uri:
            continue
        name = b.get("personLabel", {}).get("value", "")
        slot = persons.setdefault(uri, {
            "name": name,
            "websites": set(),
            "socials": {},
        })
        website = b.get("website", {}).get("value", "")
        if website:
            slot["websites"].add(website)
        for var, hint, tmpl in _WIKIDATA_SOCIAL_PROPS:
            v = b.get(var, {}).get("value", "")
            if not v:
                continue
            if hint == "mastodon":
                url = _mastodon_url_from_address(v)
            else:
                url = tmpl.format(value=v)
            if url:
                slot["socials"].setdefault(hint, url)
    return persons


# ─────────────────────────────────────────────────────────────────────
# Main driver
# ─────────────────────────────────────────────────────────────────────

async def _load_active_ontario_mpps(db: Database) -> list[dict]:
    rows = await db.fetch(
        """
        SELECT id, name, personal_url
          FROM politicians
         WHERE is_active = true
           AND level = 'provincial'
           AND province_territory = 'ON'
         ORDER BY name
        """
    )
    return [
        {"id": str(r["id"]), "name": r["name"],
         "personal_url": r["personal_url"] or ""}
        for r in rows
    ]


async def fill_ontario(db: Database) -> dict[str, int]:
    """Fill Ontario MPP gaps.

    Returns {'personal_urls': N, 'socials': N, 'unmatched': N}.

    Handles two DB states:
      a) ON MPPs already in place (common case — Open North ingest populated
         them): UPDATE personal_url + attach websites + upsert socials.
      b) ON MPPs not yet ingested: we log that and exit cleanly; the next
         run will pick them up once ingest-legislatures completes.
    """
    mpps = await _load_active_ontario_mpps(db)
    if not mpps:
        console.print(
            "[yellow]Ontario: no active provincial MPPs found — run "
            "`ingest-legislatures` / `ingest-ontario-mpps` first.[/yellow]"
        )
        return {"personal_urls": 0, "socials": 0, "unmatched": 0}

    console.print(
        f"[cyan]Ontario: {len(mpps)} active MPPs in DB "
        f"({sum(1 for m in mpps if m['personal_url'])} already have personal_url)"
        "[/cyan]"
    )
    db_names = [m["name"] for m in mpps]

    async with httpx.AsyncClient(
        headers={
            "User-Agent": (
                "CanadianPoliticalData/1.0 "
                "(+https://canadianpoliticaldata.ca; admin@thebunkerops.ca)"
            ),
            "Accept": "*/*",
        },
        follow_redirects=True,
        timeout=httpx.Timeout(30.0, connect=10.0),
    ) as client:
        # Run all four sources in parallel where possible.
        console.print("[cyan]  fetching Ontario Liberal caucus roster…[/cyan]")
        console.print("[cyan]  fetching Ontario NDP caucus roster…[/cyan]")
        console.print("[cyan]  querying Wikidata SPARQL for MPP items…[/cyan]")
        console.print("[cyan]  querying Wikipedia for per-MPP articles…[/cyan]")
        oliberal_task = _fetch_oliberal_caucus(client)
        ondp_task = _fetch_ondp_caucus(client)
        wikidata_task = _fetch_wikidata(client)
        wikipedia_task = _wikipedia_lookup(client, db_names)
        (oliberal_map, ondp_map, wikidata_persons,
         wikipedia_map) = await asyncio.gather(
            oliberal_task, ondp_task, wikidata_task, wikipedia_task,
        )

        console.print(
            f"[cyan]  oliberal: {len(oliberal_map)}; "
            f"ondp: {len(ondp_map)}; "
            f"wikidata persons: {len(wikidata_persons)}; "
            f"wikipedia hits: {len(wikipedia_map)}[/cyan]"
        )

        # Build wikidata name-index for matching into our DB rows
        wd_by_name: dict[str, str] = {}  # norm_name → uri
        for uri, p in wikidata_persons.items():
            key = _norm_loose(p["name"])
            if key and key not in wd_by_name:
                wd_by_name[key] = uri
            key2 = _norm_name(p["name"])
            if key2 and key2 not in wd_by_name:
                wd_by_name[key2] = uri

        # ── Per-MPP candidate assembly + verify-with-fallback ──
        # Rather than "choose one source then verify or fail", we build an
        # ordered candidate list per MPP spanning every source (oliberal →
        # wikipedia → wikidata-P856 → dns-probe), then verify them in order
        # and accept the first that resolves to a valid personal URL.
        # This handles the very common case where Wikipedia's infobox URL
        # is now a 301 to a PC riding page but ``{firstlast}mpp.ca`` still
        # resolves fresh (samoosterhoffmpp.ca etc).
        def build_candidates(name: str) -> list[str]:
            cands: list[str] = []
            key = _norm_loose(name)
            # 1a. Ontario Liberal caucus
            u = oliberal_map.get(key)
            if u and _is_personal_url(u):
                cands.append(u)
            # 1b. Ontario NDP caucus
            u = ondp_map.get(key)
            if u and _is_personal_url(u) and u not in cands:
                cands.append(u)
            # 2. Wikipedia
            u = wikipedia_map.get(name)
            if u and _is_personal_url(u) and u not in cands:
                cands.append(u)
            # 3. Wikidata P856
            for v in (name,
                      re.sub(r"\s+[A-Z]\.\s+", " ", name),
                      re.sub(r"\s*\([^)]+\)\s*", " ", name).strip()):
                wd_uri = wd_by_name.get(_norm_loose(v)) or wd_by_name.get(_norm_name(v))
                if wd_uri:
                    for s in sorted(wikidata_persons[wd_uri]["websites"]):
                        if _is_personal_url(s) and s not in cands:
                            cands.append(s)
                    break
            # 4. DNS probe patterns (candidate set only; validation below)
            first, last = _split_first_last(name)
            tokens = _all_tokens(name)
            if first and last:
                for p in _probe_candidates(first, last, tokens):
                    if p not in cands:
                        cands.append(p)
            return cands

        async def _try_get(url: str) -> Optional[httpx.Response]:
            """Fetch ``url`` with HTTPS; on SSL failure retry on HTTP.

            ``fedeli.com`` (Victor Fedeli) serves a broken TLS handshake but
            is reachable via plain HTTP — several older MPP domains are in
            the same shape, so we accept the downgrade.
            """
            try:
                r = await client.get(url, timeout=8.0, follow_redirects=True)
                return r
            except httpx.ConnectError:
                # Also covers "All connection attempts failed"
                pass
            except Exception as exc:
                msg = str(exc).lower()
                if "ssl" not in msg and "certificate" not in msg:
                    return None
            # Retry on http://
            if url.startswith("https://"):
                try:
                    r = await client.get(
                        "http://" + url[len("https://"):],
                        timeout=8.0,
                        follow_redirects=True,
                    )
                    return r
                except Exception:
                    return None
            return None

        async def verify_candidate(
            url: str,
            first: Optional[str],
            last: Optional[str],
        ) -> Optional[str]:
            """Return the final URL if it passes personal-URL validation.

            Checks in order:
              - HTTP 200 final response (with HTTP fallback for sites whose
                TLS is broken, e.g. fedeli.com).
              - The final-URL host is not a party/institutional/social host
                (``_is_personal_url(str(r.url))``). This alone catches the
                301-to-party-riding-subdomain pattern (e.g.
                ``carolinemulroney.ca`` → ``yorksimcoe.ontariopc.ca``) — the
                final host would match ``ontariopc.ca`` and be rejected.
              - Body contains both name tokens AND at least one MPP-context
                keyword (rejects surname-squat domains like bailey.ca /
                chrisscott.ca that match the name but are unrelated sites).
            """
            r = await _try_get(url)
            if r is None or r.status_code != 200:
                return None
            final = str(r.url)
            if not _is_personal_url(final):
                return None
            body = (r.text or "").lower()
            if first and last:
                if first.lower() not in body or last.lower() not in body:
                    return None
            if not any(kw in body for kw in _MPP_CONFIRM_KEYWORDS):
                return None
            return final

        verified: dict[str, str] = {}
        sem = asyncio.Semaphore(6)

        async def resolve(m: dict) -> None:
            name = m["name"]
            if m["personal_url"]:
                # Keep whatever's already there — do not overwrite.
                return
            first, last = _split_first_last(name)
            cands = build_candidates(name)
            if not cands:
                return
            async with sem:
                for url in cands:
                    hit = await verify_candidate(url, first, last)
                    if hit:
                        verified[name] = hit
                        return

        await asyncio.gather(*(resolve(m) for m in mpps))

        attached = 0
        for m in mpps:
            url = verified.get(m["name"])
            if not url:
                continue
            try:
                if await attach_website(db, m["id"], url, "personal"):
                    attached += 1
            except Exception as exc:
                log.warning("attach_website failed for %s: %s", m["name"], exc)

        # ── Socials via Wikidata ──
        socials_saved = 0
        for m in mpps:
            name = m["name"]
            wd_uri = None
            for v in (name, re.sub(r"\s+[A-Z]\.\s+", " ", name),
                      re.sub(r"\s*\([^)]+\)\s*", " ", name).strip()):
                wd_uri = wd_by_name.get(_norm_loose(v)) or wd_by_name.get(_norm_name(v))
                if wd_uri:
                    break
            if not wd_uri:
                continue
            socials = wikidata_persons[wd_uri]["socials"]
            if not socials:
                continue
            try:
                socials_saved += await attach_socials(db, m["id"], socials)
            except Exception as exc:
                log.warning("attach_socials failed for %s: %s", name, exc)

    unmatched = sum(
        1 for m in mpps
        if not m["personal_url"] and m["name"] not in verified
    )
    total = len(mpps)
    pct = (100.0 * len(verified) / total) if total else 0.0
    console.print(
        f"[green]Ontario: {len(verified)}/{total} ({pct:.1f}%) "
        f"verified personal URLs, attached {attached} new rows, "
        f"{socials_saved} social rows saved, {unmatched} unmatched[/green]"
    )
    return {
        "personal_urls": len(verified),
        "socials": socials_saved,
        "unmatched": unmatched,
    }


# Backwards-compat alias used by the aggregator (gap_fillers.runner)
async def run(db: Database) -> None:
    """Adapter used by ``gap_fillers.runner.run_all``."""
    await fill_ontario(db)
