"""Municipal councillor personal-URL enrichment (Phase 4 gap-filler).

Phase 4 ingested 571 councillors from 108 Canadian councils via Open North, but
Open North's `personal_url` field is almost always empty for municipal reps and
`url` typically points at a city roster / ward page rather than the councillor's
campaign site. This module walks every unenriched municipal politician and
tries to discover a true personal / campaign / community-office URL.

### Research summary (2026-04, 10-20 site sample)

Canadian municipal web presence clusters around these CMS / URL shapes:

  1. **Drupal** — ottawa.ca, toronto.ca, hamilton.ca, montreal.ca,
     peelregion.ca, sudbury.ca, fredericton.ca, grande-prairie.
     Ottawa surfaces an external campaign link inside a
     `field--name-field-links` block whose `field__label` is "Web".
     Most other Drupal cities hide personal URLs entirely.

  2. **CivicPlus** — coquitlam.ca, richmond.ca (some), various mid-sized BC
     municipalities. Footer reads "Government Websites by CivicPlus".
     Personal URLs are rare; directory pages expose a `Contact Ward X`
     block but not a website field.

  3. **eSolutionsGroup / custom ASP.NET .aspx** — milton.ca, burlington.ca,
     welland.ca, newmarket.ca, chatham-kent.ca (SharePoint variant),
     townofws.ca. Burlington's pages sometimes embed a personal site
     as a plain external `<a>` in a "Website" card.

  4. **WordPress** — mississauga.ca, sault ste marie. Mississauga's
     councillor pages use a clean `Website: <a href="…">` label pattern
     that holds the councillor's own campaign site when one exists.

  5. **Govstack / proprietary** — pickering.ca, regina.ca, oakville.ca
     (Salesforce-backed). Rare to impossible to find a personal link.

Common across every platform: **when a personal site exists at all**, its
domain almost always contains a fragment of the councillor's last name or
the ward name (e.g. `bradbutt.ca`, `craigcassar.ca`, `djkelly.ca`,
`kitchissippiward.ca`, `rideau-rockcliffe.ca`, `mayorknack.ca`). This
observation drives our generic fallback scorer.

### Ethical scraping posture

- Honour robots.txt for every host (checked once per host, cached).
- Single-flight per host with a 1s min-gap between requests.
- Browser-ish User-Agent (some sites return 403 to our default UA) but we
  always identify honestly via project URL in the UA string.
- No JS rendering, no credentialed endpoints, no query-flooding search APIs.
- Councillor websites are public campaign material; civic-transparency
  projects like this are explicitly in-scope for fair-use access patterns.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
import unicodedata
from collections import defaultdict
from typing import Awaitable, Callable, Optional
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import httpx
from rich.console import Console
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn

from .db import Database
from .enrich import _attach, _attach_socials, _extract_socials_from_html

log = logging.getLogger(__name__)
console = Console()


# Browser-ish UA — several municipal CDNs 403 our default bot UA. We still
# advertise the project URL further down in docs / whois.
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "CanadianPoliticalDataBot/1.0 Chrome/126.0.0.0 Safari/537.36 "
    "(+https://canadianpoliticaldata.ca)"
)

# Hosts/domains we *never* credit as a councillor's personal site. Covers:
# social platforms, CDNs, analytics, map/gov services, subscription vendors,
# other municipalities (a councillor in Peel Region links to Mississauga),
# meeting-management SaaS (eScribe, Granicus), etc.
_SKIP_HOST_FRAGMENTS: tuple[str, ...] = (
    # social
    "facebook.com", "fb.com", "twitter.com", "x.com", "instagram.com",
    "youtube.com", "youtu.be", "tiktok.com", "linkedin.com", "bsky.app",
    "threads.net", "mastodon.social", "pinterest.com", "reddit.com",
    "snapchat.com", "whatsapp.com", "wa.me", "t.me", "telegram",
    # CDNs / infra
    "cdnjs.cloudflare.com", "cdn.jsdelivr.net", "googletagmanager.com",
    "gstatic.com", "googleapis.com", "w3.org", "wixstatic.com",
    "fonts.googleapis.com", "fonts.gstatic.com", "cloudflare.com",
    "jquery.com", "bootstrapcdn.com", "addtoany.com", "addthis.com",
    # municipal / civic vendors
    "escribemeetings.com", "granicus.com", "iqm2.com", "icompasstech.com",
    "civicplus.com", "govstack.com", "esolutionsgroup.ca",
    "constantcontact.com", "mailchimp.com", "hubspot.com", "formassembly",
    "arcgis.com", "mapbox.com", "google.com/maps", "bing.com/maps",
    "cloudinary.com", "imgix", "amazonaws.com", "cludo.com",
    "surveymonkey", "tally.so", "typeform", "docusign",
    "verintcloudservices", "empro.verintcloudservices",
    "my.site.com",  # Salesforce Experience Cloud
    # Federal / provincial gov
    ".gc.ca", "canada.ca", "ontario.ca", "gov.bc.ca", "gov.sk.ca",
    "gov.mb.ca", "alberta.ca", "novascotia.ca", "gnb.ca", "gov.nl.ca",
    "gov.pe.ca", "yukon.ca", "ntassembly.ca",
    # Political parties (national)
    "liberal.ca", "conservative.ca", "ndp.ca", "greenparty.ca",
    "bloc.org", "peoplespartyofcanada.ca", "ppc-plc.ca",
)

# File extensions / paths that are never a personal site.
_SKIP_PATH_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE) for p in (
        r"\.(pdf|docx?|xlsx?|pptx?|zip|mp4|mp3|jpg|jpeg|png|gif|svg|webp|ico)(?:\?|$)",
        r"/wp-content/", r"/wp-json/", r"/wp-login", r"/wp-admin",
        r"/_layouts/", r"/core/", r"/sites/default/files/",
        r"share\.php", r"sharer\?", r"sharer\.php",
        r"mailto:", r"tel:",
    )
)


def _canon_name(s: str) -> str:
    """Normalise a string for fuzzy comparison to a domain.

    Strip accents, lowercase, remove non-alphanumeric. So "Ann-Marie Noyes"
    becomes "annmarienoyes" — which is then compared to a domain like
    "annmarienoyes.ca" → stripped → "annmarienoyesca".
    """
    n = unicodedata.normalize("NFKD", s or "")
    n = "".join(ch for ch in n if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", "", n.lower())


def _domain_tokens(host: str) -> set[str]:
    """Break a host into lowercased alphabetic tokens of length >=3.

    kitchissippiward.ca → {"kitchissippiward"}
    rideau-rockcliffe.ca → {"rideau", "rockcliffe"}
    mayorknack.ca → {"mayorknack"}
    """
    if not host:
        return set()
    h = host.lower()
    if h.startswith("www."):
        h = h[4:]
    # drop the TLD suffix
    parts = h.split(".")
    if len(parts) >= 2:
        h = parts[0] if len(parts) == 2 else ".".join(parts[:-1])
    # split on non-alpha
    tokens = re.split(r"[^a-z]+", h)
    return {t for t in tokens if len(t) >= 3}


def _host_of(url: str) -> str:
    try:
        h = (urlparse(url).hostname or "").lower()
        return h[4:] if h.startswith("www.") else h
    except Exception:
        return ""


def _same_registrable(a: str, b: str) -> bool:
    """Best-effort 'same site' check without depending on tldextract.

    We treat the last two dotted labels as the registrable (good enough for
    .ca / .com and the handful of .quebec / .net / .services we've seen —
    this isn't a security boundary, just a "don't count the city's own host"
    filter).
    """
    pa = a.split(".")[-2:]
    pb = b.split(".")[-2:]
    return pa == pb and len(pa) == 2


def _is_skippable(href: str) -> bool:
    lower = href.lower()
    for frag in _SKIP_HOST_FRAGMENTS:
        if frag in lower:
            return True
    for pat in _SKIP_PATH_PATTERNS:
        if pat.search(lower):
            return True
    return False


# ── Robots.txt cache ─────────────────────────────────────────────

_robots_cache: dict[str, Optional[RobotFileParser]] = {}
_robots_lock = asyncio.Lock()


async def _robots_allows(client: httpx.AsyncClient, url: str) -> bool:
    """Return True if robots.txt permits fetching `url` for our UA.

    On fetch error (404, timeout, DNS), default to allow — many municipal
    sites simply have no robots.txt and we should not refuse to scrape them.
    """
    host = _host_of(url)
    if not host:
        return True
    async with _robots_lock:
        if host in _robots_cache:
            rp = _robots_cache[host]
        else:
            rp = RobotFileParser()
            robots_url = f"{urlparse(url).scheme}://{host}/robots.txt"
            try:
                r = await client.get(robots_url, timeout=10)
                if r.status_code == 200 and r.text.strip():
                    rp.parse(r.text.splitlines())
                else:
                    rp = None  # treat missing/empty as allow-all
            except Exception:
                rp = None
            _robots_cache[host] = rp
    if rp is None:
        return True
    try:
        return rp.can_fetch(USER_AGENT, url)
    except Exception:
        return True


# ── Per-host rate limiting ───────────────────────────────────────
#
# Municipal sites are small ops; we sequence requests per-host with a 1s
# minimum gap. Different hosts proceed in parallel (outer semaphore caps
# overall concurrency).

_host_last_fetch: dict[str, float] = defaultdict(float)
_host_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
_MIN_GAP_S = 1.0


async def _polite_get(client: httpx.AsyncClient, url: str) -> Optional[httpx.Response]:
    host = _host_of(url)
    if not host:
        return None
    lock = _host_locks[host]
    async with lock:
        last = _host_last_fetch[host]
        gap = time.monotonic() - last
        if gap < _MIN_GAP_S:
            await asyncio.sleep(_MIN_GAP_S - gap)
        try:
            if not await _robots_allows(client, url):
                log.info("robots.txt blocks %s", url)
                return None
            r = await client.get(url)
            _host_last_fetch[host] = time.monotonic()
            return r
        except Exception as exc:
            log.debug("fetch failed for %s: %s", url, exc)
            _host_last_fetch[host] = time.monotonic()
            return None


# ── Candidate extraction + scoring ───────────────────────────────

# Match every <a ... href="..."> with its surrounding text and any nearby
# label. We search a window around each match for scoring signals.
_A_HREF_RE = re.compile(
    r'<a\b[^>]*?href="(?P<url>https?://[^"#]+)"[^>]*>(?P<text>[^<]{0,200})</a>',
    re.IGNORECASE,
)

# Narrow label regex: match the anchor-adjacent "Website:" / "Web:" label
# patterns we observed in real roster HTML (Mississauga, Ottawa), NOT
# arbitrary mentions of the word "website" in a browser-upgrade notice or
# cookie banner. We look for the word surrounded by label-like punctuation.
_LABEL_SIGNAL_RE = re.compile(
    r"(?i)(?:<[^>]*>\s*|>|\|\s*)"
    r"(website|personal\s+website|campaign\s+website|community\s+office|"
    r"councillor'?s?\s+website|visit\s+website|web\s*(?:site)?)"
    r"\s*(?::|</|<br)"
)


def _score_candidate(
    cand_url: str,
    cand_text: str,
    window: str,
    city_host: str,
    name_tokens: set[str],
    ward_tokens: set[str],
) -> int:
    """Score a candidate external URL 0..inf; higher = more likely personal.

    Signals (rough weights, tuned to sample of ~20 known-good sites):
      +10 per shared token between candidate domain and politician name
       +5 per shared token with ward/constituency name
       +5 if an explicit "Website/Personal/Campaign" label sits nearby
       +3 if the anchor has rel="external"
       +2 if the candidate domain is very short (likely personal, not corp)
       -5 if the domain contains the city host's own registrable token
           (bias against same-city org sites)
       -2 if the link text itself looks non-personal
             (e.g. "committee", "org", "foundation")
    """
    host = _host_of(cand_url)
    if not host or _same_registrable(host, city_host):
        return -999
    dtok = _domain_tokens(host)
    if not dtok:
        return 0

    score = 0
    name_match = False
    ward_match = False

    # Name overlap — the decisive signal. A councillor's own name appearing
    # in the domain (e.g. bradbutt.ca for Brad Butt) is close to conclusive.
    for tok in dtok:
        for nt in name_tokens:
            if len(tok) >= 4 and len(nt) >= 4 and (tok in nt or nt in tok):
                score += 10
                name_match = True
                break
    for tok in dtok:
        for wt in ward_tokens:
            if len(tok) >= 4 and len(wt) >= 4 and (tok in wt or wt in tok):
                ward_match = True
                break

    # Label window signal — we use the narrow label regex (Mississauga-style
    # "Website: <a>" or Ottawa's "Web" Drupal field label) so we don't get
    # lit up by a cookie banner or browser-upgrade notice.
    label_hit = bool(_LABEL_SIGNAL_RE.search(window or ""))
    if label_hit:
        score += 4
    if 'rel="external"' in (window or "").lower():
        score += 2

    # Ward-only match with a narrow label: this catches sites like
    # rideau-rockcliffe.ca on Ottawa ward pages. Without the label gate it
    # would catch random venues (abbotsfordcentre.ca on Abbotsford pages).
    if ward_match and label_hit and not name_match:
        score += 6

    # Short/clean domain heuristic — single-token .ca/.com of modest length
    first = host.split(".")[0]
    if len(host.split(".")) <= 3 and 4 <= len(first) <= 20:
        score += 1

    # Anchor text negative signals — these words almost never appear in a
    # personal-campaign-site link but show up in random municipal partners.
    tlow = (cand_text or "").lower()
    for bad in ("committee", "foundation", "society", "association",
                "chamber", "board", "library", "transit", "museum",
                "hospital", "college", "university", "school", "charity",
                "tourism", "visit", "news", "press release",
                "arena", "facility", "park",
                "browser", "upgrade", "support", "accessibility", "engage",
                "have your say", "report", "airport"):
        if bad in tlow:
            score -= 4
            break

    return score


def _candidates(html: str, city_host: str) -> list[tuple[str, str, str]]:
    """Return list of (url, anchor_text, context_window) for non-skippable
    external links. Context window is ±120 chars around the anchor — used
    later to look for "Website:" labels etc."""
    if not html:
        return []
    out: list[tuple[str, str, str]] = []
    for m in _A_HREF_RE.finditer(html):
        href = m.group("url").strip().rstrip(".,)\"'>")
        text = (m.group("text") or "").strip()
        if _is_skippable(href):
            continue
        host = _host_of(href)
        if not host or _same_registrable(host, city_host):
            continue
        window = html[max(0, m.start() - 150): m.end() + 150]
        out.append((href, text, window))
    return out


# ── CMS-specific fast paths ──────────────────────────────────────
# These are quick regex matchers for known patterns. If they hit cleanly,
# we return immediately; if not, we fall through to the generic scorer.

_OTTAWA_WEB_RE = re.compile(
    r'field--name-field-links[\s\S]{0,800}?field__label["\'>\s]+Web'
    r'[\s\S]{0,1200}?<a[^>]*href="(https?://[^"]+)"',
    re.IGNORECASE,
)

_MISSISSAUGA_WEBSITE_RE = re.compile(
    # <strong>Website: </strong><a href="https://…">
    r'(?i)Website\s*:?\s*</(?:strong|b|span|p)>\s*<a[^>]*href="(https?://[^"]+)"',
)


async def _scrape_ottawa(
    client: httpx.AsyncClient, url: str, city_host: str, **_: object,
) -> Optional[str]:
    """Ottawa Drupal: the 'Web' field inside field--name-field-links."""
    r = await _polite_get(client, url)
    if r is None or r.status_code != 200:
        return None
    m = _OTTAWA_WEB_RE.search(r.text)
    if m:
        cand = m.group(1).strip()
        if not _is_skippable(cand) and not _same_registrable(_host_of(cand), city_host):
            return cand
    return None


async def _scrape_mississauga(
    client: httpx.AsyncClient, url: str, city_host: str, **_: object,
) -> Optional[str]:
    r = await _polite_get(client, url)
    if r is None or r.status_code != 200:
        return None
    m = _MISSISSAUGA_WEBSITE_RE.search(r.text)
    if m:
        cand = m.group(1).strip()
        if not _is_skippable(cand) and not _same_registrable(_host_of(cand), city_host):
            return cand
    # Fall back to the generic scorer on the same page.
    return None


# Dispatch table: city-host → specialised scraper. Entries return None when
# the CMS pattern misses (or simply isn't present for this councillor);
# the coordinator then falls through to the generic scraper.
CMS_SCRAPERS: dict[str, Callable[..., Awaitable[Optional[str]]]] = {
    "ottawa.ca": _scrape_ottawa,
    "mississauga.ca": _scrape_mississauga,
}


# ── Generic scraper ──────────────────────────────────────────────

async def _scrape_generic(
    client: httpx.AsyncClient,
    url: str,
    city_host: str,
    *,
    name_tokens: set[str],
    ward_tokens: set[str],
) -> Optional[str]:
    """Fetch `url` and return the highest-scoring plausible personal site.

    Requires a minimum score threshold to avoid false positives (e.g. a BIA
    site linked from a Toronto ward page shouldn't be credited as personal).
    """
    r = await _polite_get(client, url)
    if r is None or r.status_code != 200:
        return None
    cands = _candidates(r.text, city_host)
    if not cands:
        return None
    scored: list[tuple[int, str]] = []
    for cand, text, window in cands:
        s = _score_candidate(
            cand, text, window, city_host, name_tokens, ward_tokens,
        )
        if s > 0:
            scored.append((s, cand))
    if not scored:
        return None
    scored.sort(reverse=True)
    best_score, best_url = scored[0]
    # Threshold: 10 = name match alone. 10+ clears; nothing below it does.
    # Without name overlap we fall through to None (safer: no enrichment
    # beats a false positive).
    if best_score < 10:
        return None
    return best_url


# ── Pre-enrichment pass: trust-Open-North self-URL ──────────────
#
# Open North sometimes returns the councillor's own domain as `url` for the
# small handful of Alberta councillors with personal sites (e.g. Ashley
# Salvador's `ashleysalvador.com`). Detect these by checking whether the
# stored websites.url's host contains any name token of the politician and
# is NOT a known city domain. In those cases we simply promote that row to
# personal_url with no network fetch at all.

_KNOWN_CITY_HOST_FRAGMENTS: frozenset[str] = frozenset({
    # hosts we've observed as shared-official council pages in the sample
    "edmonton.ca", "calgary.ca", "lethbridge.ca", "strathcona.ca",
    "cityofgp.com", "rmwb.ca", "saintjohn.ca", "toronto.ca", "ottawa.ca",
    "mississauga.ca", "brampton.ca", "vaughan.ca", "markham.ca",
    "burlington.ca", "oakville.ca", "hamilton.ca", "kitchener.ca",
    "waterloo.ca", "cambridge.ca", "guelph.ca", "london.ca", "windsor.ca",
    "citywindsor.ca", "thunderbay.ca", "greatersudbury.ca", "milton.ca",
    "pickering.ca", "newmarket.ca", "regionofwaterloo.ca", "peelregion.ca",
    "niagararegion.ca", "montreal.ca", "longueuil.quebec", "gatineau.ca",
    "sherbrooke.ca", "v3r.net", "sjsr.ca", "halifax.ca", "cbrm.ns.ca",
    "fredericton.ca", "saintjohn.ca", "stjohns.ca", "winnipeg.ca",
    "regina.ca", "saskatoon.ca", "surrey.ca", "burnaby.ca", "richmond.ca",
    "coquitlam.ca", "abbotsford.ca", "victoria.ca", "saanich.ca",
    "saultstemarie.ca", "georgina.ca", "king.ca", "caledon.ca",
    "forterie.ca", "lincoln.ca", "welland.ca", "tol.ca", "townofws.ca",
    "chatham-kent.ca", "cityofkingston.ca", "richmondhill.ca",
})


def _looks_like_personal_domain(host: str, politician_name: str) -> bool:
    """Guess whether host is a personal/campaign domain owned by the politician.

    Heuristic: NOT a known city host + domain tokens overlap with the
    politician's name tokens.
    """
    if not host:
        return False
    if any(frag in host for frag in _KNOWN_CITY_HOST_FRAGMENTS):
        return False
    dtok = _domain_tokens(host)
    if not dtok:
        return False
    name_canon = _canon_name(politician_name)
    if not name_canon:
        return False
    for tok in dtok:
        if len(tok) >= 4 and tok in name_canon:
            return True
    return False


# ── Main entry point ─────────────────────────────────────────────

async def enrich_municipal(
    db: Database,
    *,
    limit: Optional[int] = None,
    concurrency: int = 6,
) -> int:
    """Attempt personal-URL discovery for every municipal councillor missing one.

    Returns the number of newly-discovered personal URLs inserted.
    """
    # Candidate set: municipal, active, no personal_url, has at least one
    # website row (we need a starting URL to fetch).
    #
    # We explicitly skip URLs that are shared across multiple councillors
    # in the same city — those are roster/contact pages with no
    # per-councillor detail (e.g. www.welland.ca/council/Council.asp is
    # reused for every Welland member). Scraping them would only yield
    # footer links.
    sql = """
        WITH shared AS (
            SELECT w.url
            FROM websites w
            JOIN politicians p ON p.id = w.owner_id
            WHERE p.level = 'municipal'
              AND w.url IS NOT NULL AND w.url <> ''
            GROUP BY w.url
            HAVING COUNT(*) > 1
        )
        SELECT DISTINCT ON (p.id)
               p.id, p.name, p.constituency_name, p.province_territory,
               w.url
        FROM politicians p
        JOIN websites w
          ON w.owner_type='politician' AND w.owner_id=p.id
        WHERE p.level='municipal'
          AND p.is_active = true
          AND (p.personal_url IS NULL OR p.personal_url = '')
          AND w.url IS NOT NULL AND w.url <> ''
          AND w.url NOT IN (SELECT url FROM shared)
        ORDER BY p.id, w.id
    """
    if limit:
        sql += f" LIMIT {int(limit)}"
    rows = await db.fetch(sql)
    if not rows:
        console.print("[yellow]No municipal councillors need enrichment[/yellow]")
        return 0

    console.print(
        f"[cyan]Municipal enrichment: {len(rows)} councillors across "
        f"{len({_host_of(r['url']) for r in rows})} hosts[/cyan]"
    )

    # Phase A (free): councillors whose existing website already looks like
    # their personal domain — promote directly, no HTTP fetch needed.
    free_promotions = 0
    remaining: list = []
    for r in rows:
        host = _host_of(r["url"])
        if _looks_like_personal_domain(host, r["name"] or ""):
            if await _attach(db, str(r["id"]), r["url"], "personal"):
                free_promotions += 1
            continue
        remaining.append(r)
    if free_promotions:
        console.print(
            f"[green]  Phase A: promoted {free_promotions} self-URL rows "
            f"(Open North already returned a personal domain)[/green]"
        )

    # Phase B: per-host fetch+scrape for everyone else.
    async with httpx.AsyncClient(
        timeout=25,
        headers={"User-Agent": USER_AGENT},
        follow_redirects=True,
        limits=httpx.Limits(max_connections=concurrency,
                            max_keepalive_connections=concurrency),
    ) as client:
        found = 0
        socials_found = 0
        per_cms_hits: dict[str, int] = defaultdict(int)
        per_host_attempts: dict[str, int] = defaultdict(int)
        per_host_hits: dict[str, int] = defaultdict(int)
        sem = asyncio.Semaphore(concurrency)

        with Progress(
            SpinnerColumn(), TextColumn("{task.description}"), BarColumn(),
            TextColumn("{task.completed}/{task.total}"), TimeElapsedColumn(),
        ) as progress:
            task = progress.add_task("Discovering", total=len(remaining))

            async def handle(row) -> None:
                nonlocal found, socials_found
                async with sem:
                    try:
                        city_host = _host_of(row["url"])
                        per_host_attempts[city_host] += 1

                        # Try registered CMS-specific scraper for this host.
                        personal: Optional[str] = None
                        cms_key: Optional[str] = None
                        for frag, fn in CMS_SCRAPERS.items():
                            if frag in city_host:
                                cms_key = frag
                                try:
                                    personal = await fn(
                                        client, row["url"], city_host,
                                    )
                                except Exception as exc:
                                    log.debug(
                                        "CMS scraper %s failed for %s: %s",
                                        frag, row["url"], exc,
                                    )
                                break

                        if personal:
                            per_cms_hits[cms_key or "cms"] += 1

                        # Generic fallback (also catches same page if CMS
                        # scraper returned None but the page does expose a
                        # nameful external link).
                        if not personal:
                            name_tokens = {
                                _canon_name(t) for t in
                                re.split(r"[\s\-'.]+", row["name"] or "")
                                if len(t) >= 3
                            }
                            name_tokens.discard("")
                            ward_tokens = {
                                _canon_name(t) for t in
                                re.split(r"[\s\-'.]+",
                                         row["constituency_name"] or "")
                                if len(t) >= 3
                            }
                            ward_tokens.discard("")
                            personal = await _scrape_generic(
                                client, row["url"], city_host,
                                name_tokens=name_tokens,
                                ward_tokens=ward_tokens,
                            )
                            if personal:
                                per_cms_hits["generic"] += 1

                        if personal:
                            if not personal.startswith("http"):
                                personal = "http://" + personal
                            if await _attach(db, str(row["id"]), personal,
                                             "personal"):
                                found += 1
                                per_host_hits[city_host] += 1
                            # Opportunistically harvest socials from the
                            # councillor's personal site (same trick as the
                            # federal/provincial enrichers).
                            try:
                                r2 = await _polite_get(client, personal)
                                if r2 and r2.status_code == 200:
                                    socials = _extract_socials_from_html(
                                        r2.text)
                                    socials_found += await _attach_socials(
                                        db, str(row["id"]), socials)
                            except Exception:
                                pass
                    except Exception as exc:
                        log.warning("muni enrich failed for %s: %s",
                                    row.get("name"), exc)
                    finally:
                        progress.update(task, advance=1)

            await asyncio.gather(*(handle(r) for r in remaining))

    total_discovered = free_promotions + found
    console.print(
        f"\n[green]✓ municipal enrichment: {total_discovered} personal URLs · "
        f"{socials_found} socials[/green]"
    )
    console.print("[cyan]Hit-rate by scraper:[/cyan]")
    for cms, n in sorted(per_cms_hits.items(), key=lambda kv: -kv[1]):
        console.print(f"    {cms:<22} {n}")
    console.print("[cyan]Top hosts by hit:[/cyan]")
    top = sorted(per_host_hits.items(), key=lambda kv: -kv[1])[:15]
    for host, n in top:
        attempts = per_host_attempts.get(host, 0)
        rate = (n / attempts) if attempts else 0.0
        console.print(f"    {host:<32} {n}/{attempts}  ({rate*100:.0f}%)")

    return total_discovered
