# Premium Full Reports + Billing Rail

**Status:** Approved, phase 1a in progress.
**Last updated:** 2026-04-23.

## Context

Today, the "Analyze for contradictions (AI)" button on every politician card in the grouped search view sends at most 10 chunks (in practice the 5 already rendered) to OpenRouter via `services/api/src/routes/contradictions.ts`. It is free, `requireUser`-gated so the shared free-tier quota isn't burned by anonymous traffic, and the UI framing ("the model suggests…") carries the legal hedge that no claim is a verdict.

This plan adds a **paid upgrade** alongside the existing free flow:

> **"Full report / Analyze everything"** — an LLM pass over every relevant speech a politician has made on the queried topic, rendered as an authenticated `/reports/<id>` HTML page, emailed to the user, and persisted in their account. Users hold **prepaid credits** purchased via Stripe; each report debits a cost proportional to the analysis it performs.

The report itself is the visible deliverable. The **strategic prize is the billing rail** — a one-time-payments credit ledger that every future premium feature (bulk exports, cross-politician comparison, premium search filters, developer-API tiers from `docs/plans/public-developer-api.md`) plugs into without a second billing redesign.

## Coordination with `public-developer-api.md`

Both plans need Stripe. Premium-reports ships first, so it **lays the shared Stripe foundation** that the developer-API plan reuses later:

| Shared piece | Owned by | Notes |
|---|---|---|
| `users.stripe_customer_id` column | this plan (migration 0033) | Dev-API plan builds on top, no re-add. |
| `stripe_webhook_events` (idempotent event log) | this plan (migration 0033) | Handles both checkout-session and subscription webhook types out of the box. |
| `services/api/src/lib/stripe.ts` | this plan | Starts with `getOrCreateCustomer`, `createCheckoutSession(userId, priceId)`, `constructWebhookEvent`. Dev-API plan adds `createPortalSession`. |
| Stripe config block in `services/api/src/config.ts` | this plan | Starts with credit-pack price IDs; dev-API plan adds subscription price IDs. |
| `services/api/src/routes/stripe-webhook.ts` | this plan | Front door for all Stripe webhook types; dispatches by event type. Dev-API plan adds subscription-event branches. |

The credit ledger itself (what gets debited per report) is specific to premium-reports; subscriptions in the dev-API plan follow a separate `users.current_plan` model. Both are compatible on the same Stripe customer.

## Audit — what already exists in the repo

Greenfield for this feature. No Stripe SDK, no credits column, no PDF renderer, no user-report artifacts beyond `saved_searches`. Only precedent worth reusing:

- **`services/scanner/src/alerts_worker.py`** — structure for a user-triggered long-running poller. New `reports_worker.py` mirrors it.
- **`services/api/src/routes/contradictions.ts`** — the free-tier LLM flow; consent modal and "the model suggests…" framing pattern carry forward.
- **`services/api/src/lib/auth-token.ts`** — swap-seam pattern; `lib/stripe.ts` adopts it.
- **`services/api/src/middleware/user-auth.ts`** — `requireUser` + `requireAdmin` + CSRF pattern reused on every new route.

## Locked decisions

1. **Pricing model:** **credit packs** (one-time Stripe Checkout). No subscriptions, no portal, no prorations in v1.
2. **Report scope (v1):** **query-scoped** ("politician X on topic Y"). Full biographical brief deferred to v2+.
3. **LLM provider:** **OpenRouter paid tier, routed to Anthropic Claude.** Same client shape as `contradictions.ts`. Anthropic `cache_control` markers pass through — prompt caching remains available on the politician context. One-line model swap if we later want to change.
4. **Artifact format:** **HTML-only for v1.** Authenticated `/reports/<id>` page. Browser print-to-PDF is the user's responsibility. WeasyPrint-rendered PDF is a v2 item driven by first-customer feedback.
5. **Worker architecture:** **new `reports-worker` compose service** + `report_jobs` table. Mirrors `alerts_worker.py` for isolation.
6. **Pricing posture:** **lean premium** — easier to discount (promos, comps) than to hike. Numbers finalised after cost-formula calibration.
7. **Billing ledger discipline:** credit balance is **always derived** — `SUM(delta) WHERE state IN ('committed','held')`. Never a mutable `balance` column. Webhook idempotency enforced at the DB layer via unique partial indexes, not application-level check-then-insert.

