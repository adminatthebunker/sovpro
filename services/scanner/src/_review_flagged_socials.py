"""One-shot review of flagged_low_confidence rows.

Approach:
  1. Read every flagged row + politician context
  2. Fetch the profile from public.api.bsky.app (platform-specific)
  3. Apply decision rules:

     APPROVE when:
       - Display name exactly equals politician name (accent-folded, case-insensitive), AND
         (postsCount >= 20 OR followersCount >= 200), AND the account is not brand-new
       - OR the bio contains a Canadian-political keyword AND the constituency
         name or party acronym appears in the bio
       - OR the bio mentions "MP for X" / "MLA for X" where X matches the
         politician's constituency or province name

     REJECT when:
       - API returned no profile at all (actor not found)
       - Account has zero posts AND < 5 followers (likely squatted / abandoned)
       - Bio mentions a clearly different profession AND no Canadian-political
         keyword (e.g. "teaching artist", "chef", "attorney in texas")
       - Display-name tokens don't overlap with politician-name tokens at all

     REVIEW when none of the above fire.

Writes decisions to stdout and (unless --dry-run) applies them:
  APPROVE → UPDATE politician_socials SET flagged_low_confidence=false,
                                          confidence = 1.0
  REJECT  → DELETE FROM politician_socials WHERE id = $1

Run:
  docker compose run --rm scanner python /app/src/_review_flagged_socials.py [--dry-run] [--platform bluesky]
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Optional

import httpx
import orjson

from .db import Database, get_dsn


log = logging.getLogger(__name__)


BSKY_API = "https://public.api.bsky.app/xrpc/app.bsky.actor.getProfile"


POLITICAL_KEYWORDS: tuple[str, ...] = (
    "mp ", " mp", "m.p.", "member of parliament",
    "mla", "m.l.a.", "mpp", "m.p.p.", "mna", "m.n.a.", "mha", "m.h.a.",
    "senator", "senate", "sénatrice", "sénateur",
    "mayor", "councillor", "councilor", "deputy mayor",
    "liberal", "conservative", "ndp", "bloc", "green party",
    "parti liberal", "parti conservateur", "parti quebecois",
    "progressive conservative", "independent senators",
    "house of commons", "parliament", "parlement",
    "legislative assembly", "assemblée nationale", "assemblee nationale",
    "constituency", "riding", "caucus", "minister",
    "parl.gc.ca", "ourcommons.ca", "sencanada.ca",
    "leg.bc.ca", "ola.org", "députée", "députe", "depute", "deputee",
    "proudly representing", "elected", "re-elected", "reelected",
    "ministre", "premier of ", "mayor of ",
    # stronger canadian-context fallbacks
    "canada", "canadian", "canadien", "canadienne",
    "ottawa", "quebec city", "edmonton", "toronto", "winnipeg", "regina",
    "halifax", "fredericton", "charlottetown", "st. john's", "yellowknife",
    "iqaluit", "victoria", "whitehorse",
)


BENIGN_NON_POLITICAL_MARKERS: tuple[str, ...] = (
    # strongly non-political professions / locations that disqualify when
    # combined with ZERO political keywords
    "teaching artist", "theatre director", "theater director",
    "attorney at law", "software engineer", "data scientist",
    "barista", "student at", "phd candidate at",
    "chicago", "new york", "los angeles", "san francisco", "london, uk",
    "based in texas", "based in nyc", "based in la", "based in london",
    # random-person vibes
    "pizza is life", "let's gooo", "memes", "she/they",
)


def _strip_accents(s: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFKD", s or "")
        if not unicodedata.combining(c)
    )


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", _strip_accents(s or "").lower()).strip()


def _tokens(s: str) -> set[str]:
    return {t for t in _norm(s).split() if len(t) >= 2}


def _has_any(text: str, needles: tuple[str, ...]) -> bool:
    for n in needles:
        if n in text:
            return True
    return False


@dataclass
class FlaggedRow:
    id: str
    politician_id: str
    politician_name: str
    party: Optional[str]
    level: str
    province_territory: Optional[str]
    constituency_name: Optional[str]
    platform: str
    handle: Optional[str]
    url: str
    confidence: float


@dataclass
class Verdict:
    decision: str   # APPROVE | REJECT | REVIEW
    reason: str


async def _fetch_profile(client: httpx.AsyncClient, handle: str) -> Optional[dict]:
    if not handle:
        return None
    try:
        r = await client.get(BSKY_API, params={"actor": handle}, timeout=10.0)
    except httpx.HTTPError:
        return None
    if r.status_code != 200:
        return None
    try:
        return r.json()
    except Exception:
        return None


def _judge(row: FlaggedRow, profile: Optional[dict]) -> Verdict:
    if profile is None:
        return Verdict("REJECT", "bsky API returned no profile (actor not found)")

    display = profile.get("displayName") or ""
    bio = profile.get("description") or ""
    posts = int(profile.get("postsCount") or 0)
    followers = int(profile.get("followersCount") or 0)

    politician_tokens = _tokens(row.politician_name)
    display_tokens = _tokens(display)
    overlap = politician_tokens & display_tokens
    overlap_ratio = (len(overlap) / len(politician_tokens)) if politician_tokens else 0.0

    text = f"{_norm(display)} {_norm(bio)}"

    has_political = _has_any(text, POLITICAL_KEYWORDS)
    has_benign_nonpol = _has_any(text, BENIGN_NON_POLITICAL_MARKERS)

    constituency_tokens = _tokens(row.constituency_name or "")
    constituency_hit = any(t in text for t in constituency_tokens if len(t) >= 4)

    party_hit = False
    if row.party:
        for tok in _norm(row.party).split():
            if len(tok) <= 4 and tok in text:
                party_hit = True
                break

    pt = (row.province_territory or "").lower()
    province_words = {
        "ab": ("alberta",), "bc": ("british columbia",), "mb": ("manitoba",),
        "nb": ("new brunswick",), "nl": ("newfoundland", "labrador"),
        "ns": ("nova scotia",), "nt": ("northwest territories",),
        "nu": ("nunavut",), "on": ("ontario",),
        "pe": ("prince edward island", "pei"),
        "qc": ("quebec", "québec"), "sk": ("saskatchewan",), "yt": ("yukon",),
    }
    own_province = province_words.get(pt, ())
    province_hit = any(w in text for w in own_province)

    # ── REJECT rules ────────────────────────────────────────────────

    if overlap_ratio < 0.5:
        return Verdict("REJECT",
            f"display '{display}' doesn't overlap politician tokens")

    if posts == 0 and followers < 5:
        return Verdict("REJECT",
            f"squatted-looking profile (posts=0, followers={followers}, display='{display}')")

    if has_benign_nonpol and not has_political and not constituency_hit and not party_hit:
        return Verdict("REJECT",
            f"benign non-political bio + no political signal; bio='{bio[:100]}'")

    # ── APPROVE rules ───────────────────────────────────────────────

    exact_match = overlap_ratio >= 1.0 and (
        _norm(row.politician_name) == _norm(display)
    )

    if has_political and (constituency_hit or party_hit or province_hit):
        return Verdict("APPROVE",
            f"political + (const/party/province) match; bio='{bio[:100]}'")

    if exact_match and (posts >= 20 or followers >= 200) and (has_political or province_hit):
        return Verdict("APPROVE",
            f"exact name match + active account + canadian-context; posts={posts} followers={followers}")

    if exact_match and posts >= 50 and followers >= 100:
        return Verdict("APPROVE",
            f"exact name + heavy engagement; posts={posts} followers={followers}")

    # ── Otherwise, human review ─────────────────────────────────────

    return Verdict("REVIEW",
        f"overlap={overlap_ratio:.2f} posts={posts} followers={followers} "
        f"political={has_political} const={constituency_hit} party={party_hit} "
        f"bio='{bio[:120]}'")


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--platform", default="bluesky")
    ap.add_argument("--concurrency", type=int, default=10)
    args = ap.parse_args()

    dsn = get_dsn()
    db = Database(dsn)
    await db.connect()
    try:
        rows = await db.fetch(
            """
            SELECT s.id, s.politician_id, s.platform, s.handle, s.url,
                   s.confidence::float AS confidence,
                   p.name AS politician_name, p.party, p.level,
                   p.province_territory, p.constituency_name
              FROM politician_socials s
              JOIN politicians p ON p.id = s.politician_id
             WHERE s.flagged_low_confidence = true
               AND s.platform = $1
             ORDER BY s.confidence DESC
            """,
            args.platform,
        )
        flagged = [
            FlaggedRow(
                id=str(r["id"]),
                politician_id=str(r["politician_id"]),
                politician_name=r["politician_name"] or "",
                party=r["party"],
                level=r["level"],
                province_territory=r["province_territory"],
                constituency_name=r["constituency_name"],
                platform=r["platform"],
                handle=r["handle"],
                url=r["url"],
                confidence=float(r["confidence"] or 0.0),
            )
            for r in rows
        ]
        print(f"Reviewing {len(flagged)} flagged {args.platform} rows (dry_run={args.dry_run})")

        sem = asyncio.Semaphore(args.concurrency)
        verdicts: list[tuple[FlaggedRow, Verdict, Optional[dict]]] = []

        async with httpx.AsyncClient(
            headers={"User-Agent": "SovereignWatch-review/1.0 (+canadianpoliticaldata.ca)"},
        ) as client:

            async def handle_one(row: FlaggedRow) -> None:
                async with sem:
                    profile = await _fetch_profile(client, row.handle or "")
                v = _judge(row, profile)
                verdicts.append((row, v, profile))

            await asyncio.gather(*(handle_one(r) for r in flagged))

        # Summary
        bucket: dict[str, list[tuple[FlaggedRow, Verdict, Optional[dict]]]] = {
            "APPROVE": [], "REJECT": [], "REVIEW": [],
        }
        for row, v, prof in verdicts:
            bucket[v.decision].append((row, v, prof))

        print()
        print(f"APPROVE: {len(bucket['APPROVE'])}")
        print(f"REJECT : {len(bucket['REJECT'])}")
        print(f"REVIEW : {len(bucket['REVIEW'])}")

        # Write the full verdict table to a JSON file for inspection.
        out = {
            "platform": args.platform,
            "counts": {k: len(v) for k, v in bucket.items()},
            "verdicts": [
                {
                    "id": row.id,
                    "politician_name": row.politician_name,
                    "level": row.level,
                    "province_territory": row.province_territory,
                    "constituency_name": row.constituency_name,
                    "party": row.party,
                    "handle": row.handle,
                    "url": row.url,
                    "confidence": row.confidence,
                    "decision": v.decision,
                    "reason": v.reason,
                    "display_name": (prof or {}).get("displayName") if prof else None,
                    "description": (prof or {}).get("description") if prof else None,
                    "posts_count": (prof or {}).get("postsCount") if prof else None,
                    "followers_count": (prof or {}).get("followersCount") if prof else None,
                }
                for row, v, prof in verdicts
            ],
        }
        # Write to /tmp in-container (will not persist across runs) — and
        # also stream to stdout with a sentinel so the host can capture
        # even for ephemeral `docker compose run` containers.
        out_path = "/tmp/flagged_socials_review.json"
        with open(out_path, "wb") as fh:
            fh.write(orjson.dumps(out, option=orjson.OPT_INDENT_2))
        print(f"Full verdict table written to {out_path}")
        print("───BEGIN REVIEW VERDICTS JSON───")
        print(orjson.dumps(out, option=orjson.OPT_INDENT_2).decode())
        print("───END REVIEW VERDICTS JSON───")

        if args.dry_run:
            # Show a sample of each bucket.
            for k in ("APPROVE", "REJECT", "REVIEW"):
                if not bucket[k]:
                    continue
                print(f"\n── {k} samples ({len(bucket[k])}) ──")
                for row, v, prof in bucket[k][:15]:
                    print(f"  [{row.politician_name}] {row.url}  → {v.reason}")
                if len(bucket[k]) > 15:
                    print(f"  … and {len(bucket[k]) - 15} more (see JSON)")
            return

        # Apply decisions.
        approved = 0
        rejected = 0
        for row, v, _ in verdicts:
            if v.decision == "APPROVE":
                await db.execute(
                    """
                    UPDATE politician_socials
                       SET flagged_low_confidence = false,
                           confidence = GREATEST(confidence, 0.90),
                           updated_at = now()
                     WHERE id = $1
                    """,
                    row.id,
                )
                approved += 1
            elif v.decision == "REJECT":
                await db.execute(
                    "DELETE FROM politician_socials WHERE id = $1",
                    row.id,
                )
                rejected += 1

        print(f"\nApplied: APPROVE={approved} REJECT={rejected} (REVIEW left untouched)")
    finally:
        await db.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    asyncio.run(main())
