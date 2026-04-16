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

The `embed` service hosts BGE-M3 + BGE-reranker-v2-m3 on the RTX 4050 GPU via `pytorch/pytorch:2.5.1-cuda12.4-cudnn9-runtime`. Reachable inside the compose network as `http://embed:8000`.

- **Model cache.** First `/embed` or `/rerank` call downloads ~2 GB of weights into the `embedmodels` named volume. `docker compose down && up` is safe — the volume persists. `docker volume rm sovpro_embedmodels` forces a re-download.
- **GPU attachment.** Compose uses `deploy.resources.reservations.devices` with `driver: nvidia, capabilities: [gpu]` — no Docker daemon restart required, no `/etc/docker/daemon.json` edit. Confirm attachment:
  ```bash
  docker exec sw-embed curl -s http://localhost:8000/health
  # → { ..., "device": "cuda", "device_name": "NVIDIA GeForce RTX 4050 Laptop GPU", "fp16": true, ... }
  ```
- **Overrides** via `.env`:
  ```env
  EMBED_MEMORY=6g                # soft host-RAM cap (not VRAM)
  EMBED_MAX_BATCH=128            # bounded by VRAM headroom; drop to 64 if you hit OOM
  EMBED_GPU_COUNT=all            # restrict via CUDA_VISIBLE_DEVICES semantics if multi-GPU
  EMBED_CUDA_DEVICES=all
  ```
  Any change requires `docker compose up -d embed` to recreate the container.
- **Hot-path endpoints.**
  - `POST /embed` — body `{"texts": ["..."], "return_tokens": false}` → `{items: [{embedding: [...1024], token_count?: int}], elapsed_ms, dim, model}`
  - `POST /rerank` — body `{"pairs": [{"query": "...", "document": "..."}]}` → `{scores: [...], elapsed_ms}`
  - `GET /health` — `{ok, device, device_name, fp16, embed_model, rerank_model, embed_loaded, rerank_loaded}`. Loads are lazy: healthy does NOT mean models are warm.
- **Performance expectations (RTX 4050 Mobile, 2026-04-16).**
  - Cold start (first embed after container boot): ~5 s
  - batch=32: ~68 texts/sec
  - batch=64: ~125 texts/sec
  - batch=128: ~205 texts/sec  ← sweet spot on 6 GiB VRAM
  - 50k speeches at peak ≈ 4 min. 1M ≈ 80 min.
- **VRAM budget.** BGE-M3 fp16 weights ~1 GiB; BGE-reranker fp16 ~300 MiB; peak activations at batch=128 ~1.5 GiB. Total ~3 GiB with ~3 GiB headroom for the desktop compositor. Running a GPU-heavy app (gaming, Blender) alongside may OOM; pause embedding if you need the card.
- **Falling back to CPU.** If a host has no GPU: change `services/embed/Dockerfile`'s base to `python:3.11-slim`, flip `USE_FP16` guard to always-False, drop the `reservations.devices` block in compose. The CPU variant lived on disk before commit `ef26d03` — worth carrying forward in a branch if you want a reproducible CPU build.
- **Monitoring.** `docker stats sw-embed --no-stream` for host-side CPU/RAM; `nvidia-smi` on the host for GPU utilisation + VRAM; `docker logs sw-embed -f` for model-load progress.

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
