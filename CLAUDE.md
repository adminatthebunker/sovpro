# CLAUDE.md — SovereignWatch / Canadian Political Data

Project-level instructions for any AI agent working in this repo. Read before writing code.

## One-line purpose

**SovereignWatch** is the internal / codebase name. **Canadian Political Data** (domain: `canadianpoliticaldata.ca`) is the public-facing brand — use CPD in blog posts, LinkedIn, and external copy, and in commit messages, migrations, and internal docs.

The project is becoming **the definitive source of Canadian political data** — who represents whom, what they've said, how they've voted, where their infrastructure lives. See `docs/goals.md` for the full product framing. It is **not apolitical**; it takes progressive and democratic stances rooted in access-to-information principles.

## Architectural docs — read in this order

1. `docs/goals.md` — north star, audience, non-goals
2. `docs/plans/semantic-layer.md` — schema, vector store, embedding plan, phased rollout
3. `docs/research/` — one self-contained research dossier per jurisdiction (federal + 13 provinces/territories), plus `overview.md` for cross-cutting schema log, probe hierarchy, research-handoff protocol, and known blockers
4. `docs/architecture.md` — service-by-service runtime architecture
5. `docs/scanner.md`, `docs/api.md`, `docs/operations.md` — per-component references

If you find yourself guessing at product direction, the goals doc is the authority. If you find yourself guessing at schema, the semantic-layer doc is the authority.

## Stack

- **DB:** Postgres 16 + PostGIS 3.4 + pgvector 0.8.2 + unaccent, built from `db/Dockerfile` (extends `postgis/postgis:16-3.4` with `postgresql-16-pgvector`).
  - Credentials: user `sw`, database `sovereignwatch` (not `sovpro`).
  - Access inside compose: `docker exec sw-db psql -U sw -d sovereignwatch`.
  - Rebuild after Dockerfile edits: `docker compose build db && docker compose up -d db`. `pgdata` volume persists; `init.sql` / `seed.sql` run once on fresh volumes only.
- **API:** Node 20 + Fastify, zod validation, `services/api/`.
- **Frontend:** React 18 + Vite + Leaflet + React Router 6, `services/frontend/`.
- **Scanner:** Python 3.13 + asyncio + Click, `services/scanner/`.
- **Embed service:** HuggingFace **Text Embeddings Inference (TEI)** serving **Qwen3-Embedding-0.6B** (1024-dim, fp16 on GPU). Image `ghcr.io/huggingface/text-embeddings-inference:89-1.9`, compose service `tei`, reachable inside compose at `http://tei:80` (OpenAI-compatible `POST /v1/embeddings` + TEI-native `POST /embed`).
  - **Switched from BGE-M3 on 2026-04-19.** The prior custom FastAPI + FlagEmbedding wrapper (BGE-M3 + BGE-reranker-v2-m3) lives on disk at `services/embed/` for rollback only; no compose service references it. The legacy `embedding` column on `speech_chunks` was dropped in migration 0025 after the 1.48 M-chunk corpus was re-embedded with Qwen3 (see `docs/linkedin-embedding-rebuild-post.md` and `memory/project_embed_regression.md` for the incident that preceded the migration).
  - **GPU attach:** `deploy.resources.reservations.devices` (driver `nvidia`, capabilities `[gpu]`). `TEI_MEMORY` caps host memory at 6 GiB; VRAM sits well under the RTX 4050's 6 GiB at `--max-batch-tokens=16384`. `docker compose stop tei` releases the card cleanly.
  - **Model cache:** `embedmodels` named volume mounted at `/data` (TEI expects HF_HOME-style layout there). First boot pulls ~1.3 GB from HuggingFace; subsequent boots are seconds. Volume is shared with the legacy embed layout so a rollback wouldn't re-download.
  - **Reranker:** **gone** from the critical path. Qwen3 retrieval quality on multilingual Hansard proved strong enough that the cross-encoder rerank stage was removed. If you re-introduce reranking, do it as a separate service — don't resurrect the FlagEmbedding wrapper just for it.
  - **Throughput (2026-04-18 re-embed, RTX 4050 Mobile):** 242 k chunks in 1 h 19 m = **50.9 chunks/sec** end-to-end through the scanner's batched-UNNEST write path; pure GPU throughput ~75 chunks/sec. The end-to-end number is the one that matters for capacity planning; pure-GPU figures ignore DB write contention.
  - **Env the scanner reads:** `EMBED_URL` (default `http://tei:80`), `EMBED_MODEL_TAG` (default `qwen3-embedding-0.6b`, stored in `speech_chunks.embedding_model`), `EMBED_BATCH` (default 32).
