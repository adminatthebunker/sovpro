# Premium Full Reports + Billing Rail

**Status:** Phase 1a shipped 2026-04-23 (billing rail + admin comp, Stripe-disabled-by-default). Phase 1b (report-generation pipeline) is the next build.
**Last updated:** 2026-04-25.

For load-bearing invariants, see [`CLAUDE.md`](../../CLAUDE.md) § Premium reports / billing rail. For operator procedures, see [`docs/runbooks/billing-rail-operations.md`](../runbooks/billing-rail-operations.md). For the dated phase-1a session handoff, see [`docs/archive/recovery-logs/handoff-2026-04-23-billing-rail-phase-1a.md`](../archive/recovery-logs/handoff-2026-04-23-billing-rail-phase-1a.md).

This doc is the design record: why this exists, what's locked, and what 1b looks like.

## Context

Today, the "Analyze for contradictions (AI)" button on every politician card in the grouped search view sends at most 10 chunks (in practice the 5 already rendered) to OpenRouter via `services/api/src/routes/contradictions.ts`. It is free, `requireUser`-gated so the shared free-tier quota isn't burned by anonymous traffic, and the UI framing ("the model suggests…") carries the legal hedge that no claim is a verdict.

This plan adds a **paid upgrade** alongside the existing free flow:

> **"Full report / Analyze everything"** — an LLM pass over every relevant speech a politician has made on the queried topic, rendered as an authenticated `/reports/<id>` HTML page, emailed to the user, and persisted in their account. Users hold **prepaid credits** purchased via Stripe; each report debits a cost proportional to the analysis it performs.

The report itself is the visible deliverable. The **strategic prize is the billing rail** — a one-time-payments credit ledger that every future premium feature (bulk exports, cross-politician comparison, premium search filters, developer-API tiers from `docs/plans/public-developer-api.md`) plugs into without a second billing redesign.

## Coordination with `public-developer-api.md`

Both plans need Stripe. Premium-reports shipped first, so it **laid the shared Stripe foundation** that the developer-API plan reuses later:

| Shared piece | Owned by | Notes |
|---|---|---|
| `users.stripe_customer_id` column | this plan (migration 0033) | Dev-API plan builds on top, no re-add. |
| `stripe_webhook_events` (idempotent event log) | this plan (migration 0033) | Handles both checkout-session and subscription webhook types out of the box. |
| `services/api/src/lib/stripe.ts` | this plan | Phase-1a exports: `getOrCreateCustomer`, `createCheckoutSession`, `constructWebhookEvent`. Dev-API plan adds `createPortalSession`. |
| Stripe config block in `services/api/src/config.ts` | this plan | Phase 1a: credit-pack price IDs; dev-API plan adds subscription price IDs. |
| `services/api/src/routes/stripe-webhook.ts` | this plan | Front door for all Stripe webhook types; dispatches by event type. Dev-API plan adds subscription-event branches. |

The credit ledger itself (what gets debited per report) is specific to premium-reports; subscriptions in the dev-API plan follow a separate `users.current_plan` model. Both are compatible on the same Stripe customer.

## Locked decisions

1. **Pricing model:** **credit packs** (one-time Stripe Checkout). No subscriptions, no portal, no prorations in v1.
2. **Report scope (v1):** **query-scoped** ("politician X on topic Y"). Full biographical brief deferred to v2+.
3. **LLM provider:** **OpenRouter paid tier, routed to Anthropic Claude.** Same client shape as `contradictions.ts`. Anthropic `cache_control` markers pass through — prompt caching remains available on the politician context. One-line model swap if we later want to change.
4. **Artifact format:** **HTML-only for v1.** Authenticated `/reports/<id>` page. Browser print-to-PDF is the user's responsibility. WeasyPrint-rendered PDF is a v2 item driven by first-customer feedback.
5. **Worker architecture:** **new `reports-worker` compose service** + `report_jobs` table. Mirrors `alerts_worker.py` for isolation.
6. **Pricing posture:** **lean premium** — easier to discount (promos, comps) than to hike. Numbers finalised after cost-formula calibration.
7. **Billing ledger discipline:** credit balance is **always derived** — `SUM(delta) WHERE state IN ('committed','held')`. Never a mutable `balance` column. Webhook idempotency enforced at the DB layer via unique partial indexes, not application-level check-then-insert.

## Phase sequencing

| Phase | Scope | Status | Why this order |
|---|---|---|---|
| **1a** | Migration 0033, Stripe lib, config, credits ledger helpers, webhook handler, user `/me/credits` routes, admin grant-credits endpoint, `/account/credits` page, admin comp-credits UI. **No report code.** | **Shipped 2026-04-23.** | Proves the billing rail in isolation. Bugs here are money bugs — easier to find without LLM pipeline noise. Sets foundation for every future premium feature. |
| **1b** | `reports-worker` compose service, `report_jobs` table, cost-estimate endpoint, LLM map-reduce pipeline, `/reports/<id>` HTML viewer, "Full report" button in `AIContradictionAnalysis.tsx`, failure UX, bug-report flow. | Next build. | First *spender* of credits. Depends on 1a's ledger. |
| **2+** | WeasyPrint PDF renderer, full biographical brief SKU, additional premium features (bulk exports, API access from `public-developer-api.md`, cross-politician comparison). | Deferred. | All reuse the phase-1a billing rail without modification. |

## Data model — migration `0033_billing_rail.sql` (shipped)

The migration is in `db/migrations/0033_billing_rail.sql`. Load-bearing pieces:

