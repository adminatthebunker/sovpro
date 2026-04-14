"""Committee ingestion for politician_committees.

Addresses the gap documented in the dapper-brewing-petal plan: the
politician_committees table was created in Phase 1 but left empty. This
module scrapes committee membership from legislature websites and
populates the table, with soft-end semantics so churn is trackable.

Source survey (2026-04-13):

- **Federal** — parl.ca / ourcommons.ca exposes each committee's members as
  clean, server-rendered HTML at
    https://www.ourcommons.ca/Committees/en/{ACRONYM}/Members
  (Joint committees live at https://www.parl.ca/Committees/en/{ACRONYM}/Members
  with a slightly different structure — handled separately.) The canonical
  committee directory (with acronym + full name + whether it's joint /
  standing / other) is at
    https://www.ourcommons.ca/Committees/en/List
  No documented OData or XML feed for committee membership exists — the open
  data portal (/en/open-data) only exposes per-MP role XML, not per-committee
  rosters. Scraping the Members HTML is the cleanest available path.

- **Alberta** — assembly.ab.ca publishes one consolidated page listing all
  11 committees and their members at
    https://www.assembly.ab.ca/assembly-business/committees/committee-membership
  Also pure server-rendered HTML. Implemented here as `ingest_ab_committees`.

- **Provinces skipped** (this pass):
    * BC, ON, QC: committee rosters are rendered client-side or behind
      JavaScript widgets; scraping would need a headless browser, which we
      explicitly avoid in this scanner (see plan, "no paid APIs / heavy
      runtime deps").
    * MB, SK, NS, NB, PE, NL, YT, NT, NU: per-legislature committee pages
      exist but either (a) lack politicianidentifier links we could match
      against our politicians table, or (b) don't enumerate members at
      all on the public site. Adding these is a per-province effort better
      done once a single province's dataset is proven in production.

The module is source-agnostic: `upsert_committee()` takes a `source` string so
future province implementations can plug in without schema changes.
"""
from __future__ import annotations

import asyncio
import html as _html
import logging
import re
import unicodedata
from typing import Optional

import httpx
from rich.console import Console
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn

from .db import Database
from .enrich import _norm

log = logging.getLogger(__name__)
console = Console()


USER_AGENT = (
    "CanadianPoliticalDataBot/1.0 (+https://canadianpoliticaldata.ca) "
    "committees-ingest"
)


# ── Federal parl.ca sources ───────────────────────────────────────────
#
# Most standing committees live under ourcommons.ca; joint committees are
# served from parl.ca with a slightly different HTML shape. The list page
# itself exposes both.
FEDERAL_LIST_URL = "https://www.ourcommons.ca/Committees/en/List"
FEDERAL_MEMBERS_URL_HOC = "https://www.ourcommons.ca/Committees/en/{acronym}/Members"
FEDERAL_MEMBERS_URL_PARL = "https://www.parl.ca/Committees/en/{acronym}/Members"

# committee-acronym-cell + committee-name inside the List page.
_FED_ACRONYM_NAME_RE = re.compile(
    r'committee-acronym-cell">([A-Z]+)</span>\s*'
    r'<span class="committee-name">([^<]+)</span>',
    re.DOTALL,
)

# On the per-committee HoC Members page, each section has:
#   <div id="committee-chair" class="member-section">
#   <div id="committee-vice-chairs" class="member-section">
#   <div id="committee-members-only">...<div class="member-section">...
# and each desktop member card has class="committee-member-card hidden-xs" so
# we can skip mobile duplicates.
_FED_SECTION_RE = re.compile(
    r'<div[^>]*id="(committee-chair|committee-vice-chairs|committee-members-only)"[^>]*>'
    r'(.*?)(?=<div[^>]*id="committee-(?:chair|vice-chairs|members-only)"'
    r'|<div[^>]*id="membership-changes"|</div>\s*</div>\s*</section>|\Z)',
    re.DOTALL | re.IGNORECASE,
)

