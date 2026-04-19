"""Shared helpers for gap_fillers submodules.

Provides:
  - BROWSER_UA: realistic Chrome UA string (several provincial-legislature
    sites return 403 to the scanner's default UA but accept browsers).
  - upsert_politician: minimal version of opennorth._upsert_politician for
    records we construct ourselves (no Open North JSON envelope).
  - attach_website: idempotent insert into `websites`.
  - attach_socials: map {platform_hint: url} into politician_socials via
    socials.upsert_social.
"""
from __future__ import annotations

import logging
from typing import Optional

import orjson

from ..db import Database
from ..opennorth import SHARED_OFFICIAL_HOSTS
from ..socials import upsert_social

log = logging.getLogger(__name__)


# Realistic Chrome UA — several provincial-legislature sites (notably
# yukonassembly.ca behind Cloudflare) return 403 for our default bot UA.
BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/128.0.0.0 Safari/537.36"
)


def label_for(url: str, default: str) -> str:
    """Return 'shared_official' if the URL is on a known shared-infra host."""
    try:
        from urllib.parse import urlparse
        host = (urlparse(url).hostname or "").lower()
    except Exception:
        return default
    return "shared_official" if host in SHARED_OFFICIAL_HOSTS else default


async def upsert_politician(
    db: Database,
    *,
    source_id: str,
    name: str,
    level: str,
    province: str,
    office: str,
    party: Optional[str] = None,
    first_name: Optional[str] = None,
    last_name: Optional[str] = None,
    constituency_name: Optional[str] = None,
    constituency_id: Optional[str] = None,
    email: Optional[str] = None,
    phone: Optional[str] = None,
    photo_url: Optional[str] = None,
    personal_url: Optional[str] = None,
    official_url: Optional[str] = None,
    social_urls: Optional[dict] = None,
    extras: Optional[dict] = None,
) -> str:
    """Insert or update a politician row; return its UUID.

    This is the gap-filler counterpart to opennorth._upsert_politician — same
    semantics, but driven from keyword args because we're building records
    from scratch rather than from an Open North JSON envelope.
    """
    row = await db.fetchrow(
        """
        INSERT INTO politicians (
            source_id, name, first_name, last_name, party, elected_office,
            level, province_territory, constituency_name, constituency_id,
            email, phone, photo_url, personal_url, official_url,
            social_urls, extras
        )
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17)
        ON CONFLICT (source_id) DO UPDATE SET
            name = EXCLUDED.name,
            first_name = COALESCE(EXCLUDED.first_name, politicians.first_name),
            last_name = COALESCE(EXCLUDED.last_name, politicians.last_name),
            party = COALESCE(EXCLUDED.party, politicians.party),
            elected_office = EXCLUDED.elected_office,
            constituency_name = COALESCE(EXCLUDED.constituency_name, politicians.constituency_name),
            constituency_id = COALESCE(EXCLUDED.constituency_id, politicians.constituency_id),
            email = COALESCE(EXCLUDED.email, politicians.email),
            phone = COALESCE(EXCLUDED.phone, politicians.phone),
            photo_url = COALESCE(EXCLUDED.photo_url, politicians.photo_url),
            -- Only upgrade personal_url if we now have a non-empty value.
            personal_url = COALESCE(NULLIF(EXCLUDED.personal_url, ''), politicians.personal_url),
            official_url = COALESCE(NULLIF(EXCLUDED.official_url, ''), politicians.official_url),
            social_urls = politicians.social_urls || EXCLUDED.social_urls,
            extras = politicians.extras || EXCLUDED.extras,
            updated_at = now(),
            is_active = true
        RETURNING id
        """,
        source_id,
        name,
        first_name,
        last_name,
        party,
        office,
        level,
        province,
        constituency_name,
        constituency_id,
        email,
        phone,
        photo_url,
        personal_url,
        official_url,
        orjson.dumps(social_urls or {}).decode(),
        orjson.dumps(extras or {}).decode(),
    )
    return str(row["id"])


async def attach_website(
    db: Database,
    politician_id: str,
    url: str,
    label: str = "personal",
) -> bool:
    """Insert a website row; return True if new (False if row already existed).

    Applies the shared-official classifier automatically: if `url` belongs
    to a known shared institutional host the inserted label is forced to
    'shared_official', matching opennorth._label_for semantics.
    """
    if not url:
        return False
    effective = label_for(url, label)
    row = await db.fetchrow(
        """
        INSERT INTO websites (owner_type, owner_id, url, label)
        VALUES ('politician', $1, $2, $3)
        ON CONFLICT (owner_type, owner_id, url) DO NOTHING
        RETURNING id
        """,
        politician_id, url, effective,
    )
    # Also promote politicians.personal_url if this is the politician's
    # "personal" site (mirror _attach() in enrich.py).
    if effective == "personal":
        await db.execute(
            """
            UPDATE politicians
               SET personal_url = COALESCE(NULLIF(personal_url, ''), $2),
                   updated_at = now()
             WHERE id = $1
            """,
            politician_id, url,
        )
    return row is not None


async def attach_socials(
    db: Database,
    politician_id: str,
    socials: dict[str, str],
    *,
    source: str = "gap_filler",
    evidence_url: Optional[str] = None,
) -> int:
    """Upsert each {platform_hint: url} into politician_socials.

    Returns the number of rows saved. Individual failures are logged but do
    not abort the caller.
    """
    if not socials:
        return 0
    saved = 0
    for platform_hint, url in socials.items():
        try:
            canon = await upsert_social(
                db, politician_id, platform_hint, url,
                source=source,
                evidence_url=evidence_url,
            )
            if canon is not None:
                saved += 1
        except Exception as exc:
            log.debug("upsert_social failed for %s %s: %s",
                      politician_id, url, exc)
    return saved
