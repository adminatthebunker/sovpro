# Public Developer API for Canadian Political Data

**Status:** Sketched, not started.
**Last updated:** 2026-04-20.

## Context

`docs/goals.md:104` lists "API design for paid tiers is sketched (not necessarily launched)" as a phase-1 success criterion, and `docs/goals.md:20–27` names lobbyists, journalists, academics, and advocacy orgs as the secondary audience served by **paid API tiers** (bulk export, programmatic semantic search, scheduled alerts).

Today there is no developer API, no API keys, no per-tier rate limits, and no OpenAPI documentation. All read endpoints under `/api/v1/*` are unauthenticated and shaped around what the frontend needs that day. A scraper hitting `/search/speeches` competes with the frontend for the single RTX 4050's TEI throughput.

This plan ships a **registered-developer API at `/api/public/v1/*`** with opaque-token auth, three paid tiers gated by Stripe Checkout, full-text responses for all jurisdictions (TOS-enforced), and a developer portal under `/account/api-keys` and `/developers`. Internal `/api/v1/*` is untouched — the frontend keeps using it.

## Decisions locked with the user

1. **URL namespace:** `/api/public/v1/*` as a separate surface. Internal `/api/v1/*` is unchanged.
2. **Search in v1.0:** `/search/speeches` ships in v1.0 behind `pro` tier with a global TEI concurrency semaphore (max 2).
3. **Copyright:** Full text everywhere via the API. Click-through Terms of Use forbids bulk redistribution. `redistribution_policy: "tos_governed"` returned on every response so clients have a hook for future tightening.
4. **Billing:** Stripe Checkout self-serve from day one. Free is self-serve at signup; `dev` and `pro` require an active subscription.

## Architecture overview

```
Browser ──cookie──> /api/v1/*           (internal, unchanged)
Dev app ──Bearer──> /api/public/v1/*    (new, this plan)
                       │
                       ├─ requireApiKey  ── api_keys + users
                       ├─ tier rate-limit ── @fastify/rate-limit + DB quota
                       ├─ (optional) p-limit semaphore ── TEI
                       └─ stable Zod schemas ── auto-OpenAPI
```

Stripe Checkout sits beside this for self-serve tier provisioning; a webhook keeps `users.current_plan` in sync with subscription state. A key's effective tier is `min(api_keys.tier, users.current_plan)` computed at request time — no backfill job needed when subscriptions lapse.

## Data model — migration `0030_developer_api.sql`

```sql
create table api_keys (
  id                    uuid primary key default gen_random_uuid(),
  user_id               uuid not null references users(id) on delete cascade,
  name                  text not null,                    -- human label, e.g. "prod"
  prefix                text not null,                    -- "cpd_live_abc12345" first 16 chars, indexed
  token_hash            bytea not null,                   -- hmac_sha256(API_KEY_PEPPER, full_token)
  tier                  text not null default 'free' check (tier in ('free','dev','pro')),
  scopes                jsonb not null default '["read:public"]'::jsonb,
  allowed_cidrs         text[],                           -- optional IP allowlist
  terms_version         text not null,                    -- TOS version accepted at create time
  terms_accepted_at     timestamptz not null,
  created_at            timestamptz not null default now(),
  last_used_at          timestamptz,
  expires_at            timestamptz,                      -- nullable = no expiry
  revoked_at            timestamptz,
  rotated_from_id       uuid references api_keys(id),
  grace_until           timestamptz                       -- old key valid until this time post-rotate
);
create unique index api_keys_token_hash_uq on api_keys(token_hash);
create index api_keys_prefix_idx on api_keys(prefix);
create index api_keys_user_active_idx on api_keys(user_id) where revoked_at is null;

create table api_key_events (
  id            bigserial primary key,
  api_key_id    uuid not null references api_keys(id) on delete cascade,
  event_type    text not null,                            -- created|rotated|revoked|first_used|rate_limited|quota_exceeded
  ip            inet,
  user_agent    text,
  detail        jsonb,
  created_at    timestamptz not null default now()
);
create index api_key_events_key_time_idx on api_key_events(api_key_id, created_at desc);

create table api_usage_daily (
  api_key_id      uuid not null references api_keys(id) on delete cascade,
  day             date not null,
  request_count   bigint not null default 0,
  expensive_count bigint not null default 0,              -- /search/speeches etc.
  primary key (api_key_id, day)
);

-- Stripe linkage on users (one customer per user, multiple keys per user)
alter table users
  add column stripe_customer_id text unique,
  add column current_subscription_id text,
  add column current_plan text check (current_plan in ('free','dev','pro')) default 'free',
  add column subscription_status text,                    -- active|past_due|canceled|...
  add column subscription_current_period_end timestamptz;

create table stripe_webhook_events (
  id                 text primary key,                    -- Stripe event id; idempotency
  event_type         text not null,
  payload            jsonb not null,
  received_at        timestamptz not null default now(),
  processed_at       timestamptz
);
```

