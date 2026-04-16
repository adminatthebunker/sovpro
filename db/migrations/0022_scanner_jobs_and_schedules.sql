-- Admin-panel foundation: scheduled + on-demand scanner jobs.
--
-- Two tables land together because jobs FK to schedules, and neither is
-- useful alone. See docs/plans + CLAUDE.md § Admin panel for the whole
-- picture.
--
-- `scanner_schedules` holds cron-like recurring plans. The worker
-- daemon expands each enabled row to a `scanner_jobs` row whenever
-- `next_run_at <= now()`. `last_enqueued_at` + `next_run_at` are
-- worker-maintained so the UI can display "last run" / "next run"
-- without a join.
--
-- `scanner_jobs` is the unified queue + history: pending rows are
-- `status='queued'`, a worker flips to `running` via SKIP LOCKED, and
-- terminal states are `succeeded` / `failed` / `cancelled`.
-- `stdout_tail` / `stderr_tail` keep the last ~4 KB of each stream so
-- failures are debuggable without digging through container logs.
--
-- Intentionally NOT in v1:
--   - FK from schedules to a "commands" table (the whitelist lives in
--     application code, not the DB — so changes to the catalog don't
--     require a migration).
--   - Row-level audit log (requested_by is free text; add an
--     admin_users table when multi-user lands).

CREATE TABLE IF NOT EXISTS scanner_schedules (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name              TEXT NOT NULL,
    command           TEXT NOT NULL,
    args              JSONB NOT NULL DEFAULT '{}'::jsonb,
    cron              TEXT NOT NULL,
    enabled           BOOLEAN NOT NULL DEFAULT true,
    last_enqueued_at  TIMESTAMPTZ,
    next_run_at       TIMESTAMPTZ,
    created_by        TEXT,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TRIGGER trg_scanner_schedules_touch BEFORE UPDATE ON scanner_schedules
    FOR EACH ROW EXECUTE FUNCTION touch_updated_at();

-- The queue index filters `WHERE status = 'queued'` because that's the
-- single query the worker runs every poll. Running/terminal rows stay
-- out of the hot index.
CREATE INDEX IF NOT EXISTS idx_scanner_sched_due
    ON scanner_schedules (next_run_at)
    WHERE enabled = true;

CREATE TABLE IF NOT EXISTS scanner_jobs (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    command          TEXT NOT NULL,
    args             JSONB NOT NULL DEFAULT '{}'::jsonb,
    status           TEXT NOT NULL DEFAULT 'queued'
                         CHECK (status IN ('queued','running','succeeded','failed','cancelled')),
    priority         SMALLINT NOT NULL DEFAULT 0,
    schedule_id      UUID REFERENCES scanner_schedules(id) ON DELETE SET NULL,
    requested_by     TEXT,
    queued_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at       TIMESTAMPTZ,
    finished_at      TIMESTAMPTZ,
    exit_code        INTEGER,
    stdout_tail      TEXT,
    stderr_tail      TEXT,
    error            TEXT
);

CREATE INDEX IF NOT EXISTS idx_scanner_jobs_queue
    ON scanner_jobs (status, priority DESC, queued_at)
    WHERE status = 'queued';

CREATE INDEX IF NOT EXISTS idx_scanner_jobs_history
    ON scanner_jobs (queued_at DESC);

CREATE INDEX IF NOT EXISTS idx_scanner_jobs_schedule
    ON scanner_jobs (schedule_id)
    WHERE schedule_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_scanner_jobs_running
    ON scanner_jobs (started_at)
    WHERE status = 'running';
