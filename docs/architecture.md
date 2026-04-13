# Architecture

SovereignWatch is a small fleet of cooperating services orchestrated by Docker Compose. Each service owns one concern.

```
                ┌───────────────────────────────────────┐
                │              nginx :80/:443           │
                │  /api/* → API   /status/* → Kuma      │
                │  /*     → Frontend                    │
                └────────────┬──────────────┬───────────┘
                             │              │
        ┌────────────────────▼────┐   ┌─────▼──────────┐
        │  Frontend (React/TS)   │   │  API (Fastify) │
        │  Vite build, served    │   │  Node 20       │
        │  by nginx:alpine       │   │  zod-validated │
        └────────────────────────┘   └──┬─────────────┘
                                        │ pg pool
                                ┌───────▼─────────────┐
                                │  PostgreSQL 16      │
                                │  + PostGIS 3.4      │
                                │  + materialized     │
                                │    views            │
                                └───────▲─────────────┘
                                        │
                ┌───────────────────────┴───────────┐
                │                                   │
       ┌────────▼──────────┐              ┌─────────▼──────────┐
       │  Scanner (Python) │              │  Change detection  │
       │  asyncio + dns +  │              │  ghcr.io/.../change│
       │  geoip2 + httpx   │              │  → POST /webhooks  │
       └────────┬──────────┘              └────────────────────┘
                │
       ┌────────▼──────────┐
       │  scanner-cron     │
       │  loop + bootstrap │
       └───────────────────┘
```

## Services

### `db` (PostgreSQL 16 + PostGIS)
- Runs `db/init.sql` on first start to create schema + materialized views
- Runs `db/seed.sql` next to populate referendum organizations
- Persists in named volume `pgdata`

### `api` (Node 20 + Fastify)
- `/api/v1/politicians`, `/organizations`, `/map/*`, `/stats/*`, `/changes`, `/webhooks/change`
- pg.Pool with 10 connections
- HMAC-verifies incoming `change` webhooks
- Health: `GET /health`

### `frontend` (React + Vite + Leaflet)
- Built once at image build time (`vite build`)
- Served as static files by an internal `nginx:alpine`
- Dark CARTO basemap; toggleable layers for constituencies, server pins, connection lines
- Three tabs: Map, Referendum spotlight, Changes feed

### `scanner` (Python 3.13 async)
- One-shot CLI invoked via `docker compose run --rm scanner <cmd>`
- Three commands: `ingest-*`, `seed-orgs`, `scan`
- Concurrency capped via `SCANNER_CONCURRENCY` (default 16)
- Uses MaxMind GeoLite2 DBs from `./data/`

### `scanner-cron`
- Long-running sidecar that wakes hourly, runs the right scan job for the time of day
- Bootstraps a fresh database (seeds + ingests) on first boot
- Falls back gracefully if any sub-step fails

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