## Token format

`cpd_<env>_<22-char-base62-random>_<6-char-base62-checksum>`

- `env` ∈ `{live, test}`. Test keys hit the same DB but are rate-limited tighter and excluded from billing usage counters.
- 22 chars base62 random ≈ 131 bits entropy.
- 6-char checksum = first 6 base62 chars of `hmac_sha256(API_KEY_PEPPER, prefix || random)`. Lets the server reject typos in O(1) without a DB hit and lets GitHub secret-scanning detect leaks.
- Stored as `token_hash = hmac_sha256(API_KEY_PEPPER, full_token)`. Plaintext shown exactly once at creation, never again.
- `prefix` (first 16 chars) is stored unhashed so `revoke-by-prefix` ops queries are cheap.

New env var: `API_KEY_PEPPER` (32+ bytes; rotating it is the "revoke-all-keys" emergency button, mirroring `JWT_SECRET`).

## Files to create

### Backend

| File | Purpose |
|---|---|
| `db/migrations/0030_developer_api.sql` | Schema above. |
| `services/api/src/lib/api-key-token.ts` | `mintApiKey()`, `verifyApiKey()`, `formatToken()`, `parseToken()`, `hashToken()` — mirrors the shape of `lib/auth-token.ts`. Pure; no DB. |
| `services/api/src/lib/stripe.ts` | Thin Stripe SDK wrapper: `getOrCreateCustomer(user)`, `createCheckoutSession(user, plan)`, `createPortalSession(user)`, `verifyWebhookSignature()`. Reads `STRIPE_SECRET_KEY` + `STRIPE_WEBHOOK_SECRET`. |
| `services/api/src/lib/tei-semaphore.ts` | `p-limit`-backed semaphore wrapping `encodeQuery()`. Configurable via `MAX_TEI_CONCURRENCY` env (default 2). Returns 503 + `Retry-After` if queue wait > 3s. |
| `services/api/src/middleware/api-key-auth.ts` | `requireApiKey`, `optionalApiKey`. Extracts `Authorization: Bearer cpd_*`, validates checksum, hashes, looks up joined to `users`, computes effective tier, attaches `request.apiKey`. Fire-and-forget `last_used_at` update (throttled to once per minute per key). |
| `services/api/src/middleware/api-rate-limit.ts` | `keyGenerator` returning `apikey:<id>` or `ip:<addr>`; per-tier `max` resolver; integrates with `@fastify/rate-limit`. Also writes per-day quota counter via `INSERT … ON CONFLICT DO UPDATE`. |
| `services/api/src/routes/public/index.ts` | Registers all `/api/public/v1/*` routes under one Fastify plugin. All routes use Zod schemas with `fastify-type-provider-zod` so OpenAPI auto-generates. |
| `services/api/src/routes/public/politicians.ts` | `GET /politicians`, `GET /politicians/:id`, `GET /politicians/:id/speeches`, `GET /politicians/:id/bills`, `GET /politicians/:id/terms`. Reuses queries from internal `routes/politicians.ts` but with stable response envelope. |
| `services/api/src/routes/public/bills.ts` | `GET /bills`, `GET /bills/:id`, `GET /bills/:id/sponsors`. New surface — bills currently aren't exposed in the internal API. |
| `services/api/src/routes/public/speeches.ts` | `GET /speeches/:id`, `GET /search/speeches` (pro tier only, semaphore-wrapped), `GET /search/facets` (dev tier+). Reuses `baseFilterSchema` from internal `routes/search.ts`. |
| `services/api/src/routes/public/coverage.ts` | `GET /coverage`, `GET /jurisdiction-sources`. Gated by API key (free tier acceptable), since these are cheap and the goal is to encourage signup. |
| `services/api/src/routes/public/lookup.ts` | `GET /lookup/postcode/:code`. Free tier. |
| `services/api/src/routes/public/openapi.ts` | Serves `/api/public/v1/openapi.json` and `/api/public/v1/docs` (Swagger UI). |
| `services/api/src/routes/keys.ts` | Authenticated key management (`requireUser` + `requireCsrf`): `GET /me/api-keys`, `POST /me/api-keys`, `DELETE /me/api-keys/:id`, `POST /me/api-keys/:id/rotate`. Plaintext returned exactly once on create + rotate. |
| `services/api/src/routes/billing.ts` | `POST /me/billing/checkout` (creates Stripe Checkout session for a chosen plan), `POST /me/billing/portal` (customer portal redirect), `POST /webhooks/stripe` (verifies signature, idempotent via `stripe_webhook_events`, updates `users.current_plan` + `subscription_status`). |

