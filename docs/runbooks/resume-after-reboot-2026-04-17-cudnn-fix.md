# Resume after reboot — 2026-04-17 (cuDNN 9.5 fix attempt)

**Status when paused:** NVIDIA driver wedged again (second time in 24h). We traced the `CUDA error: unspecified launch failure` pattern to kernel **Xid 62 + Xid 154** ("GPU Reset Required") — a GPU-internal-state fault class that only a full host reboot reliably clears. The strongest-fitting root cause is a documented **cuDNN 9.1.0 bug in the fp16 multi-head-attention path on sm_89 (Ada)** that manifests as random launch failures after ~1000 attention kernel launches, fixed in cuDNN 9.3+. Our workload (BGE-M3 fp16 on the RTX 4050 Mobile) hits all four markers.

**TL;DR to resume:** reboot → `docker compose up -d embed scanner-jobs` → queue `embed-speech-chunks` with `batch_size=32` → watch for whether it sustains past ~2000 chunks (previously died at ~1000–1200).

---

## Where we left off

### Progress tonight (pre-reboot)

| Metric | Before tonight | After | Δ |
|---|---:|---:|---:|
| Speech chunks embedded | 74,175 | **75,775** | +1,600 |
| Speech chunks pending | 167,839 | **166,239** | -1,600 |
| Crash runs attempted | — | 2 | Both hit Xid 62 + 154 |

Of the 1,600 chunks embedded tonight, ~448 came from the first run (pre-revert) and ~1,152 from the second run (post per-request `empty_cache()` revert). That partial improvement (2.5×) was real but nowhere near the pre-regression baseline of ~71k chunks/run, which is why we kept digging.

### The smoking-gun Xid pattern

Every embed crash matched the same fingerprint in `/var/log/kern.log`:

```
NVRM: Xid (PCI:0000:01:00): 62, <hex payload>
NVRM: Xid (PCI:0000:01:00): 45, pid=uvicorn, channel 0x...   (×10-20 consequential cleanups)
NVRM: Xid (PCI:0000:01:00): 154, GPU recovery action changed from 0x0 (None) to 0x1 (GPU Reset Required)
```

An earlier crash (2026-04-16 18:02:31) was `Xid 31 MMU Fault: FAULT_PDE ACCESS_TYPE_VIRT_READ` — classic "illegal memory access" signature. Both class into the **driver/kernel-bug territory** (not thermal, not OOM, not application-level).

### Why we believe it's cuDNN 9.1 and not something else

| Hypothesis | Fit |
|---|---|
| **cuDNN 9.1 fp16 attention bug on sm_89** | 4/4 markers: fp16 ✓, sm_89 (Ada, 4050 Mobile) ✓, attention-heavy workload (BGE-M3 transformer) ✓, crash at ~1000 kernel launches ✓. Fix: cuDNN 9.3+. |
| Thermal / hardware | GPU at 74°C idle (13°C headroom); no DRAM/SRAM ECC events; same GPU worked fine weeks ago on the same code (c7af29f baseline). |
| VRAM OOM | `nvidia-smi` showed 15 MiB free before each crash; peak VRAM during healthy inference was ~5.6 GiB / 6.1 GiB — headroom, not exhaustion. |
| Per-request `empty_cache()` (`bc46b7d`) | Contributed a 2.5× shortening. Fully reverted in `a41d57f`. Remaining 100× → 40× gap points to a second, deeper cause. |
| Driver 580.x Ada regression | Plausible but un-cited; kept as Stage C fallback. |
| `expandable_segments:True` | Previously blamed (2026-04-16) but the runbook concluded that was driver-state mis-attribution. Still unverified on a clean driver. |

### Containers and services at pause time

- `sw-embed` — **stopped via `docker compose stop`** so it won't auto-start on reboot and immediately crash-loop against the wedged driver
- `sw-scanner-jobs` — **stopped** for the same reason (pending queue job would hammer a crash-looping embed)
- `sw-db`, `sw-api`, `sw-frontend`, `sw-nginx`, `sw-kuma`, `sw-newt` — healthy, unaffected; `unless-stopped` will restart them automatically
- `embedmodels` and `pgdata` named volumes — intact

### Commits pushed to `main` this session