- **`users.stripe_customer_id`** (text, unique) — set on first checkout, reused for every subsequent purchase. Same Stripe customer feeds the dev-API plan's subscription rail later.
- **`users.rate_limit_tier`** (text, check-constrained to `'default' | 'extended' | 'unlimited' | 'suspended'`) — re-read every request by `requireUser` so flipping a user to `suspended` takes effect on their next request. Phase 1a only enforces `suspended`; per-tier rate ceilings are phase 1b.
- **`stripe_webhook_events`** — primary key on Stripe `event.id`. Inserted FIRST in the webhook handler; a duplicate event id is the **upstream dedup layer** that catches retries.
- **`credit_ledger`** — append-only. Balance is `SUM(delta) WHERE state IN ('committed','held')`. **Never** a mutable balance column. Partial unique index on `(kind, reference_id) WHERE reference_id IS NOT NULL` is the **downstream dedup layer** — even if the upstream webhook check fails open, this catches duplicate ledger entries for the same Stripe event.
- **`credit_purchases`** — one row per completed Stripe checkout. `raw_webhook` JSONB holds the full event for forensic use; do **not** return it from any HTTP response.
- **`rate_limit_increase_requests`** — one-pending-per-user (app-layer guard for now; switch to a partial unique index if abuse surfaces).

Phase 1b adds `report_jobs` and `report_bug_reports` in a separate migration (0034).

## Env vars (phase 1a, all shipped behind "unset → feature 503s")

Same ergonomics as `JWT_SECRET` / `OPENROUTER_API_KEY`:

- `STRIPE_SECRET_KEY` — server-side SDK key. Unset → `POST /me/credits/checkout` returns 503.
- `STRIPE_WEBHOOK_SECRET` — signature verification secret. Unset → webhook route refuses all events (200-discard, never 5xx).
- `STRIPE_PRICE_ID_CREDIT_PACK_SMALL` / `_MEDIUM` / `_LARGE` — one-time-payment prices created in the Stripe dashboard. Unset → corresponding pack is hidden on the frontend pack listing, not an error.
- `STRIPE_SUCCESS_URL` / `STRIPE_CANCEL_URL` — optional overrides; default to `${PUBLIC_SITE_URL}/account/credits?purchase=success|cancel`.

Phase 1b adds `OPENROUTER_REPORT_MODEL` (a higher-tier model id — `anthropic/claude-sonnet-4.6` or similar — distinct from the free-tier `OPENROUTER_MODEL` that powers `contradictions.ts`).

## Files (phase 1a)

The complete file list is in `CLAUDE.md` § Premium reports / billing rail § Files involved. Audit it from there rather than maintaining a parallel list here.

## Legal / trust framing (non-negotiable from round 1)

Every claim in a phase-1b report is linked back to the source quote (`/speeches/<id>` or a chunk-deep link). Paid reports ship with both an updated consent modal and an in-report footer:

> *This report is a model-generated synthesis of public Hansard records. Every claim below links back to a source quote; read the quotes before drawing conclusions. Canadian Political Data is not responsible for conclusions drawn from this brief.*

Phase 1a does not ship report UI, so the disclaimer copy lands in phase 1b. Round 3 of the planning cycle finalises the exact wording before deployment, ideally with a legal read.

## Verification + deployment

Phase 1a verification SQL, the test-mode → live-mode Stripe enablement sequence, the rollback procedure, and the nginx-rebuild gotcha all live in [`docs/runbooks/billing-rail-operations.md`](../runbooks/billing-rail-operations.md). They were operator procedures, not design rationale, so they belong in a runbook.

Phase 1b verification will land in this doc when 1b ships.

## Out of scope for phase 1a

- Report generation (phase 1b).
- PDF output (v2+).
- Stripe Tax / Canadian GST/HST compliance (required before public launch — addressed in a pre-launch pass).
- Report retention / deletion policy (phase 1b decision).
- Subscription products (owned by `public-developer-api.md`; billing rail here is one-time-payment only).

## Open questions (gating phase 1b)

Architecture is locked; these are numbers + copy:

1. **Credit-pack SKU final numbers.** Round-2 starter: $5 → 50 credits, $20 → 250 credits (12 % bonus), $50 → 700 credits (17 % bonus). User has said "lean premium." Confirm or adjust before creating Stripe products.
2. **Cost-per-report formula.** `credits = ceil(chunks / K) × map_cost + reduce_flat`. K depends on OpenRouter→Claude Sonnet token pricing × typical chunk counts. Run the math against real DB distributions:
   ```sql
   SELECT percentile_cont(array[0.5, 0.9, 0.99]) WITHIN GROUP (ORDER BY chunk_count) AS p50_p90_p99
     FROM (
       SELECT politician_id, COUNT(*) AS chunk_count
         FROM speech_chunks
        WHERE politician_id IS NOT NULL
        GROUP BY politician_id
     ) t;
   ```
   Plus a second query bounded by a typical query embedding for concrete distributions.
3. **Rate-limit defaults.** Round-2 starter: 5 reports/day + 200 credits/hour in `default` tier; `extended` tier bumps to 20/day + 1000/hour. Values live in code config (not DB) — trivial to adjust.
4. **Disclaimer copy.** Paid reports need consent-modal text + in-report footer text. Should get a legal read before public launch.
5. **Admin-comp UI placement.** Currently embedded in `/admin/users`. Consider whether it should have a dedicated page; current placement is fine for the volume we expect.