### Frontend

| File | Purpose |
|---|---|
| `services/frontend/src/pages/Developers.tsx` | Public `/developers` landing: pitch, tier table with prices, code samples (curl, JS, Python), link to docs, CTA to sign up. |
| `services/frontend/src/pages/account/ApiKeysPage.tsx` | `/account/api-keys`: list, create (modal showing plaintext exactly once + copy button + warning), rename, rotate, revoke. Reuses `useUserAuth` + CSRF helpers. |
| `services/frontend/src/pages/account/BillingPage.tsx` | `/account/billing`: current plan, usage chart for the month (from `api_usage_daily`), upgrade buttons (Stripe Checkout), manage-subscription button (Stripe Portal). |
| `services/frontend/src/components/ApiKeyCreateModal.tsx` | Plaintext-once display, copy-to-clipboard, "I have saved this key" checkbox before dismissal. |
| `services/frontend/src/styles/developers.css` | Styles for the developer pages. |

### Docs

| File | Purpose |
|---|---|
| `docs/public-api.md` | Authoritative reference: auth model, token format, rate limits per tier, endpoint catalog, response envelope, error codes, redistribution policy, key rotation, leaked-key recovery. Linked from the `/developers` page. |
| `docs/operations.md` | (modify) Add: provisioning a key via psql for trusted partners, `revoke-by-prefix` one-liner, Stripe webhook replay, rotating `API_KEY_PEPPER`. |
| `docs/api.md` | (modify) Add a header noting that `/api/v1/*` is internal/frontend and pointing developers to `docs/public-api.md`. |

## Files to modify