- **Orchestration:** Docker Compose, single host, Pangolin tunnel to public.
- **Public edge:** nginx → api / frontend / uptime-kuma.

## Load-bearing conventions (do not break without discussion)

### 1. Jurisdiction-specific ID columns on `politicians`

Every upstream legislature that ships a stable integer or slug ID for its members gets a column on `politicians`:

- Federal: `openparliament_slug`
- Nova Scotia: `nslegislature_slug`
- Ontario: `ola_slug`
- BC: `lims_member_id` (int)
- Quebec: `qc_assnat_id` (int)
- Alberta: `ab_assembly_mid` (zero-padded text)

When adding a new jurisdiction, **find and persist its canonical member ID first**. It replaces name-fuzz with exact FK joins and makes sponsor / speaker resolution trivial. Current state: 840 of 13,633 `bill_sponsors` rows are FK-linked to politicians — the raw percentage is low because sub-national legislatures with sparse structured rosters (QC, NB, NL, NT, NU) dominate the denominator, while federal + NS + BC + ON + AB sponsor resolution remains >99% where the canonical-ID pattern is in place. Closing the gap means adding ID columns for the remaining legislatures, not rewriting the resolver.

### 2. Discriminated tables, not per-jurisdiction tables

One `bills` table, one `speeches` table, one `votes` table — all discriminated by `level` + `province_territory`. Do not create `bills_ab`, `bills_on`, etc.

### 3. Store `raw_html` / `raw_text` alongside parsed fields

Pattern from `bills.raw_html` — persist the upstream artifact, not just the parsed derivative. Re-parsing is cheaper than re-fetching and often the only option under WAFs (see NS budget below).

### 4. Probe hierarchy before writing a scraper

Before building any new ingestion pipeline, check in order:

1. **RSS feeds** — `/rss`, `/feed`, `/feed.xml`, `/rss.xml` at the legislative-business root.
2. **Drupal `?_format=json`** — every node on Drupal sites serializes if REST is on (the ola.org / Ontario pattern).
3. **Iframe-backed content servers** — `lims.leg.bc.ca` proxied from `www.leg.bc.ca`-style subdomain splits.
4. **Open GraphQL endpoints** — search the main SPA bundle for `graphql`, `uri:`, `baseURL`.
5. **HTML scrape** — only after 1–4 come up empty.

### 5. Research-handoff rule (user-enforced)

**Before starting any new provincial pipeline, pause and ask the user for their research pass.** No probing, no migration, no code until the user has either shared their findings or explicitly said "probe yourself."

As of 2026-04-19 the rule still applies to the four unbuilt bills pipelines (**MB, SK, PE, YT**) and to *every* provincial Hansard / votes / committees pipeline on top of the 9 live bills pipelines. Federal Hansard ingestion **has shipped** — 1.08 M federal speeches are live — so research-handoff is no longer gating federal work.

Rationale: multiple documented cases where user-led research beat agent-driven probing (ON Drupal JSON, BC LIMS JSON). See `docs/research/overview.md` (and the per-jurisdiction dossier under `docs/research/<slug>.md`) plus `feedback_research_handoff.md` for the full protocol.

### 6. Rate-limit and cache persistently

Log every upstream request by URL + etag. Re-runs should be free. NS WAF cost us 3,500 re-fetches we did not need; don't repeat that.

### 7. Idempotent Click subcommands for ingest

Every ingest command in `services/scanner/src/__main__.py` is idempotent and restartable. New pipelines follow the same shape — `ingest-<source>`, `fetch-<source>-pages`, `parse-<source>-pages`, `resolve-<source>-sponsors` — split by stage so each can be retried independently.

## Admin panel

Private `/admin` surface that lets the operator queue scanner jobs, set cron schedules, and watch a stats dashboard. Read-only public site is unaffected.

### Auth

Shared bearer token via `ADMIN_TOKEN` in `.env` (min 32 chars, `openssl rand -hex 32`). Paste into `/admin/login`; the token is stored in browser `localStorage` as `sw_admin_token` and attached as `Authorization: Bearer <token>` on every admin-scoped request. Unset token → `/api/v1/admin/*` returns **503** (clearly disabled, not wrong password). Wrong token → **401** with timing-safe comparison. Rotate by editing `.env` + `docker compose up -d api`.

