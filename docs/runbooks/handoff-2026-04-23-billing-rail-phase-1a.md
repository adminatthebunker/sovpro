# Handoff — 2026-04-23 (Premium reports billing rail — phase 1a live)

**Session arc:** designed and shipped the billing rail that every future premium feature will plug into. Phase 1a = Stripe Checkout + credit ledger + admin comp flow, **deliberately Stripe-unconfigured in production** so the code + DB changes landed as a dark deploy with zero new payment surface. Phase 1b (reports-worker + LLM pipeline that spends credits) has NOT been built — it's the next chunk. One commit landed on `main`; nginx upstream was reloaded after the rebuild to force IP re-resolve.

**TL;DR resume path (if you pick this up):**

```bash
# 1. Confirm state
git log --oneline -3
# Expected top: 233634c feat(billing): phase 1a billing rail — credit ledger + stripe checkout

docker exec sw-db psql -U sw -d sovereignwatch -At -c "
  SELECT table_name FROM information_schema.tables
   WHERE table_schema='public'
     AND table_name IN ('stripe_webhook_events','credit_ledger','credit_purchases','rate_limit_increase_requests')
   ORDER BY table_name;"
# Expected: 4 rows

curl -sS -w "\nHTTP %{http_code}\n" http://localhost:8088/api/v1/webhooks/stripe -X POST \
     -H "Content-Type: application/json" -d '{}'
# Expected: HTTP 200 {"received":false,"reason":"stripe not configured"}

# 2. Next-steps menu (choose one):
#   a) Finish the CORS tightening (operator .env edit — see §"Left for operator")
#   b) Stripe test-mode enablement (see §"Stripe enablement" in docs/plans/premium-reports.md)
#   c) Phase 1b: reports-worker + LLM pipeline + /reports/<id> viewer
#      (needs round-3 numbers first — see §"Round 3 decisions still open" below)
```

---

## Read-first orientation for the next agent

Before touching anything in this area, load these in order. They capture the "why" and the load-bearing invariants that are easy to break without noticing.

| # | Doc | Why |
|---|---|---|
| 1 | [`docs/plans/premium-reports.md`](../plans/premium-reports.md) | The canonical plan. Locked decisions, phase-1a/1b/2+ sequencing, data model, new env vars, staged Stripe enablement procedure, and the rollback sequence. **Start here.** |
| 2 | [`CLAUDE.md` § Premium reports / billing rail](../../CLAUDE.md) | Load-bearing invariants distilled: two-layer idempotency discipline, "balance is always derived, never cached," webhook metadata-trust rule, admin comp pattern. Also carries the "What not to do" list — read every bullet before writing new code in this area. |
| 3 | [`docs/api.md` § Credits / Rate-limit / Stripe webhook / Admin user management](../api.md) | Every endpoint shape + auth expectation. |
| 4 | [`docs/operations.md` § Billing rail](../operations.md) | Ops workflows: admin comp, user suspend, webhook secret rotation, ledger correction discipline. |
| 5 | [`docs/architecture.md` § `api` service](../architecture.md) | Quick service-level view of where the billing routes + lib sit. |
| 6 | *(design history, not code)* `/home/bunker-admin/.claude/plans/ah-gotcha-i-prancy-snowflake.md` | The round-by-round conversation notes (rounds 1–3). Useful if you need to understand *why* a decision was made that seems non-obvious. **Not in the repo** — this lives in Claude's planning dir. |

Secondary reading if your work overlaps:

- [`docs/plans/public-developer-api.md`](../plans/public-developer-api.md) — sketches subscription-based API tiers. It will reuse the Stripe customer + webhook infrastructure from this phase. Do NOT duplicate `users.stripe_customer_id`, `stripe_webhook_events`, or `services/api/src/lib/stripe.ts` when that plan activates — extend them.

---

## What shipped this session

### Commit on `main`

| SHA | Title | Files | Lines |
|---|---|---:|---:|
| `233634c` | feat(billing): phase 1a billing rail — credit ledger + stripe checkout | 23 | +2859 −83 |

Deliberately a single coherent commit. Clean rollback point: `git revert 233634c && docker compose build api frontend && docker compose up -d api frontend` plus the DB rollback in `docs/plans/premium-reports.md` § Rollback.

