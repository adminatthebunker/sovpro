"""Tier-3 Sonnet agent for discovering politician social handles.

Invokes `claude-sonnet-4-6` via the Anthropic API with the built-in
`web_search_20250305` tool. One agent call handles a *batch* of
politicians (default 10) to amortise the system prompt. The agent
returns structured JSON; we parse it and route through `upsert_social`
with `source='agent_sonnet'`.

Cost guardrails:

  * --batch-size  — politicians per call (default 10, max 25)
  * --max-batches — hard cap on calls per invocation (default 20)
  * --dry-run     — write candidates to stdout; do not insert

Insertion thresholds (see `socials.py::_should_flag`):

  agent_sonnet rows are flagged_low_confidence=true below 0.85.

Provenance: every row written by this module gets `source='agent_sonnet'`,
the agent's reported `confidence`, and the agent-supplied `evidence_url`.

Cache: this module does NOT persist a response cache in v1. Re-running
is idempotent because we only query politicians still missing the given
platform; `upsert_social` is a no-op for already-known handles.

Environment:

  ANTHROPIC_API_KEY must be set (see .env / .env.example).
  ANTHROPIC_MODEL defaults to 'claude-sonnet-4-6' and can be overridden
  for evaluation runs.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
from collections import Counter
from dataclasses import dataclass
from typing import Any, Optional

import orjson
from rich.console import Console
from rich.table import Table

from .db import Database
from .socials import ALLOWED_PLATFORMS, upsert_social

log = logging.getLogger(__name__)
console = Console()


DEFAULT_MODEL = "claude-sonnet-4-6"
DEFAULT_MAX_TOKENS = 4096
DEFAULT_BATCH_SIZE = 10
MAX_BATCH_SIZE = 25
# Agent-source rows below this land in the review queue. Must match the
# value in socials._AGENT_FLAG_THRESHOLD or routing will be inconsistent.
AGENT_PROMOTE_THRESHOLD = 0.85
AGENT_MIN_WRITE = 0.60


SYSTEM_PROMPT = """You are auditing social-media handles for Canadian politicians.

For each politician in the batch and each platform in their `missing_platforms`, use the web_search tool to find the politician's **official personal** handle on that platform. Return one JSON object for the whole batch with this shape:

{
  "results": [
    {
      "politician_id": "<uuid from the batch>",
      "platform": "twitter" | "facebook" | "instagram" | "youtube" | "tiktok" | "linkedin" | "mastodon" | "bluesky" | "threads",
      "url": "https://...",
      "handle": "<bare handle or null>",
      "confidence": 0.0,
      "evidence_url": "https://...",
      "reasoning": "<one short line>"
    },
    ...
  ]
}

Hard rules:

- Return **only** the JSON object — no prose before or after.
- Return at most ONE hit per (politician_id, platform). If you can't find a
  clear hit, simply omit that platform for that politician.
- Use web_search evidence. Set `evidence_url` to the page that proves the
  handle belongs to this specific person (their parliamentary bio, their
  party profile, their Wikipedia article, or the social profile itself
  only if it clearly names them + their role).
- **Do NOT invent handles.** If no evidence, omit.
- **Official personal handle only** — not a party caucus, constituency
  office, riding association, or parody account. "Team SomeName" campaign
  accounts do not count.
- Prefer .ca / parl.gc.ca / ourcommons.ca / sencanada.ca / provincial
  legislature domains as evidence when available.
- Confidence scale:
    0.95-1.00  evidence page names the person + links the handle directly
    0.85-0.94  strong circumstantial (bio explicitly describes this role
               and jurisdiction; name matches; no ambiguity)
    0.60-0.84  likely but some ambiguity (common name, partial evidence)
    <0.60      do not return (just omit)
- If the politician has already-known handles in `known_socials`, respect
  them — you are filling gaps, not replacing.

Constraints:

- Up to 3 web_searches per politician-platform pair. Do not spiral.
- If a politician is clearly retired/defeated, their old handles may be
  archived — that's OK, mark confidence <= 0.80.