## Phase sequencing

| Phase | Scope | Ships | Why this order |
|---|---|---|---|
| **1a** | Migration 0033, Stripe lib, config, credits ledger helpers, webhook handler, user `/me/credits` routes, admin grant-credits endpoint, `/account/credits` page, admin comp-credits UI. **No report code.** | Live "buy credits / see balance" flow. | Proves the billing rail in isolation. Bugs here are money bugs — easier to find without LLM pipeline noise. Sets foundation for every future premium feature. |
| **1b** | `reports-worker` compose service, `report_jobs` table, cost-estimate endpoint, LLM map-reduce pipeline, `/reports/<id>` HTML viewer, "Full report" button in `AIContradictionAnalysis.tsx`, failure UX, bug-report flow. | First revenue-earning feature. | First *spender* of credits. Depends on 1a's ledger. |
| **2+** | WeasyPrint PDF renderer, full biographical brief SKU, additional premium features (bulk exports, API access from `public-developer-api.md`, cross-politician comparison). | Growth features. | All reuse the phase-1a billing rail without modification. |

## Data model — migration `0033_billing_rail.sql`

```sql
-- Users get two new columns.
alter table users add column stripe_customer_id text unique;
alter table users add column rate_limit_tier text not null default 'default'
    check (rate_limit_tier in ('default','extended','unlimited','suspended'));

-- stripe_webhook_events is the idempotent-dispatch log. Every Stripe
-- webhook event id is inserted here FIRST; a duplicate insert (same
-- event.id) means we've already processed it and the handler returns
-- 200 without reprocessing. This is the upstream dedup layer.
create table stripe_webhook_events (
    id              text primary key,                -- Stripe event.id
    type            text not null,                   -- checkout.session.completed, etc.
    received_at     timestamptz not null default now(),
    processed_at    timestamptz,
    raw_payload     jsonb not null
);
create index idx_stripe_webhook_events_type_time
    on stripe_webhook_events(type, received_at desc);

-- credit_ledger is immutable append-only. Balance is always SUM(delta)
-- over (state IN ('committed','held')). Never add a mutable balance
-- column. The unique partial index on (kind, reference_id) is the
-- downstream dedup layer — even if the upstream webhook dedup fails,
-- we cannot double-credit a single Stripe checkout.
create table credit_ledger (
    id                  uuid primary key default gen_random_uuid(),
    user_id             uuid not null references users(id) on delete cascade,
    delta               integer not null,            -- +N for grants/purchases, -N for holds/debits
    state               text not null
                            check (state in ('pending','held','committed','refunded')),
    kind                text not null
                            check (kind in (
                                'stripe_purchase',
                                'admin_credit',
                                'report_hold',
                                'report_commit',
                                'report_refund'
                            )),
    reference_id        text,                        -- stripe checkout id, report_jobs.id, etc.
    reason              text,                        -- admin-supplied note (comps, refunds)
    created_by_admin_id uuid references users(id),   -- populated on kind='admin_credit'
    created_at          timestamptz not null default now()
);
create index idx_credit_ledger_user_time
    on credit_ledger(user_id, created_at desc);
create unique index uniq_credit_ledger_kind_ref
    on credit_ledger(kind, reference_id)
    where reference_id is not null;

-- credit_purchases records Stripe Checkout completions. Mirrors the
-- relevant fields from the webhook event for auditing. raw_webhook
-- carries the full event for future forensic needs.
create table credit_purchases (
    id                        uuid primary key default gen_random_uuid(),
    user_id                   uuid not null references users(id) on delete cascade,
    stripe_checkout_id        text not null unique,
    stripe_payment_intent_id  text,
    amount_cents              integer not null,
    currency                  text not null,
    credits_granted           integer not null,
    ledger_entry_id           uuid references credit_ledger(id),
    status                    text not null
                                  check (status in ('pending','completed','refunded','failed')),
    raw_webhook               jsonb not null,
    created_at                timestamptz not null default now(),
    updated_at                timestamptz not null default now()
);
create trigger trg_credit_purchases_touch before update on credit_purchases
    for each row execute function touch_updated_at();
create index idx_credit_purchases_user
    on credit_purchases(user_id, created_at desc);

-- rate_limit_increase_requests surfaces rate-limited users to the
-- admin. Admin decides case-by-case and bumps users.rate_limit_tier.
create table rate_limit_increase_requests (
    id                uuid primary key default gen_random_uuid(),
    user_id           uuid not null references users(id) on delete cascade,
    reason            text not null,
    requested_tier    text not null default 'extended',
    status            text not null default 'pending'
                          check (status in ('pending','approved','denied')),
    admin_response    text,
    resolved_by       uuid references users(id),
    created_at        timestamptz not null default now(),
    resolved_at       timestamptz
);
create index idx_rate_limit_requests_status
    on rate_limit_increase_requests(status, created_at desc);
```

