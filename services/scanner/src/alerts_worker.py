"""alerts-worker daemon — runs saved_searches with alert_cadence != 'none'
and emails digests of new matches.

Matching strategy (phase 1, deliberately simple):

  For each due saved_search:
    1. Re-run the same HNSW query the /search/speeches endpoint uses,
       but constrained to `speeches.spoken_at > last_checked_at` so we
       only surface new material.
    2. Take the top 10 matches.
    3. If any matches, send a digest email via the same Proton SMTP
       relay the API uses for magic links.
    4. Advance last_checked_at (always) and last_notified_at (only on
       send) so the next tick picks up where we left off.

`saved_searches.query_embedding` is already populated at save time by
the API (one TEI call, cached forever). This worker never calls TEI.

Cadence handling (phase 1):
  daily  → due when last_checked_at is NULL or < now() - 1 day
  weekly → due when last_checked_at is NULL or < now() - 7 days

Polling runs every ALERTS_POLL_INTERVAL seconds (default 300) — the
poll frequency just controls how quickly newly-due searches are picked
up; the actual send cadence is bounded by last_checked_at.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import signal
import smtplib
import ssl
from email.message import EmailMessage
from typing import Any

from .db import Database, get_dsn

log = logging.getLogger("alerts_worker")

POLL_INTERVAL = int(os.environ.get("ALERTS_POLL_INTERVAL", "300"))   # seconds
DIGEST_LIMIT = int(os.environ.get("ALERTS_DIGEST_LIMIT", "10"))

SMTP_HOST = os.environ.get("SMTP_HOST", "smtp.protonmail.ch")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USERNAME = os.environ.get("SMTP_USERNAME", "")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
SMTP_FROM = os.environ.get("SMTP_FROM", "")
PUBLIC_SITE_URL = os.environ.get("PUBLIC_SITE_URL", "http://localhost:5173").rstrip("/")
# Shared with the api service; used to HMAC-sign one-click-unsubscribe tokens.
# Unset → digests ship without List-Unsubscribe headers (deliverability degrades
# but nothing breaks).
JWT_SECRET = os.environ.get("JWT_SECRET", "")


# ── Unsubscribe tokens -----------------------------------------------


def generate_unsubscribe_token(saved_search_id: str) -> str | None:
    """HMAC-SHA256(JWT_SECRET, 'unsubscribe:' + saved_search_id) hex.

    The 'unsubscribe:' prefix binds the signature to this purpose even
    though we share JWT_SECRET with the RSS feed tokens — a feed token
    can never be replayed as an unsubscribe token (different message).
    """
    if not JWT_SECRET:
        return None
    msg = f"unsubscribe:{saved_search_id}".encode("utf-8")
    return hmac.new(JWT_SECRET.encode("utf-8"), msg, hashlib.sha256).hexdigest()


# ── SMTP -------------------------------------------------------------


def smtp_is_configured() -> bool:
    return bool(SMTP_USERNAME and SMTP_PASSWORD and SMTP_FROM)


def send_smtp(
    to: str,
    subject: str,
    text: str,
    html: str | None = None,
    extra_headers: dict[str, str] | None = None,
) -> None:
    """Blocking SMTP send. Matches the API's nodemailer config shape
    (smtp.protonmail.ch:587, STARTTLS). Runs in a thread from the
    async loop so we don't block the event loop on slow networks.

    extra_headers let callers attach RFC-2369 List-Unsubscribe and
    similar protocol headers."""
    msg = EmailMessage()
    msg["From"] = SMTP_FROM
    msg["To"] = to
    msg["Subject"] = subject
    if extra_headers:
        for k, v in extra_headers.items():
            msg[k] = v
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


# ── Matching query ---------------------------------------------------


def _build_filter_sql(payload: dict[str, Any], param_offset: int) -> tuple[str, list[Any]]:
    """Translate a saved_searches.filter_payload JSON into a SQL WHERE
    suffix + params. Mirrors the filters /search/speeches accepts; kept
    local rather than shared because the API is TypeScript and the
    worker is Python — duplicating 30 lines of SQL is cheaper than a
    cross-language bridge."""
    clauses: list[str] = []
    params: list[Any] = []
    lang = payload.get("lang")
    if lang and lang != "any":
        params.append(lang)
        clauses.append(f"s.language = ${param_offset + len(params)}")
    level = payload.get("level")
    if level:
        params.append(level)
        clauses.append(f"s.level = ${param_offset + len(params)}")
    pt = payload.get("province_territory")
    if pt:
        params.append(pt)
        clauses.append(f"s.province_territory = ${param_offset + len(params)}")
    pol = payload.get("politician_id")
    if pol:
        params.append(pol)
        clauses.append(f"s.politician_id = ${param_offset + len(params)}")
    party = payload.get("party")
    if party:
        params.append(party)
        clauses.append(f"s.party_at_time = ${param_offset + len(params)}")
    d_from = payload.get("from")
    if d_from:
        params.append(d_from)
        clauses.append(f"s.spoken_at >= ${param_offset + len(params)}::date")
    d_to = payload.get("to")
    if d_to:
        params.append(d_to)
        clauses.append(f"s.spoken_at <= ${param_offset + len(params)}::date")
    return (" AND " + " AND ".join(clauses) if clauses else ""), params


async def find_new_matches(
    db: Database,
    saved_search_id: str,
    query_embedding: Any,
    filter_payload: dict[str, Any],
    since: Any,
    limit: int,
) -> list[dict[str, Any]]:
    """Return up to `limit` new speech_chunks matching the saved search,
    ordered by semantic distance to the cached query vector.

    `since` is the last_checked_at watermark; we only look at chunks
    whose parent speech was spoken after that timestamp.
    """
    # query_embedding comes back from asyncpg as a string like
    # "[0.1,0.2,...]" (pgvector's text form) — pass it as-is back into
    # the $N::vector cast.
    params: list[Any] = [query_embedding, since]
    filter_sql, filter_params = _build_filter_sql(filter_payload, param_offset=2)
    params.extend(filter_params)

    sql = f"""
        SELECT c.id AS chunk_id,
               c.text,
               c.chunk_index,
               s.id AS speech_id,
               s.spoken_at,
               s.source_url,
               s.speaker_name_raw,
               p.name AS politician_name,
               p.id AS politician_id,
               (c.embedding <=> $1::vector) AS distance
          FROM speech_chunks c
          JOIN speeches s ON s.id = c.speech_id
          LEFT JOIN politicians p ON p.id = s.politician_id
         WHERE c.embedding IS NOT NULL
           AND s.spoken_at > $2
           {filter_sql}
         ORDER BY c.embedding <=> $1::vector
         LIMIT {int(limit)}
    """
    rows = await db.fetch(sql, *params)
    return [dict(r) for r in rows]


# ── Digest rendering -------------------------------------------------


def _unsubscribe_url(saved_search_id: str) -> str | None:
    token = generate_unsubscribe_token(saved_search_id)
    if not token:
        return None
    return f"{PUBLIC_SITE_URL}/api/v1/alerts/unsubscribe?t={token}"


def render_digest_text(
    user_email: str,
    saved_name: str,
    matches: list[dict[str, Any]],
    unsubscribe_url: str | None = None,
) -> str:
    q_line = f'"{saved_name}"'
    lines = [
        f"New matches for {q_line}",
        "",
        f"{len(matches)} new speech excerpt(s) since we last checked:",
        "",
    ]
    for m in matches:
        date = m["spoken_at"].date().isoformat() if m.get("spoken_at") else "(no date)"
        speaker = m.get("politician_name") or m.get("speaker_name_raw") or "Unknown speaker"
        snippet = (m.get("text") or "").strip().replace("\n", " ")
        if len(snippet) > 280:
            snippet = snippet[:277].rsplit(" ", 1)[0] + "…"
        url = m.get("source_url") or ""
        lines.append(f"— {date} · {speaker}")
        lines.append(f"  {snippet}")
        if url:
            lines.append(f"  Source: {url}")
        lines.append("")
    lines.append(f"Manage or turn off alerts: {PUBLIC_SITE_URL}/account/saved-searches")
    if unsubscribe_url:
        lines.append(f"Unsubscribe from this alert: {unsubscribe_url}")
    lines.append("")
    lines.append("Canadian Political Data · https://canadianpoliticaldata.ca")
    return "\n".join(lines)


def render_digest_html(
    user_email: str,
    saved_name: str,
    matches: list[dict[str, Any]],
    unsubscribe_url: str | None = None,
) -> str:
    items = []
    for m in matches:
        date = m["spoken_at"].date().isoformat() if m.get("spoken_at") else "(no date)"
        speaker = m.get("politician_name") or m.get("speaker_name_raw") or "Unknown speaker"
        snippet = (m.get("text") or "").strip().replace("\n", " ")
        if len(snippet) > 280:
            snippet = snippet[:277].rsplit(" ", 1)[0] + "…"
        url = m.get("source_url") or ""
        source_link = f'<br><a href="{url}" style="color:#4a9eff">View source</a>' if url else ""
        items.append(
            f'<li style="margin-bottom:1em"><strong>{date} · {speaker}</strong>'
            f'<br>{_html_escape(snippet)}{source_link}</li>'
        )
    manage_link = f"{PUBLIC_SITE_URL}/account/saved-searches"
    unsub_html = (
        f' · <a href="{unsubscribe_url}">Unsubscribe from this alert</a>'
        if unsubscribe_url else ""
    )
    return f"""<p>New matches for <strong>{_html_escape(saved_name)}</strong></p>
