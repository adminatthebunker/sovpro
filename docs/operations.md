# Operations Guide

## First boot

```bash
sovpro init                  # creates .env + git repo + data/, backups/ dirs
$EDITOR .env                 # set DB_PASSWORD + WEBHOOK_SECRET
make geoip-download          # instructions for GeoLite2 .mmdb files
sovpro up                    # build + start
sovpro doctor                # sanity check
```

After ~30 seconds the database is ready and `scanner-cron` will:
1. seed organizations
2. ingest federal MPs, Alberta MLAs, Edmonton + Calgary councils
3. scan everything
4. refresh map views

You can watch progress with:

```bash
sovpro logs scanner-cron
```

## Common operations

| Goal | Command |
|------|---------|
| Re-scan everything | `sovpro scan full` |
| Re-scan stale only | `sovpro scan` |
| Re-ingest politicians | `sovpro ingest all` |
| Inspect DB | `sovpro db psql` |
| Backup | `sovpro db backup` |
| See current sovereignty stats | `sovpro stats` |
| Tail logs | `sovpro logs api` |
| Restart one service | `sovpro rebuild api` |

## Embedding service

The `tei` service runs HuggingFace Text Embeddings Inference serving Qwen3-Embedding-0.6B (1024-dim, fp16) on the RTX 4050 GPU. Image, compose wiring, throughput numbers, and the BGE-M3 → Qwen3 cutover history live in `CLAUDE.md` § Stack. Operator details below.

- **Health check.**
  ```bash
  docker exec sw-tei curl -s http://localhost:80/health
  docker logs sw-tei 2>&1 | head  # expect "Starting Qwen3 model on Cuda" near the top
  ```
- **Overrides** via `.env`:
  ```env
  TEI_MODEL=Qwen/Qwen3-Embedding-0.6B       # HF repo ID
  TEI_MAX_CLIENT_BATCH=64                   # max array length per HTTP call
  TEI_MAX_BATCH_TOKENS=16384                # token-budget across the batch
  TEI_MEMORY=6g                             # soft host-RAM cap (not VRAM)
  EMBED_CUDA_DEVICES=all                    # CUDA_VISIBLE_DEVICES-style
  EMBED_GPU_COUNT=all
  ```
  Any change requires `docker compose up -d tei` to recreate the container.
- **Hot-path endpoints.**
  - `POST /embed` (TEI-native) — body `{"inputs": ["..."], "normalize": true}` → bare JSON array of float arrays.
  - `POST /v1/embeddings` (OpenAI-compatible) — body `{"input": [...], "model": "..."}` → `{data: [{embedding: [...]}, ...]}`.
  - `GET /health` — minimal liveness; weights load on first request (lazy).
- **Query-time instruction wrapper (critical).** Qwen3-Embedding needs queries prefixed with an instruction; documents are NOT prefixed. Without the wrapper NDCG drops from ~0.43 to ~0.22. Format:
  ```
  Instruct: Given a parliamentary search query, retrieve relevant Canadian Hansard speech excerpts
  Query: {user query}
  ```
  Indexing code writes documents unwrapped. See `docs/plans/search-features-handoff.md` for the full retrieval contract.
- **Scanner env.** The scanner reads `EMBED_URL` (default `http://tei:80`), `EMBED_MODEL_TAG` (default `qwen3-embedding-0.6b`, written to `speech_chunks.embedding_model`), and `EMBED_BATCH` (default 32).
- **Monitoring.** `docker stats sw-tei --no-stream` for host-side CPU/RAM; `nvidia-smi` on the host for GPU utilisation + VRAM; `docker logs sw-tei -f` for model-load progress. `docker compose stop tei` releases the card cleanly when you need it for other work.

## Admin panel

`/admin` on the public frontend surfaces a private operator console: queue any whitelisted scanner command, set cron schedules, and watch dashboard counts (speeches, chunks, pending embeds, job throughput).