# Desktop card only (mobile duplicates the name and would double-count).
_FED_CARD_RE = re.compile(
    r'<span class="committee-member-card hidden-xs">\s*'
    r'<a[^>]*href="(//www\.ourcommons\.ca/members/en/[^"]+)"[^>]*>'
    r'(.*?)</a>\s*</span>',
    re.DOTALL | re.IGNORECASE,
)

# Joint committee variant (parl.ca) — distinctly different markup. The page
# is a desktop two-column table (Senate | HoC) where each role section is
# introduced by a <div class="joint-member-title"><span class="title">X</span></div>
# immediately followed by a <div class="row joint-member-section"> block. The
# *title text* is authoritative for role (the element IDs in the source HTML
# are reused incorrectly between sections — "committee-vice-chairs" appears
# on the Members section too, so we can't key off IDs).
_FED_JOINT_TITLE_RE = re.compile(
    r'<div[^>]*class="joint-member-title"[^>]*>\s*'
    r'<span class="title">([^<]+)</span>\s*</div>',
    re.DOTALL | re.IGNORECASE,
)
_FED_JOINT_CARD_RE = re.compile(
    r'<div class="committee-joint-member-card hidden-xs">\s*'
    r'<a[^>]*href="(//www\.ourcommons\.ca/members/en/[^"]+)"[^>]*>'
    r'(.*?)</a>\s*</div>',
    re.DOTALL | re.IGNORECASE,
)


def _joint_title_to_role(title: str) -> str:
    t = title.strip().lower()
    if "joint chair" in t or t == "chairs" or t == "chair":
        return "Joint Chair"
    if "vice" in t:
        return "Vice-Chair"
    return "Member"

_FULL_NAME_RE = re.compile(
    r'<span class="first-name">([^<]+)</span>\s*'
    r'<span class="last-name">\s*([^<]+?)\s*(?:<|$)',
    re.DOTALL,
)


def _section_to_role(section_id: str) -> str:
    return {
        "committee-chair": "Chair",
        "committee-cochairs": "Joint Chair",
        "committee-vice-chairs": "Vice-Chair",
        "committee-members-only": "Member",
    }.get(section_id, "Member")


# ── Alberta assembly.ab.ca ──────────────────────────────────────────
AB_MEMBERSHIP_URL = (
    "https://www.assembly.ab.ca/assembly-business/committees/committee-membership"
)

# The AB consolidated page renders each committee inside a <div class="card ...">
# with a header that looks like
#   Standing Committee on X (N Members)
# followed by <h4><a>Mr. Firstname Lastname (Role)</a></h4> entries for each
# member. We parse committees by splitting on those headers.
_AB_COMMITTEE_HEADER_RE = re.compile(
    r'(Standing Committee on[^<(]+|Special Standing Committee on[^<(]+|'
    r'Select Special[^<(]+Committee|Special Committee on[^<(]+)'
    r'\s*\(\d+ Members\)',
    re.IGNORECASE,
)
# Each entry:  <h4><a href="...mid=NNNN...">Mr. Tany Yao (Chair)</a></h4>
_AB_MEMBER_RE = re.compile(
    r'<h4>\s*<a[^>]*mid=\d+[^>]*>\s*([^<]+?)\s*</a>\s*</h4>',
    re.IGNORECASE,
)
# Extract trailing role parenthetical if present.
_AB_ROLE_RE = re.compile(r"\(([^)]+)\)\s*$")
# Leading honorific we drop before name matching.
_AB_HONORIFIC_RE = re.compile(
    r'^(?:Mr\.?|Mrs\.?|Ms\.?|Ms|Dr\.?|Hon\.?|Honourable|Member)\s+',
    re.IGNORECASE,
)


# ── DB helpers ────────────────────────────────────────────────────────

