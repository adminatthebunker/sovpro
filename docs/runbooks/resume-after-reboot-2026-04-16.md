# Resume after reboot — 2026-04-16 embedding incident

**Status when paused:** GPU driver stuck after cascading CUDA faults during the full-P44 Hansard embedding run. `sw-embed` stopped to prevent further crash-loop churn; reboot required to clear driver state.

**TL;DR to resume:** reboot → `docker compose up -d embed` → queue an `embed-speech-chunks` job via the admin panel → watch it work through the ~168k pending chunks.

---

## Where we left off

### Data shipped tonight (all safe through a reboot — `pgdata` volume persists)

| Layer | Before | After | Δ |
|---|---:|---:|---|
| Federal speeches | 2,318 | **182,870** | +180,552 (full P44, openparliament mirror) |
| Speech chunks | 3,135 | **242,014** | +238,879 |
| Speech chunks embedded | 3,135 | **74,175** | +71,040 (the rest is what we're resuming) |
| Bills (AB full backfill) | 115 | **11,133** | +11,018 (Legislature 1+, ~137 sessions) |
| Bills (BC full backfill) | 36 | **2,276** | +2,240 (sessions back to 1872) |
| Bills (NL full backfill) | 12 | **1,193** | +1,181 (24 sessions) |
| Bills (QC full backfill) | 102 | **497** | +395 |
| Bills (NB current-legislature backfill) | 33 | **33** | +0 |
| Total bills across 9 jurisdictions | 3,948 | **19,081** | +15,133 |
| Postgres DB size | 157 MB | **2,519 MB** | +2.36 GB |

**What's pending:** 167,839 `speech_chunks` still have `embedding IS NULL`. No other work queued.

### Commits pushed to `main` during this session

| SHA | Purpose |
|---|---|
| `bc46b7d` | `torch.cuda.empty_cache()` after every `/embed` + `/rerank` call; closed 4 admin-catalog gaps |
| `9d32b5e` | Admin catalog exposes AB `--all-sessions` + backfill flags |
| `9ace319` | **`_fatal_on_cuda_error` → `os._exit(42)` for container auto-restart** on poisoned CUDA context |
| `1003154` | `PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True` *(reverted — see uncommitted changes below)* |

### Uncommitted changes on disk

- `docker-compose.yml` — the `PYTORCH_CUDA_ALLOC_CONF` env var in the `embed` service is now set to empty (effectively unset). We added `expandable_segments:True` in `1003154`, later decided against it, but the actual culprit of the crash-loop was the GPU driver being in a corrupted state, not the allocator flag. After reboot we can either commit this revert or experiment again.

**Recommendation:** commit this small revert immediately after reboot and before re-queuing work, so the state is clean:

```bash
cd /home/bunker-admin/sovpro
git add docker-compose.yml
git commit -m "chore(embed): leave PYTORCH_CUDA_ALLOC_CONF unset pending re-test"
```

### Container state when paused

- `sw-embed` — **stopped** (was in a crash-loop due to driver-level "CUDA device busy" errors)
- `sw-scanner-jobs` — running, idle (no queued jobs)
- `sw-db`, `sw-api`, `sw-frontend`, `sw-nginx`, `sw-kuma`, `sw-newt` — all healthy and unaffected
- `embedmodels` and `pgdata` named volumes — intact (no re-download, no data loss on reboot)

---

## Why we're rebooting

Two distinct CUDA errors hit tonight:

1. **`RuntimeError: CUDA error: unspecified launch failure`** during embedding at ~30-40k chunks into a run. Fired twice (once on the original run, once on the first re-embed). Cause: likely VRAM fragmentation + long-running kernel accumulation on the RTX 4050 Mobile (6 GiB). Once this fires, the CUDA context in that process is permanently poisoned — every subsequent `/embed` call returns 500.

2. **`CUDA error: CUDA-capable device(s) is/are busy or unavailable`** on *every* fresh container startup after the second crash. This is a **driver-level** state, not a process-level one. The NVIDIA driver kept resources allocated for the crashed contexts, so new processes see the GPU as unavailable even though `nvidia-smi` shows only 103 MiB used (just Steam's compositor). The fail-fast guard (commit `9ace319`) did what it was designed for — detected the fatal, `os._exit(42)`'d — but because every fresh container hit the same driver-level error on its first kernel call, compose crash-looped the container 12 times.

**Only a reboot (or `nvidia-smi --gpu-reset` with no other GPU processes — not trivial while the desktop is live) clears the driver state.** This is well-known behaviour for long-running PyTorch + consumer-grade GPUs; dedicated compute cards have reset-on-bus mechanisms that avoid it.

---

## After reboot: step-by-step

### 1. Verify host comes up clean

```bash
# Docker containers should auto-start (restart: unless-stopped)
docker compose ps

# Expected: db, api, frontend, nginx, kuma, scanner-jobs, scanner-cron, newt, change-detection
# all "Up" and "healthy". sw-embed will NOT be running — we stopped it intentionally.

nvidia-smi
# Expected: low memory usage (~100 MB compositor only), GPU-Util 0%
```

### 2. Commit the revert (optional but tidy)

```bash
cd /home/bunker-admin/sovpro
git add docker-compose.yml
git commit -m "chore(embed): leave PYTORCH_CUDA_ALLOC_CONF unset pending re-test"
```

### 3. Start embed container

```bash
docker compose up -d embed

# Wait a few seconds, then sanity-check
sleep 5
docker exec sw-embed curl -s http://localhost:8000/health
# Expected: {"ok":true,"device":"cuda","device_name":"NVIDIA GeForce RTX 4050 Laptop GPU",...}

# Confirm first embed call succeeds (this is the moment of truth — if the driver
# is still stuck, this will return 500 and the container will die)
docker exec sw-embed curl -s -X POST http://localhost:8000/embed \
  -H "Content-Type: application/json" \
  -d '{"texts":["Mr. Speaker, I rise today."]}' | head -c 200

# Expected: a JSON blob starting with {"model":"BAAI/bge-m3","dim":1024,"items":[...]}
# If you see an empty response or 500, the driver is still stuck — try a second reboot
# or run `nvidia-smi --gpu-reset -i 0` (requires no X session on the GPU, which is
# tricky with an active desktop).
```

### 4. Queue `embed-speech-chunks` via the admin panel

**Browser:** `http://localhost:8088/admin/login`, paste the `ADMIN_TOKEN` from `.env`, go to Jobs → "Run a command":

- Command: `embed-speech-chunks`
- `batch_size`: **64** (conservative — we saw crashes at 128)
- Submit

**Or via curl:**
```bash
TOKEN=$(grep ^ADMIN_TOKEN= /home/bunker-admin/sovpro/.env | cut -d= -f2)
curl -s -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -X POST -d '{"command":"embed-speech-chunks","args":{"batch_size":64},"priority":10}' \
  http://localhost:8088/api/v1/admin/jobs
```

### 5. Monitor progress

```bash
# Quick count check
docker exec sw-db psql -U sw -d sovereignwatch -c "
  SELECT count(embedding) AS embedded,
         count(*) - count(embedding) AS pending
    FROM speech_chunks;"

# Follow the running job
TOKEN=$(grep ^ADMIN_TOKEN= /home/bunker-admin/sovpro/.env | cut -d= -f2)
curl -s -H "Authorization: Bearer $TOKEN" 'http://localhost:8088/api/v1/admin/jobs?status=running' \
  | python3 -c 'import sys,json; [print(j["id"], j["command"], j["started_at"]) for j in json.load(sys.stdin)["jobs"]]'
```

Expected progression at ~15 chunks/sec: 167,839 / 15 ≈ 11,200s ≈ **~3 hours** to finish if we don't crash. Expect another "unspecified launch failure" somewhere around the 30-40k chunk mark, in which case:

- Fail-fast guard triggers → container exits with code 42
- Compose restarts container
- Worker's next batch gets a fresh context → continues
- ~5-10 batches of failures during the ~10s restart window — acceptable
- Overall the run will self-heal; re-queue `embed-speech-chunks` if still pending rows remain after it terminates

### 6. If the first crash recurs and we want to reduce the risk further

Options (in order of cost):

| Lever | Change | Expected effect |
|---|---|---|
| Batch size ↓ | `batch_size: 32` instead of 64 | Smaller peak VRAM per batch; slower throughput |
| MAX_INPUT_LEN ↓ | Server env `MAX_INPUT_LEN=4096` instead of 8192 | Truncates long committee speeches; fewer pathological activations |
| Try `expandable_segments` again | Set `PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True` in `.env` | Was blamed tonight but actually wasn't the culprit — try on a healthy GPU |
| Accept the crash cadence | Do nothing, let fail-fast handle it | Embed will self-heal across restarts; total wall-time slightly longer |

---

## Longer-term learnings worth keeping

- **`restart: unless-stopped`** on GPU services can create crash-loops when the failure mode is driver-level, not container-level. A future improvement: give `sw-embed` a healthcheck that actually tries inference (not just `/health`), so compose's restart backoff can notice and slow down.
- **Fail-fast is the right pattern** for poisoned CUDA context, but it's not a panacea — if the underlying driver is stuck, no amount of container restarts will help. The "userland recoverable" vs "driver-state" distinction is the key axis.
- **Consumer-grade GPUs (like the 4050 Mobile) don't have good reset-on-bus mechanisms.** Long-running inference on them benefits from: conservative batch sizes, periodic process restarts (every N batches, by design not by accident), and always an out-of-band recovery plan (this runbook).
- **The pipeline itself is validated end-to-end.** Ingest (openparliament → speeches) works at ~180 speeches/sec. Chunk works at ~460 chunks/sec. Embed works at ~15-17 chunks/sec on the GPU (when the GPU cooperates). 74k chunks already embedded prove the stack is functionally correct; tonight's issue was exclusively in reliability, not correctness.

---

## When this is done

Once all 242,014 chunks are embedded, the semantic search layer has real data to test against. Natural next steps (for a separate session):

1. **Search endpoint** (`POST /api/v1/search`) — embed the query, HNSW + tsvector hybrid retrieval, BGE-reranker cross-encoder. First real dogfood of the whole semantic stack.
2. **Nightly Hansard update schedule** — cron a `ingest-federal-hansard --since yesterday` + chunk + embed at 2/3/4 AM UTC.
3. **Backfill AB sponsors** — after the all-sessions pull, only 285/11,116 sponsors FK-resolved (historical MLAs aren't in `politicians`). Needs a separate historical-MLAs ingest.
