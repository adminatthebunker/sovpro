# Billing rail — operations

Operator-facing procedures for the credit-ledger / Stripe-Checkout billing rail.

- **Design context:** [`docs/plans/premium-reports.md`](../plans/premium-reports.md).
- **Load-bearing invariants:** [`CLAUDE.md`](../../CLAUDE.md) § Premium reports / billing rail. Read those before editing anything in this area — the ledger gets incoherent quickly if any are violated.
- **HTTP shapes:** [`docs/api.md`](../api.md) § Credits / Rate-limit / Stripe webhook / Admin user management.

This file is **procedures only.** No design rationale, no architectural framing.

## Quick health check

```bash
# DB schema present?
docker exec sw-db psql -U sw -d sovereignwatch -At -c "
  SELECT table_name FROM information_schema.tables
   WHERE table_schema='public'
     AND table_name IN ('stripe_webhook_events','credit_ledger',
                        'credit_purchases','rate_limit_increase_requests')
   ORDER BY table_name;"
# Expected: 4 rows.

# Webhook route responds correctly when Stripe is unconfigured?
curl -sS -w "\nHTTP %{http_code}\n" http://localhost:8088/api/v1/webhooks/stripe \
  -X POST -H "Content-Type: application/json" -d '{}'
# Expected: HTTP 200 {"received":false,"reason":"stripe not configured"}.
```

## Enabling Stripe (test mode → live mode)

The phase-1a code ships Stripe-disabled by default. **Do NOT flip to live mode without passing the test-mode smoke first.** Any webhook-handler bug in live mode corresponds to real customer money landing in a broken ledger.

### Test mode

1. Create a Stripe account if you don't have one. Create three one-time products in **test mode** ($5 / $20 / $50) — note each `price_…` ID.
2. Fill `.env`:
   ```
   STRIPE_SECRET_KEY=sk_test_…
   STRIPE_PRICE_ID_CREDIT_PACK_SMALL=price_…
   STRIPE_PRICE_ID_CREDIT_PACK_MEDIUM=price_…
   STRIPE_PRICE_ID_CREDIT_PACK_LARGE=price_…
   STRIPE_WEBHOOK_SECRET=                 # leave blank, populated in step 4
   ```