### Execution pipeline

1. UI `POST /api/v1/admin/jobs` → row in `scanner_jobs` (`status='queued'`).
2. `sw-scanner-jobs` daemon polls every `JOBS_POLL_INTERVAL` seconds, claims the next row via `UPDATE … FOR UPDATE SKIP LOCKED`.
3. Spawns `python -m src <cli> [flags]` as subprocess (same scanner image), captures last 4 KB of stdout/stderr into `stdout_tail` / `stderr_tail`.
4. Flips status to `succeeded` / `failed` with `exit_code` and `finished_at`.

Schedules table (`scanner_schedules`) is expanded by the same worker — enabled rows whose `next_run_at <= now()` enqueue a new job, then `next_run_at` is advanced via `croniter`. Bash `scripts/scanner-cron.sh` schedules coexist for v1; migrating them into the DB is a deferred task.

### Curated command whitelist

The admin UI exposes a subset of the 95 scanner commands. Catalog lives in **two places that must stay in sync**:

- `services/scanner/src/jobs_catalog.py` (authoritative for the worker, maps `key` → `{cli, args}`)
- The `COMMAND_CATALOG` constant near the top of `services/api/src/routes/admin.ts` (served to the frontend form generator verbatim)

If they diverge the worker refuses the command with `unknown command` — the UI will show the stale option, but nothing unsafe runs. When adding a command, update both.

### Files involved

| Concern | Path |
|---|---|
| Queue + schedule schema | `db/migrations/0022_scanner_jobs_and_schedules.sql` |
| Worker daemon | `services/scanner/src/jobs_worker.py` + `jobs_catalog.py` |
| API middleware | `services/api/src/middleware/admin-auth.ts` |
| API routes | `services/api/src/routes/admin.ts` |
| Frontend auth hook | `services/frontend/src/hooks/useAdminAuth.ts` |
| Frontend admin shell | `services/frontend/src/components/AdminLayout.tsx` |
| Frontend pages | `services/frontend/src/pages/admin/*.tsx` |
| Command form generator | `services/frontend/src/components/CommandForm.tsx` |
| Compose service | `scanner-jobs` in `docker-compose.yml` |

### What not to do

- **Do not link `/admin` from the public nav.** Access is by direct URL.
- **Do not mount `/var/run/docker.sock` anywhere.** The worker executes via subprocess in the same container — socket mounting is root-equivalent.
- **Do not allow arbitrary commands.** Every admin-submitted command goes through the whitelist. `jobs_catalog.build_cli_args` validates args against schema before any subprocess spawn.
- **Do not log tokens.** Fastify's default logger doesn't log headers; if you enable request-body logging add a redact rule for `headers.authorization`.

## User accounts

Public passwordless auth surface, **completely orthogonal to the admin token**. A request can carry admin bearer, user session cookie, or neither — they're independent trust boundaries.

### Auth model

Magic-link only (no passwords). Email → one-time nonce → httpOnly `sw_session` JWT cookie + non-httpOnly `sw_csrf` cookie (double-submit CSRF on mutating routes). 30-day session TTL. Rotating `JWT_SECRET` is the phase-1 "force logout everyone" button.

The `services/api/src/lib/auth-token.ts` module is the designated IdP-swap seam: `signSessionToken()` / `verifySessionToken()` today emit/verify HS256 JWTs; a future Keycloak/Zitadel/Logto swap replaces those two functions with a JWKS verifier and every route that calls `requireUser` keeps working. Keep the swap surface *there* — do not spread JWT parsing across route handlers.

### Env vars (feature-disabled ergonomics)

| Var | Purpose | Unset behaviour |
|---|---|---|
| `JWT_SECRET` | HS256 session-cookie signing key. `openssl rand -hex 32`. | `/api/v1/auth/*` + `/api/v1/me/*` return **503**. |
| `SMTP_HOST` / `SMTP_PORT` | Proton submission (`smtp.protonmail.ch:587`). | Defaults applied. |
| `SMTP_USERNAME` / `SMTP_PASSWORD` | Proton per-address submission token. | Emails logged to server stdout instead of sent (dev-stub mode). Auth flow still returns 202 so local smoke-tests work. |
| `SMTP_FROM` | Friendly `From:` header. | As above. |
| `PUBLIC_SITE_URL` | Used to build magic-link + digest URLs. | Defaults to `http://localhost:5173`. |