async def upsert_committee(
    db: Database,
    politician_id: str,
    committee_name: str,
    role: str,
    level: str,
    source: str,
) -> bool:
    """Idempotent upsert for politician_committees.

    The table has no unique constraint (by design — membership can repeat
    across terms with different started_at/ended_at). We emulate it by
    looking for an existing *open* row (ended_at IS NULL) with the same
    (politician_id, committee_name). If found, update role/source. If not,
    insert with started_at=now().

    Returns True on insert, False on update.
    """
    existing = await db.fetchrow(
        """
        SELECT id, role, source FROM politician_committees
        WHERE politician_id = $1
          AND committee_name = $2
          AND ended_at IS NULL
        LIMIT 1
        """,
        politician_id, committee_name,
    )
    if existing is not None:
        if existing["role"] != role or existing["source"] != source:
            await db.execute(
                """
                UPDATE politician_committees
                   SET role = $2, source = $3
                 WHERE id = $1
                """,
                existing["id"], role, source,
            )
        return False

    await db.execute(
        """
        INSERT INTO politician_committees
            (politician_id, committee_name, role, level, started_at, source)
        VALUES ($1, $2, $3, $4, now(), $5)
        """,
        politician_id, committee_name, role, level, source,
    )
    return True


async def soft_end_missing(
    db: Database,
    *,
    level: str,
    source: str,
    seen: set[tuple[str, str]],
) -> int:
    """Close open memberships no longer present in the latest scrape.

    `seen` is a set of (politician_id, committee_name) tuples observed in
    this ingestion pass. Any *open* row (ended_at IS NULL) from the same
    source+level that is NOT in that set is stamped with ended_at=now().
    """
    rows = await db.fetch(
        """
        SELECT id, politician_id, committee_name
        FROM politician_committees
        WHERE level = $1 AND source = $2 AND ended_at IS NULL
        """,
        level, source,
    )
    closed = 0
    for row in rows:
        key = (str(row["politician_id"]), row["committee_name"])
        if key not in seen:
            await db.execute(
                "UPDATE politician_committees SET ended_at = now() WHERE id = $1",
                row["id"],
            )
            closed += 1
    return closed


# ── Politician matching ───────────────────────────────────────────────

async def _build_name_index(
    db: Database, *, level: str, province: Optional[str] = None,
) -> tuple[dict[str, str], set[str]]:
    """Return ({normalized_name: politician_id}, {normalized_names_with_collisions}).

    Names that appear on more than one active politician in the scope are
    stashed in the collision set so callers know to skip them rather than
    guess. Matching is intentionally strict (normalized full name only) —
    we prefer false-negatives to false-positives in a public dataset.
    """
    sql = """
        SELECT id, name, first_name, last_name
        FROM politicians
        WHERE is_active = true AND level = $1
    """
    params: list = [level]
    if province is not None:
        sql += " AND province_territory = $2"
        params.append(province)

    rows = await db.fetch(sql, *params)

    index: dict[str, list[str]] = {}
    for row in rows:
        pid = str(row["id"])
        # Primary key: full `name` column
        keys = {_norm(row["name"])}
        fn, ln = row["first_name"], row["last_name"]
        if fn and ln:
            keys.add(_norm(f"{fn} {ln}"))
            # Also index last-name-comma-first form used on parl.ca joint
            # committee cards.
            keys.add(_norm(f"{ln} {fn}"))
            # Strip middle initials / middle names from first_name to widen
            # matching: "Robert J." on ourcommons registers, "Robert" on
            # committee pages also registers.
            fn_first = fn.split()[0] if fn.split() else fn
            if fn_first and fn_first != fn:
                keys.add(_norm(f"{fn_first} {ln}"))
                keys.add(_norm(f"{ln} {fn_first}"))
        for k in keys:
            if not k:
                continue
            index.setdefault(k, []).append(pid)

    # Flatten: unique hits -> ok, duplicate hits -> collision.
    resolved: dict[str, str] = {}
    collisions: set[str] = set()
    for k, ids in index.items():
        unique = set(ids)
        if len(unique) == 1:
            resolved[k] = next(iter(unique))
        else:
            collisions.add(k)
    return resolved, collisions