| SHA | Purpose |
|---|---|
| `a41d57f` | **Revert per-request `empty_cache()` + `ipc_collect()`** from `bc46b7d`. Documented as "flush-only" helper; still exposed via `POST /flush-cache` endpoint. |

(Still 10 commits ahead of origin/main — see `git log` for the pending push.)

### Uncommitted changes on disk (staged for this attempt)

1. **`services/embed/Dockerfile`** — adds `RUN pip install --upgrade nvidia-cudnn-cu12==9.5.1.17` after the requirements install. Upgrades cuDNN from the base image's 9.1.0.70. Rationale documented inline in the Dockerfile.
2. **`docker-compose.yml`** (embed service env block):
   - Re-enables `PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True,garbage_collection_threshold:0.8,max_split_size_mb:512`. Previously reverted on 2026-04-16 under the belief it caused the "device busy" cascade, but that diagnosis was wrong — the cascade was driver state. Worth a real trial on a clean driver.
   - Adds `CUBLAS_WORKSPACE_CONFIG=:4096:8` to cap cuBLAS scratch memory so it doesn't compete with BGE-M3 weights in the 6 GiB budget.
   - Switched the `PYTORCH_CUDA_ALLOC_CONF` default-operator from `:-` (colon-dash) to `-` (single-dash) so an empty `.env` value can disable it for A/B testing.

**Image state:** `sovpro-embed:latest` has already been rebuilt against these Dockerfile changes. Verified: `pip show nvidia-cudnn-cu12` inside the image reports `9.5.1.17`.

**These changes are NOT committed.** Hold the commits until we have empirical evidence (post-reboot run) that they actually fix the regression. If the cuDNN bump doesn't help, we'll revert and try Stage C before polluting history.

---

## After reboot: step-by-step

### 1. Verify host comes up clean

```bash
docker compose ps
# Expected: db, api, frontend, nginx, kuma, scanner-cron, newt, change-detection all "Up"
# and "healthy". sw-embed and sw-scanner-jobs will NOT be running — we stopped them intentionally.

nvidia-smi --query-gpu=memory.used,utilization.gpu --format=csv,noheader
# Expected: ~15 MiB, 0% — clean driver

# Confirm no stale Xid-154 recovery-required state lingers
grep -cE "Xid.*154" /var/log/kern.log
# Any count is fine — the counter won't reset, but the state clears on reboot.
# What matters is that nothing NEW fires when we start embed.
```

### 2. Start embed and verify cuDNN version live on GPU

```bash
cd /home/bunker-admin/sovpro
docker compose up -d embed

# Give it ~6 s to start; then confirm:
sleep 6
docker exec sw-embed curl -s http://localhost:8000/health
# Expected: {"ok":true,"device":"cuda","device_name":"NVIDIA GeForce RTX 4050 Laptop GPU",...}

# Confirm cuDNN is 9.5.1.17 in the running container
docker exec sw-embed python -c "import torch; print('cudnn:', torch.backends.cudnn.version())"
# Expected: cudnn: 90500 or higher (was 90100 before)

# Moment-of-truth first inference — if driver is still wedged or
# allocator flags are incompatible, this returns empty or 500
docker exec sw-embed curl -s -X POST http://localhost:8000/embed \
  -H "Content-Type: application/json" \
  -d '{"texts":["Mr. Speaker, I rise today."]}' | head -c 200
# Expected: JSON blob starting with {"model":"BAAI/bge-m3","dim":1024,"items":[...]}
```

**If the first inference call returns empty or fails**, the allocator flags are likely the culprit — jump to the "Fallback A" block below before going further.

### 3. Queue a conservative run

Conservative because we want to confirm the fix at `batch_size=32` first, not race to finish. If we sustain past 5k chunks we can bump back up to 64 for the long haul.

```bash
docker compose up -d scanner-jobs
TOKEN=$(grep ^ADMIN_TOKEN= .env | cut -d= -f2)
curl -s -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -X POST -d '{"command":"embed-speech-chunks","args":{"batch_size":32},"priority":10}' \
  http://localhost:8088/api/v1/admin/jobs
```

### 4. Monitor

