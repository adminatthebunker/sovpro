"""Fetch and cache per-bill HTML pages from nslegislature.ca.

Phase 2 of the NS bills pipeline:
  phase 1 (ns_bills.py)         — Socrata JSON → bills table
  phase 2 (this module)         — fetch HTML for each bill.source_url,
                                  store in bills.raw_html
  phase 3 (ns_bill_parse.py)    — pure offline parser: sponsor, events,
                                  intro date → bill_sponsors / bill_events

Separation matters: the fetcher is network-bound and slow (~3,500 pages
at polite rate = ~1 hour); the parser is CPU-bound and fast (seconds).
Re-running the parser to improve extraction never hits the network.

Re-entrancy: by default we only fetch bills where raw_html IS NULL, so
you can interrupt and resume. Pass --force to refetch everything.
"""
from __future__ import annotations

import asyncio
import logging
import random
from datetime import datetime, timezone
from typing import Optional

import httpx

from ..db import Database
from ..gap_fillers.shared import BROWSER_UA

log = logging.getLogger(__name__)

# nslegislature.ca sits behind an F5-style WAF with burst detection.
# Empirically: 1 req/sec trips the block after ~14 successful requests,
# then returns HTTP 200 + a ~244-byte "Request Rejected" body for every
# subsequent request; cooldown is a few minutes.
#
# Strategy: (a) pace conservatively with jitter to avoid looking like a
# scraper, (b) fingerprint the WAF response and abort the whole run the
# moment we see it — continuing would waste network + deepen the block.
DEFAULT_DELAY_SECS = 4.0    # min inter-request delay
DEFAULT_JITTER_SECS = 2.0   # add 0..jitter seconds random on top
REQUEST_TIMEOUT = 30

# Consecutive WAF hits before we abort the run entirely. First hit is a
# maybe-transient glitch; two in a row means the block is live.
WAF_ABORT_THRESHOLD = 2

# F5 ASM "Request Rejected" page fingerprint. The full body is ~244
# bytes, but the title alone is a stable, unambiguous marker.
_WAF_MARKER = "Request Rejected"


def _looks_like_waf(body: str) -> bool:
    return len(body) < 1000 and _WAF_MARKER in body


class WAFBlocked(Exception):
    """Signalled by _fetch_one when the WAF fingerprint is detected."""


async def _fetch_one(
    client: httpx.AsyncClient, url: str
) -> tuple[Optional[str], Optional[str]]:
    """Return (html, error). Exactly one is non-None. Raises WAFBlocked
    when the F5 "Request Rejected" fingerprint is seen so the caller
    can halt the run instead of burning requests into a live block."""
    try:
        r = await client.get(url, timeout=REQUEST_TIMEOUT)
        if r.status_code != 200:
            return None, f"http {r.status_code}"
        if _looks_like_waf(r.text):
            raise WAFBlocked(url)
        if len(r.text) < 500:
            return None, f"suspiciously short response ({len(r.text)} bytes)"
        return r.text, None
    except httpx.HTTPError as exc:
        return None, f"{type(exc).__name__}: {exc}"


async def fetch_ns_bill_pages(
    db: Database,
    *,
    limit: Optional[int] = None,
    force: bool = False,
    delay_secs: float = DEFAULT_DELAY_SECS,
    jitter_secs: float = DEFAULT_JITTER_SECS,
) -> dict[str, int]:
    """Fetch HTML for each bill lacking a cached copy.

    Args:
        limit: max bills to fetch this run.
        force: re-fetch even if raw_html already populated.
        delay_secs: minimum sleep between requests.
        jitter_secs: add 0..jitter random seconds on top (anti-pattern).
    """
    if force:
        where = "source_url IS NOT NULL"
    else:
        where = "source_url IS NOT NULL AND raw_html IS NULL"
    sql = f"SELECT id, source_url FROM bills WHERE {where} ORDER BY status_changed_at DESC NULLS LAST"
    if limit is not None:
        sql += f" LIMIT {int(limit)}"

    rows = await db.fetch(sql)
    total = len(rows)
    log.info("ns_bill_pages: queued %d bills (force=%s)", total, force)

    stats = {"ok": 0, "err": 0, "waf_aborted": 0, "total": total}
    if not rows:
        return stats

    # Full browser-style headers — F5 ASM scores request "completeness"
    # and a bare User-Agent alone looks bot-like.
    headers = {
        "User-Agent": BROWSER_UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
    }

    waf_hits = 0
    async with httpx.AsyncClient(
        headers=headers,
        follow_redirects=True,
    ) as client:
        for i, row in enumerate(rows, start=1):
            url = row["source_url"]
            bill_id = str(row["id"])
            try:
                html, err = await _fetch_one(client, url)
            except WAFBlocked:
                waf_hits += 1
                log.warning(
                    "ns_bill_pages: WAF fingerprint detected (hit %d/%d) at %s",
                    waf_hits, WAF_ABORT_THRESHOLD, url,
                )
                if waf_hits >= WAF_ABORT_THRESHOLD:
                    stats["waf_aborted"] = 1
                    log.error(
                        "ns_bill_pages: aborting run — WAF block is live. "
                        "Resume later; progress is saved (cached=%d).",
                        stats["ok"],
                    )
                    break
                # First hit could be a glitch; back off and retry once.
                await asyncio.sleep(60)
                continue
            # Reset the counter on any successful response — we want to
            # abort only on *consecutive* WAF hits.
            waf_hits = 0
            now = datetime.now(timezone.utc)
            if html is not None:
                await db.execute(
                    """
                    UPDATE bills
                       SET raw_html          = $2,
                           html_fetched_at   = $3,
                           html_last_error   = NULL,
                           html_last_error_at = NULL,
                           updated_at        = now()
                     WHERE id = $1
                    """,
                    bill_id, html, now,
                )
                stats["ok"] += 1
            else:
                await db.execute(
                    """
                    UPDATE bills
                       SET html_last_error    = $2,
                           html_last_error_at = $3,
                           updated_at         = now()
                     WHERE id = $1
                    """,
                    bill_id, err, now,
                )
                stats["err"] += 1
                log.warning("ns_bill_pages: fetch failed for %s: %s", url, err)

            # Progress log every 50 bills — useful when this runs for an hour.
            if i % 50 == 0:
                log.info(
                    "ns_bill_pages: %d/%d done ok=%d err=%d",
                    i, total, stats["ok"], stats["err"],
                )

            if i < total:
                pause = delay_secs + (random.random() * jitter_secs if jitter_secs > 0 else 0.0)
                await asyncio.sleep(pause)

    log.info(
        "ns_bill_pages: finished ok=%d err=%d waf_aborted=%d total=%d",
        stats["ok"], stats["err"], stats["waf_aborted"], stats["total"],
    )
    return stats
