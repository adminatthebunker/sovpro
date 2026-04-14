"""Aggregator — run every gap_fillers submodule in sequence.

Non-fatal per-filler: if any one scraper explodes, the runner logs the
exception and continues with the next province.
"""
from __future__ import annotations

import logging

from rich.console import Console

from ..db import Database
from . import bc, nb, nl, nunavut, ontario, yukon

log = logging.getLogger(__name__)
console = Console()


FILLERS = (
    ("nunavut", nunavut.run),
    ("yukon", yukon.run),
    ("nb", nb.run),
    ("nl", nl.run),
    ("bc", bc.run),
    ("ontario", ontario.run),
)


async def run_all(db: Database) -> None:
    console.print(
        f"[cyan bold]━━ gap_fillers.run_all: {len(FILLERS)} scrapers ━━"
        f"[/cyan bold]"
    )
    for name, fn in FILLERS:
        console.print(f"[cyan bold]━━ {name} ━━[/cyan bold]")
        try:
            await fn(db)
        except Exception as exc:
            log.exception("gap_filler %s failed: %s", name, exc)
            console.print(f"[red]  {name}: {exc}[/red]")
