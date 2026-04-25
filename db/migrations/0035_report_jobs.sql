-- Premium reports phase 1b: queued report jobs + bug-report queue.
--
-- Phase 1a (migration 0033) shipped the billing rail; this migration
-- introduces the first credit *spender*. Both tables are pure additions
-- — no changes to credit_ledger semantics, no changes to existing
-- constraints. The credit_ledger.kind CHECK already includes
-- 'report_hold' / 'report_commit' / 'report_refund' from 0033.
--
-- Two tables:
--
-- 1. report_jobs — one row per "Full report — analyze everything"
--    request. Lifecycle: queued → running → succeeded | failed |
--    cancelled | refunded. The hold_ledger_id back-reference makes the
--    worker's commitHold/releaseHold path a single column lookup.
--    estimated_credits is captured at queue time so the user sees a
--    stable cost in the confirm modal and the actual debit matches.
--    chunk_count_actual / model_used / tokens_in / tokens_out support
--    after-the-fact cost analysis without parsing the html blob.
--
-- 2. report_bug_reports — submitter-flagged report quality issues.
--    Admin reviews in /admin/bug-reports; refunds (when warranted) are
--    issued via the existing admin comp flow. v1 does not auto-refund.

-- ─── report_jobs ──────────────────────────────────────────────────

create table if not exists report_jobs (
    id                  uuid primary key default gen_random_uuid(),
    user_id             uuid not null references users(id) on delete cascade,
    politician_id       uuid not null references politicians(id) on delete cascade,

    -- Free-text user query (the "topic" half of "politician X on topic Y").
    -- Length-bounded by the route, not the column — the route's zod
    -- schema is the source of truth.
    query               text not null,

    status              text not null default 'queued'
                            check (status in ('queued','running','succeeded','failed','cancelled','refunded')),

    -- Worker priority. Higher fires first within the queued bucket.
    -- Phase 1b ships with all jobs at priority=0; the column exists so
    -- a future "VIP / paid-tier instant" lane can be added without a
    -- second migration.
    priority            integer not null default 0,

    -- Cost accounting. Captured at submit time so the user sees a
    -- stable number in the cost-confirm modal and the actual hold
    -- matches. estimated_chunks tracks the candidate-pool size that
    -- drove the cost; chunk_count_actual is what the worker ended up
    -- using (≤ estimated_chunks, capped at REPORT_MAX_CHUNKS).
    estimated_chunks    integer not null,
    estimated_credits   integer not null,

    -- The credit_ledger row that holds the cost. Populated after the
    -- INSERT-then-holdCredits sequence in POST /reports. Worker calls
    -- commitHold / releaseHold against this id.
    hold_ledger_id      uuid references credit_ledger(id),

    -- Output. Populated by the worker on success.
    -- html is server-side-sanitised (sanitize-html allowlist) before
    -- persistence; the viewer renders it via dangerouslySetInnerHTML.
    -- summary is a one-paragraph framing for /me/reports list view.
    html                text,
    summary             text,
    chunk_count_actual  integer,
    model_used          text,
    tokens_in           integer,
    tokens_out          integer,

    -- Failure mode for the UI to show. Plain-text user-facing message;
    -- never raw stack traces. Detailed errors stay in api/worker logs.
    error               text,

    -- Worker claim + timing. claimed_at supports the stale-claim
    -- re-queue: a job stuck in 'running' past now() - interval '15min'
    -- is considered abandoned and re-queued. The hold stays in place
    -- across re-queues — no double-debit risk.
    claimed_at          timestamptz,
    started_at          timestamptz,
    finished_at         timestamptz,

    created_at          timestamptz not null default now(),
    updated_at          timestamptz not null default now()
);

create trigger trg_report_jobs_touch
    before update on report_jobs
    for each row execute function touch_updated_at();

create index if not exists idx_report_jobs_user_time
    on report_jobs(user_id, created_at desc);

create index if not exists idx_report_jobs_status_time
    on report_jobs(status, created_at);

-- Hot path for the worker claim: oldest queued job, partial index so
-- the lookup is O(queued) rather than O(all jobs).
create index if not exists idx_report_jobs_queue
    on report_jobs(priority desc, created_at)
    where status = 'queued';

-- Stale-claim sweep helper: find rows in 'running' state that have
-- exceeded the claim TTL. Partial because non-running rows are
-- irrelevant.
create index if not exists idx_report_jobs_running_claimed
    on report_jobs(claimed_at)
    where status = 'running';

-- ─── report_bug_reports ───────────────────────────────────────────

create table if not exists report_bug_reports (
    id            uuid primary key default gen_random_uuid(),
    report_id     uuid not null references report_jobs(id) on delete cascade,
    user_id       uuid not null references users(id) on delete cascade,
    message       text not null,
    status        text not null default 'open'
                      check (status in ('open','reviewing','resolved','dismissed')),
    admin_notes   text,
    created_at    timestamptz not null default now(),
    resolved_at   timestamptz
);

create index if not exists idx_report_bug_reports_status
    on report_bug_reports(status, created_at desc);

create index if not exists idx_report_bug_reports_report
    on report_bug_reports(report_id, created_at desc);