"""


@dataclass
class PoliticianContext:
    id: str
    name: str
    party: Optional[str]
    level: str
    province_territory: Optional[str]
    constituency_name: Optional[str]
    official_url: Optional[str]
    personal_url: Optional[str]
    known_socials: dict[str, str]           # platform -> handle
    missing_platforms: list[str]
    openparliament_slug: Optional[str] = None
    ola_slug: Optional[str] = None
    nslegislature_slug: Optional[str] = None


@dataclass
class AgentHit:
    politician_id: str
    platform: str
    url: str
    handle: Optional[str]
    confidence: float
    evidence_url: Optional[str]
    reasoning: Optional[str]


# ── Data assembly ────────────────────────────────────────────────────


async def _fetch_batch_contexts(
    db: Database,
    *,
    platform: Optional[str],
    batch_size: int,
    offset: int,
) -> list[PoliticianContext]:
    """Read `batch_size` politicians from v_socials_missing, grouped by politician.

    If `platform` is provided, only that missing-platform drives the query;
    each politician in the batch has exactly one entry in `missing_platforms`.
    If `platform` is None, we pull every politician that has at least one
    missing platform and include ALL their missing platforms.
    """
    if platform is not None:
        rows = await db.fetch(
            """
            SELECT DISTINCT politician_id, name, level, province_territory,
                            constituency_name, party,
                            official_url, personal_url,
                            openparliament_slug, ola_slug, nslegislature_slug
              FROM v_socials_missing
             WHERE platform = $1
             ORDER BY politician_id
             OFFSET $2 LIMIT $3
            """,
            platform, int(offset), int(batch_size),
        )
        politician_ids = [str(r["politician_id"]) for r in rows]
        missing_by_pol = {pid: [platform] for pid in politician_ids}
    else:
        # Unique politicians first, then their missing-platform list.
        pid_rows = await db.fetch(
            """
            SELECT DISTINCT politician_id
              FROM v_socials_missing
             ORDER BY politician_id
             OFFSET $1 LIMIT $2
            """,
            int(offset), int(batch_size),
        )
        politician_ids = [str(r["politician_id"]) for r in pid_rows]
        if not politician_ids:
            return []
        rows = await db.fetch(
            """
            SELECT politician_id, name, level, province_territory,
                   constituency_name, party,
                   official_url, personal_url,
                   openparliament_slug, ola_slug, nslegislature_slug,
                   platform
              FROM v_socials_missing
             WHERE politician_id = ANY($1)
             ORDER BY politician_id, platform
            """,
            politician_ids,
        )
        missing_by_pol: dict[str, list[str]] = {pid: [] for pid in politician_ids}
        for r in rows:
            missing_by_pol[str(r["politician_id"])].append(r["platform"])

    if not politician_ids:
        return []

    known = await db.fetch(
        """
        SELECT politician_id, platform, handle
          FROM politician_socials
         WHERE politician_id = ANY($1) AND handle IS NOT NULL
        """,
        politician_ids,
    )
    known_by_pol: dict[str, dict[str, str]] = {pid: {} for pid in politician_ids}
    for r in known:
        known_by_pol[str(r["politician_id"])][r["platform"]] = r["handle"]

    seen_ids: set[str] = set()
    contexts: list[PoliticianContext] = []
    for r in rows:
        pid = str(r["politician_id"])
        if pid in seen_ids:
            continue
        seen_ids.add(pid)
        contexts.append(PoliticianContext(
            id=pid,
            name=r["name"] or "",
            party=r["party"],
            level=r["level"],
            province_territory=r["province_territory"],
            constituency_name=r["constituency_name"],
            official_url=r["official_url"],
            personal_url=r["personal_url"],
            known_socials=known_by_pol.get(pid, {}),
            missing_platforms=missing_by_pol.get(pid, []),
            openparliament_slug=r.get("openparliament_slug") if isinstance(r, dict) else r["openparliament_slug"],
            ola_slug=r.get("ola_slug") if isinstance(r, dict) else r["ola_slug"],
            nslegislature_slug=r.get("nslegislature_slug") if isinstance(r, dict) else r["nslegislature_slug"],
        ))
    return contexts


def _ctx_to_brief(ctx: PoliticianContext) -> dict[str, Any]:
    """Slim dict for inclusion in the agent's user message."""
    d: dict[str, Any] = {
        "politician_id": ctx.id,
        "name": ctx.name,
        "level": ctx.level,
        "province_territory": ctx.province_territory,
        "party": ctx.party,
        "missing_platforms": ctx.missing_platforms,
    }
    if ctx.constituency_name:
        d["constituency_name"] = ctx.constituency_name
    if ctx.official_url:
        d["official_url"] = ctx.official_url
    if ctx.personal_url:
        d["personal_url"] = ctx.personal_url
    if ctx.known_socials:
        d["known_socials"] = ctx.known_socials
    if ctx.openparliament_slug:
        d["openparliament_slug"] = ctx.openparliament_slug
    if ctx.ola_slug:
        d["ola_slug"] = ctx.ola_slug
    if ctx.nslegislature_slug:
        d["nslegislature_slug"] = ctx.nslegislature_slug
    return d