def _extract_full_name(card_html: str) -> Optional[str]:
    """Pull first+last name text out of a committee-member-card inner HTML."""
    m = _FULL_NAME_RE.search(card_html)
    if not m:
        return None
    first = _html.unescape(m.group(1)).strip()
    last = _html.unescape(m.group(2)).strip()
    if not first or not last:
        return None
    return f"{first} {last}"


# ── Federal ingestion ─────────────────────────────────────────────────

async def _fetch(client: httpx.AsyncClient, url: str) -> Optional[str]:
    try:
        r = await client.get(url)
        if r.status_code != 200:
            return None
        return r.text
    except Exception as exc:
        log.debug("committees fetch failed for %s: %s", url, exc)
        return None


async def _list_federal_committees(client: httpx.AsyncClient) -> list[tuple[str, str]]:
    """Return [(acronym, full_name), ...] for every federal committee + joint."""
    html = await _fetch(client, FEDERAL_LIST_URL)
    if not html:
        return []
    out: list[tuple[str, str]] = []
    for m in _FED_ACRONYM_NAME_RE.finditer(html):
        acronym = m.group(1)
        name = m.group(2).strip()
        out.append((acronym, name))
    # De-duplicate while preserving first-seen order.
    seen: set[str] = set()
    uniq: list[tuple[str, str]] = []
    for ac, nm in out:
        if ac in seen:
            continue
        seen.add(ac)
        uniq.append((ac, nm))
    return uniq


def _parse_federal_members(html: str) -> list[tuple[str, str]]:
    """Parse a HoC committee Members page. Returns [(full_name, role), ...]."""
    out: list[tuple[str, str]] = []
    for sec in _FED_SECTION_RE.finditer(html):
        section_id = sec.group(1)
        role = _section_to_role(section_id)
        block = sec.group(2)
        for card in _FED_CARD_RE.finditer(block):
            name = _extract_full_name(card.group(2))
            if name:
                out.append((name, role))
    return out


def _parse_joint_members(html: str) -> list[tuple[str, str]]:
    """Parse a parl.ca joint committee Members page.

    Skips the mobile layout (it duplicates the desktop cards) by only looking
    inside the `joint-committee-members-desktop` wrapper. Role is determined
    from the `<span class="title">` text rather than the broken element IDs.

    Implementation: iterate the title matches, then pull every member card
    whose start position lies between this title and the next (or end of
    desktop scope).
    """
    desktop_marker = 'id="joint-committee-members-desktop"'
    mobile_marker = 'id="joint-committee-members-mobile"'
    d = html.find(desktop_marker)
    if d == -1:
        return []
    m = html.find(mobile_marker, d)
    scope = html[d:m] if m != -1 else html[d:]

    titles = list(_FED_JOINT_TITLE_RE.finditer(scope))
    if not titles:
        return []
    # Pre-scan all cards; each will be assigned to the nearest preceding title.
    cards = list(_FED_JOINT_CARD_RE.finditer(scope))

    out: list[tuple[str, str]] = []
    for i, t in enumerate(titles):
        title_end = t.end()
        next_start = titles[i + 1].start() if i + 1 < len(titles) else len(scope)
        role = _joint_title_to_role(t.group(1))
        for card in cards:
            if card.start() < title_end or card.start() >= next_start:
                continue
            href = card.group(1)
            if "ourcommons.ca/members" not in href:
                continue
            name = _extract_full_name(card.group(2))
            if name:
                out.append((name, role))
    return out