### Execution pipeline

1. User submits email on `/login` → `POST /api/v1/auth/request-link`. Rate-limited 5/hr/IP + 3/hr/email.
2. API generates a 256-bit nonce, stores SHA-256 hash in `login_tokens` (15-min TTL), emails the plaintext nonce in a link. **Plaintext nonce is never stored.** DB leak cannot leak working links.
3. User clicks link → `/auth/verify?token=…` → `POST /api/v1/auth/verify`. API redeems token (marks `consumed_at`, no re-use), upserts `users` row, mints JWT, sets session + CSRF cookies.
4. `/api/v1/me/*` routes use `requireUser` preHandler. Mutating routes additionally require `X-CSRF-Token: <value>` matching the `sw_csrf` cookie.

### Saved searches

`saved_searches` stores `filter_payload` (the same 12-field struct `/search/speeches` validates) plus a cached `query_embedding VECTOR(1024)` computed once at save time from TEI. The alerts worker reads the cached vector directly — **TEI is never called by the worker.**

The create-endpoint reuses `baseFilterSchema` exported from `services/api/src/routes/search.ts` — single source of truth for "what's a valid search." Do not fork the shape.

### Alerts worker

Separate compose service `alerts-worker` (Python, same scanner image). Poll interval `ALERTS_POLL_INTERVAL` (default 300s). Every tick: fetch `saved_searches` due by cadence (`alert_cadence != 'none'` and `last_checked_at` older than the cadence), re-run the HNSW query constrained to `spoken_at > last_checked_at`, send digest if matches, advance watermarks. Digest renders both text/plain and text/html.

Complexity ceiling: O(users × saved-searches) HNSW queries per day. At 1000 × 3 = 3000 queries/day, trivial on the existing DB.

### Files involved

| Concern | Path |
|---|---|
| Migration | `db/migrations/0027_users_and_saved_searches.sql` |
| Token sign/verify (IdP-swap seam) | `services/api/src/lib/auth-token.ts` |
| Email adapter (nodemailer SMTP) | `services/api/src/lib/email.ts` |
| CSRF double-submit helper | `services/api/src/lib/csrf.ts` |
| User-auth preHandlers | `services/api/src/middleware/user-auth.ts` |
| Auth routes | `services/api/src/routes/auth.ts` |
| `/me` routes + saved-searches CRUD | `services/api/src/routes/me.ts` |
| Frontend auth hook | `services/frontend/src/hooks/useUserAuth.ts` |
| Frontend pages | `LoginPage`, `VerifyPage`, `AccountPage`, `SavedSearchesPage` under `services/frontend/src/pages/` |
| `SaveSearchButton` (in `/search`) | `services/frontend/src/components/SaveSearchButton.tsx` |
| Header sign-in indicator | `AuthIndicator` in `services/frontend/src/components/Layout.tsx` |
| User-auth CSS | `services/frontend/src/styles/user-auth.css` |
| Alerts worker + digest renderer | `services/scanner/src/alerts_worker.py` |
| Alerts compose service | `alerts-worker` in `docker-compose.yml` |

### What not to do

- **Do not merge admin and user auth.** Two trust boundaries, deliberately. Admin routes use `ADMIN_TOKEN` bearer header; user routes use the session cookie.
- **Do not bypass CSRF on new `/me/*` mutations.** Every POST/PATCH/DELETE on `/me/*` runs `requireCsrf` alongside `requireUser`.
- **Do not store plaintext magic-link nonces** — only `sha256(nonce)` in `login_tokens.token_hash`.
- **Do not log session cookies or CSRF tokens.** Fastify's default logger skips `Cookie`; add redact rules if custom logging is introduced.
- **Do not call TEI from the alerts worker.** The query embedding is cached on `saved_searches.query_embedding` at save time; re-embedding at alert time would scale poorly and can drift from the user's original query.
- **Do not add social login** (Google/Meta/GitHub). Wrong trust model for civic research — leaks user intent to ad platforms.
- **Do not bump to Keycloak casually.** Revisit only when a concrete need surfaces (partner newsroom SSO, OAuth clients). The `verifyToken` seam is specifically designed so the swap is mechanical.