```bash
# Progress
docker exec sw-db psql -U sw -d sovereignwatch -c "
  SELECT count(embedding) AS embedded,
         count(*) - count(embedding) AS pending
    FROM speech_chunks;"

# Watch the embed log for any fatal-CUDA-error signals (should stay quiet)
docker logs -f sw-embed 2>&1 | grep -vE "GET /health|INFO:.*OK"

# Watch kern.log for ANY new Xid event (the definitive signal)
tail -f /var/log/kern.log | grep -iE "NVRM.*Xid"
```

### 5. Interpret the result

| Outcome | Meaning | Next step |
|---|---|---|
| Sustains past **~2,000 chunks** with no Xid 62 | cuDNN 9.1 was the culprit. The fix worked. | Let it run to completion (~1.5–3 h). Commit the Dockerfile + compose changes. Bump `batch_size` back to 64 for a future run. |
| Crashes around **~1,000 chunks again** with Xid 62 | cuDNN upgrade didn't solve it. Move to Fallback B. | Revert cuDNN change; try driver downgrade (Stage C). |
| Crashes **immediately on first /embed call** with "device busy" | Allocator flags are incompatible with this build on a clean driver. | Use Fallback A. |
| Crashes with a **different Xid** (13, 31, 79) | New failure class — re-diagnose with research agent before acting. | Capture the Xid code and payload from `kern.log`; pause. |

---

## Fallbacks

### Fallback A: allocator flags are the real problem

```bash
# Start embed with allocator flags disabled (requires the single-dash
# default-operator in docker-compose.yml, which this runbook's staged
# changes include)
PYTORCH_CUDA_ALLOC_CONF="" docker compose up -d embed
docker exec sw-embed env | grep PYTORCH_CUDA_ALLOC_CONF   # should be empty
```

Retry step 2's inference check. If it works, the flags are incompatible with this image/driver combo. Commit only the Dockerfile (cuDNN) change and drop the compose env-var changes.

### Fallback B: cuDNN didn't help — back out cleanly and try next lever

```bash
cd /home/bunker-admin/sovpro
git checkout services/embed/Dockerfile docker-compose.yml   # drop staged changes
docker compose build embed
docker compose up -d embed
```

Then escalate to Stage C options in priority order:

| Lever | Effort | Effect |
|---|---|---|
| **Driver downgrade 580.x → 570.124.06** | Host-level, requires `apt` + reboot | Research flagged 580.x as having documented Ada regressions. 570.x is the last widely-validated laptop branch. |
| Lower `MAX_INPUT_LEN` to 4096 | Env var on embed service | Halves peak attention activation memory; long committee speeches get truncated but they tail off in relevance anyway. |
| Batch size 32 → 16 | Job arg | Another halving of peak VRAM. |
| Disable fp16, run fp32 | Code flip in `server.py` | Much slower (~2× throughput loss) but takes the suspect fp16 attention path out of play entirely. Last resort. |

---

## Longer-term learnings worth keeping

- **Xid codes are the diagnostic starting point, not the English error string.** `CUDA error: unspecified launch failure` maps to many possible Xids. `/var/log/kern.log` (the user is in the `adm` group so no sudo needed) has the real classification. Tonight's Xid 62 + 154 pattern immediately narrowed us from "could be anything" to "driver/kernel bug class" within minutes.
- **"Revert the most recently changed suspect" is a weak default when you can root-cause.** We spent one run reverting `bc46b7d` and gained 2.5×. We spent one run chasing the Xid fingerprint and reached a named, cited bug. The second approach was slower per iteration but had a much higher hit rate; this is the pattern to prefer.
- **Docker images bundle cuDNN via pip, not dpkg.** `pip install --upgrade nvidia-cudnn-cu12==X.Y.Z` inside the Dockerfile cleanly upgrades cuDNN over a PyTorch base image; cuDNN 9.x maintains ABI stability so torch doesn't need to be rebuilt. This is a cheap lever the next time an ML container hits a cuDNN-version-specific bug.
- **Stopping `sw-embed` + `sw-scanner-jobs` before a reboot prevents crash-loop cascades.** Compose's `unless-stopped` respects the stopped state across reboot. Do this whenever we suspect driver wedge.

---

## When this is done

If the cuDNN fix holds and the full 167k chunks embed cleanly, the semantic search layer finally has a full federal Hansard corpus to query against. See the tail of the 2026-04-16 runbook for the natural next steps (search endpoint, nightly cron, AB historical-MLA backfill). None of those are blocked on anything other than this embed run finishing.