async def _ingest_one_federal_committee(
    db: Database,
    client: httpx.AsyncClient,
    acronym: str,
    committee_name: str,
    *,
    name_index: dict[str, str],
    collisions: set[str],
    seen: set[tuple[str, str]],
) -> tuple[int, int, int]:
    """Ingest a single committee. Returns (inserted, skipped_collision, unmatched)."""
    # Try HoC members URL first; fall back to parl.ca for joint committees.
    url = FEDERAL_MEMBERS_URL_HOC.format(acronym=acronym)
    html = await _fetch(client, url)
    parsed: list[tuple[str, str]] = []
    if html:
        parsed = _parse_federal_members(html)
        if not parsed:
            # Might be a joint committee whose HoC URL still renders but
            # uses the joint-card markup.
            parsed = _parse_joint_members(html)
    if not parsed:
        url = FEDERAL_MEMBERS_URL_PARL.format(acronym=acronym)
        html = await _fetch(client, url)
        if html:
            parsed = _parse_joint_members(html)

    inserted = 0
    skipped_collision = 0
    unmatched = 0

    for full_name, role in parsed:
        key = _norm(full_name)
        if key in collisions:
            skipped_collision += 1
            log.info("federal: ambiguous name %r for committee %s — skipping",
                     full_name, acronym)
            continue
        pid = name_index.get(key)
        if pid is None:
            unmatched += 1
            continue
        try:
            is_new = await upsert_committee(
                db, pid, committee_name, role,
                level="federal", source="parl.ca",
            )
            if is_new:
                inserted += 1
            seen.add((pid, committee_name))
        except Exception as exc:
            log.warning("upsert_committee failed for %s/%s: %s",
                        full_name, committee_name, exc)
    return inserted, skipped_collision, unmatched


async def ingest_federal_committees(db: Database) -> int:
    """Scrape parl.ca + ourcommons.ca committee members → politician_committees.

    Returns the count of newly-opened (inserted) memberships.
    """
    name_index, collisions = await _build_name_index(db, level="federal")
    console.print(
        f"[cyan]federal committees: indexed {len(name_index)} names "
        f"({len(collisions)} ambiguous) for matching[/cyan]"
    )

    async with httpx.AsyncClient(
        timeout=30,
        headers={"User-Agent": USER_AGENT},
        follow_redirects=True,
        limits=httpx.Limits(max_connections=4, max_keepalive_connections=4),
    ) as client:
        committees = await _list_federal_committees(client)
        if not committees:
            console.print("[red]Failed to retrieve federal committee list[/red]")
            return 0
        console.print(
            f"[cyan]federal committees: discovered "
            f"{len(committees)} committees on ourcommons.ca/List[/cyan]"
        )

        sem = asyncio.Semaphore(3)
        seen: set[tuple[str, str]] = set()
        total_inserted = 0
        total_skipped = 0
        total_unmatched = 0

        progress = Progress(
            SpinnerColumn(),
            TextColumn("{task.description}"),
            BarColumn(),
            TextColumn("{task.completed}/{task.total}"),
            TimeElapsedColumn(),
        )

        async def handle(acronym: str, committee_name: str) -> None:
            nonlocal total_inserted, total_skipped, total_unmatched
            async with sem:
                ins, skip, un = await _ingest_one_federal_committee(
                    db, client, acronym, committee_name,
                    name_index=name_index,
                    collisions=collisions,
                    seen=seen,
                )
                total_inserted += ins
                total_skipped += skip
                total_unmatched += un

        with progress:
            task = progress.add_task("Ingesting", total=len(committees))

            async def wrapped(ac: str, nm: str) -> None:
                try:
                    await handle(ac, nm)
                finally:
                    progress.update(task, advance=1)

            await asyncio.gather(*(wrapped(ac, nm) for ac, nm in committees))

    closed = await soft_end_missing(
        db, level="federal", source="parl.ca", seen=seen,
    )
    console.print(
        f"[green]✓ federal committees: inserted {total_inserted} · "
        f"skipped (ambiguous) {total_skipped} · "
        f"unmatched {total_unmatched} · "
        f"soft-ended {closed}[/green]"
    )
    return total_inserted


# ── Alberta ingestion ─────────────────────────────────────────────────