3. `docker compose up -d api` (frontend doesn't need a rebuild — the packs endpoint drives the UI). Reload nginx — see § Nginx rebuild gotcha below.
4. In Stripe dashboard, register the webhook endpoint at `https://canadianpoliticaldata.ca/api/v1/webhooks/stripe`. Select **only** `checkout.session.completed`. Copy the signing secret into `STRIPE_WEBHOOK_SECRET`. `docker compose up -d api` again; reload nginx.
5. Exercise the buy flow with card `4242 4242 4242 4242`, any future CVC + expiry. Verify rows appear in `stripe_webhook_events`, `credit_purchases`, `credit_ledger`. Re-send the same webhook from the Stripe dashboard → API should respond 200 with `{duplicate: true}` and **no new DB rows**.

### Live mode

Only after test mode passes:

1. Create live-mode versions of the three products in Stripe. Note the new `price_…` IDs (different from test mode).
2. Replace `sk_test_…` with `sk_live_…` and the test-mode webhook secret with the live one. Replace the three `STRIPE_PRICE_ID_*` values.
3. Register a second webhook endpoint in **live mode** in Stripe dashboard.
4. `docker compose up -d api`; reload nginx.
5. **Tax:** Stripe Tax for Canadian GST/HST is not wired in this phase. Confirm with accounting before taking real payments. Stripe Tax can be enabled per-product in the dashboard with no code change.

## Admin comp / grant credits

Admin UI route: `/admin/users` → search by email → Open user → "Grant credits (comp)" form. Amount 1–100,000; reason is user-visible in their `/account/credits` history (write it for them, not for yourself).

The form posts to:

```
POST /api/v1/admin/users/<user-id>/grant-credits
Content-Type: application/json
Cookie: sw_session=…
X-CSRF-Token: …

{ "amount": 100, "reason": "comp for early-access feedback" }
```

Inserts a `credit_ledger` row with `kind='admin_credit'` and `created_by_admin_id` set. **Never** bypass this by inserting ledger rows directly via psql except in the disaster-recovery flow below.

Audit trail:
```sql
SELECT * FROM credit_ledger WHERE kind='admin_credit' ORDER BY created_at DESC;
```

## Suspending a user

`/admin/users` → search → Open → "Rate-limit tier" dropdown → `suspended` → blur. Takes effect on the user's next request — `requireUser` re-reads the tier on every request.

Direct-SQL alternative if the admin UI is unavailable:

```sql
UPDATE users SET rate_limit_tier = 'suspended' WHERE email = 'abuser@example.com';
-- to unsuspend:
UPDATE users SET rate_limit_tier = 'default'   WHERE email = '…';
```

## Rotating the Stripe webhook signing secret

1. In Stripe dashboard → Developers → Webhooks → your endpoint → "Roll signing secret."
2. Copy the new `whsec_…` value.
3. Update `.env` `STRIPE_WEBHOOK_SECRET=whsec_<new>`.
4. `docker compose up -d api` (api restart only; the SDK picks up the new secret at boot). Reload nginx.

Stripe gives a 24h overlap window where both old and new secrets validate, so the brief restart pause is fine.

## Verifying a user's balance

```sql
SELECT COALESCE(SUM(delta), 0) AS balance
  FROM credit_ledger
 WHERE user_id = (SELECT id FROM users WHERE email = 'you@example.com')
   AND state IN ('committed','held');
```

`held` rows contribute their negative delta — the result is **spendable** balance, not gross grant total.

## Disaster: "the ledger is wrong"

The ledger is append-only by discipline. **Never** `UPDATE credit_ledger SET delta = …`. Every correction is a new row:

```sql
-- Refund 50 credits to a user after a failed report,
-- outside the automatic hold-release path.
INSERT INTO credit_ledger
       (user_id, delta, state, kind, reason, created_by_admin_id)
VALUES ($user_id, 50, 'committed', 'admin_credit',
        'Manual refund — report #xxx hung', $admin_id);
```

Common scenarios:

- **User says "I bought $20 of credits but only see $5."** Investigate the Stripe checkout — confirm the SKU. The `PACK_CREDITS` catalog is the source of truth, not `metadata.credits`. If a real bug under-credited a user, insert an `admin_credit` row with a `reason` referencing the support thread.
- **User wants a refund.** Refund in Stripe (dashboard or `POST /v1/refunds`); the `charge.refunded` webhook will mark the matching `credit_ledger` row's `state` to `'refunded'` (drops out of balance). For partial refunds, do an `admin_credit` row with negative `delta` referencing the original purchase.

## Nginx rebuild gotcha

When `docker compose up -d --build api` (or `frontend`) recreates the container, its internal IP changes. Nginx resolves `api:3000` once at startup and pins the IP, so all requests return `502` until you reload:

```bash
docker exec sw-nginx nginx -s reload
```

This is the standard procedure after any rebuild of `api` or `frontend`. A permanent fix would be to add `resolver 127.0.0.11 valid=10s;` in nginx config and reference the upstream via a variable so nginx re-resolves per request — left as a future improvement.

## Verification SQL

After any DB-touching change to the billing rail, replay these. All three should pass:

```sql
-- 1. Balance derivation.
INSERT INTO credit_ledger (user_id, delta, state, kind, reason)
  VALUES ($1, 100, 'committed', 'admin_credit', 'verification test');
INSERT INTO credit_ledger (user_id, delta, state, kind, reference_id)
  VALUES ($1, -30, 'held', 'report_hold', 'job-test-1');
SELECT SUM(delta) FROM credit_ledger
  WHERE user_id = $1 AND state IN ('committed','held');
-- Expect: 70.

-- 2. Webhook idempotency, downstream layer.
INSERT INTO credit_ledger (user_id, delta, state, kind, reference_id)
  VALUES ($1, 50, 'committed', 'stripe_purchase', 'cs_test_dup');
INSERT INTO credit_ledger (user_id, delta, state, kind, reference_id)
  VALUES ($1, 50, 'committed', 'stripe_purchase', 'cs_test_dup');
-- Expect second insert to fail with unique-violation on
-- uniq_credit_ledger_kind_ref.

-- 3. Suspended-tier enforcement.
UPDATE users SET rate_limit_tier = 'suspended' WHERE id = $1;
-- Now any authenticated request from this user returns 403 instantly.
UPDATE users SET rate_limit_tier = 'default' WHERE id = $1;
-- Restored.
```

## What not to do

The canonical list lives in [`CLAUDE.md`](../../CLAUDE.md) § Premium reports / billing rail § What not to do — re-read it before editing anything in this area. The most load-bearing items, repeated here because forgetting them costs money:

- Do not add a mutable `balance` column on `users`. Always `SUM(delta)`.
- Do not grant credits from `session.metadata.credits`. Always look up via `getPackBySku(metadata.sku)` server-side. Stripe signs events after metadata edits — signature verification does NOT protect against tampered amounts.
- Do not log `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, or the `stripe-signature` header.
- Do not return `credit_purchases.raw_webhook` from any HTTP response.
- Do not accept negative credit amounts on any user-facing route.
- Do not build a second Stripe integration for the dev-API plan — extend `services/api/src/lib/stripe.ts`.
- Do not bypass the one-pending-per-user rate-limit-request guard without adding a DB partial unique index.
