# Architecture

Canadian Political Data is a small fleet of cooperating services orchestrated by Docker Compose. Each service owns one concern.

```
                ┌───────────────────────────────────────┐
                │              nginx :80/:443           │
                │  /api/* → API   /status/* → Kuma      │
                │  /*     → Frontend                    │
                └────────────┬──────────────┬───────────┘
                             │              │
        ┌────────────────────▼────┐   ┌─────▼──────────┐
        │  Frontend (React/TS)   │   │  API (Fastify) │
        │  Vite build + MDX blog │   │  Node 20       │
        │  served by nginx:alpine│   │  zod-validated │
        └────────────────────────┘   └──┬─────────────┘
                                        │ pg pool
                                ┌───────▼───────────────────┐
                                │  PostgreSQL 16            │
                                │  + PostGIS 3.4 + pgvector │
                                │  + materialized views     │
                                └───────▲───────────────────┘
                                        │
                ┌───────────────────────┴───────────┐
                │                                   │
       ┌────────▼──────────┐              ┌─────────▼──────────┐
       │  Scanner (Python) │              │  Change detection  │
       │  asyncio + dns +  │              │  ghcr.io/.../change│
       │  geoip2 + httpx   │              │  → POST /webhooks  │
       └────┬──────────────┘              └────────────────────┘
            │ HTTP POST /embed (or /v1/embeddings)
            │
       ┌────▼────────────────────────┐   ┌─────────────────────┐
       │  TEI (HuggingFace)          │   │  scanner-cron       │
       │  Qwen3-Embedding-0.6B fp16  │   │  loop + bootstrap   │
       │  GPU (RTX 4050 Mobile)      │   └─────────────────────┘
       └─────────────────────────────┘
```

## Services

### `db` (PostgreSQL 16 + PostGIS + pgvector)
- Built from `db/Dockerfile` (extends `postgis/postgis:16-3.4` with `postgresql-16-pgvector`)
- Runs `db/init.sql` on first start to create schema + materialized views
- Runs `db/seed.sql` next to populate referendum organizations
- Migrations in `db/migrations/` applied manually (see CLAUDE.md)
- pgvector powers HNSW indexes on `speech_chunks.embedding`; `unaccent` for FR tsvector
- **HNSW database-level tuning** (applied via `ALTER DATABASE`; verify with `SELECT unnest(setconfig) FROM pg_db_role_setting WHERE setdatabase = (SELECT oid FROM pg_database WHERE datname='sovereignwatch')`):
  - `hnsw.iterative_scan = relaxed_order` — without this, filtered semantic searches (e.g. `province_territory='BC'`) return 0 rows because the HNSW walk's default `ef_search=40` is dominated by the 1.5M federal chunks; top-K rarely includes any filter-matching rows. With iterative scan, pgvector keeps walking the graph until enough filter-matches accumulate.
  - `hnsw.ef_search = 200` — raised from 40 to give the iterative scan a larger candidate pool. Provincial-filtered queries run in ~130ms; unfiltered federal stays at ~25ms. Both values persist across DB restarts.
- Persists in named volume `pgdata`

### `api` (Node 20 + Fastify)
- `/api/v1/politicians`, `/organizations`, `/map/*`, `/stats/*`, `/changes`, `/webhooks/change`, `/coverage`, `/og`, `/parties`, `/committees`, `/socials`, `/lookup`, `/alberta`, politicians' `/openparliament` sub-resource
- User account surface: `/auth/*`, `/me/*` (profile, saved searches, corrections, credits, rate-limit requests)
- Admin surface: `/admin/*` (jobs, schedules, socials review, corrections, users + credit grants + tier adjustments)
- Billing: `/me/credits/*` (balance, packs, checkout), `/webhooks/stripe` (plugin-scoped raw-body parser, two-layer idempotency via `stripe_webhook_events` PK + `credit_ledger` partial unique index)
- pg.Pool with 10 connections
- HMAC-verifies incoming `change` webhooks
- Stripe SDK wrapper in `src/lib/stripe.ts` is the sole importer of the `stripe` npm package — other consumers (dev-API plan, future premium features) reuse the same wrapper
- Zod validation on every query / body
- Health: `GET /health`

### `frontend` (React + Vite + Leaflet + MDX)
- Built once at image build time (`vite build`)
- Served as static files by an internal `nginx:alpine`
- Dark CARTO basemap; toggleable layers for constituencies, server pins, connection lines
- Routes: `/`, `/map`, `/politicians`, `/politicians/:id`, `/coverage`, `/blog`, `/blog/:slug`
- Blog posts authored as MDX in `src/content/blog/*.mdx` — see CLAUDE.md § Blog
- Build-time only: `VITE_SHOW_DRAFTS=1` exposes draft posts for preview builds