## Blog (MDX-in-repo)

Posts live as `.mdx` files under `services/frontend/src/content/blog/`. The frontend bundles them at build time via `@mdx-js/rollup`; there is no DB, no CMS, no auth. Git history is the editorial audit trail.

### Post shape

```yaml
---
title: "Post headline"
slug: "url-slug"          # becomes /blog/<slug>
date: "2026-04-17"        # ISO-8601, drives sort order
excerpt: "One-liner hook shown on the list page + as meta description."
author: "adminatthebunker"
tags: ["launch", "semantic-search"]
draft: true               # true hides in production builds
---

MDX body…
```

Post filename convention: `YYYY-MM-DD-short-slug.mdx`. Sort is by frontmatter `date`, not filename.

### Draft workflow

- `draft: true` → post is hidden in production; visible in dev (`npm run dev`) because `useBlogPosts` checks `import.meta.env.DEV`.
- For a staging preview build that shows drafts, pass `--build-arg VITE_SHOW_DRAFTS=1` to `docker compose build frontend`. The resulting image exposes draft posts; revert with a plain rebuild.
- To ship a draft: flip `draft: false` in the frontmatter, commit, rebuild the frontend image. That's the whole publish flow.

### Publish checklist

1. Edit `services/frontend/src/content/blog/<file>.mdx`; flip `draft: true` → `draft: false`.
2. `docker compose build frontend && docker compose up -d frontend`.
3. Verify at `/blog` and `/blog/<slug>`. Document title, meta description, and auto-linked headings come from MDX plugins (`remark-frontmatter`, `rehype-slug`, `rehype-autolink-headings`).
4. Commit the change.

### What not to put in the blog

- Nothing that belongs in `docs/` (architecture, schema, operations — those are authoritative docs; blog is narrative).
- No credentials, tokens, or private URLs.
- No machine-generated status logs — the blog is for readers, not internal tracking.

## Database reference

### Core tables