- **Enable:** set `JWT_SECRET` + SMTP in `.env`, then `docker compose up -d api scanner-jobs`. Admin access is "signed-in user with `is_admin = true`" — no separate ADMIN_TOKEN anymore.
- **Promote an account:** sign in once via the magic-link flow (`/login` → email → verify), then in psql run `UPDATE users SET is_admin = true WHERE email = 'you@example.com';`. The very next admin request sees the new role (re-read per request).
- **Login:** browse to `/admin`; if not signed in, you'll be bounced to `/login?from=/admin`. Signed-in non-admins see a small "not authorized" surface rather than a redirect loop.
- **Demote / force logout:** `UPDATE users SET is_admin = false WHERE email = '…';` (instant for admin routes). To fully sign someone out, rotate `JWT_SECRET` — invalidates every session in one move.
- **Disabled state:** with `JWT_SECRET` unset, `/api/v1/auth/*` + `/api/v1/me/*` + `/api/v1/admin/*` all return **503**.

### Scheduling commands

- Use `/admin/schedules` → "New schedule". Cron is 5-field UTC (`m h dom mon dow`).
- Schedules that fire too fast + job duration > interval: the worker is single-threaded, so overlapping fires just stack in the queue. Drop the cron frequency or split the work.
- `next_run_at` updates after each fire; stale rows (worker was down) re-sync on next worker boot.
- To disable temporarily, toggle the `enabled` checkbox — no deletion needed.

### Operator-friendly commands

All catalog entries live in `services/scanner/src/jobs_catalog.py`. Out of the box, the admin panel exposes:

- Federal Hansard: `ingest-federal-hansard`, `chunk-speeches`, `embed-speech-chunks`
- NS Hansard: `ingest-ns-mlas`, `ingest-ns-hansard`, `resolve-ns-speakers`
- Provincial bills: one entry per live pipeline (AB/BC/NB/NL/NS/ON/QC + their RSS variants)
- Rosters: `ingest-mps`, `ingest-senators`, `ingest-mlas`, `ingest-councils`, `ingest-legislatures`
- Enrichment: `harvest-personal-socials`
- Maintenance: `refresh-views`, `seed-orgs`, `scan`

Adding a new command requires updates in **two** spots (see CLAUDE.md § Admin panel).

### Worker restart + stuck jobs

`sw-scanner-jobs` is a long-running container. On boot it requeues any `status='running'` row older than `JOBS_STUCK_MINUTES` (default 10 min) with an `error='recovered after worker restart'` note. That makes `docker compose restart scanner-jobs` safe even mid-job — the current run is abandoned, the DB row flips to queued, the next worker picks it up.

## Billing rail (premium reports phase 1a)

Operator procedures (Stripe enablement, admin comp, ledger correction, webhook secret rotation, verification SQL) live in [`docs/runbooks/billing-rail-operations.md`](runbooks/billing-rail-operations.md). Design context: [`docs/plans/premium-reports.md`](plans/premium-reports.md). Load-bearing invariants: `CLAUDE.md` § Premium reports / billing rail.

`STRIPE_SECRET_KEY` unset → feature disabled. UI hides purchase buttons; `POST /me/credits/checkout` returns 503; `POST /webhooks/stripe` returns 200-discard (NOT 5xx — Stripe would retry for 72h and burn its budget).

## Scheduled jobs

`scanner-cron` runs an hourly loop:
- Quick scan every hour for sites stale > 6h
- Full sweep daily at 06:00 UTC
- Re-ingest from Open North weekly Sunday 02:00 UTC

## Backups

```bash
sovpro db backup                    # writes backups/<timestamp>.sql.gz
sovpro db restore backups/foo.sql.gz
```

For production, copy `backups/` to off-host storage (S3, B2, etc) on a cron.

## Deploying

### Local/single host
```bash
sovpro up
```

### Remote single host
```bash
sovpro deploy remote user@host
```
This rsyncs the repo (excluding .env, .git, data/*.mmdb) and runs `docker compose up -d --build` on the remote. You must scp `.env` and the GeoLite2 files to the remote yourself once.

### Behind Pangolin / Cloudflare Tunnel
Point your tunnel at `nginx:80`. nginx is the only public surface — the API, DB, and Kuma stay on the internal network.

## Disaster recovery

If a release breaks the schema:

```bash
sovpro down
sovpro db restore backups/<last-good>.sql.gz
git checkout <last-good-tag>
sovpro up
```

If the DB volume itself is corrupted:

```bash
sovpro db reset             # wipes pgdata (irreversible)
sovpro up
sovpro db restore backups/<last-good>.sql.gz
```