<p>{len(matches)} new speech excerpt(s) since we last checked:</p>
<ul>{''.join(items)}</ul>
<p style="color:#888;font-size:.9em">
<a href="{manage_link}">Manage or turn off alerts →</a>{unsub_html}
</p>
<p style="color:#888;font-size:.8em">Canadian Political Data</p>"""


def _html_escape(s: str) -> str:
    return (
        s.replace("&", "&amp;")
         .replace("<", "&lt;")
         .replace(">", "&gt;")
         .replace('"', "&quot;")
    )


# ── Main loop --------------------------------------------------------


async def process_due_searches(db: Database) -> int:
    """One pass: process every saved_search that's due. Returns count
    of digests sent (not counting no-match short-circuits)."""
    rows = await db.fetch(
        """
        SELECT s.id, s.user_id, s.name, s.filter_payload, s.query_embedding,
               s.alert_cadence, s.last_checked_at, u.email
          FROM saved_searches s
          JOIN users u ON u.id = s.user_id
         WHERE s.alert_cadence <> 'none'
           AND (
             s.last_checked_at IS NULL
             OR (s.alert_cadence = 'daily'  AND s.last_checked_at < now() - interval '1 day')
             OR (s.alert_cadence = 'weekly' AND s.last_checked_at < now() - interval '7 days')
           )
        """
    )
    sent = 0
    for row in rows:
        raw_payload = row["filter_payload"]
        if isinstance(raw_payload, str):
            filter_payload = json.loads(raw_payload) if raw_payload else {}
        elif isinstance(raw_payload, dict):
            filter_payload = raw_payload
        else:
            filter_payload = {}
        qvec = row["query_embedding"]
        # Watermark: if never checked, start from 30 days ago — arbitrary
        # but avoids emailing a user their first-ever digest with years
        # of matches. Future refinement: let users opt into a "catch-me-up"
        # initial digest if they want one.
        from datetime import datetime, timedelta, timezone
        since = row["last_checked_at"] or (datetime.now(timezone.utc) - timedelta(days=30))

        # A saved search without an embedding AND without meaningful
        # filters can't be matched — skip it and update the watermark.
        if qvec is None and not any(
            filter_payload.get(k) for k in ("level", "province_territory", "politician_id", "party")
        ):
            await db.execute(
                "UPDATE saved_searches SET last_checked_at = now() WHERE id = $1",
                row["id"],
            )
            continue

        try:
            if qvec is not None:
                matches = await find_new_matches(
                    db, row["id"], qvec, filter_payload, since, DIGEST_LIMIT
                )
            else:
                # Filter-only mode: order by recency instead of distance.
                matches = await _find_filter_only(db, filter_payload, since, DIGEST_LIMIT)
        except Exception as e:  # noqa: BLE001
            log.exception("match query failed for saved_search %s: %s", row["id"], e)
            # Don't advance the watermark on error — retry next tick.
            continue

        if matches:
            try:
                await _deliver_digest(row["email"], row["name"], matches, str(row["id"]))
                sent += 1
                await db.execute(
                    """UPDATE saved_searches
                          SET last_checked_at = now(),
                              last_notified_at = now()
                        WHERE id = $1""",
                    row["id"],
                )
                log.info(
                    "sent digest saved_search=%s user=%s matches=%d",
                    row["id"], row["email"], len(matches),
                )
            except Exception as e:  # noqa: BLE001
                log.exception("digest send failed for %s: %s", row["email"], e)
                # Do NOT advance watermark — we want to retry.
        else:
            await db.execute(
                "UPDATE saved_searches SET last_checked_at = now() WHERE id = $1",
                row["id"],
            )
    return sent


async def _find_filter_only(
    db: Database, filter_payload: dict[str, Any], since: Any, limit: int
) -> list[dict[str, Any]]:
    params: list[Any] = [since]
    filter_sql, filter_params = _build_filter_sql(filter_payload, param_offset=1)
    params.extend(filter_params)
    sql = f"""
        SELECT s.id AS speech_id,
               NULL::uuid AS chunk_id,
               s.text,
               0 AS chunk_index,
               s.spoken_at,
               s.source_url,
               s.speaker_name_raw,
               p.name AS politician_name,
               p.id AS politician_id,
               NULL::float AS distance
          FROM speeches s
          LEFT JOIN politicians p ON p.id = s.politician_id
         WHERE s.spoken_at > $1
           {filter_sql}
         ORDER BY s.spoken_at DESC
         LIMIT {int(limit)}
    """
    rows = await db.fetch(sql, *params)
    return [dict(r) for r in rows]


async def _deliver_digest(
    email: str,
    saved_name: str,
    matches: list[dict[str, Any]],
    saved_search_id: str,
) -> None:
    subject = f"[CPD] {len(matches)} new match(es) for \"{saved_name}\""
    unsub_url = _unsubscribe_url(saved_search_id)
    text = render_digest_text(email, saved_name, matches, unsub_url)
    html = render_digest_html(email, saved_name, matches, unsub_url)

    # RFC-2369 + RFC-8058 one-click unsubscribe. Gmail/Outlook both surface
    # a built-in Unsubscribe button when these are present; we want it.
    # Skip the headers (not the body link) if JWT_SECRET wasn't configured —
    # an unverifiable token would just 400 for the user.
    extra_headers: dict[str, str] = {}
    if unsub_url:
        extra_headers["List-Unsubscribe"] = f"<{unsub_url}>"
        extra_headers["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"

    if not smtp_is_configured():
        log.info(
            "[smtp:stub] would send to=%s subject=%r headers=%s\n--- body ---\n%s\n--- end ---",
            email, subject, extra_headers, text,
        )
        return

    await asyncio.to_thread(send_smtp, email, subject, text, html, extra_headers)


# ── Entrypoint -------------------------------------------------------


_stop = asyncio.Event()


def _handle_signal(sig: int) -> None:
    log.info("signal %d — shutting down", sig)
    _stop.set()


async def main() -> None:
    logging.basicConfig(
        level=os.environ.get("ALERTS_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s alerts-worker %(message)s",
    )
    log.info(
        "alerts-worker starting poll=%ds smtp_configured=%s site=%s",
        POLL_INTERVAL, smtp_is_configured(), PUBLIC_SITE_URL,
    )

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _handle_signal, sig)

    db = Database(get_dsn())
    await db.connect()
    try:
        while not _stop.is_set():
            try:
                sent = await process_due_searches(db)
                if sent:
                    log.info("tick complete sent=%d", sent)
            except Exception as e:  # noqa: BLE001
                log.exception("tick failed: %s", e)
            # Sleep in small chunks so SIGTERM stops us quickly.
            for _ in range(POLL_INTERVAL):
                if _stop.is_set():
                    break
                await asyncio.sleep(1)
    finally:
        await db.close()
