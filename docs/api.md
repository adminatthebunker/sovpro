# API Reference

Base URL: `http://<host>/api/v1`

All endpoints return JSON. Pagination: `?page=N&limit=M` where applicable.

## Politicians

### `GET /politicians`
Query params:
- `level` — `federal | provincial | municipal`
- `province` — 2-letter (`AB`, `ON`, ...)
- `party` — exact match
- `sovereignty_tier` — 1-6
- `search` — name substring (ILIKE)
- `page`, `limit` (max 500, default 50)

### `GET /politicians/:id`
Returns the politician, all websites with their latest scan, and the constituency boundary GeoJSON.

## Organizations

### `GET /organizations`
- `type` — `referendum_leave | referendum_stay | political_party | indigenous_rights | advocacy | government_body | media`
- `side` — `leave | stay | neutral`
- `search` — name substring

### `GET /organizations/:idOrSlug`
Looks up by UUID or slug (e.g. `alberta-prosperity-project`).

## Map

### `GET /map/geojson`
- `level`, `province`, `group=politicians|organizations|all`

Returns a `FeatureCollection` containing three feature kinds:
- `kind: "constituency"` — MultiPolygon
- `kind: "server"` — Point
- `kind: "connection"` — LineString from constituency centroid → server

### `GET /map/referendum`
Returns a `FeatureCollection` focused on referendum orgs, with the AB provincial boundary as context.

## Stats

### `GET /stats`
Top-level rollup: politicians by level/party, sovereignty distribution, top providers + locations, organizations summary.

### `GET /stats/referendum`
```json
{
  "leave_side":  { "orgs": [...], "total_websites": N, "hosted_in_us": N, ... },
  "stay_side":   { ... },
  "irony_score": "Organizations advocating to leave Canada..."
}
```

## Coverage

### `GET /coverage`
Query params:
- `status` — filter by `bills_status`: `live | partial | blocked | none`

Returns the `jurisdiction_sources` table plus a rollup summary:

```json
{
  "jurisdictions": [
    {
      "jurisdiction": "AB",
      "legislature_name": "Legislative Assembly of Alberta",
      "seats": 87,
      "bills_status": "live",
      "hansard_status": "none",
      "votes_status": "none",
      "committees_status": "live",
      "bills_difficulty": 2,
      "blockers": null,
      "notes": "Legislature 31 S1+S2 live (114 bills); Hansard is PDF-only",
      "bills_count": 0,
      "speeches_count": 0,
      "votes_count": 0,
      "politicians_count": 0,
      "last_verified_at": null
    }
  ],
  "summary": { "total": 14, "live": 8, "partial": 2, "blocked": 2, "none": 2 }
}
```

Seeded on migration 0019 and kept current by `jurisdiction_sources` updates from ingest pipelines.

## Changes

### `GET /changes`
- `since` (ISO timestamp), `owner_type`, `change_type`, `severity`
- Returns scan deltas with owner name + URL.

## Webhooks

### `POST /webhooks/change`
Receives notifications from the `change` detection container. Authenticated via:
```
X-Signature: sha256=<hex(hmac_sha256(WEBHOOK_SECRET, raw_body))>
```
If `WEBHOOK_SECRET` is unset, the endpoint accepts unsigned posts (dev mode only).

## Open Graph

### `GET /og/share`
Returns a dynamic **1200×630 PNG** share card with the current headline stat
(% of Canadian politicians hosting outside Canada) and a sovereignty-tier bar
chart. Intended for use in `<meta property="og:image">` tags.

- `Content-Type: image/png`
- `Cache-Control: public, max-age=300`
- In-process cache refreshes every 5 minutes from live `/stats` data.

## Admin

All `/api/v1/admin/*` routes require a valid user session cookie (`sw_session`) on a user whose `users.is_admin = true`. Mutating verbs (POST / PATCH / DELETE) additionally require the double-submit CSRF token in `X-CSRF-Token`.

- Not signed in → **401** `{"error":"not signed in"}`.
- Signed in but not admin → **403** `{"error":"admin access required"}`.
- CSRF missing/invalid on a mutating route → **403** `{"error":"csrf check failed"}`.
- `JWT_SECRET` unset on the server → **503** (admin surface is disabled along with all user auth).

No `POST /admin/login` endpoint — admins sign in via the shared magic-link flow (`POST /api/v1/auth/request-link` → email → `POST /api/v1/auth/verify`). The `is_admin` flag is included on `GET /me`.

### `GET /admin/commands`
Returns the whitelist catalog:
```json
{ "commands": [ { "key": "chunk-speeches", "category": "hansard",
                  "description": "...", "args": [ ... ] }, ... ] }
```
Used by the frontend form generator.

### `GET /admin/jobs`
Query: `?status=queued|running|succeeded|failed|cancelled`, `?schedule_id=…`, `?limit=1..500` (default 100).
Returns `{ jobs: [...] }` with a `stdout_snippet`/`stderr_snippet` (first 500 chars each) for list-view rendering.

### `POST /admin/jobs`
Body: `{ command: string, args: object, priority?: 0..100 }`. Command must be in the whitelist. Returns `{ id }` on 201.

### `GET /admin/jobs/:id`
Full row with `stdout_tail` / `stderr_tail` (last 4 KB each) and `error`.

### `POST /admin/jobs/:id/cancel`
Flips status to `cancelled` **only** if currently `queued`. Running jobs are not interrupted (returns 409).

