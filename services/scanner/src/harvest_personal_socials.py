"""Nationwide harvest of social handles from politicians' personal campaign sites.

Phase 5's discovery passes (`enrich.py`) already call `_scrape_personal_site()`
for politicians whose `personal_url` was freshly discovered via ourcommons.ca
or assembly.ab.ca. But a large fraction of `personal_url` values in the DB
come from *other* sources that never ran the header/footer harvest:

  * Team A gap fillers (NU / YT / NB / NL / BC / ON)
  * Ontario Wikipedia + Wikidata lookups
  * Senate Umbraco partials
  * Municipal enrichment (muni_enrich runs it for newly-discovered URLs,
    but not for ones promoted via the "self-URL" free-phase)

This module walks *every* active politician with a personal URL — whether
stored in `politicians.personal_url` or only present as a
`websites.label='personal'` row — fetches it once, extracts socials from
the HTML via `enrich._extract_socials_from_html`, and upserts via
`socials.upsert_social`. It's safe to re-run (upserts are idempotent).

Politeness:
  * robots.txt honoured per host (cached).
  * 1s minimum gap per host. Because campaign sites occasionally share
    hosting infra (e.g. two councillors on the same WordPress stack) we
    serialise per host rather than per politician.
  * Global concurrency cap of 8 across hosts.
  * Browser-ish User-Agent (several WordPress/Shopify fronts 403 our
    default bot UA) that still advertises the project URL.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections import Counter, defaultdict
from typing import Optional
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import httpx
from rich.console import Console
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn

from .db import Database
from .enrich import _extract_socials_from_html
from .socials import upsert_social

log = logging.getLogger(__name__)
console = Console()


USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "CanadianPoliticalDataBot/1.0 Chrome/126.0.0.0 Safari/537.36 "
    "(+https://canadianpoliticaldata.ca)"
)

# Concurrency / politeness knobs.
_CONCURRENCY = 8
_MIN_GAP_S = 1.0
_TIMEOUT_S = 15.0


# ── Robots.txt cache (per-host) ──────────────────────────────────

_robots_cache: dict[str, Optional[RobotFileParser]] = {}
_robots_lock = asyncio.Lock()


def _host_of(url: str) -> str:
    try:
        h = (urlparse(url).hostname or "").lower()
        return h[4:] if h.startswith("www.") else h
    except Exception:
        return ""


async def _robots_allows(client: httpx.AsyncClient, url: str) -> bool:
    """Return True if robots.txt permits fetching `url` for our UA.

    Missing / unreachable / empty robots.txt is treated as allow-all, which
    matches the RFC-9309 "no rules = all paths allowed" default.
    """
    host = _host_of(url)
    if not host:
        return True
    async with _robots_lock:
        if host in _robots_cache:
            rp = _robots_cache[host]
        else:
            rp = RobotFileParser()
            scheme = urlparse(url).scheme or "https"
            robots_url = f"{scheme}://{host}/robots.txt"
            try:
                r = await client.get(robots_url, timeout=10)
                if r.status_code == 200 and r.text.strip():
                    rp.parse(r.text.splitlines())
                else:
                    rp = None
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

_host_last_fetch: dict[str, float] = defaultdict(float)
_host_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)


async def _polite_get(
    client: httpx.AsyncClient, url: str,
) -> Optional[httpx.Response]:
    """Fetch `url` honouring robots.txt + a 1s-per-host min gap."""
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
                _host_last_fetch[host] = time.monotonic()
                return None
            r = await client.get(url)
            _host_last_fetch[host] = time.monotonic()
            return r
        except Exception as exc:
            log.debug("fetch failed for %s: %s", url, exc)
            _host_last_fetch[host] = time.monotonic()
            return None


# ── Social upsert (local copy so we can count per-platform) ──────

async def _attach_socials_counted(
    db: Database,
    politician_id: str,
    socials: dict[str, str],
    per_platform: Counter[str],
    *,
    evidence_url: Optional[str] = None,
) -> int:
    """Upsert each discovered social. Returns count *newly* inserted.

    `upsert_social` returns a CanonicalSocial on success (insert OR update)
    and None on canonicalisation failure. To distinguish new vs already-known
    we peek at politician_socials before upserting.
    """
    if not socials:
        return 0
    saved = 0
    for platform_hint, url in socials.items():
        try:
            # Pre-check existence: only count 'new' inserts, not URL-only
            # updates of known (politician, platform, handle) rows.
            from .socials import canonicalize
            canon = canonicalize(platform_hint, url)
            if canon is None or canon.platform == "other" or not canon.handle:
                continue
            existing = await db.fetchrow(
                """
                SELECT 1 FROM politician_socials
                WHERE politician_id = $1 AND platform = $2
                  AND LOWER(handle) = LOWER($3)
                """,
                politician_id, canon.platform, canon.handle,
            )
            result = await upsert_social(
                db, politician_id, platform_hint, url,
                source="personal_site",
                evidence_url=evidence_url,
            )
            if result is not None and existing is None:
                saved += 1
                per_platform[result.platform] += 1
        except Exception as exc:
            log.debug(
                "upsert_social failed for %s %s: %s",
                politician_id, url, exc,
            )
    return saved


# ── Main entry point ─────────────────────────────────────────────

async def harvest_all_personal_socials(
    db: Database, *, limit: Optional[int] = None,
) -> dict[str, int]:
    """Fetch every politician's personal site and harvest socials from it.

    Target set = active politicians where either:
      * `politicians.personal_url` is set, OR
      * there exists a `websites` row with owner = this politician and
        `label='personal'`.

    Returns a stats dict with:
      politicians_touched   unique politicians we attempted to scrape
      pages_fetched         successful HTTP 200 responses
      pages_failed          HTTP non-200 / transport errors / robots-denied
      socials_added         count of NEW politician_socials rows inserted
      per_platform          {platform: new_count}
    """
    sql = """
        WITH candidates AS (
            -- 1) politicians with a non-empty personal_url field
            SELECT p.id, p.name, p.personal_url AS target_url, 1 AS rank
            FROM politicians p
            WHERE p.is_active = true
              AND p.personal_url IS NOT NULL AND p.personal_url <> ''
            UNION ALL
            -- 2) politicians without personal_url but with a
            --    websites.label='personal' row
            SELECT p.id, p.name, w.url AS target_url, 2 AS rank
            FROM politicians p
            JOIN websites w
              ON w.owner_type = 'politician' AND w.owner_id = p.id
            WHERE p.is_active = true
              AND (p.personal_url IS NULL OR p.personal_url = '')
              AND w.label = 'personal'
              AND w.url IS NOT NULL AND w.url <> ''
        )
        SELECT DISTINCT ON (id) id, name, target_url
        FROM candidates
        ORDER BY id, rank, target_url
    """
    if limit:
        sql += f" LIMIT {int(limit)}"
    rows = await db.fetch(sql)

    if not rows:
        console.print("[yellow]No politicians with personal URLs to harvest[/yellow]")
        return {
            "politicians_touched": 0, "pages_fetched": 0,
            "pages_failed": 0, "socials_added": 0,
        }

    n_hosts = len({_host_of(r["target_url"]) for r in rows})
    console.print(
        f"[cyan]Harvesting {len(rows)} personal sites across "
        f"{n_hosts} hosts (concurrency={_CONCURRENCY}, "
        f"min_gap={_MIN_GAP_S:.0f}s/host)[/cyan]"
    )

    pages_fetched = 0
    pages_failed = 0
    socials_added = 0
    per_platform: Counter[str] = Counter()
    sem = asyncio.Semaphore(_CONCURRENCY)

    async with httpx.AsyncClient(
        timeout=_TIMEOUT_S,
        headers={"User-Agent": USER_AGENT},
        follow_redirects=True,
        limits=httpx.Limits(
            max_connections=_CONCURRENCY,
            max_keepalive_connections=_CONCURRENCY,
        ),
    ) as client:
        with Progress(
            SpinnerColumn(),
            TextColumn("{task.description}"),
            BarColumn(),
            TextColumn("{task.completed}/{task.total}"),
            TimeElapsedColumn(),
        ) as progress:
            task = progress.add_task("Harvesting", total=len(rows))

            async def handle(row) -> None:
                nonlocal pages_fetched, pages_failed, socials_added
                async with sem:
                    url = (row["target_url"] or "").strip()
                    if not url:
                        progress.update(task, advance=1)
                        return
                    if not url.startswith("http"):
                        url = "http://" + url
                    try:
                        r = await _polite_get(client, url)
                        if r is None or r.status_code != 200:
                            pages_failed += 1
                            log.debug(
                                "fetch failed (%s) for %s: %s",
                                row.get("name"),
                                url,
                                None if r is None else r.status_code,
                            )
                            return
                        pages_fetched += 1
                        socials = _extract_socials_from_html(r.text)
                        if not socials:
                            return
                        added = await _attach_socials_counted(
                            db, str(row["id"]), socials, per_platform,
                            evidence_url=url,
                        )
                        socials_added += added
                        if added:
                            log.info(
                                "harvested %d new socials from %s (%s)",
                                added, row.get("name"), url,
                            )
                    except Exception as exc:
                        pages_failed += 1
                        log.warning(
                            "harvest failed for %s: %s",
                            row.get("name"), exc,
                        )
                    finally:
                        progress.update(task, advance=1)

            await asyncio.gather(*(handle(r) for r in rows))

    console.print(
        f"\n[green]harvest done: "
        f"politicians_touched={len(rows)} "
        f"pages_fetched={pages_fetched} "
        f"pages_failed={pages_failed} "
        f"socials_added={socials_added}[/green]"
    )
    if per_platform:
        console.print("[cyan]New rows per platform:[/cyan]")
        for plat, n in per_platform.most_common():
            console.print(f"    {plat:<10} {n}")

    return {
        "politicians_touched": len(rows),
        "pages_fetched": pages_fetched,
        "pages_failed": pages_failed,
        "socials_added": socials_added,
        **{f"p_{k}": v for k, v in per_platform.items()},
    }
