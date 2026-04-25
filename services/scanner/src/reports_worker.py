"""reports-worker daemon — runs queued report_jobs through an LLM
map-reduce against every speech_chunk matching (politician_id, query),
persists the rendered HTML, commits or releases the credit hold, and
emails the user a "report ready" link.

Mirrors services/scanner/src/alerts_worker.py in shape: poll loop,
graceful SIGTERM, stub-on-missing-SMTP. The map-reduce prompt strings
are kept char-for-char identical to services/api/src/lib/reports.ts so
the model behaviour is a function of the prompt, not the entry point.

Two ledger interactions, both inline SQL UPDATE statements (no import
of the TS lib — we replicate the exact statements the lib emits):

  Success → credit_ledger row with kind='report_hold' and reference_id=jobId
            flips state 'held' → 'committed'. balance now reflects a real debit.
  Failure → same row flips 'held' → 'refunded'. delta drops out of balance.

Stale-claim re-queue: a job in 'running' state with claimed_at older
than 15 minutes is considered abandoned by a crashed worker and gets
re-queued. The hold stays in place, so the same job runs to completion
exactly once across re-queues — no double-debit.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import signal
import smtplib
import ssl
from email.message import EmailMessage
from typing import Any

import bleach
import httpx

from .db import Database, get_dsn

log = logging.getLogger("reports_worker")

POLL_INTERVAL = int(os.environ.get("REPORTS_POLL_INTERVAL", "5"))
STALE_CLAIM_MINUTES = int(os.environ.get("REPORTS_STALE_CLAIM_MINUTES", "15"))

OPENROUTER_BASE_URL = os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1").rstrip("/")
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
OPENROUTER_REPORT_MODEL = os.environ.get("OPENROUTER_REPORT_MODEL", "anthropic/claude-sonnet-4.6")
OPENROUTER_REPORT_TIMEOUT_MS = int(os.environ.get("OPENROUTER_REPORT_TIMEOUT_MS", "120000"))
OPENROUTER_SITE_URL = os.environ.get("OPENROUTER_SITE_URL", "https://canadianpoliticaldata.ca")
OPENROUTER_APP_NAME = os.environ.get("OPENROUTER_APP_NAME", "Canadian Political Data")

REPORT_BUCKET_SIZE = int(os.environ.get("REPORT_BUCKET_SIZE", "10"))
REPORT_MAX_CHUNKS = int(os.environ.get("REPORT_MAX_CHUNKS", "300"))
REPORT_HNSW_EF_SEARCH = int(os.environ.get("REPORT_HNSW_EF_SEARCH", "1000"))

EMBED_URL = os.environ.get("EMBED_URL", "http://tei:80").rstrip("/")
INSTRUCT_PREFIX = "Instruct: Retrieve relevant Canadian political speeches.\nQuery: "

SMTP_HOST = os.environ.get("SMTP_HOST", "smtp.protonmail.ch")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USERNAME = os.environ.get("SMTP_USERNAME", "")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
SMTP_FROM = os.environ.get("SMTP_FROM", "")
PUBLIC_SITE_URL = os.environ.get("PUBLIC_SITE_URL", "http://localhost:5173").rstrip("/")


# ── Prompts (KEEP IN SYNC with services/api/src/lib/reports.ts) ─────

SYSTEM_PROMPT_MAP = """You are a careful research analyst. You will be shown N quotes from a single Canadian politician on a specific topic. Extract the politician's positions and themes from these quotes. Output strictly valid JSON of this exact shape:

{
  "themes": [
    {
      "label": "<short noun-phrase label, < 60 chars>",
      "positions": [
        {
          "summary": "<one neutral sentence describing the politician's stated position>",
          "chunk_ids": ["<chunk_id from input, copied verbatim>", ...]
        }
      ]
    }
  ]
}

