"""Populate ``politician_offices`` from Open North ``offices`` JSON.

Open North exposes a per-representative ``offices`` array that typically
contains one ``legislature`` entry (the parliament/assembly seat) and one
or more ``constituency``/``office`` entries. Example shape::

    [
      {
        "type": "constituency",
        "postal": "102-517 King street\\nBridgewater NS  B4V 1B3",
        "tel": "1 902 527-5680",
        "fax": "1 902 527-5681"
      },
      {
        "type": "legislature",
        "postal": "House of Commons\\nOttawa ON  K1A 0A6",
        "tel": "1 613 995-6182"
      }
    ]

Field notes (empirical, verified 2026-04-13):
  * ``postal``  -- multi-line string where the final line is
                   ``"<city> <PROV>  <POSTAL>"``. Earlier lines are the
                   building / suite / street address.
  * ``tel``     -- primary phone.
  * ``alt``     -- alternate phone (treat like a second tel; not separate row).
  * ``fax``     -- fax number.
  * ``type``    -- one of ``constituency``, ``legislature``, ``office``.
                   Stored verbatim in ``politician_offices.kind``; default
                   ``constituency`` if missing.
  * ``email``   -- occasionally present; copied across when set.
  * ``hours``   -- rare; copied when set.

This module exposes two callables:

``backfill_offices(db)``
    One-time migration — walks every politician row whose
    ``extras ? 'offices'`` is true and materialises each entry. Idempotent
    via the ``(politician_id, kind, phone)`` dedupe check.

``_upsert_offices(db, politician_id, offices)``
    Used by ongoing ingestion (see ``opennorth._upsert_politician``). Safe
    to call on every ingest — skips entries already materialised.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Iterable, Optional

from rich.console import Console

from .db import Database

log = logging.getLogger(__name__)
console = Console()


# Canadian postal-code regex (anchored to end of line in parse_postal).
# Matches e.g. ``K1A 0A6``, ``K1A0A6`` (space optional).
POSTAL_RE = re.compile(r"\b([A-Z]\d[A-Z])\s*(\d[A-Z]\d)\b", re.IGNORECASE)

# Two-letter province/territory code.
PROVINCE_RE = re.compile(
    r"\b(AB|BC|MB|NB|NL|NS|NT|NU|ON|PE|QC|SK|YT)\b"
)


def _norm(v: Any) -> Optional[str]:
    """Return ``v`` stripped, or ``None`` for empty/None/blank strings."""
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def parse_postal(raw: Optional[str]) -> dict[str, Optional[str]]:
    """Best-effort parse of an Open North ``postal`` multi-line string.

    Returns a dict with ``address``, ``city``, ``province_territory``,
    ``postal_code`` -- any of which may be ``None`` when the string is
    ambiguous. Never raises.

    Strategy:
      1. Strip, then split on newlines. Drop blank lines.
      2. The last non-blank line usually looks like "<city> <PROV>  <POSTAL>".
      3. Regex-extract postal code + province. Everything before them on
         that line is treated as the city.
      4. Everything that precedes that line (joined with ", ") is
         ``address``.
      5. If no postal/province is detected we fall back to treating the
         whole string as ``address``.
    """
    s = _norm(raw)
    if not s:
        return {"address": None, "city": None,
                "province_territory": None, "postal_code": None}

    # Normalise: many Open North strings use "\n"; some also use ", ".
    # Prefer newline splits; fall back to comma if no newlines present.
    if "\n" in s:
        lines = [ln.strip().rstrip(",") for ln in s.split("\n")]
    else:
        lines = [ln.strip() for ln in s.split(",")]
    lines = [ln for ln in lines if ln]

    if not lines:
        return {"address": None, "city": None,
                "province_territory": None, "postal_code": None}

    last = lines[-1]
    postal_match = POSTAL_RE.search(last)
    postal_code: Optional[str] = None
    province: Optional[str] = None
    city: Optional[str] = None
    address_lines: list[str] = lines[:-1]

    if postal_match:
        postal_code = f"{postal_match.group(1).upper()} {postal_match.group(2).upper()}"
        # City+province is everything before the postal code on this line.
        prefix = last[: postal_match.start()].strip().rstrip(",")
        prov_match = PROVINCE_RE.search(prefix)
        if prov_match:
            province = prov_match.group(1).upper()
            city = prefix[: prov_match.start()].strip().rstrip(",")
            city = city or None
        else:
            # No 2-letter province code on this line -- the prefix might be
            # just the city, or empty (postal code on its own line). In the
            # latter case the previous line is usually the city.
            if prefix:
                city = prefix
            elif address_lines:
                # Promote the previous line to `city`, keep the rest as address.
                city = address_lines[-1]
                address_lines = address_lines[:-1]
    else:
        # No postal code anywhere on the last line. Check if the last line
        # looks like "..., Edmonton, AB" (common Alberta-legislature shape).
        prov_match = PROVINCE_RE.search(last)
        if prov_match:
            province = prov_match.group(1).upper()
            prefix = last[: prov_match.start()].strip().rstrip(",")
            # Prefix might be "..., Edmonton" -- city is the final comma-token.
            parts = [p.strip() for p in prefix.split(",") if p.strip()]
            if parts:
                city = parts[-1]
                # Earlier comma-parts are address continuation.
                if len(parts) > 1:
                    address_lines.append(", ".join(parts[:-1]))
        else:
            # Couldn't extract anything structured; whole string is address.
            address_lines = lines
            last = None

    address = ", ".join([ln for ln in address_lines if ln]) or None
    return {
        "address": address,
        "city": city,
        "province_territory": province,
        "postal_code": postal_code,
    }


def _iter_office_objects(offices: Any) -> Iterable[dict]:
    """Yield each valid office dict from an offices payload.

    Defensive against legacy rows where ``offices`` has been stored as a
    JSON string, a single dict, or missing entirely.
    """
    if not offices:
        return
    if isinstance(offices, list):
        iterable = offices
    elif isinstance(offices, dict):
        iterable = [offices]
    else:
        return
    for o in iterable:
        if isinstance(o, dict):
            yield o


def _extract_fields(office: dict) -> dict[str, Optional[str]]:
    """Flatten one Open North office dict into DB columns."""
    kind = _norm(office.get("type")) or "constituency"
    # Prefer 'tel', fall back to 'alt' (some entries only have alt).
    phone = _norm(office.get("tel")) or _norm(office.get("alt"))
    fax = _norm(office.get("fax"))
    email = _norm(office.get("email"))
    hours = _norm(office.get("hours"))
    parsed = parse_postal(office.get("postal"))
    return {
        "kind": kind,
        "address": parsed["address"],
        "city": parsed["city"],
        "province_territory": parsed["province_territory"],
        "postal_code": parsed["postal_code"],
        "phone": phone,
        "fax": fax,
        "email": email,
        "hours": hours,
    }


async def _office_already_exists(
    db: Database,
    politician_id: str,
    kind: str,
    phone: Optional[str],
    postal_code: Optional[str],
) -> bool:
    """Return True if an office row for this politician is already recorded.

    Dedupe key is ``(politician_id, kind, phone)`` -- the plan spec. When
    phone is ``NULL`` we fall back to matching on postal_code too so rows
    without a phone (e.g. ward-level councillor offices) aren't duplicated
    on re-run.
    """
    if phone is not None:
        row = await db.fetchrow(
            """
            SELECT 1 FROM politician_offices
             WHERE politician_id = $1
               AND kind IS NOT DISTINCT FROM $2
               AND phone IS NOT DISTINCT FROM $3
             LIMIT 1
            """,
            politician_id, kind, phone,
        )
    else:
        row = await db.fetchrow(
            """
            SELECT 1 FROM politician_offices
             WHERE politician_id = $1
               AND kind IS NOT DISTINCT FROM $2
               AND phone IS NULL
               AND postal_code IS NOT DISTINCT FROM $3
             LIMIT 1
            """,
            politician_id, kind, postal_code,
        )
    return row is not None


async def _upsert_offices(
    db: Database,
    politician_id: str,
    offices: Any,
    *,
    source: str = "opennorth",
) -> tuple[int, int]:
    """Insert each office in ``offices`` for the given politician.

    Returns ``(inserted, skipped)``. Safe to call repeatedly -- existing
    rows matching the dedupe key are skipped, not updated. Caller is
    responsible for swallowing exceptions if it needs the call to be
    non-fatal; this function itself raises on DB errors.
    """
    inserted = 0
    skipped = 0
    for office in _iter_office_objects(offices):
        fields = _extract_fields(office)
        if await _office_already_exists(
            db, politician_id,
            fields["kind"], fields["phone"], fields["postal_code"],
        ):
            skipped += 1
            continue
        await db.execute(
            """
            INSERT INTO politician_offices
              (politician_id, kind, address, city, province_territory,
               postal_code, phone, fax, email, hours, source)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
            """,
            politician_id,
            fields["kind"],
            fields["address"],
            fields["city"],
            fields["province_territory"],
            fields["postal_code"],
            fields["phone"],
            fields["fax"],
            fields["email"],
            fields["hours"],
            source,
        )
        inserted += 1
    return inserted, skipped


async def backfill_offices(db: Database) -> dict[str, int]:
    """Walk every politician whose ``extras`` has an ``offices`` array and
    materialise each entry into ``politician_offices``.

    Returns ``{inserted, skipped, politicians_touched, parse_failures}``.
    Idempotent. Safe to re-run.
    """
    rows = await db.fetch(
        """
        SELECT id, name, extras -> 'offices' AS offices
          FROM politicians
         WHERE extras ? 'offices'
           AND jsonb_typeof(extras -> 'offices') = 'array'
           AND jsonb_array_length(extras -> 'offices') > 0
        """
    )
    console.print(
        f"[cyan]backfill_offices: {len(rows)} politicians with offices JSON[/cyan]"
    )

    inserted_total = 0
    skipped_total = 0
    politicians_touched = 0
    parse_failures = 0

    for row in rows:
        pid = str(row["id"])
        offices = row["offices"]
        # asyncpg returns JSON columns as already-decoded Python values when
        # the column is JSONB, but can return strings for legacy data.
        if isinstance(offices, (bytes, str)):
            try:
                import orjson
                offices = orjson.loads(offices)
            except Exception as exc:
                parse_failures += 1
                log.warning(
                    "backfill_offices: could not decode offices for %s (%s): %s",
                    row.get("name"), pid, exc,
                )
                continue
        try:
            ins, skp = await _upsert_offices(db, pid, offices)
        except Exception as exc:
            parse_failures += 1
            log.exception(
                "backfill_offices: insert failed for %s (%s): %s",
                row.get("name"), pid, exc,
            )
            continue
        if ins:
            politicians_touched += 1
        inserted_total += ins
        skipped_total += skp

    stats = {
        "inserted": inserted_total,
        "skipped": skipped_total,
        "politicians_touched": politicians_touched,
        "parse_failures": parse_failures,
    }
    console.print(
        f"[green]backfill_offices complete[/green]: "
        f"inserted={stats['inserted']} skipped={stats['skipped']} "
        f"politicians_touched={stats['politicians_touched']} "
        f"parse_failures={stats['parse_failures']}"
    )
    log.info("backfill_offices: %s", stats)
    return stats