def _parse_alberta_page(html: str) -> list[tuple[str, str, str]]:
    """Return [(committee_name, full_name, role), ...] for Alberta.

    The AB page renders every committee on one document. We walk the HTML
    by splitting on committee headers, then within each slice pull member
    anchors in order.
    """
    # Find all header positions so we can slice between them.
    headers = list(_AB_COMMITTEE_HEADER_RE.finditer(html))
    if not headers:
        return []

    out: list[tuple[str, str, str]] = []
    for i, hdr in enumerate(headers):
        name_raw = hdr.group(1).strip()
        # Normalize whitespace / trailing dashes etc.
        committee_name = re.sub(r"\s+", " ", name_raw).strip()
        start = hdr.end()
        end = headers[i + 1].start() if i + 1 < len(headers) else len(html)
        chunk = html[start:end]
        for mem in _AB_MEMBER_RE.finditer(chunk):
            raw = _html.unescape(mem.group(1)).strip()
            # Split off trailing role parenthetical, e.g. "Mr. Tany Yao (Chair)".
            role = "Member"
            m = _AB_ROLE_RE.search(raw)
            if m:
                role = m.group(1).strip().title()
                raw = raw[: m.start()].strip()
            # Drop honorific prefix.
            name_part = _AB_HONORIFIC_RE.sub("", raw).strip()
            if not name_part:
                continue
            out.append((committee_name, name_part, role))
    return out


async def ingest_ab_committees(db: Database) -> int:
    """Scrape assembly.ab.ca → politician_committees for provincial Alberta."""
    name_index, collisions = await _build_name_index(
        db, level="provincial", province="AB",
    )
    console.print(
        f"[cyan]Alberta committees: indexed {len(name_index)} names "
        f"({len(collisions)} ambiguous) for matching[/cyan]"
    )

    async with httpx.AsyncClient(
        timeout=30,
        headers={"User-Agent": USER_AGENT},
        follow_redirects=True,
    ) as client:
        html = await _fetch(client, AB_MEMBERSHIP_URL)
        if not html:
            console.print("[red]Failed to fetch Alberta committee membership page[/red]")
            return 0

        rows = _parse_alberta_page(html)
        if not rows:
            console.print(
                "[yellow]Alberta committees page parsed to 0 rows — "
                "header regex may need updating[/yellow]"
            )
            return 0

        console.print(
            f"[cyan]Alberta committees: parsed {len(rows)} "
            f"committee-member rows[/cyan]"
        )

        seen: set[tuple[str, str]] = set()
        inserted = 0
        skipped = 0
        unmatched = 0

        for committee_name, full_name, role in rows:
            key = _norm(full_name)
            if key in collisions:
                skipped += 1
                log.info("ab: ambiguous name %r for committee %s — skipping",
                         full_name, committee_name)
                continue
            pid = name_index.get(key)
            if pid is None:
                unmatched += 1
                continue
            try:
                is_new = await upsert_committee(
                    db, pid, committee_name, role,
                    level="provincial", source="assembly.ab.ca",
                )
                if is_new:
                    inserted += 1
                seen.add((pid, committee_name))
            except Exception as exc:
                log.warning("upsert_committee failed for %s/%s: %s",
                            full_name, committee_name, exc)

        closed = await soft_end_missing(
            db, level="provincial", source="assembly.ab.ca", seen=seen,
        )

    console.print(
        f"[green]✓ Alberta committees: inserted {inserted} · "
        f"skipped (ambiguous) {skipped} · "
        f"unmatched {unmatched} · "
        f"soft-ended {closed}[/green]"
    )
    return inserted


# ── Coordinator ──────────────────────────────────────────────────────

async def ingest_all_committees(db: Database) -> None:
    """Run every committee ingester sequentially (best-effort).

    Failures in one source don't abort the others. Unimplemented provinces
    are documented in the module docstring above.
    """
    for label, fn in (
        ("federal", ingest_federal_committees),
        ("alberta", ingest_ab_committees),
    ):
        console.print(f"[cyan bold]━━ committees: {label} ━━[/cyan bold]")
        try:
            await fn(db)
        except Exception as exc:
            log.exception("committee ingest %s failed: %s", label, exc)
            console.print(f"[red]  {label}: {exc}[/red]")