### `scanner` (Python 3.13 async)
- One-shot CLI invoked via `docker compose run --rm scanner <cmd>` (module is `python -m src`, not `python -m scanner`)
- 115 Click subcommands covering ingestion, enrichment, legislative pipelines, embeddings, gap fillers
- Concurrency capped via `SCANNER_CONCURRENCY` (default 16)
- Uses MaxMind GeoLite2 DBs from `./data/`
- Legislative bills pipelines live for 10 of 13 sub-national legislatures (NS, ON, BC, QC, AB, NB, NL, NT, NU, MB) — see `docs/scanner.md`
- Talks to embeddings via `EMBED_URL` (default `http://tei:80`); model tag stored in `speech_chunks.embedding_model` via `EMBED_MODEL_TAG` (default `qwen3-embedding-0.6b`)

### `tei` (HuggingFace Text Embeddings Inference — Qwen3-Embedding-0.6B)
- Image `ghcr.io/huggingface/text-embeddings-inference:89-1.9` serving **Qwen3-Embedding-0.6B** at fp16 on an NVIDIA RTX 4050 Mobile (CUDA 12.4, sm_89)
- Endpoints: TEI-native `POST /embed`, OpenAI-compatible `POST /v1/embeddings`, `GET /health` on `tei:80` inside the `sw` network
- Output: 1024-dim dense vectors, L2-normalised when `normalize: true` is passed
- Model cache in the `embedmodels` named volume (mounted at `/data`); first boot pulls ~1.3 GB, subsequent boots are seconds
- `--max-client-batch-size=64`, `--max-batch-tokens=16384`, `--dtype=float16` by default (overridable via `TEI_MAX_CLIENT_BATCH`, `TEI_MAX_BATCH_TOKENS`, env)
- Measured ~75 chunks/sec pure-GPU throughput, 50.9 chunks/sec end-to-end through the scanner's batched-UNNEST write path (2026-04-18 re-embed landed 242 k chunks in 1 h 19 m)
- Replaced the prior custom FastAPI + FlagEmbedding wrapper (BGE-M3 + BGE-reranker-v2-m3) on 2026-04-19. Legacy code still lives at `services/embed/` for rollback. Reranker stage is **no longer in the critical path** — Qwen3 retrieval quality cleared the bar without it

### `scanner-cron`
- Long-running sidecar that wakes hourly, runs the right scan job for the time of day
- Bootstraps a fresh database (seeds + ingests) on first boot
- Falls back gracefully if any sub-step fails

### `scanner-jobs`
- Long-running worker that drives the private `/admin` panel.
- Polls the `scanner_jobs` table every `JOBS_POLL_INTERVAL` seconds; claims the next queued row with `SELECT FOR UPDATE SKIP LOCKED`, one job at a time.
- Expands any due `scanner_schedules` rows into new queued jobs using `croniter`.
- Runs each job as a subprocess of `python -m src <cli>` inside the same container image — no docker-socket mount, no cross-container dispatch.
- Captures rolling 4 KB tails of stdout/stderr into the job row so failures are debuggable without log spelunking.
- On boot recovers any `status='running'` row older than `JOBS_STUCK_MINUTES` (default 10) back to `queued`.

### `change-detection` (external `ghcr.io/thedurancode/change`)
- Watches website content for changes
- POSTs to `/api/v1/webhooks/change` with HMAC sig

### `uptime-kuma`
- Independent uptime monitoring UI under `/status/`

### `nginx`
- Public entrypoint; routes traffic and adds security headers
- Designed to sit behind Pangolin (or Cloudflare Tunnel) in production

## Data flow

1. **Ingestion** (`scanner ingest-mps`) → upserts `politicians` + `websites` + `constituency_boundaries`.
2. **Scan** (`scanner scan`) → for each website, DNS → GeoIP → TLS → HTTP → classify → INSERT into `infrastructure_scans` and any deltas into `scan_changes`.
3. **View refresh** (`scanner refresh-views`) → `REFRESH MATERIALIZED VIEW CONCURRENTLY map_politicians, map_organizations`.
4. **Read** (`API GET /map/geojson`) → query materialized view → emit GeoJSON FeatureCollection of polygons + pins + lines.
5. **Render** (frontend) → React Leaflet draws layers tinted by sovereignty tier color.

## Sovereignty tier classification

Encoded in `services/scanner/src/classify.py`. The decision tree:

| Tier | Condition |
|------|-----------|
| 1 | `ip_country == 'CA'` AND ASN/org marked Canadian-owned |
| 2 | `ip_country == 'CA'` (foreign provider, Canadian DC) |
| 3 | CDN detected (Cloudflare/Cloudfront/Akamai/etc) AND no clear non-US origin |
| 4 | `ip_country == 'US'` |
| 5 | Anything else outside CA/US |
| 6 | Scan failed or no IP resolved |

The classifier is deliberately conservative: when in doubt, mark CDN-fronted (3) rather than guessing the origin.