| File | Change |
|---|---|
| `services/api/src/config.ts` | Add `apiKeyPepper`, `stripeSecretKey`, `stripeWebhookSecret`, `stripePriceDevMonthly`, `stripePriceProMonthly`, `maxTeiConcurrency`. Same Zod-validation pattern. Optional in dev (degrade gracefully like JWT_SECRET / SMTP do today). |
| `services/api/src/index.ts` | Register the `routes/public/index.ts` plugin under `/api/public/v1`. Register `routes/keys.ts` and `routes/billing.ts` under `/api/v1`. Add `redact: ['req.headers.authorization', 'req.headers.cookie']` to the Fastify logger config. Configure CORS to be permissive (`*`) for `/api/public/v1/*` and stay restricted on `/api/v1/*`. Install `fastify-type-provider-zod` and `@fastify/swagger` + `@fastify/swagger-ui`. |
| `services/api/src/routes/search.ts` | Wrap `encodeQuery()` calls in the `tei-semaphore` so internal frontend search is also protected from API-induced contention. |
| `services/api/src/middleware/user-auth.ts` | No change — referenced as the design template for `api-key-auth.ts`. |
| `services/api/package.json` | Add deps: `stripe`, `@fastify/swagger`, `@fastify/swagger-ui`, `fastify-type-provider-zod`, `p-limit`. |
| `services/frontend/src/main.tsx` | Add routes for `/developers`, `/account/api-keys`, `/account/billing`. |
| `services/frontend/src/components/Layout.tsx` | Add a "Developers" nav link in the public header. |
| `docker-compose.yml` | Add `API_KEY_PEPPER`, `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, `STRIPE_PRICE_DEV_MONTHLY`, `STRIPE_PRICE_PRO_MONTHLY`, `MAX_TEI_CONCURRENCY` env vars on the `api` service. |
| `.env.example` | Document the new env vars with example values. |
| `CLAUDE.md` | Add a "Developer API" section after "User accounts" describing the routing split, token format, tiers, files, and what-not-to-do (don't bypass `requireApiKey`, don't trust the `tier` claim without recomputing from `users.current_plan`, don't log Bearer tokens). |

## Tier matrix (initial)

| Feature | free | dev ($20/mo) | pro ($200/mo) |
|---|---|---|---|
| Requests / hour | 60 | 1,000 | 10,000 |
| Requests / day | 500 | 20,000 | 250,000 |
| `/search/speeches` | ❌ | ❌ | ✅ (semaphore) |
| `/search/facets` | ❌ | ✅ | ✅ |
| `/politicians`, `/bills`, `/speeches/:id` | ✅ | ✅ | ✅ |
| `/lookup/postcode/:code` | ✅ | ✅ | ✅ |
| `/coverage`, `/jurisdiction-sources` | ✅ | ✅ | ✅ |
| Bulk export (`read:bulk` scope) | ❌ | ❌ | v1.2 |
| IP allowlists | ❌ | ✅ | ✅ |
| Key rotation w/ grace period | ✅ | ✅ | ✅ |

Prices in `STRIPE_PRICE_*` env vars; the table above is the operator's intent, not a hardcode.

## Reused primitives (do not re-implement)

- `services/api/src/middleware/user-auth.ts:42` — `requireUser` shape; clone for `requireApiKey`.
- `services/api/src/lib/auth-token.ts:74` — `SESSION_COOKIE` pattern; the IdP-swap-seam comment block applies equally to `api-key-token.ts`.
- `services/api/src/lib/csrf.ts` — already in place for `/me/api-keys` and `/me/billing` mutations.
- `services/api/src/routes/search.ts` — `baseFilterSchema`, `encodeQuery()`, `toPgVector()` exports. Public `/search/speeches` route imports these directly.
- `services/api/src/routes/auth.ts` — per-email DB rate-limit pattern; mirror it for per-key daily quota.
- `services/scanner/src/alerts_worker.py` — pattern for fire-and-forget background hooks (used here for `last_used_at` and `api_key_events`).

## Verification

End-to-end smoke test (run locally before declaring done):

1. **Migration:** `docker exec -i sw-db psql -U sw -d sovereignwatch -v ON_ERROR_STOP=1 < db/migrations/0030_developer_api.sql`. Confirm tables exist + indexes are listed in `\d api_keys`.
2. **Sign up + create key:** Magic-link sign in, hit `/account/api-keys`, click Create, confirm plaintext shown once and prefix matches `cpd_test_`. Verify the row in `api_keys` has `token_hash` set and no plaintext anywhere.
3. **Authentication:**
   ```bash
   curl -H "Authorization: Bearer cpd_test_xxx..." http://localhost:8080/api/public/v1/politicians?limit=2
   ```
   Returns 200 with `{ data: [...], meta: { ... } }`. Verify `last_used_at` updated, `api_key_events` row created.
4. **Tier gating:** With a `free` key, hit `/api/public/v1/search/speeches?q=health` → 403 with body indicating tier requirement. Upgrade to `pro` via the operator psql command, retry → 200.
5. **Rate limiting:** Loop 70 calls in a minute against `/coverage` with a free key → eventually 429 with `Retry-After`. Verify `api_key_events` has a `rate_limited` row.
6. **TEI semaphore:** Spin up 5 concurrent `/search/speeches` calls. Confirm only 2 hit TEI simultaneously; the rest queue or 503 after 3s.
7. **OpenAPI:** Browser to `http://localhost:8080/api/public/v1/docs` → Swagger UI renders with all routes. `curl /openapi.json` → valid OpenAPI 3.1 JSON; pipe through `npx @apidevtools/swagger-cli validate -`.
8. **Stripe (test mode):** With test Stripe keys, click Upgrade → Checkout opens → complete with `4242 4242 4242 4242` → webhook received → `users.current_plan` flips to `dev`. Cancel via Customer Portal → webhook → plan flips back to `free` at period end.
9. **Key rotation:** Click Rotate → new plaintext shown. Old key still works for 24 h (`grace_until`). Forced revoke → both keys 401.
10. **Leaked-key drill:** `UPDATE api_keys SET revoked_at = now() WHERE prefix LIKE 'cpd_test_abc%'` — subsequent requests with that key 401 within one request.
11. **Logger redaction:** Tail `docker compose logs api` while making an authenticated request; confirm `authorization` header is `[REDACTED]`.
12. **CORS:** From a `localhost:3000` page (or the browser console on a different origin), `fetch('http://localhost:8080/api/public/v1/coverage', { headers: { Authorization: '...' } })` succeeds; same call to `/api/v1/coverage` from a foreign origin fails preflight.

## Phasing within this plan

- **v1.0 (this plan):** Everything above.
- **v1.1 (next):** `read:bulk` scope, CSV export of `/search/speeches` results, per-key webhooks for delivering saved-search alerts to API consumers, audit-log UI in `/account/api-keys`.
- **v1.2 (later):** Bulk Parquet downloads via presigned URLs (`.parquet.zst` of speeches per jurisdiction-month); `.pmtiles` static tile downloads; OAuth-on-behalf-of-user for partner newsroom integrations.

## Out of scope for this plan

- Refactoring internal `/api/v1/*` to share response shapes with public.
- Replacing `users.is_admin` with role-based scopes (separate concern).
- Full FOI / lobbying-records ingestion (per `goals.md` non-goals).
- Per-key watermarking of speech text for anti-redistribution forensics (TOS is the layer-1 control; revisit only if abuse actually happens).

## Open follow-ups for later conversations

- `last_used_at` write throttling — start with once-per-minute-per-key in-memory cache; revisit only if write pressure shows up.
- Whether to publish a list of public developers (transparency vs. privacy). Default: no.
- FR/EN bilingual response shaping — currently returns source language verbatim; a future `lang=en|fr` param could force one side.
