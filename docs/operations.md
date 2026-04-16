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

The `embed` service hosts BGE-M3 + BGE-reranker-v2-m3 on CPU. It's reachable inside the compose network as `http://embed:8000`.

- **Model cache.** First `/embed` or `/rerank` call downloads ~2 GB of weights into the `embedmodels` named volume. `docker compose down && up` is safe — the volume persists. `docker volume rm sovpro_embedmodels` forces a re-download.
- **CPU / memory cap.** Defaults to 4 CPUs + 4 GiB via compose `deploy.resources.limits`. Override in `.env`:

  ```env
  EMBED_CPUS=6          # host has spare headroom
  EMBED_MEMORY=6g
  EMBED_THREADS=6       # keep torch/OMP in sync with the cpu cap
  ```

  Any change requires `docker compose up -d embed` to recreate the container.
- **Hot-path endpoints.**
  - `POST /embed` — body `{"texts": ["..."], "return_tokens": false}` → `{items: [{embedding: [...1024], token_count?: int}], elapsed_ms, dim, model}`
  - `POST /rerank` — body `{"pairs": [{"query": "...", "document": "..."}]}` → `{scores: [...], elapsed_ms}`
  - `GET /health` — `{ok, embed_model, rerank_model, embed_loaded, rerank_loaded}`. Loads are lazy: healthy does NOT mean models are warm.
- **Performance expectations (4-CPU cap, 2026-04-16).** ~1 text/sec dense embedding at batch=32. 50k speeches ≈ 14 hours. Bump `EMBED_CPUS`/`EMBED_THREADS` in lockstep to trade host usability for throughput.
- **Monitoring.** `docker stats sw-embed --no-stream` shows live CPU / memory utilisation. `docker logs sw-embed -f` surfaces model-load progress on first call.

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