def _build_user_message(contexts: list[PoliticianContext]) -> str:
    payload = [_ctx_to_brief(c) for c in contexts]
    return (
        "Find official personal social-media handles for these politicians.\n\n"
        "```json\n"
        + orjson.dumps(payload, option=orjson.OPT_INDENT_2).decode()
        + "\n```\n\n"
        "Return a single JSON object as specified in the system prompt."
    )


# ── Response parsing ─────────────────────────────────────────────────


_JSON_OBJ_RE = re.compile(r"\{[\s\S]*\}", re.MULTILINE)


def _parse_response(text: str) -> list[AgentHit]:
    """Extract the {results: [...]} JSON from the agent's final assistant text."""
    # Be forgiving: the agent may wrap JSON in a code fence despite the
    # instruction to return raw JSON.
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```\s*$", "", text)
    m = _JSON_OBJ_RE.search(text)
    if not m:
        return []
    try:
        obj = orjson.loads(m.group(0))
    except Exception as exc:
        log.warning("agent returned unparseable JSON: %s", exc)
        return []
    if not isinstance(obj, dict):
        return []
    results = obj.get("results") or []
    if not isinstance(results, list):
        return []
    hits: list[AgentHit] = []
    for item in results:
        if not isinstance(item, dict):
            continue
        pid = item.get("politician_id")
        platform = item.get("platform")
        url = item.get("url")
        if not (pid and platform and url):
            continue
        if platform not in ALLOWED_PLATFORMS:
            continue
        conf = item.get("confidence")
        try:
            conf = float(conf)
        except (TypeError, ValueError):
            continue
        conf = max(0.0, min(1.0, conf))
        hits.append(AgentHit(
            politician_id=str(pid),
            platform=platform,
            url=str(url),
            handle=item.get("handle") if isinstance(item.get("handle"), str) else None,
            confidence=conf,
            evidence_url=item.get("evidence_url") if isinstance(item.get("evidence_url"), str) else None,
            reasoning=item.get("reasoning") if isinstance(item.get("reasoning"), str) else None,
        ))
    return hits


# ── Agent call ───────────────────────────────────────────────────────


async def _call_agent(
    client: Any,
    *,
    model: str,
    contexts: list[PoliticianContext],
    max_tokens: int,
) -> tuple[list[AgentHit], dict[str, int]]:
    """Invoke the Anthropic API and return (hits, usage_summary)."""
    messages = [
        {"role": "user", "content": _build_user_message(contexts)},
    ]
    tools = [{"type": "web_search_20250305", "name": "web_search"}]
    try:
        resp = await client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=SYSTEM_PROMPT,
            tools=tools,
            messages=messages,
        )
    except Exception as exc:
        log.error("anthropic call failed: %s", exc)
        return [], {"input_tokens": 0, "output_tokens": 0, "error": 1}

    # Extract the final text block from the assistant message.
    text_chunks: list[str] = []
    for block in resp.content:
        if getattr(block, "type", None) == "text":
            text_chunks.append(block.text)
    final_text = "\n".join(text_chunks).strip()
    hits = _parse_response(final_text)

    usage = getattr(resp, "usage", None)
    usage_summary = {
        "input_tokens": getattr(usage, "input_tokens", 0) if usage else 0,
        "output_tokens": getattr(usage, "output_tokens", 0) if usage else 0,
        "error": 0,
    }
    # web_search_requests is surfaced in usage as server_tool_use.web_search_requests
    if usage is not None:
        stu = getattr(usage, "server_tool_use", None)
        if stu is not None:
            usage_summary["web_searches"] = getattr(stu, "web_search_requests", 0)

    return hits, usage_summary


# ── Insertion ────────────────────────────────────────────────────────


async def _ingest_hits(
    db: Database,
    hits: list[AgentHit],
    *,
    dry_run: bool,
) -> dict[str, int]:
    stats = Counter()
    for h in hits:
        if h.confidence < AGENT_MIN_WRITE:
            stats["below_min_write"] += 1
            continue
        if dry_run:
            stats["dry_run"] += 1
            continue
        try:
            canon = await upsert_social(
                db, h.politician_id, h.platform, h.url,
                source="agent_sonnet",
                confidence=h.confidence,
                evidence_url=h.evidence_url,
            )
        except Exception as exc:
            log.warning("agent upsert failed for %s %s: %s", h.politician_id, h.url, exc)
            stats["insert_error"] += 1
            continue
        if canon is None:
            stats["upsert_rejected"] += 1
            continue
        if h.confidence >= AGENT_PROMOTE_THRESHOLD:
            stats["auto_inserted"] += 1
        else:
            stats["flagged_inserted"] += 1
    return dict(stats)


# ── Driver ───────────────────────────────────────────────────────────


async def agent_find_socials(
    db: Database,
    *,
    platform: Optional[str] = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
    max_batches: int = 20,
    model: str = DEFAULT_MODEL,
    dry_run: bool = False,
    max_tokens: int = DEFAULT_MAX_TOKENS,
) -> None:
    """Run the Tier-3 agent loop.

    Iterates the v_socials_missing matrix, submits batches of
    politicians to the model, and ingests the returned hits. Stops when
    max_batches is hit or the matrix is exhausted.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        console.print("[red]ANTHROPIC_API_KEY is not set. Aborting.[/red]")
        return

    try:
        import anthropic  # type: ignore
    except ImportError:
        console.print("[red]The 'anthropic' package is not installed. Run: pip install anthropic[/red]")
        return

    batch_size = max(1, min(batch_size, MAX_BATCH_SIZE))
    client = anthropic.AsyncAnthropic(api_key=api_key)

    running_tokens = {"input_tokens": 0, "output_tokens": 0, "web_searches": 0, "error": 0}
    running_ingest = Counter()
    all_hits: list[AgentHit] = []

    console.print(
        f"[cyan]agent-missing-socials:[/cyan] platform={platform or 'all-missing'} "
        f"batch_size={batch_size} max_batches={max_batches} "
        f"model={model} dry_run={dry_run}"
    )

    offset = 0
    batch_n = 0
    while batch_n < max_batches:
        contexts = await _fetch_batch_contexts(
            db, platform=platform, batch_size=batch_size, offset=offset,
        )
        if not contexts:
            console.print("[yellow]no more politicians to process — stopping[/yellow]")
            break
        offset += len(contexts)
        batch_n += 1

        console.print(
            f"[cyan]batch {batch_n}/{max_batches}:[/cyan] {len(contexts)} politicians, "
            f"{sum(len(c.missing_platforms) for c in contexts)} target cells"
        )
        hits, usage = await _call_agent(
            client, model=model, contexts=contexts, max_tokens=max_tokens,
        )
        running_tokens["input_tokens"] += usage.get("input_tokens", 0)
        running_tokens["output_tokens"] += usage.get("output_tokens", 0)
        running_tokens["web_searches"] += usage.get("web_searches", 0)
        running_tokens["error"] += usage.get("error", 0)

        all_hits.extend(hits)
        if hits:
            console.print(
                f"  → {len(hits)} hits returned "
                f"(tokens: in={usage.get('input_tokens', 0)} "
                f"out={usage.get('output_tokens', 0)} "
                f"searches={usage.get('web_searches', 0)})"
            )
        else:
            console.print("  → no hits returned for this batch")

        ingest_stats = await _ingest_hits(db, hits, dry_run=dry_run)
        for k, v in ingest_stats.items():
            running_ingest[k] += v

    _print_summary(all_hits, running_tokens, running_ingest, dry_run=dry_run)


def _print_summary(
    hits: list[AgentHit],
    tokens: dict[str, int],
    ingest: Counter,
    *,
    dry_run: bool,
) -> None:
    console.print()
    console.print(
        f"[green]✓ agent run complete[/green] — "
        f"{len(hits)} hits, "
        f"input={tokens.get('input_tokens', 0):,} tokens, "
        f"output={tokens.get('output_tokens', 0):,} tokens, "
        f"web_searches={tokens.get('web_searches', 0)}, "
        f"errors={tokens.get('error', 0)}"
    )

    if ingest:
        tbl = Table(title="Ingestion outcome" + (" (dry-run)" if dry_run else ""))
        tbl.add_column("bucket", style="cyan")
        tbl.add_column("n", justify="right")
        for k, v in ingest.most_common():
            style = "green" if k == "auto_inserted" else "yellow" if k == "flagged_inserted" else None
            tbl.add_row(
                f"[{style}]{k}[/{style}]" if style else k,
                str(v),
            )
        console.print(tbl)

    # Show a sample of high-confidence hits for spot-checking.
    auto = [h for h in hits if h.confidence >= AGENT_PROMOTE_THRESHOLD]
    flagged = [h for h in hits if AGENT_MIN_WRITE <= h.confidence < AGENT_PROMOTE_THRESHOLD]
    if auto:
        console.print(f"[green]Auto-inserted samples ({len(auto)}):[/green]")
        for h in auto[:15]:
            console.print(
                f"  {h.platform:<10} {h.url}  conf={h.confidence:.2f}  "
                f"ev={(h.evidence_url or '')[:80]}"
            )
    if flagged:
        console.print(f"[yellow]Flagged samples ({len(flagged)}):[/yellow]")
        for h in flagged[:15]:
            console.print(
                f"  {h.platform:<10} {h.url}  conf={h.confidence:.2f}  "
                f"ev={(h.evidence_url or '')[:80]}"
            )
