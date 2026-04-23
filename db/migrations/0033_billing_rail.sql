-- Billing rail phase 1a: Stripe customer linkage + credit ledger.
--
-- This is the foundation for the premium-reports feature (see
-- docs/plans/premium-reports.md) and the dev-API subscription tiers
-- sketched in docs/plans/public-developer-api.md. It ships with NO
-- report code — the goal of phase 1a is to prove the money plumbing
-- in isolation (buy credits, see balance, admin can comp) before any
-- LLM pipeline complexity enters the picture.
--
-- Five objects added:
--
-- 1. users.stripe_customer_id — one Stripe customer per user, shared
--    across premium-reports (one-time credit purchases) and the future
--    developer-API plan (subscription purchases). Nullable: created on
--    first purchase attempt, not at signup.
--
-- 2. users.rate_limit_tier — controls how many reports/credits a user
--    can spend per window. Default in code maps to sensible starter
--    limits; admin can bump an individual user to 'extended' /
--    'unlimited' via the admin comp UI. 'suspended' is the safety
--    valve for abuse.
--
-- 3. stripe_webhook_events — idempotent-dispatch log. Every Stripe
--    webhook event id is inserted here FIRST. A duplicate insert
--    (Stripe retries on 5xx or network hiccups) fails with a PK
--    violation and the handler returns 200 without reprocessing.
--    This is the UPSTREAM dedup layer.
--
-- 4. credit_ledger — immutable append-only money ledger. Balance is
--    derived, never cached: SUM(delta) WHERE state IN
--    ('committed','held'). Held credits debit the visible spendable
--    balance but can still be refunded (released back to committed)
--    on report-job failure. The unique partial index on
--    (kind, reference_id) is the DOWNSTREAM dedup layer: even if the
--    upstream webhook dedup above fails, a single Stripe checkout
--    session cannot grant credits twice.
--
-- 5. credit_purchases — audit record of Stripe Checkout completions,
--    one row per successful checkout.session.completed. Carries the
--    full webhook payload for forensic debugging.
--
-- 6. rate_limit_increase_requests — user-submitted "I need more than
--    the default limit" requests. Admin reviews in the admin panel,
--    bumps users.rate_limit_tier on approval, logs the rationale in
--    admin_response for audit.

-- ─── users: billing-related columns ───────────────────────────────

alter table users
    add column if not exists stripe_customer_id text unique;

alter table users
    add column if not exists rate_limit_tier text not null default 'default'
        check (rate_limit_tier in ('default','extended','unlimited','suspended'));

-- ─── stripe_webhook_events: upstream idempotency ──────────────────

create table if not exists stripe_webhook_events (
    id            text primary key,                 -- Stripe event.id (e.g. "evt_1Abc…")
    type          text not null,                    -- "checkout.session.completed" etc.
    received_at   timestamptz not null default now(),
    processed_at  timestamptz,
    error_message text,                             -- populated if processing failed permanently
    raw_payload   jsonb not null
);

create index if not exists idx_stripe_webhook_events_type_time
    on stripe_webhook_events(type, received_at desc);

-- ─── credit_ledger: immutable append-only money ledger ────────────

create table if not exists credit_ledger (
    id                  uuid primary key default gen_random_uuid(),
    user_id             uuid not null references users(id) on delete cascade,

    -- Positive for grants/purchases, negative for holds/debits. A
    -- refund is modelled as the hold row flipping state from 'held'
    -- to 'refunded' (the refunded delta then drops out of the balance
    -- sum), NOT as a separate positive row. Keeps the audit trail
    -- one-row-per-event.
    delta               integer not null,

    state               text not null
                            check (state in ('pending','held','committed','refunded')),

    kind                text not null
                            check (kind in (
                                'stripe_purchase',  -- +N on checkout.session.completed
                                'admin_credit',     -- +N from admin comp
                                'report_hold',      -- -N when a report job is queued
                                'report_commit',    -- marker entry when a hold commits (no delta change, state flip)
                                'report_refund'     -- marker entry when a hold refunds (no delta change, state flip)
                            )),

    -- External key this row ties to. For stripe_purchase: the Stripe
    -- checkout_session id. For report_hold/commit/refund: the
    -- report_jobs.id. For admin_credit: free-text (e.g. support
    -- ticket ref) or null.
    reference_id        text,

    -- Admin-supplied note for comps, refunds, and manual
    -- interventions. User-visible in the /account/credits history
    -- for admin_credit and report_refund rows.
    reason              text,

    -- Populated only for kind='admin_credit' — lets the admin panel
    -- filter "credits granted by me" and preserves audit attribution
    -- even if the granting admin is later demoted.
    created_by_admin_id uuid references users(id),

    created_at          timestamptz not null default now()
);

create index if not exists idx_credit_ledger_user_time
    on credit_ledger(user_id, created_at desc);

-- Downstream idempotency: a single Stripe checkout can only grant
-- credits once. A single report_jobs row can only have one hold,
-- one commit, one refund. Partial index skips rows with null
-- reference_id (e.g. admin_credit rows without a reference).
create unique index if not exists uniq_credit_ledger_kind_ref
    on credit_ledger(kind, reference_id)
    where reference_id is not null;

-- Hot path: balance lookups filter by user_id + state. Supports
-- "held" rows reporting for the user's /account/credits view.
create index if not exists idx_credit_ledger_balance
    on credit_ledger(user_id, state)
    where state in ('committed','held');

-- ─── credit_purchases: Stripe Checkout completions ────────────────

create table if not exists credit_purchases (
    id                        uuid primary key default gen_random_uuid(),
    user_id                   uuid not null references users(id) on delete cascade,
    stripe_checkout_id        text not null unique,         -- e.g. "cs_test_…"
    stripe_payment_intent_id  text,                          -- e.g. "pi_test_…"
    amount_cents              integer not null,
    currency                  text not null,                 -- "cad" / "usd" / …
    credits_granted           integer not null,
    ledger_entry_id           uuid references credit_ledger(id),
    status                    text not null
                                  check (status in ('pending','completed','refunded','failed')),
    raw_webhook               jsonb not null,
    created_at                timestamptz not null default now(),
    updated_at                timestamptz not null default now()
);

create trigger trg_credit_purchases_touch
    before update on credit_purchases
    for each row execute function touch_updated_at();

create index if not exists idx_credit_purchases_user
    on credit_purchases(user_id, created_at desc);

-- ─── rate_limit_increase_requests ─────────────────────────────────

create table if not exists rate_limit_increase_requests (
    id              uuid primary key default gen_random_uuid(),
    user_id         uuid not null references users(id) on delete cascade,
    reason          text not null,
    requested_tier  text not null default 'extended'
                        check (requested_tier in ('extended','unlimited')),
    status          text not null default 'pending'
                        check (status in ('pending','approved','denied')),
    admin_response  text,
    resolved_by     uuid references users(id),
    created_at      timestamptz not null default now(),
    resolved_at     timestamptz
);

create index if not exists idx_rate_limit_requests_status
    on rate_limit_increase_requests(status, created_at desc);

create index if not exists idx_rate_limit_requests_user
    on rate_limit_increase_requests(user_id, created_at desc);