Rules:
- "chunk_ids" MUST be copied verbatim from the input. Never invent IDs.
- Every position must reference at least one input chunk_id.
- "summary" must be neutral and observational — do not editorialise, do not draw conclusions, do not call statements right or wrong.
- If a quote is the politician quoting an opponent ("the member opposite said…"), treat it as rhetorical framing, not their own position. Do not include such quotes as positions.
- Some quotes may be only tangentially related to the query topic — the retrieval system errs on the side of recall, so a few off-topic chunks may slip in. Omit any chunk where the politician is not actually speaking about the topic in a substantive way. Producing fewer, well-evidenced themes is preferred over many themes built on weak evidence.
- If multiple quotes express the same position, group them under one "positions" entry with multiple chunk_ids.
- Themes should be granular but not redundant: prefer 2-5 themes per bucket."""

SYSTEM_PROMPT_REDUCE = """You are synthesising the work of multiple analysts who each read a subset of a politician's quotes on a topic. You will be shown each analyst's themes and positions in JSON form. Produce a single coherent HTML report.

Output strictly valid JSON of this exact shape:

{
  "summary": "<one paragraph (60-120 words) framing what the politician's record shows on this topic, in neutral observational tone>",
  "html": "<HTML body, see allowed tags below>"
}

Allowed HTML tags ONLY: <p>, <h2>, <h3>, <ul>, <ol>, <li>, <blockquote>, <em>, <strong>, <a href="…">. Any other tag will be stripped server-side.