### `GET /admin/schedules`
List all rows in `scanner_schedules`.

### `POST /admin/schedules`
Body: `{ name, command, args, cron, enabled? }`. `cron` is 5-field UTC.

### `PATCH /admin/schedules/:id`
Partial update. Changing `cron` clears `next_run_at` so the worker recomputes it.

### `DELETE /admin/schedules/:id`
Returns 204. `scanner_jobs.schedule_id` is set NULL via FK (jobs history is preserved).

### `GET /admin/stats`
Dashboard counters:
```json
{
  "speeches": 20,
  "chunks": { "total": 20, "embedded": 20, "pending": 0 },
  "jobs":    { "queued": 0, "running": 0, "succeeded_24h": 3, "failed_24h": 0 },
  "jurisdictions": { "live": 8, "total": 14 },
  "recent_failures": [ { "id", "command", "finished_at", "error" }, ... ]
}
```

## Credits (billing rail — phase 1a)

All `/me/credits/*` routes require a signed-in session (`sw_session` cookie). Mutating routes additionally require the `sw_csrf` cookie echoed in the `X-CSRF-Token` header. When Stripe is unconfigured (`STRIPE_SECRET_KEY` unset), the feature returns `stripe_enabled: false` and the purchase endpoint 503s — no payment surface is exposed.

### `GET /me/credits`
Current spendable balance + recent ledger history (up to 50 entries, newest first). `reference_id` is deliberately omitted from the user-facing shape — see `/admin/users/:id` for the full-fidelity admin view.
```json
{
  "balance": 120,
  "history": [
    { "id": "uuid", "delta": 100, "state": "committed", "kind": "stripe_purchase", "reason": null, "created_at": "2026-04-23T..." },
    { "id": "uuid", "delta": 20,  "state": "committed", "kind": "admin_credit",    "reason": "Launch promo", "created_at": "..." }
  ],
  "stripe_enabled": true
}
```

### `GET /me/credits/packs`
Lists the credit packs currently offered. Filtered to packs whose `STRIPE_PRICE_ID_*` env var is set — if a pack isn't configured, it's simply omitted.
```json
{
  "enabled": true,
  "packs": [
    { "sku": "small",  "credits": 50,  "display_price": "$5",  "bonus_label": null },
    { "sku": "medium", "credits": 250, "display_price": "$20", "bonus_label": "12% bonus" }
  ]
}
```

### `POST /me/credits/checkout`
Per-route rate limit: 5/min. Creates a Stripe Checkout Session for the given SKU and returns the hosted-page URL. The frontend `window.location.assign`s to that URL. The actual credit grant happens via the `POST /webhooks/stripe` handler after payment completion.
```json
// request
{ "sku": "small" }
// response
{ "url": "https://checkout.stripe.com/c/pay/cs_test_…", "session_id": "cs_test_…" }
```

## Rate-limit requests

### `GET /me/rate-limit-requests`
The caller's own rate-limit increase requests (up to 20, newest first).

### `POST /me/rate-limit-requests`
Per-route rate limit: 3/hour. One-pending-per-user: returns 409 if the caller already has an unresolved request.
```json
// request
{ "reason": "Covering the upcoming federal election, need higher report volume", "requested_tier": "extended" }
// response 201
{ "id": "uuid", "reason": "...", "requested_tier": "extended", "status": "pending", "admin_response": null, "created_at": "...", "resolved_at": null }
```

## Stripe webhook

### `POST /webhooks/stripe`
Not called by clients — registered in the Stripe dashboard as the endpoint for `checkout.session.completed` events. Verifies the `Stripe-Signature` header before any DB write. Two-layer idempotency via `stripe_webhook_events.id` PK + `credit_ledger (kind, reference_id)` partial unique index. Returns 200 with `{ received: true }` on success, `{ received: true, duplicate: true }` on re-delivery, `{ received: false, reason: "stripe not configured" }` 200 when disabled, 400 on signature failure.

## Admin — user management (phase 1a additions)

All routes under `/admin/*` require `is_admin=true` on the session user plus CSRF on mutations.

### `GET /admin/users`
Query: `?q=<email-substring>&limit=<n>` (limit 1–100, default 20). Returns users matching the email ILIKE pattern.

### `GET /admin/users/:id`
Single user detail + current balance + ledger history (up to 100 entries, retains `reference_id`).

### `POST /admin/users/:id/grant-credits`
Admin comp flow. Body: `{ "amount": <1..100_000>, "reason": "<3..500 chars>" }`. Produces a `credit_ledger` row with `kind='admin_credit'`, `created_by_admin_id` = acting admin, `reason` = supplied note.

### `PATCH /admin/users/:id`
Body: `{ "rate_limit_tier": "default" | "extended" | "unlimited" | "suspended" }`. Suspending a user takes effect on their next request via `requireUser`'s re-read.

### `GET /admin/rate-limit-requests`
Query: `?status=pending|approved|denied&limit=<n>`. Queue of user-submitted increase requests.

### `PATCH /admin/rate-limit-requests/:id`
Body: `{ "status": "approved"|"denied", "admin_response": "<message to user>", "apply_tier": "extended"|"unlimited"? }`. When approved with `apply_tier`, the user's `rate_limit_tier` is bumped atomically.

## Health

### `GET /health`
```json
{ "ok": true, "db": true }
```
