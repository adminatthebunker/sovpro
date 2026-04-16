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
            │ HTTP /embed, /rerank
            │
       ┌────▼──────────────┐              ┌─────────────────────┐
       │  Embed (Python)   │              │  scanner-cron       │
       │  FastAPI + BGE-M3 │              │  loop + bootstrap   │
       │  CPU, 4-core cap  │              └─────────────────────┘
       └───────────────────┘
```

## Services

### `db` (PostgreSQL 16 + PostGIS + pgvector)
- Built from `db/Dockerfile` (extends `postgis/postgis:16-3.4` with `postgresql-16-pgvector`)
- Runs `db/init.sql` on first start to create schema + materialized views
- Runs `db/seed.sql` next to populate referendum organizations
- Migrations in `db/migrations/` applied manually (see CLAUDE.md)
- pgvector powers HNSW indexes on `speech_chunks.embedding`; `unaccent` for FR tsvector
- Persists in named volume `pgdata`

### `api` (Node 20 + Fastify)
- `/api/v1/politicians`, `/organizations`, `/map/*`, `/stats/*`, `/changes`, `/webhooks/change`, `/coverage`, `/og`, `/parties`, `/committees`, `/socials`, `/lookup`, `/alberta`, politicians' `/openparliament` sub-resource
- pg.Pool with 10 connections
- HMAC-verifies incoming `change` webhooks
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
- One-shot CLI invoked via `docker compose run --rm scanner <cmd>`
- 70+ Click subcommands covering ingestion, enrichment, legislative pipelines, gap fillers
- Concurrency capped via `SCANNER_CONCURRENCY` (default 16)
- Uses MaxMind GeoLite2 DBs from `./data/`
- Legislative bills pipelines live for 9 of 13 sub-national legislatures (NS, ON, BC, QC, AB, NB, NL, NT, NU) — see `docs/scanner.md`

### `embed` (Python 3.11 + FastAPI + FlagEmbedding)
- Self-hosted BGE-M3 (dense embeddings, 1024-dim, multilingual) + BGE-reranker-v2-m3 (cross-encoder scoring)
- `POST /embed`, `POST /rerank`, `GET /health` on `embed:8000` inside the `sw` network
- Model weights cache in the `embedmodels` named volume — first call downloads ~2 GB, subsequent boots are fast
- CPU capped at 4 cores / 4 GiB RAM by default (override via `.env`); torch threads bounded to match
- No outbound API dependency once models are cached; ~1 text/sec throughput at batch=32 on the baseline host

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