- `politicians` — 3,086 rows, per-jurisdiction slug columns (see convention #1).
- `politician_terms` — role / party / level / constituency over time.
- `politician_socials` — platform handles, no content. Provenance columns added in 0026.
- `politician_committees`, `politician_offices` — supporting detail.
- `politician_changes` — audit trail of mutations to the politicians table.
- `organizations` — referendum orgs, advocacy, media (20 seeded).
- `websites`, `infrastructure_scans`, `scan_changes` — the hosting-sovereignty layer.
- `constituency_boundaries` — temporal (`effective_from` / `effective_to`) as of 0021.

### Legislative tables (current row counts, 2026-04-19)

- `legislative_sessions` — jurisdiction + parliament + session.
- `bills` / `bill_events` / `bill_sponsors` — **18,782 bills** across NS (3,522) / AB (11,133) / BC (2,276) / ON (104) / QC (497) / NB (33) / NL (1,193) / NT (20) / NU (4). 13,633 sponsor rows; 840 FK-linked to politicians (see convention #1 for why that ratio is not a regression).
- `speeches` / `speech_chunks` / `speech_references` — **1,716,550 speeches, 2,144,232 chunks**, of which **2,067,709 (96.4%) carry Qwen3-Embedding-0.6B vectors** in `speech_chunks.embedding` (vector(1024), HNSW index `idx_chunks_embedding`). Federal Hansard is fully ingested; provincial Hansard ingesters are the next build-out.
- `votes` / `vote_positions` — **still does not exist.** `0018_votes.sql` remains on disk and intentionally unapplied pending real NT/NU consensus-gov't data.
- `jurisdiction_sources` — coverage + blockers (seeded with all 14 jurisdictions). Feeds the public coverage dashboard. Refreshed by `refresh-coverage-stats` scanner command.
- `correction_submissions` — corrections inbox (web + email sources).
- `scanner_jobs` / `scanner_schedules` — admin queue + cron (see Admin panel section).

### Embedding column naming

`speech_chunks` currently has a single vector column named `embedding` (plus `embedding_model` / `embedded_at`). The earlier blue-green `embedding_next` column from the Qwen3 migration (0023) was renamed back to `embedding` in 0025 once the BGE-M3 column was dropped. Do **not** reintroduce `_next` suffixes — one canonical column, one HNSW index.

### Materialized views

- `map_politicians` / `map_organizations` — refreshed via `SELECT refresh_map_views()` after scan batches.

## Migrations

Numbered sequentially under `db/migrations/`. No automated runner — apply manually with:

```bash
docker exec -i sw-db psql -U sw -d sovereignwatch -v ON_ERROR_STOP=1 < db/migrations/<file>.sql
```

**Latest applied migrations (2026-04-19):**
- `0022_scanner_jobs_and_schedules.sql` — admin queue + cron table.
- `0023_embedding_next.sql` — parallel Qwen3 vector column for blue-green re-embed.
- `0024_fix_federal_session_tagging.sql` — retag federal speeches into correct parliaments.
- `0025_drop_legacy_embedding_column.sql` — drop BGE-M3 column, rename `embedding_next` → `embedding`.
- `0026_politician_photo_local.sql` and `0026_politician_socials_provenance.sql` — two files share the `0026` number (accidental collision; both applied). When adding the next migration bump to `0027` regardless; do not back-renumber.

**Intentionally unapplied:** `0018_votes.sql` — waits on real NT/NU consensus-gov't data before landing. See `docs/plans/semantic-layer.md` for the rationale per file.

## Command reference

Operator CLI lives at `cli/sovpro` (bash wrapper over `docker compose`).

```bash
sovpro up                 # docker compose up -d --build
sovpro logs <service>     # tail a service
sovpro db psql            # interactive psql as sw on sovereignwatch
sovpro db backup          # writes backups/<timestamp>.sql.gz
sovpro ingest all         # seed-orgs + ingest-mps + ingest-mlas + ingest-councils + ingest-ab-extras
sovpro scan full          # scan --stale-hours 0 (re-scan everything, ignore staleness)
sovpro doctor             # sanity-check all services
docker compose run --rm scanner python -m src <subcommand>
```

The Click entrypoint is `python -m src` (module is `src`, not `scanner` — the compose mount is `./services/scanner/src:/app/src`). Every Click subcommand is in `services/scanner/src/__main__.py` — **95 `@cli.command` decorators** as of 2026-04-19. Grep there for the full list.

## Development workflow

1. **Read the relevant plan doc first.** Skip and you'll end up rebuilding what's already there.
2. **Check `jurisdiction_sources` / the research doc** before assuming a data source is live.
3. **Run locally first** — `sovpro up` + `sovpro db psql` to validate queries before writing API/scanner code.
4. **Migrations are forward-only.** Bump the number, don't edit an applied migration.
5. **Each Click command should log what it did** — bill counts, sponsor resolution rate, HTML cache hits. Ingest without telemetry is unverifiable.
6. **UI changes need a browser check** — run the dev server, hit the actual page. Type-check passes ≠ feature works.
7. **Git identity:** commits are authored by `adminatthebunker <admin@thebunkerops.ca>`.

## Style

- Python: type hints on public functions; asyncio throughout the scanner.
- TypeScript: strict mode, zod for API request/response schemas.
- SQL: lowercase keywords in migrations, UUIDs for primary keys, `NOT NULL` by default, `created_at` / `updated_at` timestamps, `raw JSONB` for source payloads.
- Commit messages: lowercase imperative, component prefix (`feat(frontend):`, `fix(map):`, `infra:`, etc.). See `git log` for examples.

## What not to do

- **Do not make this apolitical.** It is civic transparency rooted in democratic values and progressive stances. Non-neutrality is a feature.
- **Do not add hosted API dependencies** (OpenAI, Cohere, etc.) in the critical path. Self-hosted first; hosted only with user sign-off. The one sanctioned exception is the Anthropic API behind `ANTHROPIC_API_KEY`, used only by `agent-missing-socials` (Tier-3 socials backfill) and gated to abort cleanly when unset.
- **Do not build per-jurisdiction UI variants** for the same data type. One speeches view, filterable.
- **Do not redact non-politician names from source text.** Don't surface them as first-class entities either — the distinction lives in retrieval UX, not at ingest.
- **Do not adopt OpenCivicData `ocd-person/*` IDs.** Per-jurisdiction slug columns + `politician_terms` covers the Canadian context.
- **Do not create new `CLAUDE.md` / `AGENTS.md` files in subdirectories** without asking. This root one is the authority.

## When in doubt

Ask the user. Research-handoff rule is a specific instance of a broader principle: short pauses for alignment beat long rollbacks.
