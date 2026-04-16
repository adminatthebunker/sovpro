"""scanner-jobs daemon — consumes `scanner_jobs` + expands `scanner_schedules`.

Single-worker design on purpose (see plan). Every poll cycle:

1. Expand any schedules whose `next_run_at <= now()` into new queued
   job rows, then update `last_enqueued_at` + `next_run_at` via croniter.
2. Recover orphaned `status='running'` rows from a previous worker boot
   (anything running > `WORKER_STUCK_MINUTES` minutes is requeued).
3. Claim the next queued job (priority DESC, queued_at ASC) via
   `SELECT FOR UPDATE SKIP LOCKED` so future multi-worker setups Just
   Work without double-dispatching.
4. Spawn `python -m src <cli> [flags]` as a subprocess, with a
   configurable timeout. Stream stdout/stderr into rolling 4 KB tails
   so failures are debuggable without digging into container logs.
5. Mark the job `succeeded` / `failed` / (on Popen exceptions) `failed`
   with an `error` string describing what went wrong at the worker
   level.

Runs in the same container image as the scanner CLI (`services/scanner`)
so `python -m src <cli>` is just another invocation of the same code —
no cross-container dispatch, no docker-socket mounts.
"""
from __future__ import annotations

import asyncio
import logging
import os
import signal
import subprocess
import time
from datetime import datetime, timezone
from typing import Any, Optional

from croniter import croniter

from .db import Database
from .jobs_catalog import build_cli_args, get_command

log = logging.getLogger("jobs_worker")

POLL_INTERVAL = int(os.environ.get("JOBS_POLL_INTERVAL", "5"))          # seconds
DEFAULT_TIMEOUT = int(os.environ.get("JOBS_DEFAULT_TIMEOUT", "7200"))   # 2h
TAIL_BYTES = int(os.environ.get("JOBS_TAIL_BYTES", "4096"))
WORKER_STUCK_MINUTES = int(os.environ.get("JOBS_STUCK_MINUTES", "10"))
PYTHON_BIN = os.environ.get("PYTHON_BIN", "python")


# ── Schedule expansion ───────────────────────────────────────────────


async def enqueue_due_schedules(db: Database) -> int:
    """Materialise any enabled schedule whose next_run_at has passed.

    Returns the number of jobs enqueued. The UPDATE + INSERT happens in
    a single transaction per schedule so two workers can't double-fire.
    """
    enqueued = 0
    # Fetch candidates outside the lock to keep the hot path light.
    # Correctness: we re-lock each schedule in the transaction below so
    # a second worker that sees the same row will no-op after the first
    # has moved next_run_at forward.
    rows = await db.fetch(
        """
        SELECT id, name, command, args, cron, next_run_at, last_enqueued_at
          FROM scanner_schedules
         WHERE enabled = true AND (next_run_at IS NULL OR next_run_at <= now())
        """
    )
    for row in rows:
        async with db.pool.acquire() as conn:
            async with conn.transaction():
                # Re-check under row lock to avoid double-enqueue across workers.
                cur = await conn.fetchrow(
                    """
                    SELECT id, cron, next_run_at
                      FROM scanner_schedules
                     WHERE id = $1 AND enabled = true
                       AND (next_run_at IS NULL OR next_run_at <= now())
                     FOR UPDATE
                    """,
                    row["id"],
                )
                if cur is None:
                    continue

                # Parse args (asyncpg returns JSONB as str)
                import json
                args_dict = row["args"] if isinstance(row["args"], dict) else json.loads(row["args"] or "{}")

                await conn.execute(
                    """
                    INSERT INTO scanner_jobs
                        (command, args, status, priority, schedule_id, requested_by)
                    VALUES ($1, $2::jsonb, 'queued', 0, $3, $4)
                    """,
                    row["command"],
                    json.dumps(args_dict),
                    row["id"],
                    f"schedule:{row['name']}",
                )
                try:
                    nxt = _next_cron_after(row["cron"], datetime.now(timezone.utc))
                except Exception as exc:
                    log.warning("schedule %s has bad cron %r: %s", row["name"], row["cron"], exc)
                    nxt = None
                await conn.execute(
                    """
                    UPDATE scanner_schedules
                       SET last_enqueued_at = now(), next_run_at = $2
                     WHERE id = $1
                    """,
                    row["id"],
                    nxt,
                )
                enqueued += 1
                log.info("enqueued job for schedule %s (next=%s)", row["name"], nxt)
    return enqueued


