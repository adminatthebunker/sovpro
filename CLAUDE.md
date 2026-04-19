# CLAUDE.md — SovereignWatch

Project-level instructions for any AI agent working in this repo. Read before writing code.

## One-line purpose

SovereignWatch is becoming **the definitive source of Canadian political data** — who represents whom, what they've said, how they've voted, where their infrastructure lives. See `docs/goals.md` for the full product framing. It is **not apolitical**; it takes progressive and democratic stances rooted in access-to-information principles.

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
- **Embed service:** Python + FastAPI + FlagEmbedding (BGE-M3 + BGE-reranker-v2-m3), **CUDA fp16 inference**, `services/embed/`. Exposes `POST /embed` (1024-dim dense) and `POST /rerank` (cross-encoder scores). Model cache on `embedmodels` named volume. Service hostname on the `sw` network is `embed:8000`.
  - **Base image:** `pytorch/pytorch:2.5.1-cuda12.4-cudnn9-runtime`. GPU attached via `deploy.resources.reservations.devices` (driver `nvidia`, capabilities `[gpu]`). `/health` reports `device`, `device_name`, and `fp16` so the mode is introspectable.
  - **Override to CPU mode** (for hosts without a GPU): swap the Dockerfile base back to `python:3.11-slim`, flip `use_fp16=False` at the top of `server.py`, remove the `reservations.devices` block in compose. The CPU variant lives in git history at commit `ef26d03` for reference.
  - **Host memory cap** defaults to 6 GiB (`EMBED_MEMORY` in `.env`). VRAM usage is ~3 GiB at batch=64, well under the RTX 4050's 6 GiB. `docker compose stop embed` releases the card cleanly if you need it for something else.
  - Benchmark on RTX 4050 Mobile (2026-04-16): ~68 texts/sec at batch=32, ~125 at batch=64, ~205 at batch=128. 50k speeches in ~4 min at peak. 1M speeches (all federal Hansard 1994+) ≈ 80 min of continuous compute. Cross-precision cosine similarity between CPU fp32 and GPU fp16 vectors measured at 0.999999 — existing vectors are compatible with future GPU-embedded queries.
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

When adding a new jurisdiction, **find and persist its canonical member ID first**. It replaces name-fuzz with exact FK joins and makes sponsor / speaker resolution trivial. Sponsor resolution currently stands at 99.7% (360/361) because of this pattern.

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

As of 2026-04-16 the rule still applies to the four unbuilt bills pipelines (**MB, SK, PE, YT**) and to *every* Hansard / votes / committees pipeline on top of the 9 live bills pipelines.

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

The admin UI exposes a subset of the ~70 scanner commands. Catalog lives in **two places that must stay in sync**:

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

- `politicians` — 1,815 rows, per-jurisdiction slug columns (see convention #1).
- `politician_terms` — role / party / level / constituency over time (expand use in phase 0).
- `politician_socials` — platform handles, no content.
- `politician_committees`, `politician_offices` — supporting detail.
- `organizations` — referendum orgs, advocacy, media (20 seeded).
- `websites`, `infrastructure_scans`, `scan_changes` — the hosting-sovereignty layer.
- `constituency_boundaries` — current only; phase-0 extends with `effective_from` / `effective_to`.

### Legislative tables

- `legislative_sessions` — jurisdiction + parliament + session.
- `bills` / `bill_events` / `bill_sponsors` — shipped for NS / AB / BC / ON / QC / NB / NL / NT / NU (9 jurisdictions, ~3,945 bills, 393/394 sponsors FK-linked).
- `speeches` / `speech_chunks` / `speech_references` — tables exist (0015–0017 applied). Zero rows yet; ingesters are the next implementation step.
- `votes` / `vote_positions` — `0018_votes.sql` drafted but **not applied**. Wait for real NT/NU consensus-gov't data before landing.
- `jurisdiction_sources` — coverage + blockers (seeded with all 14 jurisdictions). Feeds the public coverage dashboard.
- `correction_submissions` — corrections inbox (web + email sources).

### Materialized views

- `map_politicians` / `map_organizations` — refreshed via `SELECT refresh_map_views()` after scan batches.

## Migrations

Numbered sequentially under `db/migrations/`. No automated runner — apply manually with:

```bash
docker exec -i sw-db psql -U sw -d sovereignwatch -v ON_ERROR_STOP=1 < db/migrations/<file>.sql
```

**Latest is `0021_constituency_boundary_temporal.sql`.** `0018_votes.sql` sits on disk but is intentionally unapplied pending real NT/NU consensus-gov't data. See `docs/plans/semantic-layer.md` for the rationale per file.

## Command reference

```bash
sovpro up                 # docker compose up -d --build
sovpro logs <service>     # tail a service
sovpro db psql            # interactive psql as sw on sovereignwatch
sovpro db backup          # writes backups/<timestamp>.sql.gz
sovpro ingest all         # re-ingest all Open North reps
sovpro scan full          # re-scan every tracked website
sovpro doctor             # sanity-check all services
docker compose run --rm scanner python -m scanner <subcommand>
```

Every scanner Click subcommand is in `services/scanner/src/__main__.py` — grep there for the full list (70+ commands).

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
- **Do not add hosted API dependencies** (OpenAI, Cohere, etc.) in the critical path. Self-hosted first; hosted only with user sign-off.
- **Do not build per-jurisdiction UI variants** for the same data type. One speeches view, filterable.
- **Do not redact non-politician names from source text.** Don't surface them as first-class entities either — the distinction lives in retrieval UX, not at ingest.
- **Do not adopt OpenCivicData `ocd-person/*` IDs.** Per-jurisdiction slug columns + `politician_terms` covers the Canadian context.
- **Do not create new `CLAUDE.md` / `AGENTS.md` files in subdirectories** without asking. This root one is the authority.

## When in doubt

Ask the user. Research-handoff rule is a specific instance of a broader principle: short pauses for alignment beat long rollbacks.
