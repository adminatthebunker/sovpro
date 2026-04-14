"""Gap fillers — direct scrapers for legislatures that Open North doesn't
cover (Nunavut) or leaves with empty `url` fields (NB, NL, BC, Yukon, Ontario).

Each submodule exposes ``async def run(db: Database) -> None`` and is a
best-effort, non-fatal scraper. Failures are logged; they never raise.

The aggregator ``gap_fillers.runner.run_all(db)`` invokes every sub-scraper
in sequence. Each scraper upserts into ``politicians`` + ``websites`` using
the shared helpers below so the pipeline stays consistent with the Open
North ingestion path (``opennorth._upsert_politician`` / ``_attach_websites``).
"""
from __future__ import annotations

from .shared import (
    BROWSER_UA,
    attach_socials,
    attach_website,
    upsert_politician,
)

__all__ = [
    "BROWSER_UA",
    "attach_socials",
    "attach_website",
    "upsert_politician",
]