Rules:
- Structure the HTML with <h2> sections per theme; under each theme, group positions and reference quotes inline.
- Every claim that asserts a position MUST link to at least one source quote. Format the link as <a href="CHUNK:<chunk_id>">…</a> using the literal token CHUNK: followed by a chunk_id from the input. The system will rewrite these to real anchored URLs after you respond. Never output a real URL — only the CHUNK:<id> token form.
- Preserve the chunk_ids verbatim from the input analyst output. Never invent IDs.
- Neutral observational tone throughout. Frame as "the politician has said X (link)", never as "the politician is wrong about X" or "the politician contradicts themselves on X".
- If the analyst output includes contradictory positions across time, describe them descriptively — "in <year> they said X (link); in <later year> they said Y (link)" — without using the word "contradiction".
- The summary paragraph is the FIRST thing the user reads. Make it factual and substantive; avoid filler like "this report covers…".
- Do not include a top-level <h1> — the page chrome supplies the title. Start with a <p> or <h2>."""


def _ordinal_suffix(n: int) -> str:
    mod100 = n % 100
    if 11 <= mod100 <= 13:
        return "th"
    return {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")


def _format_date(d: Any) -> str:
    if d is None:
        return "unknown"
    return d.date().isoformat() if hasattr(d, "date") else str(d)[:10]


def build_map_prompt(politician_name: str, party: str | None, topic: str, chunks: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    party_fragment = f" ({party})" if party else ""
    lines.append(f"Politician: {politician_name}{party_fragment}")
    lines.append(f"Query topic: {topic}")
    lines.append("")
    for c in chunks:
        text = c["text"] or ""
        truncated = text[:1200] + "…[truncated]" if len(text) > 1200 else text
        lines.append(f"Quote (chunk_id={c['id']}):")
        lines.append(f"  Date: {_format_date(c.get('spoken_at'))}")
        if c.get("parliament_number") is not None and c.get("session_number") is not None:
            lines.append(
                f"  Parliament: {c['parliament_number']}{_ordinal_suffix(c['parliament_number'])}, Session {c['session_number']}"
            )
        if c.get("party_at_time"):
            lines.append(f"  Party at time: {c['party_at_time']}")
        lines.append(f"  Text: {truncated}")
        lines.append("")
    lines.append("Return the JSON object described in the system prompt.")
    return "\n".join(lines)


def build_reduce_prompt(politician_name: str, party: str | None, topic: str, bucket_summaries: list[Any]) -> str:
    return "\n".join([
        f"Politician: {politician_name}{f' ({party})' if party else ''}",
        f"Query topic: {topic}",
        "",
        "Per-bucket analyst output (JSON array, each element is one analyst's themes):",
        json.dumps(bucket_summaries, indent=2),
        "",
        "Return the synthesised JSON object described in the system prompt.",
    ])


# ── OpenRouter ──────────────────────────────────────────────────────


class OpenRouterError(RuntimeError):
    def __init__(self, kind: str, status: int | None = None, body: str = ""):
        super().__init__(f"openrouter {kind} status={status} body={body[:200]}")
        self.kind = kind
        self.status = status
        self.body = body


async def call_json_object_model(
    client: httpx.AsyncClient, system: str, user: str
) -> tuple[dict[str, Any], int, int, str]:
    """Returns (parsed_json, tokens_in, tokens_out, model_used).
    Raises OpenRouterError on auth/rate_limit/timeout/upstream/non_json."""
    if not OPENROUTER_API_KEY:
        raise OpenRouterError("auth", status=401)
    try:
        resp = await client.post(
            f"{OPENROUTER_BASE_URL}/chat/completions",
            headers={
                "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                "HTTP-Referer": OPENROUTER_SITE_URL,
                "X-Title": OPENROUTER_APP_NAME,
                "Content-Type": "application/json",
            },
            json={
                "model": OPENROUTER_REPORT_MODEL,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "response_format": {"type": "json_object"},
                "plugins": [{"id": "response-healing"}],
                "temperature": 0.2,
            },
            timeout=OPENROUTER_REPORT_TIMEOUT_MS / 1000.0,
        )
    except httpx.TimeoutException as e:
        raise OpenRouterError("timeout") from e
    except httpx.HTTPError as e:
        raise OpenRouterError("network", body=str(e)) from e

    if resp.status_code == 401:
        raise OpenRouterError("auth", status=401, body=resp.text[:500])
    if resp.status_code == 429:
        raise OpenRouterError("rate_limit", status=429, body=resp.text[:500])
    if resp.status_code >= 400:
        raise OpenRouterError("upstream", status=resp.status_code, body=resp.text[:500])

    try:
        body = resp.json()
    except json.JSONDecodeError as e:
        raise OpenRouterError("non_json", body=resp.text[:500]) from e

    content = (body.get("choices") or [{}])[0].get("message", {}).get("content")
    if not isinstance(content, str):
        raise OpenRouterError("bad_shape", body=str(body)[:500])

    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as e:
        raise OpenRouterError("bad_json", body=content[:500]) from e

    usage = body.get("usage") or {}
    return (
        parsed,
        int(usage.get("prompt_tokens") or 0),
        int(usage.get("completion_tokens") or 0),
        body.get("model") or OPENROUTER_REPORT_MODEL,
    )


# ── HTML sanitise + chunk-link rewrite ──────────────────────────────

ALLOWED_TAGS = ["p", "h2", "h3", "ul", "ol", "li", "blockquote", "em", "strong", "a"]
ALLOWED_ATTRS = {"a": ["href"]}
ALLOWED_PROTOCOLS = ["http", "https"]

_CHUNK_HREF_RE = re.compile(r"""href=(["'])CHUNK:([0-9a-f-]{36})\1""", re.IGNORECASE)


def rewrite_chunk_links(html: str, chunks: list[dict[str, Any]]) -> str:
    by_id = {str(c["id"]): c for c in chunks}

    def _repl(m: re.Match[str]) -> str:
        quote = m.group(1)
        chunk_id = m.group(2)
        c = by_id.get(chunk_id)
        if not c:
            return ""  # strip unknown href entirely
        return f"href={quote}/speeches/{c['speech_id']}#chunk-{chunk_id}{quote}"

    return _CHUNK_HREF_RE.sub(_repl, html)


def sanitise_html(html: str) -> str:
    cleaned = bleach.clean(
        html,
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRS,
        protocols=ALLOWED_PROTOCOLS,
        strip=True,
    )
    # Internal-paths-only allowlist on <a href>: anything that doesn't
    # start with /speeches/ has its href stripped. The link text remains.
    return re.sub(
        r'<a\s+([^>]*?)href=("[^"]*"|\'[^\']*\')([^>]*)>',
        lambda m: _enforce_internal_href(m),
        cleaned,
    )


def _enforce_internal_href(m: re.Match[str]) -> str:
    pre = m.group(1)
    href = m.group(2).strip("\"'")
    post = m.group(3)
    if href.startswith("/speeches/"):
        return f'<a {pre}href="{href}"{post}>'
    return f"<a {pre}{post}>"


# ── Embedding (TEI) ─────────────────────────────────────────────────


async def embed_query(client: httpx.AsyncClient, text: str) -> list[float]:
    wrapped = INSTRUCT_PREFIX + text
    r = await client.post(
        f"{EMBED_URL}/embed",
        json={"inputs": [wrapped], "normalize": True},
        timeout=30.0,
    )
    r.raise_for_status()
    data = r.json()
    if isinstance(data, list) and isinstance(data[0], list):
        return data[0]
    if isinstance(data, dict) and "data" in data:
        return data["data"][0]["embedding"]
    raise RuntimeError("Unexpected TEI /embed response shape")


def to_pgvector(vec: list[float]) -> str:
    return "[" + ",".join(str(x) for x in vec) + "]"


# ── DB ──────────────────────────────────────────────────────────────


async def claim_next_job(db: Database) -> dict[str, Any] | None:
    """Atomically claim the oldest queued job. Also re-queues any
    'running' job whose claim has gone stale (worker crashed mid-run)."""
    # First sweep: re-queue stale 'running' rows so they become claimable.
    await db.execute(
        f"""UPDATE report_jobs
              SET status = 'queued', claimed_at = NULL
            WHERE status = 'running'
              AND claimed_at IS NOT NULL
              AND claimed_at < now() - interval '{STALE_CLAIM_MINUTES} minutes'""",
    )
    row = await db.fetchrow(
        """UPDATE report_jobs
              SET status = 'running',
                  claimed_at = now(),
                  started_at = COALESCE(started_at, now())
            WHERE id = (
              SELECT id FROM report_jobs
               WHERE status = 'queued'
               ORDER BY priority DESC, created_at
               LIMIT 1
               FOR UPDATE SKIP LOCKED
            )
            RETURNING id, user_id, politician_id, query, estimated_chunks,
                      estimated_credits, hold_ledger_id"""
    )
    return dict(row) if row else None


async def commit_hold(db: Database, hold_ledger_id: Any) -> None:
    """Mirror of services/api/src/lib/credits.ts:commitHold (idempotent state-flip)."""
    if hold_ledger_id is None:
        return
    await db.execute(
        """UPDATE credit_ledger
              SET state = 'committed'
            WHERE id = $1
              AND state = 'held'
              AND kind = 'report_hold'""",
        hold_ledger_id,
    )


async def release_hold(db: Database, hold_ledger_id: Any, reason: str) -> None:
    """Mirror of services/api/src/lib/credits.ts:releaseHold."""
    if hold_ledger_id is None:
        return
    await db.execute(
        """UPDATE credit_ledger
              SET state = 'refunded',
                  reason = $2
            WHERE id = $1
              AND state = 'held'
              AND kind = 'report_hold'""",
        hold_ledger_id,
        reason,
    )


async def select_chunks(
    db: Database, politician_id: Any, vec_literal: str, limit: int
) -> list[dict[str, Any]]:
    """SET LOCAL hnsw.ef_search must live inside a transaction or it has
    no effect. Acquire a dedicated connection so the SET applies to the
    SELECT that follows it."""
    async with db.pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(f"SET LOCAL hnsw.ef_search = {REPORT_HNSW_EF_SEARCH}")
            rows = await conn.fetch(
                """SELECT sc.id, sc.speech_id, sc.text, sc.spoken_at, sc.party_at_time,
                          ls.parliament_number, ls.session_number,
                          s.source_url, s.source_anchor
                     FROM speech_chunks sc
                     JOIN speeches s ON s.id = sc.speech_id
                     LEFT JOIN legislative_sessions ls ON ls.id = s.session_id
                    WHERE sc.embedding IS NOT NULL
                      AND sc.politician_id = $1
                      AND (sc.embedding <=> $2::vector) <= 0.55
                    ORDER BY sc.embedding <=> $2::vector
                    LIMIT $3""",
                politician_id,
                vec_literal,
                limit,
            )
    return [dict(r) for r in rows]


# ── Email (mirrors api/lib/email.ts:sendReportReadyEmail) ───────────


def smtp_is_configured() -> bool:
    return bool(SMTP_USERNAME and SMTP_PASSWORD and SMTP_FROM)


def render_ready_text(politician_name: str, topic: str, summary: str | None, report_url: str) -> str:
    return (
        f"Your full report on {politician_name} ({topic}) is ready.\n"
        f"\n"
        f"{(summary or '').strip()}\n"
        f"\n"
        f"Read the full report: {report_url}\n"
        f"\n"
        f"Every claim in the report links back to a source quote. Read the\n"
        f"quotes before drawing conclusions — the synthesis is generative\n"
        f"and can omit, misweight, or mischaracterise.\n"
        f"\n"
        f"Canadian Political Data\n"
    )


def render_ready_html(politician_name: str, topic: str, summary: str | None, report_url: str) -> str:
    safe_summary = (summary or "").replace("<", "&lt;").replace(">", "&gt;")
    safe_pol = politician_name.replace("<", "&lt;").replace(">", "&gt;")
    safe_topic = topic.replace("<", "&lt;").replace(">", "&gt;")
    return f"""<p>Your full report on <strong>{safe_pol}</strong> on the topic
<em>{safe_topic}</em> is ready.</p>
<blockquote style="border-left:3px solid #e11d48;padding-left:1em;color:#444">
{safe_summary}
</blockquote>
<p><a href="{report_url}" style="background:#e11d48;color:white;padding:10px 18px;
border-radius:6px;text-decoration:none">Read the full report</a></p>
<p style="color:#666;font-size:.9em">Every claim links back to a source quote. The
synthesis is generative — read the quotes before drawing conclusions.</p>
<p style="color:#888;font-size:.8em">Canadian Political Data</p>"""


def render_failed_text(politician_name: str, topic: str, bug_url: str | None) -> str:
    parts = [
        f"Your full report on {politician_name} ({topic}) couldn't be generated.",
        "",
        "Your credits have been refunded automatically.",
        "",
    ]
    if bug_url:
        parts.append(f"Tell us what went wrong: {bug_url}")
        parts.append("")
    parts.append("Canadian Political Data")
    return "\n".join(parts)


def render_failed_html(politician_name: str, topic: str, bug_url: str | None) -> str:
    safe_pol = politician_name.replace("<", "&lt;").replace(">", "&gt;")
    safe_topic = topic.replace("<", "&lt;").replace(">", "&gt;")
    bug_section = (
        f'<p><a href="{bug_url}">Tell us what went wrong →</a></p>' if bug_url else ""
    )
    return f"""<p>Your full report on <strong>{safe_pol}</strong> on the topic
<em>{safe_topic}</em> couldn't be generated.</p>
<p><strong>Your credits have been refunded automatically.</strong></p>
{bug_section}
<p style="color:#888;font-size:.8em">Canadian Political Data</p>"""


def send_smtp(to: str, subject: str, text: str, html: str | None = None) -> None:
    msg = EmailMessage()
    msg["From"] = SMTP_FROM
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(text)
    if html is not None:
        msg.add_alternative(html, subtype="html")
    ctx = ssl.create_default_context()
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as s:
        s.ehlo()
        s.starttls(context=ctx)
        s.ehlo()
        s.login(SMTP_USERNAME, SMTP_PASSWORD)
        s.send_message(msg)


async def deliver_email(to: str, subject: str, text: str, html: str) -> None:
    if not smtp_is_configured():
        log.info("[smtp:stub] would send to=%s subject=%r\n--- body ---\n%s\n--- end ---", to, subject, text)
        return
    await asyncio.to_thread(send_smtp, to, subject, text, html)


# ── Job runner ─────────────────────────────────────────────────────


async def process_job(db: Database, job: dict[str, Any]) -> None:
    job_id = job["id"]
    log.info("processing job=%s user=%s politician=%s", job_id, job["user_id"], job["politician_id"])

    # Look up display metadata for prompt + emails.
    pol = await db.fetchrow(
        "SELECT name, party FROM politicians WHERE id = $1",
        job["politician_id"],
    )
    user = await db.fetchrow(
        "SELECT email, display_name, email_bounced_at FROM users WHERE id = $1",
        job["user_id"],
    )
    if not pol or not user:
        await fail_job(db, job, "missing politician or user row")
        return

    politician_name = pol["name"] or "Unknown politician"
    party = pol["party"]
    topic = job["query"]

    async with httpx.AsyncClient() as client:
        # 1. Embed query.
        try:
            vec = await embed_query(client, topic)
        except Exception as e:  # noqa: BLE001
            log.exception("embed failed for job=%s: %s", job_id, e)
            await fail_job(db, job, "Failed to embed query")
            await maybe_send_failed_email(user, politician_name, topic, job_id)
            return
        vec_literal = to_pgvector(vec)

        # 2. Fetch chunks.
        try:
            chunks = await select_chunks(db, job["politician_id"], vec_literal, REPORT_MAX_CHUNKS)
        except Exception as e:  # noqa: BLE001
            log.exception("chunk fetch failed for job=%s: %s", job_id, e)
            await fail_job(db, job, "Failed to retrieve speech chunks")
            await maybe_send_failed_email(user, politician_name, topic, job_id)
            return
        if not chunks:
            await fail_job(db, job, "No matching quotes found for this politician + query")
            await maybe_send_failed_email(user, politician_name, topic, job_id)
            return

        # 3. Map calls (concurrency 2).
        buckets = [chunks[i:i + REPORT_BUCKET_SIZE] for i in range(0, len(chunks), REPORT_BUCKET_SIZE)]
        bucket_outputs: list[Any] = []
        tokens_in = 0
        tokens_out = 0
        model_used = OPENROUTER_REPORT_MODEL

        sem = asyncio.Semaphore(2)

        async def run_map(bucket: list[dict[str, Any]]) -> Any:
            async with sem:
                user_prompt = build_map_prompt(politician_name, party, topic, bucket)
                return await call_json_object_model(client, SYSTEM_PROMPT_MAP, user_prompt)

        try:
            map_results = await asyncio.gather(*[run_map(b) for b in buckets])
        except OpenRouterError as e:
            log.warning("map call failed for job=%s: %s", job_id, e)
            await fail_job(db, job, _user_facing_openrouter_error(e))
            await maybe_send_failed_email(user, politician_name, topic, job_id)
            return
        except Exception as e:  # noqa: BLE001
            log.exception("map call unexpected failure for job=%s: %s", job_id, e)
            await fail_job(db, job, "AI service error during analysis")
            await maybe_send_failed_email(user, politician_name, topic, job_id)
            return

        for parsed, ti, tout, model in map_results:
            tokens_in += ti
            tokens_out += tout
            model_used = model
            bucket_outputs.append(parsed)

        # 4. Reduce.
        try:
            reduce_parsed, ri, ro, reduce_model = await call_json_object_model(
                client,
                SYSTEM_PROMPT_REDUCE,
                build_reduce_prompt(politician_name, party, topic, bucket_outputs),
            )
        except OpenRouterError as e:
            log.warning("reduce call failed for job=%s: %s", job_id, e)
            await fail_job(db, job, _user_facing_openrouter_error(e))
            await maybe_send_failed_email(user, politician_name, topic, job_id)
            return
        except Exception as e:  # noqa: BLE001
            log.exception("reduce call unexpected failure for job=%s: %s", job_id, e)
            await fail_job(db, job, "AI service error during synthesis")
            await maybe_send_failed_email(user, politician_name, topic, job_id)
            return

        tokens_in += ri
        tokens_out += ro
        model_used = reduce_model

        raw_html = reduce_parsed.get("html")
        summary = reduce_parsed.get("summary")
        if not isinstance(raw_html, str) or not isinstance(summary, str):
            await fail_job(db, job, "AI synthesis returned unexpected shape")
            await maybe_send_failed_email(user, politician_name, topic, job_id)
            return

        # 5. Rewrite chunk links → real URLs, then sanitise.
        rewritten = rewrite_chunk_links(raw_html, chunks)
        clean_html = sanitise_html(rewritten)

        # 6. Persist + commit hold.
        await db.execute(
            """UPDATE report_jobs
                  SET status = 'succeeded',
                      html = $2,
                      summary = $3,
                      chunk_count_actual = $4,
                      model_used = $5,
                      tokens_in = $6,
                      tokens_out = $7,
                      finished_at = now(),
                      error = NULL
                WHERE id = $1""",
            job_id,
            clean_html,
            summary,
            len(chunks),
            model_used,
            tokens_in,
            tokens_out,
        )
        await commit_hold(db, job["hold_ledger_id"])
        log.info("job=%s succeeded chunks=%d tokens=%d/%d", job_id, len(chunks), tokens_in, tokens_out)

        # 7. Email.
        if user["email_bounced_at"] is None:
            report_url = f"{PUBLIC_SITE_URL}/reports/{job_id}"
            try:
                await deliver_email(
                    to=user["email"],
                    subject=f"Your report on {politician_name} is ready",
                    text=render_ready_text(politician_name, topic, summary, report_url),
                    html=render_ready_html(politician_name, topic, summary, report_url),
                )
            except Exception as e:  # noqa: BLE001
                log.warning("ready email send failed for job=%s: %s", job_id, e)


def _user_facing_openrouter_error(e: OpenRouterError) -> str:
    if e.kind == "rate_limit":
        return "AI service is currently rate-limited. Please try again later."
    if e.kind == "auth":
        return "AI service authentication failed. Operator has been notified."
    if e.kind == "timeout":
        return "AI service timed out while generating the report."
    if e.kind == "upstream":
        return "AI service returned an error."
    return "AI service error during report generation."


async def fail_job(db: Database, job: dict[str, Any], message: str) -> None:
    await db.execute(
        """UPDATE report_jobs
              SET status = 'failed',
                  error = $2,
                  finished_at = now()
            WHERE id = $1""",
        job["id"],
        message,
    )
    await release_hold(db, job["hold_ledger_id"], f"report failed: {message[:200]}")
    log.info("job=%s failed: %s", job["id"], message)


async def maybe_send_failed_email(user_row: Any, politician_name: str, topic: str, job_id: Any) -> None:
    if user_row is None or user_row["email_bounced_at"] is not None:
        return
    bug_url = f"{PUBLIC_SITE_URL}/reports/{job_id}"
    try:
        await deliver_email(
            to=user_row["email"],
            subject=f"Your report on {politician_name} couldn't be generated",
            text=render_failed_text(politician_name, topic, bug_url),
            html=render_failed_html(politician_name, topic, bug_url),
        )
    except Exception as e:  # noqa: BLE001
        log.warning("failed-email send failed for job=%s: %s", job_id, e)


# ── Main loop ──────────────────────────────────────────────────────


_stop = asyncio.Event()


def _handle_signal(sig: int) -> None:
    log.info("signal %d — shutting down", sig)
    _stop.set()


async def main() -> None:
    logging.basicConfig(
        level=os.environ.get("REPORTS_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s reports-worker %(message)s",
    )
    log.info(
        "reports-worker starting poll=%ds model=%s smtp_configured=%s site=%s",
        POLL_INTERVAL, OPENROUTER_REPORT_MODEL, smtp_is_configured(), PUBLIC_SITE_URL,
    )
    if not OPENROUTER_API_KEY:
        log.warning("OPENROUTER_API_KEY unset — every job will fail at the map step until configured")

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _handle_signal, sig)

    db = Database(get_dsn())
    await db.connect()
    try:
        while not _stop.is_set():
            try:
                job = await claim_next_job(db)
                if job:
                    await process_job(db, job)
                    continue
            except Exception as e:  # noqa: BLE001
                log.exception("tick failed: %s", e)
            for _ in range(POLL_INTERVAL):
                if _stop.is_set():
                    break
                await asyncio.sleep(1)
    finally:
        await db.close()