### New files

- `db/migrations/0033_billing_rail.sql` — additive-only migration: `users.stripe_customer_id` + `users.rate_limit_tier`, `stripe_webhook_events`, `credit_ledger` (with partial unique index on `(kind, reference_id) WHERE reference_id IS NOT NULL`), `credit_purchases`, `rate_limit_increase_requests`. **Already applied in prod** before the code deploy (schema-first discipline — column/table adds are invisible to the old container).
- `services/api/src/lib/stripe.ts` — SDK wrapper (the **sole** importer of the `stripe` npm package). `getOrCreateCustomer`, `createCheckoutSession`, `constructWebhookEvent`, `PACK_CREDITS` catalog, `getPackBySku`. Pins `apiVersion = "2026-03-25.dahlia"` explicitly to prevent silent drift on SDK upgrades.
- `services/api/src/lib/credits.ts` — ledger helpers. `getBalance` = `SUM(delta) WHERE state IN ('committed','held')`. `holdCredits/commitHold/releaseHold` operate by state-flip (one row per economic event, not marker rows). `grantStripePurchase` is a single BEGIN/COMMIT transaction with ledger + purchase rows inserted atomically.
- `services/api/src/routes/credits.ts` — `GET /me/credits`, `GET /me/credits/packs`, `POST /me/credits/checkout` (per-route 5/min cap).
- `services/api/src/routes/stripe-webhook.ts` — `POST /webhooks/stripe`. **Plugin-scoped raw-body parser** so signature verification isn't broken by Fastify's default JSON re-serialisation. Two-layer idempotency (event-id PK + ledger partial unique). 200-discards when Stripe is unconfigured (not 5xx — Stripe retries 5xx for 72h and burns retry budget).
- `services/api/src/routes/rate-limit-requests.ts` — user-facing GET/POST under `/me/rate-limit-requests`. One-pending-per-user guard at the app layer (to avoid yet another migration; switch to a partial unique index if spam surfaces).
- `services/frontend/src/pages/CreditsPage.tsx` — `/account/credits` balance + pack selection + ledger history. `reference_id` deliberately stripped from the user-facing shape (admin view retains it).
- `services/frontend/src/pages/admin/AdminUsers.tsx` — new admin page: user search, user detail with balance + ledger, credit grant form (admin comp), rate-limit tier adjustment dropdown, pending rate-limit-request queue with approve/deny + optional tier-bump.
- `docs/plans/premium-reports.md` — canonical plan doc.

### Modified