def _next_cron_after(expr: str, base: datetime) -> datetime:
    """Return the next fire time strictly after `base` for a 5-field cron."""
    it = croniter(expr, base)
    nxt = it.get_next(datetime)
    if nxt.tzinfo is None:
        nxt = nxt.replace(tzinfo=timezone.utc)
    return nxt


# ── Stuck-job recovery ──────────────────────────────────────────────


async def recover_stuck_jobs(db: Database) -> int:
    """Requeue any 'running' rows older than WORKER_STUCK_MINUTES.

    Triggered each poll loop. If the worker was killed mid-job the row
    is stuck `running` forever otherwise; this makes restarts safe.
    """
    n = await db.fetchval(
        f"""
        UPDATE scanner_jobs
           SET status = 'queued',
               started_at = NULL,
               error = COALESCE(error, '') ||
                       CASE WHEN error IS NULL THEN '' ELSE '; ' END ||
                       'recovered after worker restart'
         WHERE status = 'running'
           AND started_at < now() - interval '{WORKER_STUCK_MINUTES} minutes'
        RETURNING 1
        """
    )
    # With asyncpg, fetchval returns the first row's first column — we
    # actually want the count. Re-run as execute to get the status:
    status = await db.execute(
        f"""
        UPDATE scanner_jobs
           SET status = 'queued',
               started_at = NULL
         WHERE status = 'running'
           AND started_at < now() - interval '{WORKER_STUCK_MINUTES} minutes'
        """
    )
    # asyncpg execute returns a string like "UPDATE 0" — parse count.
    try:
        count = int(status.rsplit(" ", 1)[-1])
    except Exception:
        count = 0
    if count:
        log.warning("recovered %d stuck job(s) from a previous worker", count)
    return count


# ── Job claim + run ──────────────────────────────────────────────────


async def claim_next_job(db: Database) -> Optional[dict[str, Any]]:
    """Atomically flip the next queued job to 'running' and return it.

    SKIP LOCKED means future parallel workers will each grab different
    rows, not fight over the same one.
    """
    row = await db.fetchrow(
        """
        UPDATE scanner_jobs
           SET status = 'running', started_at = now()
         WHERE id = (
               SELECT id FROM scanner_jobs
                WHERE status = 'queued'
                ORDER BY priority DESC, queued_at
                LIMIT 1
                FOR UPDATE SKIP LOCKED
         )
        RETURNING id, command, args, priority, schedule_id, requested_by,
                  queued_at, started_at
        """
    )
    if row is None:
        return None
    return dict(row)


async def run_job(db: Database, job: dict[str, Any]) -> None:
    """Execute one job via subprocess, capture tails, update DB on finish."""
    import json
    command_key = job["command"]
    args = job["args"]
    if isinstance(args, str):
        args = json.loads(args or "{}")

    cmd_meta = get_command(command_key)
    if cmd_meta is None:
        await _finalise(
            db, job["id"],
            exit_code=None, status="failed",
            error=f"unknown command in catalog: {command_key}",
        )
        return

    try:
        cli_tokens = build_cli_args(command_key, args)
    except ValueError as exc:
        await _finalise(
            db, job["id"],
            exit_code=None, status="failed",
            error=f"bad args: {exc}",
        )
        return

    full_cmd = [PYTHON_BIN, "-m", "src"] + cli_tokens
    timeout = int(args.get("timeout_seconds") or DEFAULT_TIMEOUT)
    log.info("running job %s: %s (timeout=%ds)", job["id"], " ".join(full_cmd), timeout)

    stdout_tail = _RollingTail(TAIL_BYTES)
    stderr_tail = _RollingTail(TAIL_BYTES)
    status = "failed"
    exit_code: Optional[int] = None
    error_msg: Optional[str] = None

    try:
        proc = await asyncio.create_subprocess_exec(
            *full_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd="/app",
            # Don't inherit signals from the controlling TTY
            start_new_session=True,
        )
    except Exception as exc:
        await _finalise(
            db, job["id"],
            exit_code=None, status="failed",
            error=f"spawn failed: {exc}",
        )
        return

    async def _drain(stream: asyncio.StreamReader, tail: "_RollingTail") -> None:
        while True:
            chunk = await stream.read(4096)
            if not chunk:
                return
            tail.write(chunk)

    drainers = asyncio.gather(
        _drain(proc.stdout, stdout_tail),
        _drain(proc.stderr, stderr_tail),
    )
    try:
        await asyncio.wait_for(proc.wait(), timeout=timeout)
        await drainers
        exit_code = proc.returncode
        status = "succeeded" if exit_code == 0 else "failed"
        if exit_code != 0:
            error_msg = f"non-zero exit: {exit_code}"
    except asyncio.TimeoutError:
        log.warning("job %s exceeded timeout=%ds; killing", job["id"], timeout)
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        try:
            await asyncio.wait_for(proc.wait(), timeout=10)
        except asyncio.TimeoutError:
            pass
        await drainers
        exit_code = proc.returncode
        status = "failed"
        error_msg = f"timed out after {timeout}s"
    except Exception as exc:
        log.exception("job %s crashed in wait loop", job["id"])
        try:
            proc.kill()
        except Exception:
            pass
        await drainers
        status = "failed"
        error_msg = f"worker exception: {exc}"

    await _finalise(
        db, job["id"],
        exit_code=exit_code, status=status,
        stdout_tail=stdout_tail.decode(),
        stderr_tail=stderr_tail.decode(),
        error=error_msg,
    )


