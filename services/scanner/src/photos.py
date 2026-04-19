"""Mirror politician portraits from upstream URLs onto the local
`assets` Docker volume.

See db/migrations/0026_politician_photo_local.sql for the schema
rationale and docs/plans/sovereignty-runtime-deps.md for the broader
in-house-media strategy this command is part of.

Runtime shape (idempotent, per CLAUDE.md convention #7):

    SELECT candidates
    for each politician:
        GET upstream
        sha256 the bytes
        if hash matches existing: just UPDATE photo_fetched_at
        else:                     write file + UPDATE path/hash/ts

Rate-limit is per upstream host (convention #6) — openparliament.ca is
the aggressive one; most provincial sites tolerate a modest burst.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urlsplit

import httpx

from .db import Database


log = logging.getLogger(__name__)

# Volume mount point inside the scanner container. Matches the RW mount
# declared in docker-compose.yml.
ASSETS_ROOT = os.environ.get("ASSETS_ROOT", "/assets")
PHOTOS_SUBDIR = "politicians"

REQUEST_TIMEOUT = 30.0
MAX_BYTES = 5 * 1024 * 1024  # 5 MB — portraits are ~50 KB; defends against wrong-URL HTML
DEFAULT_STALE_DAYS = 30
DEFAULT_CONCURRENCY = 4

USER_AGENT = os.environ.get(
    "SCANNER_USER_AGENT",
    "CanadianPoliticalDataBot/1.0 (+https://canadianpoliticaldata.ca)",
)
HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "image/*, */*;q=0.5",
}

# Politeness spacing by host. Fallback applies to everything not listed.
# openparliament is documented at ~1 req/sec sustained; we match that.
HOST_SPACING_S: dict[str, float] = {
    "openparliament.ca": 1.2,
    "api.openparliament.ca": 1.2,
    "represent.opennorth.ca": 0.3,
    "sencanada.ca": 0.5,
}
DEFAULT_SPACING_S = 0.3

_CONTENT_TYPE_EXT: dict[str, str] = {
    "image/jpeg": "jpg",
    "image/jpg": "jpg",
    "image/pjpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
    "image/gif": "gif",
    "image/svg+xml": "svg",
}


@dataclass
class PhotoStats:
    considered: int = 0
    fetched: int = 0          # bytes actually downloaded this run
    unchanged: int = 0        # hash matched; only fetched_at bumped
    written: int = 0          # new or changed bytes written to disk
    skipped: int = 0          # no photo_url, or error after retries
    failed: int = 0
    fail_samples: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"considered={self.considered} fetched={self.fetched} "
            f"unchanged={self.unchanged} written={self.written} "
            f"skipped={self.skipped} failed={self.failed}"
        )


class _HostSpacer:
    """One global monotonic clock per host, enforcing a minimum gap
    between outbound requests. `wait()` is reentrant from multiple
    coroutines — the lock is per-host so two different hosts don't
    block each other."""

    def __init__(self) -> None:
        self._locks: dict[str, asyncio.Lock] = {}
        self._last: dict[str, float] = {}

    def _lock_for(self, host: str) -> asyncio.Lock:
        lock = self._locks.get(host)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[host] = lock
        return lock

    async def wait(self, host: str) -> None:
        spacing = HOST_SPACING_S.get(host, DEFAULT_SPACING_S)
        async with self._lock_for(host):
            now = time.monotonic()
            last = self._last.get(host, 0.0)
            gap = now - last
            if gap < spacing:
                await asyncio.sleep(spacing - gap)
            self._last[host] = time.monotonic()


def _ext_from_response(ct: Optional[str], url: str) -> str:
    if ct:
        base = ct.split(";", 1)[0].strip().lower()
        if base in _CONTENT_TYPE_EXT:
            return _CONTENT_TYPE_EXT[base]
    path = urlsplit(url).path.lower()
    for candidate in (".jpg", ".jpeg", ".png", ".webp", ".gif", ".svg"):
        if path.endswith(candidate):
            return "jpg" if candidate == ".jpeg" else candidate.lstrip(".")
    return "bin"


async def _fetch_one(
    client: httpx.AsyncClient,
    spacer: _HostSpacer,
    url: str,
) -> tuple[bytes, str]:
    """Return (bytes, content_type). Raises on non-2xx or oversize."""
    host = urlsplit(url).netloc.lower()
    await spacer.wait(host)
    r = await client.get(url, headers=HEADERS, follow_redirects=True, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    data = r.content
    if len(data) > MAX_BYTES:
        raise ValueError(f"oversize response {len(data)} bytes for {url}")
    return data, r.headers.get("content-type", "")


async def backfill_politician_photos(
    db: Database,
    *,
    limit: Optional[int] = None,
    stale_days: int = DEFAULT_STALE_DAYS,
    politician_id: Optional[str] = None,
    concurrency: int = DEFAULT_CONCURRENCY,
) -> PhotoStats:
    """Mirror upstream portraits to ASSETS_ROOT/politicians/<id>.<ext>."""
    photos_dir = os.path.join(ASSETS_ROOT, PHOTOS_SUBDIR)
    os.makedirs(photos_dir, exist_ok=True)

    if politician_id is not None:
        rows = await db.fetch(
            """
            SELECT id::text AS id, photo_url, photo_bytes_hash
              FROM politicians
             WHERE id = $1::uuid
               AND photo_url IS NOT NULL
            """,
            politician_id,
        )
    else:
        rows = await db.fetch(
            """
            SELECT id::text AS id, photo_url, photo_bytes_hash
              FROM politicians
             WHERE photo_url IS NOT NULL
               AND (photo_path IS NULL
                    OR photo_fetched_at IS NULL
                    OR photo_fetched_at < now() - ($1::int * interval '1 day'))
             ORDER BY (photo_path IS NULL) DESC, photo_fetched_at NULLS FIRST
             LIMIT $2
            """,
            stale_days,
            limit if limit is not None else 1_000_000,
        )

    stats = PhotoStats()
    if not rows:
        return stats

    spacer = _HostSpacer()
    sem = asyncio.Semaphore(max(1, concurrency))

    async with httpx.AsyncClient() as client:

        async def process(row) -> None:
            async with sem:
                await _process_one(db, client, spacer, photos_dir, row, stats)

        await asyncio.gather(*(process(r) for r in rows))

    return stats


async def _process_one(
    db: Database,
    client: httpx.AsyncClient,
    spacer: _HostSpacer,
    photos_dir: str,
    row,
    stats: PhotoStats,
) -> None:
    stats.considered += 1
    pid: str = row["id"]
    url: str = row["photo_url"]
    prior_hash: Optional[str] = row["photo_bytes_hash"]

    try:
        data, ct = await _fetch_one(client, spacer, url)
    except Exception as e:
        stats.failed += 1
        if len(stats.fail_samples) < 5:
            stats.fail_samples.append(f"{pid}: {type(e).__name__}: {e}")
        log.warning("photo fetch failed pid=%s url=%s err=%s", pid, url, e)
        return

    stats.fetched += 1
    digest = hashlib.sha256(data).hexdigest()

    if digest == prior_hash:
        await db.execute(
            """
            UPDATE politicians
               SET photo_fetched_at = now(),
                   photo_source_url = $2
             WHERE id = $1::uuid
            """,
            pid,
            url,
        )
        stats.unchanged += 1
        return

    ext = _ext_from_response(ct, url)
    rel_path = f"{PHOTOS_SUBDIR}/{pid}.{ext}"
    abs_path = os.path.join(photos_dir, f"{pid}.{ext}")
    tmp_path = abs_path + ".part"

    # Atomic-ish write: tmp + rename. Prevents partial files being served
    # if the scanner is killed mid-write.
    with open(tmp_path, "wb") as f:
        f.write(data)
    os.replace(tmp_path, abs_path)

    # If the extension changed (e.g. upstream switched png→webp), the
    # previous file is orphaned. Clean it up so /assets/ never 404s a
    # stale path that's still in the DB.
    prior_row = await db.fetchrow(
        "SELECT photo_path FROM politicians WHERE id = $1::uuid",
        pid,
    )
    prior_path = prior_row["photo_path"] if prior_row else None

    await db.execute(
        """
        UPDATE politicians
           SET photo_path        = $2,
               photo_bytes_hash  = $3,
               photo_fetched_at  = now(),
               photo_source_url  = $4
         WHERE id = $1::uuid
        """,
        pid,
        rel_path,
        digest,
        url,
    )

    if prior_path and prior_path != rel_path:
        orphan = os.path.join(ASSETS_ROOT, prior_path)
        try:
            os.remove(orphan)
        except FileNotFoundError:
            pass
        except OSError as e:
            log.warning("orphan cleanup failed path=%s err=%s", orphan, e)

    stats.written += 1