Phase 1b adds `report_jobs` and `report_bug_reports` in a separate migration (0034).

## New env vars (phase 1a)

Added to `services/api/src/config.ts` behind the usual "unset → feature 503s" pattern (same ergonomics as `JWT_SECRET`, `OPENROUTER_API_KEY`):

- `STRIPE_SECRET_KEY` — server-side SDK key. Unset → `POST /me/credits/checkout` returns 503.
- `STRIPE_WEBHOOK_SECRET` — signature verification secret. Unset → webhook route refuses all events.
- `STRIPE_PRICE_ID_CREDIT_PACK_SMALL` / `_MEDIUM` / `_LARGE` — one-time-payment prices created in the Stripe dashboard. Unset → corresponding pack is hidden on the frontend pack listing, not an error.
- `STRIPE_SUCCESS_URL` / `STRIPE_CANCEL_URL` — optional overrides; default to `${PUBLIC_SITE_URL}/account/credits?purchase=success|cancel`.

Phase 1b adds `OPENROUTER_REPORT_MODEL` (a higher-tier model id — `anthropic/claude-sonnet-4.6` or similar — distinct from the free-tier `OPENROUTER_CONTRADICTIONS_MODEL` that powers `contradictions.ts`; the latter is the canonical name for what was originally `OPENROUTER_MODEL`, which is still read as a deprecated fallback).

## Files added / modified — phase 1a

| File | Concern |
|---|---|
| `db/migrations/0033_billing_rail.sql` | Schema above. |
| `services/api/package.json` | Add `stripe` dependency. |
| `services/api/src/config.ts` | Add Stripe env block with `enabled` flag. |
| `services/api/src/lib/stripe.ts` | Lazy-initialised SDK wrapper. Exports `getOrCreateCustomer(user)`, `createCheckoutSession(userId, priceId)`, `constructWebhookEvent(rawBody, signature)`. Reused by the dev-API plan later. |
| `services/api/src/lib/credits.ts` | Ledger helpers: `getBalance`, `holdCredits`, `commitHold`, `releaseHold`, `grantStripePurchase`, `grantAdminCredit`. All single-statement or single-transaction. |
| `services/api/src/routes/credits.ts` | `GET /me/credits` (balance + recent history), `GET /me/credits/packs` (public pack listing), `POST /me/credits/checkout` (create Checkout Session). All gated on `requireUser` + `requireCsrf` where mutating. |
| `services/api/src/routes/stripe-webhook.ts` | `POST /webhooks/stripe`. Raw-body preserved for signature verification. Idempotent via `stripe_webhook_events` + `credit_ledger` unique index. |
| `services/api/src/routes/admin.ts` | Add `GET /admin/users` (search by email) and `POST /admin/users/:id/grant-credits` (comp). Inserts `credit_ledger` row with `kind='admin_credit'` + `created_by_admin_id`. |
| `services/api/src/index.ts` | Register new routes. Raw-body override for the webhook path. |
| `services/frontend/src/pages/AccountPage.tsx` | Add "Your credits" link + balance chip. |
| `services/frontend/src/pages/CreditsPage.tsx` | New `/account/credits` route. Balance, pack selection, ledger history table. |
| `services/frontend/src/pages/admin/AdminGrantCredits.tsx` | New admin page: user search → enter amount + reason → grant. |
| `services/frontend/src/api.ts` (or equivalent) | New typed client methods. |
| `services/frontend/src/main.tsx` | Register new routes. |
| `.env.example` | New Stripe env vars with placeholder comments. |
| `CLAUDE.md` | Add a "Premium reports / billing rail" section alongside the existing User accounts / Admin panel sections. |