class _RollingTail:
    """Keeps the last N bytes of an unbounded byte stream."""

    def __init__(self, max_bytes: int) -> None:
        self.max = max_bytes
        self._buf = bytearray()
        self._truncated = False

    def write(self, chunk: bytes) -> None:
        self._buf.extend(chunk)
        if len(self._buf) > self.max:
            self._truncated = True
            overflow = len(self._buf) - self.max
            del self._buf[:overflow]

    def decode(self) -> str:
        prefix = "[…earlier output truncated…]\n" if self._truncated else ""
        try:
            return prefix + self._buf.decode("utf-8", errors="replace")
        except Exception:
            return prefix + self._buf.decode("latin-1", errors="replace")


async def _finalise(
    db: Database,
    job_id: Any,
    *,
    status: str,
    exit_code: Optional[int] = None,
    stdout_tail: Optional[str] = None,
    stderr_tail: Optional[str] = None,
    error: Optional[str] = None,
) -> None:
    await db.execute(
        """
        UPDATE scanner_jobs
           SET status = $2,
               exit_code = $3,
               stdout_tail = COALESCE($4, stdout_tail),
               stderr_tail = COALESCE($5, stderr_tail),
               error = COALESCE($6, error),
               finished_at = now()
         WHERE id = $1
        """,
        job_id, status, exit_code, stdout_tail, stderr_tail, error,
    )
    log.info("finalised job %s: status=%s exit=%s error=%s",
             job_id, status, exit_code, error)


# ── Main loop ────────────────────────────────────────────────────────


async def main() -> None:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    log.info(
        "jobs worker started (poll=%ds, default_timeout=%ds, tail=%d bytes)",
        POLL_INTERVAL, DEFAULT_TIMEOUT, TAIL_BYTES,
    )
    db = Database(os.environ["DATABASE_URL"])
    await db.connect()

    # Hook SIGTERM so Ctrl-C / docker stop trigger a clean pool close.
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            pass

    try:
        await recover_stuck_jobs(db)
        while not stop.is_set():
            try:
                await enqueue_due_schedules(db)
            except Exception:
                log.exception("enqueue_due_schedules failed")
            try:
                job = await claim_next_job(db)
            except Exception:
                log.exception("claim_next_job failed")
                job = None

            if job is None:
                # idle — sleep, but wake on shutdown
                try:
                    await asyncio.wait_for(stop.wait(), timeout=POLL_INTERVAL)
                except asyncio.TimeoutError:
                    pass
                continue

            try:
                await run_job(db, job)
            except Exception as exc:
                log.exception("run_job raised; marking failed")
                await _finalise(db, job["id"], status="failed",
                                error=f"worker crash: {exc}")
    finally:
        await db.close()
        log.info("jobs worker exited cleanly")


if __name__ == "__main__":
    asyncio.run(main())