- `services/api/src/config.ts` — Stripe env block (`STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, `STRIPE_PRICE_ID_CREDIT_PACK_SMALL/MEDIUM/LARGE`, `STRIPE_SUCCESS_URL`, `STRIPE_CANCEL_URL`), CORS default tightened `*` → `https://canadianpoliticaldata.ca` with comma-separated multi-origin support.
- `services/api/src/middleware/user-auth.ts` — `requireUser` now re-reads `users.rate_limit_tier` per request and 403s `suspended` users immediately (same DB-re-read discipline as `requireAdmin` uses for `is_admin`).
- `services/api/src/routes/admin.ts` — appended 6 endpoints: `GET /admin/users`, `GET /admin/users/:id`, `POST /admin/users/:id/grant-credits`, `PATCH /admin/users/:id`, `GET /admin/rate-limit-requests`, `PATCH /admin/rate-limit-requests/:id`.
- `services/api/src/index.ts` — registered new plugins at `/api/v1/me/credits`, `/api/v1/me/rate-limit-requests`, `/api/v1/webhooks/stripe`.
- `services/api/package.json` + `package-lock.json` — added `stripe` dep.
- `services/frontend/src/components/AdminLayout.tsx` — added `Users` nav link.
- `services/frontend/src/main.tsx` — registered `/account/credits` + `/admin/users` routes.
- `services/frontend/src/pages/AccountPage.tsx` — added credits balance chip + link (visible only when `stripe_enabled: true` comes back from `/me/credits`).
- `.env.example` — documents the new Stripe block with "why" comments; CORS default updated with a note that `*` is wrong in prod.
- `CLAUDE.md` — added "Premium reports / billing rail" section alongside "User accounts" and "Admin panel." Updated "Latest applied migrations" list through 0033.
- `docs/api.md`, `docs/operations.md`, `docs/architecture.md` — see §"Read-first orientation" links above.

### Security sweep findings (all fixed before ship)

The auth-security-reviewer agent did a full sweep. One BLOCK-severity finding (webhook trusted `metadata.credits` which is Stripe-dashboard-editable), two should-fix-now (checkout rate-limit, suspended-tier enforcement), three low (webhook 503→200, `reference_id` leak in user-facing response, Stripe SDK apiVersion pin), one informational (missing user POST for rate-limit-requests). All fixed in the same commit. Full write-up at the end of the session; tl;dr — **never trust any value on `session.metadata.credits`; always look up via `getPackBySku(metadata.sku)` server-side.** Stripe signs events after metadata edits so signature validation does NOT protect against tampered amounts.

---

## Deploy state verified post-ship

### Containers

```bash
docker compose ps
# Expected healthy: sw-api, sw-frontend, sw-db, sw-nginx, sw-tei, sw-scanner-jobs,
#                   sw-alerts-worker, sw-kuma, sw-newt
```

API image SHA after rebuild: `sha256:a822182d47d21614454e5b1e2739c50a15c5bb5941670e32a043b9ce01063146` (the numbered tag is `sovpro-api:latest`; resolve to the sha above if you need to pin).

### Smoke tests that passed

| Endpoint | Expected | Got |
|---|---|---|
| `GET /health` | 200 `{ok:true,db:true}` | ✓ |
| `GET /api/v1/me/credits` (anonymous) | 401 | ✓ `{"error":"not signed in"}` |
| `POST /api/v1/webhooks/stripe` (unsigned, Stripe unconfigured) | 200 discard | ✓ `{"received":false,"reason":"stripe not configured"}` |
| `GET /api/v1/me/rate-limit-requests` (anonymous) | 401 | ✓ |
| `GET /api/v1/admin/users` (anonymous) | 401 | ✓ |
| Frontend `/` | 200 | ✓ |
| Frontend `/account/credits` | 200 (SPA) | ✓ |

DB smoke tests on the new migration (5 tests) also passed during initial rollout — see the plan doc's Verification section for replayable SQL.

### Startup log — expected warnings

```
sw-api | [config] Stripe secret key or webhook secret unset in production; /me/credits/checkout and /webhooks/stripe will be disabled.
sw-api | Server listening at http://0.0.0.0:3000
sw-api | API listening on 0.0.0.0:3000
```

The Stripe warning is **designed-in**, not a bug. Ignore it until you explicitly enable Stripe.

---

## Nginx upstream gotcha (important for any future rebuild)

When you `docker compose up -d --build api` and the container is recreated, **its internal IP changes** (was `172.20.0.2`, became `172.20.0.7` on this deploy). Nginx resolves `api:3000` once at startup and pins the IP, so all requests return `502` until you reload:

```bash
docker exec sw-nginx nginx -s reload
```

This is now the standard rebuild procedure for services behind nginx. A permanent fix would be to add `resolver 127.0.0.11 valid=10s;` in nginx config and reference the upstream via a variable so nginx re-resolves per request — left as a future improvement; the manual reload is fine for now.

---

## Left for operator (you) to do manually

### 1. Finish the CORS tightening

```bash
# In .env, change:
#   API_CORS_ORIGIN=*
# to:
#   API_CORS_ORIGIN=https://canadianpoliticaldata.ca

docker compose up -d api
docker exec sw-nginx nginx -s reload   # same gotcha, same fix
```

The code default in `services/api/src/config.ts` is already correct; the `.env` override is what's still loose. I deliberately did not edit `.env` because that's production config and should be operator-owned.

### 2. Stripe enablement (whenever you're ready — own deploy)

Full sequence in [`docs/plans/premium-reports.md` § Stripe enablement](../plans/premium-reports.md). TL;DR:

1. Create Stripe account if needed; create three one-time products in **test mode** ($5 / $20 / $50) — note the `price_…` IDs.
2. Fill `.env` with `sk_test_…`, the three price IDs, and a placeholder for `STRIPE_WEBHOOK_SECRET` (populated in step 4).
3. `docker compose up -d api` (frontend doesn't need a rebuild — packs endpoint drives the UI).
4. Register webhook endpoint in Stripe dashboard → `https://canadianpoliticaldata.ca/api/v1/webhooks/stripe`. Select only `checkout.session.completed`. Copy the signing secret into `STRIPE_WEBHOOK_SECRET`. Restart API again.
5. Exercise the buy flow with card `4242 4242 4242 4242`. Verify rows appear in `stripe_webhook_events`, `credit_purchases`, `credit_ledger`. Re-send the webhook from Stripe dashboard → should 200 with `duplicate: true` and no new DB rows.
6. Only then swap to live-mode keys on a subsequent restart.

**Do NOT flip to live mode without passing the test-mode smoke first.** Any webhook-handler bug in live mode corresponds to real customer money landing in a broken ledger.

---

## Round 3 decisions still open (needed before phase 1b)

Phase 1b (reports-worker + LLM pipeline) is gated on **numbers and copy**, not architecture. Locking these is the first task of the next session:

1. **Credit-pack SKU final numbers.** Starter proposal from round 2: `$5 → 50 credits`, `$20 → 250 credits` (12% bonus), `$50 → 700 credits` (17% bonus). User has said "lean premium." Confirm or adjust before creating Stripe products.
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
3. **Rate-limit defaults.** Starter: 5 reports/day + 200 credits/hour in `default` tier; `extended` tier bumps to 20/day + 1000/hour. Values live in code config (not DB) — trivial to adjust.
4. **Disclaimer copy.** Paid reports need consent-modal text + in-report footer text. Should get a legal read before public launch.
5. **Admin-comp UI placement.** Currently embedded in `/admin/users`. Consider whether it should have a dedicated page; current placement is fine for the volume we expect.

User confirmed in round 2: OpenRouter paid tier routed to Anthropic Claude (not Anthropic direct), new `reports-worker` compose service mirroring `alerts_worker.py`, HTML-only artifact (no PDF in v1).

---

## Do-not-break invariants (read before editing anything in this area)

Reiterating the most load-bearing ones from CLAUDE.md § Premium reports / billing rail. If you violate any of these, the ledger gets incoherent and money is lost or double-granted:

1. **Balance is ALWAYS derived** from `SUM(delta) WHERE state IN ('committed','held')`. Never add a mutable `balance` column. Refunds are state-flips (`'held' → 'refunded'`), not compensating positive rows.
2. **NEVER trust `session.metadata.credits`** from a Stripe webhook. Always look up `getPackBySku(metadata.sku)` server-side. Stripe dashboard admins can edit metadata before payment; the signature is computed after that edit.
3. **Webhook handler must fail-closed on missing signing secret.** `constructWebhookEvent` throws when `STRIPE_WEBHOOK_SECRET` is unset; the route returns 400 before any DB write on signature failure.
4. **Webhook returns 200 (not 5xx) when Stripe is unconfigured.** Stripe retries 5xx for 72h and burns retry budget.
5. **Plugin-scoped raw-body parser** in `stripe-webhook.ts` — do not move the parser registration out of the plugin's encapsulation context. Every other route needs the default JSON parser.
6. **Admin comp grants produce normal `credit_ledger` rows** with `kind='admin_credit'` + `created_by_admin_id` set. There is no parallel "free credits" system. Any future "promo" or "referral" feature must insert ledger rows, not invent a new counter column.
7. **Never `UPDATE credit_ledger SET delta = …`**. All corrections are new rows. The ledger is append-only by discipline, not just schema.
8. **Do not bypass the one-pending-per-user rate-limit-request guard.** App-level check is the minimum; upgrade to a DB partial unique index if abuse surfaces.

---

## Open working-tree state

62 unrelated pending changes remain uncommitted (docker-compose.yml, README.md, research dossiers, etc.). **Deliberately untouched** — they're separate work streams that should be committed on their own cadence. `git status --short` will show them all; none are related to the billing rail.