## Legal / trust framing (non-negotiable from round 1)

Every claim in a phase-1b report is linked back to the source quote (`/speeches/<id>` or a chunk-deep link). Paid reports ship with both an updated consent modal and an in-report footer:

> *This report is a model-generated synthesis of public Hansard records. Every claim below links back to a source quote; read the quotes before drawing conclusions. Canadian Political Data is not responsible for conclusions drawn from this brief.*

Phase 1a does not ship report UI, so the disclaimer copy lands in phase 1b. Round 3 of the planning cycle finalises the exact wording before deployment, ideally with a legal read.

## Verification — phase 1a

1. **Migration applies cleanly** against sw-db with no constraint errors:
   ```bash
   docker exec -i sw-db psql -U sw -d sovereignwatch -v ON_ERROR_STOP=1 \
     < db/migrations/0033_billing_rail.sql
   ```
2. **Balance derivation**:
   ```sql
   -- After a grant + hold, balance should reflect both.
   insert into credit_ledger (user_id, delta, state, kind, reason)
     values ($1, 100, 'committed', 'admin_credit', 'test');
   insert into credit_ledger (user_id, delta, state, kind, reference_id)
     values ($1, -30, 'held', 'report_hold', 'job-test-1');
   select sum(delta) from credit_ledger
     where user_id = $1 and state in ('committed','held');
   -- Expect: 70
   ```
