"""Ontario gap-filler — **deferred**.

Open North does ingest Ontario MPPs (124 rows via
``/representatives/ontario-legislature/``), so the roster itself is
already captured. What's missing is each MPP's *personal* / campaign site.

Why this gap is NOT a programmatic target today:

  - ``ola.org``'s ``/en/members/all/<slug>`` member pages do NOT expose an
    external personal-site link. Verified 2026-04-13 against Susan Holt's
    analogue + several sampled MPPs — the only external links on those
    pages are ola.org navigation, Queen's Park committee pages, and the
    institutional social handles (@OntLegislature, not per-member).
  - Elections Ontario's candidate directory lists campaign-period websites
    for candidates, but these domains routinely lapse after the writ drops
    and are unstable to attribute to sitting MPPs.
  - Wikidata coverage is sparse: querying ``P39=Q3305347``
    ("member of the Ontario Provincial Parliament") with P582 unset and
    P580 ≥ 2020 returns ~5 current sitting members, of whom 0 have
    ``P856`` (official website). The 44th Parliament item (Q132860911)
    has only 1 member tagged.
  - There is no single Ontario-wide "constituency associations" directory
    that programmatically lists per-riding sites; each of the ~720 active
    riding associations across PC/OLP/NDP/Green has its own URL
    convention — per-party scraping is ~4 separate Phase-4-scale projects.

Net effect: filling this gap cleanly requires one of:
  (a) a paid or partnership-based data feed (e.g. Progress Champions'
      curated MPP database),
  (b) manual per-MPP link curation, or
  (c) a long-running page-level crawler with fuzzy name-to-site matching
      (high false-positive rate, not trivially scoped to this task).

We keep a no-op scraper here so the CLI command exists for symmetry with
the other gap-fillers and so the aggregator has something to call.
"""
from __future__ import annotations

import logging

from rich.console import Console

from ..db import Database

log = logging.getLogger(__name__)
console = Console()


async def run(db: Database) -> None:  # noqa: ARG001 — signature-compat only
    console.print(
        "[yellow]Ontario gap-filler is deferred — ola.org does not expose "
        "personal URLs, Wikidata coverage is ~0 for sitting MPPs, and "
        "per-riding-association scraping is out of scope. "
        "Use ingest-ontario-mpps (Open North) to refresh the roster.[/yellow]"
    )