3. **Webhook idempotency**: insert two `stripe_webhook_events` rows with the same `id` — second must fail with primary-key violation. Insert two `credit_ledger` rows with the same `(kind='stripe_purchase', reference_id='cs_test_…')` — second must fail with unique-index violation.
4. **Admin comp flow**: `grantAdminCredit` inserts a ledger row with `kind='admin_credit'` + `created_by_admin_id` set; non-admin callers get 403; admin-auth removed mid-session takes effect on the next request (per `requireAdmin`'s re-read pattern).
5. **Stripe test-mode checkout** (requires test keys in env):
   - Click "Buy $5 pack" → Stripe Checkout opens → complete with `4242 4242 4242 4242`
   - Webhook fires → `stripe_webhook_events` row + `credit_purchases` row + `credit_ledger` row (kind `stripe_purchase`) all created
   - Balance reflected on `/account/credits`
   - Replay the same webhook event (Stripe CLI `stripe trigger`) → no duplicate rows, webhook returns 200
6. **Rate-limit-increase request**: submitting the form creates a `pending` row; admin approving it updates `users.rate_limit_tier` and marks the request `approved`.

Verification for phase 1b lives in that phase's eventual plan section.

## Out of scope for phase 1a

- Report generation (phase 1b).
- PDF output (v2+).
- Stripe Tax / Canadian GST-HST compliance (required before public launch — addressed in a pre-launch pass).
- Report retention / deletion policy (phase 1b decision).
- Subscription products (owned by `public-developer-api.md`; billing rail here is one-time-payment only).

## Open questions for phase 1a

- **Credit-pack SKU final numbers.** Starter proposal: $5 → 50 credits, $20 → 250 credits, $50 → 700 credits. User confirmation needed before Stripe products are created.
- **Rate-limit defaults.** Starter proposal: 5 reports/day, 200 credits/hour in the `default` tier. `extended` tier bumps to 20/day + 1000/hour. These values live in code config, not in the DB — trivial to adjust.

## Production deployment sequence

This project runs live on a single production host (Pangolin-tunnelled to the public). Every `docker compose up --build` is a real deploy with ~30s of visible downtime per restarted service. The phase-1a code is deliberately **Stripe-disabled by default** so the code + schema can ship independently of the payment layer.

### Pre-deploy checklist (operator)

1. **Back up the DB.** Migration 0033 is additive (columns + new tables, no destructive DDL) but any prod deploy should have a recent snapshot.
   ```bash
   sovpro db backup       # writes backups/<ts>.sql.gz
   ```
2. **Tighten CORS in `.env`.** The shipped default in `config.ts` is `https://canadianpoliticaldata.ca`, but an existing `API_CORS_ORIGIN=*` line in the operator's `.env` overrides it. Edit `.env` to match the default (or delete the line) before restart.
3. **Confirm `STRIPE_*` vars are unset.** The first rollout deliberately ships without Stripe — the UI renders "no packs available," the webhook 200-discards, and no payment surface is exposed. Verify with:
   ```bash
   grep -E "^STRIPE_" .env           # should return nothing, or only blank values
   ```
4. **Pick a low-traffic window** for the restart. Kuma dashboard is the reference for current request volume.

### Deploy (no Stripe, phase 1a code only)

Migration 0033 is already applied in prod (landed 2026-04-23). The remaining step is to build and restart the services carrying the new code:

```bash
docker compose build api frontend
docker compose up -d api frontend
docker compose logs -f api   # watch for the "API listening" line and no config warnings
```

Expected post-restart state:
- `/account/credits` renders (signed-in users see balance `0`, no pack buttons).
- `/admin/users` renders for admins with the user picker + grant form.
- `GET /api/v1/me/credits` returns `{ balance: 0, history: [], stripe_enabled: false }` for any signed-in user.
- `POST /api/v1/webhooks/stripe` returns `{ received: false, reason: "stripe not configured" }` with HTTP 200 if hit externally.

### Rollback (if needed)

1. `docker compose rollback` is not a thing. Rollback = rebuild the prior git SHA:
   ```bash
   git checkout <previous-sha> -- services/api services/frontend
   docker compose build api frontend
   docker compose up -d api frontend
   ```
2. The migration stays applied. All new tables are independent — removing the code doesn't leave dangling references. If the DB must be rolled back too, `DROP TABLE rate_limit_increase_requests, credit_purchases, credit_ledger, stripe_webhook_events; ALTER TABLE users DROP COLUMN stripe_customer_id, DROP COLUMN rate_limit_tier;` (in that order).

### Stripe enablement (separate follow-up deploy)

Shipped as its own deploy, not bundled with the phase-1a restart. Sequence:

1. **Test mode first**: create Stripe account (if not already), create three one-time products in test mode, note the `price_…` ids. Add to `.env`:
   ```
   STRIPE_SECRET_KEY=sk_test_…
   STRIPE_WEBHOOK_SECRET=whsec_test_…
   STRIPE_PRICE_ID_CREDIT_PACK_SMALL=price_…
   STRIPE_PRICE_ID_CREDIT_PACK_MEDIUM=price_…
   STRIPE_PRICE_ID_CREDIT_PACK_LARGE=price_…
   ```
2. **Register the webhook endpoint** in Stripe dashboard pointing to `https://canadianpoliticaldata.ca/api/v1/webhooks/stripe`. Select only `checkout.session.completed` for event types (phase 1a handles no other types). Copy the signing secret into `STRIPE_WEBHOOK_SECRET`.
3. **Restart API only** (no frontend change needed):
   ```bash
   docker compose up -d api
   ```
4. **End-to-end smoke test**:
   - Sign in with a test account at `/login` → `/account/credits` → buy the small pack.
   - Use test card `4242 4242 4242 4242`, any future CVC + expiry.
   - Stripe redirects to `/account/credits?purchase=success`.
   - Within seconds, `stripe_webhook_events` should have a fresh row with `processed_at` set, `credit_purchases` a `completed` row, `credit_ledger` a `committed` row with the catalog credit amount.
   - The balance chip + history table on the page should update after the one-shot 2-second poll.
5. **Idempotency check**: from the Stripe dashboard → Webhook attempts, manually "Resend" the `checkout.session.completed` event. The API should respond 200 with `{duplicate: true}` and no new rows should appear in the DB.
6. **Flip to live mode**: once (4) and (5) pass, replace the `sk_test_…`, `whsec_test_…`, and price IDs with their live-mode equivalents, register a second webhook endpoint in live mode, restart the API again.
7. **Tax**: Stripe Tax for Canadian GST-HST compliance is not wired in this phase. Confirm with accounting before taking real payments; Stripe Tax can be enabled per-product in the dashboard with no code change.
