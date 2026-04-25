# Resume after reboot — 2026-04-19 (AB Hansard embed, blocked on CUDA UVM)

**Status when paused:** AB Hansard historical backfill **ingest + chunk finished cleanly** (Legs 24–31, 439,125 speeches → 487,221 chunks). Embedding failed mid-run: TEI's CUDA context couldn't initialize (`CUDA_ERROR_UNKNOWN`), fell back to CPU, and refused 17,393 embed batches. Root cause is host-level — `nvidia_uvm` module stuck with non-zero usage count; a fresh PyTorch container also hits `CUDA-capable device(s) is/are busy or unavailable`. `rmmod nvidia_uvm` failed even after stopping every user-mode GPU client we could find. **Reboot is the fix.** No data loss — everything is safe in Postgres.

**TL;DR to resume:** reboot → `docker compose up -d tei` → verify logs say `Starting Qwen3 model on Cuda` → `docker compose run --rm scanner embed-speech-chunks` → `docker compose run --rm scanner refresh-coverage-stats` → browser-check `/coverage`.

---

## Where we left off

### DB state (verified pre-reboot)

| Metric | Value |
|---|---:|
| AB speeches | **439,125** |
| AB sittings | 2,217 |
| AB sessions indexed | 29 (L24-S4 → L31-S2) |
| AB chunks total | **487,221** |
| AB chunks embedded | 69,210 (**14.2 %**) |
| AB chunks pending | **418,011** |
| `jurisdiction_sources.AB.hansard_status` | `live` (refresh-coverage-stats ran; count drives status) |

Search semantics: full-text works across all 439 k; **semantic (Qwen3) works only on the 69 k embedded chunks** until the remaining 418 k are re-queued and processed.

### What caused the stall

```
sw-tei logs:
  WARN  Could not find a compatible CUDA device on host: CUDA is not available
  Caused by: DriverError(CUDA_ERROR_UNKNOWN, "unknown error")
  WARN  Using CPU instead
  INFO  Starting Qwen3 model on Cpu
```

The GPU itself was idle: `nvidia-smi` reported 15 MiB/6141 MiB used, no compute processes. But any attempt to create a CUDA *context* (TEI's Candle backend, a fresh `pytorch/pytorch:2.5.1-cuda12.4-cudnn9-runtime` container) failed with `CUDA_ERROR_UNKNOWN` / `CUDA-capable device(s) is/are busy or unavailable`.

`lsmod | grep nvidia_uvm` showed usage count **4** (was 8 earlier; killing `glances` dropped it to 4). The remaining 4 references are stuck kernel state from crashed CUDA contexts — the module refused `rmmod` even with every known user-mode client terminated, and even with `media-manager-api-1` (the only other container with a GPU reservation) stopped.

This is the same class of failure as `docs/runbooks/resume-after-reboot-2026-04-17-cudnn-fix.md`, minus the Xid-62/154 signature. There was no GPU kernel fault in `kern.log` this time — just a wedged UVM module.

### Commits / scope touched tonight

This session's commits are **not yet landed** — the AB Hansard pipeline was built + run but not committed, pending your review. Files of interest:

- `services/scanner/src/legislative/ab_hansard.py` (new, ~750 lines — PDF listing scrape, Poppler `pdftotext` extract, era-aware parser for L24–L31, three-tiered MLA resolver, idempotent upsert)
- `services/scanner/Dockerfile` (adds `poppler-utils`)
- `services/scanner/src/__main__.py` (adds `cmd_ingest_ab_hansard`)
- `services/scanner/src/jobs_catalog.py` + `services/api/src/routes/admin.ts` (catalog entries)

The parser already survived one major iteration — the initial version was case-sensitive and missed L24–L25 ALL CAPS speakers (`MR. KLEIN:`). Current version is case-insensitive with era-aware `head:` handling (inline `head: Prayers` for L24–L25, separated `head:\n\nPrayers` for L26+) and role-name / honorific / surname normalization so aggregates don't fragment across eras.

---

## Resume procedure

### 1. Verify CUDA is sound

```bash
# Should return quickly with GPU listed; no "CUDA_ERROR" in stderr
docker run --rm --gpus all pytorch/pytorch:2.5.1-cuda12.4-cudnn9-runtime \
  python -c "import torch; x = torch.randn(100, device='cuda'); print('cuda ok:', float((x*x).sum()))"
```

Expected: `cuda ok: <some float>`. If you see `CUDA error: CUDA-capable device(s) is/are busy or unavailable`, the reboot didn't clear it — try a cold power cycle (full shutdown, then start).

### 2. Bring TEI back with GPU

```bash
cd /home/bunker-admin/sovpro
docker compose up -d tei

# Wait ~20 s then confirm GPU mode
docker logs sw-tei --tail 20 2>&1 | grep -E "Cuda|Cpu"
# Should print:  INFO  Starting Qwen3 model on Cuda
# If it prints   "Starting Qwen3 model on Cpu"  — STOP and investigate (CUDA still wedged).
```

### 3. Finish embedding the 418 k pending chunks

```bash
docker compose run --rm scanner embed-speech-chunks 2>&1 | tee /tmp/ab-embed-resume.log
```

Throughput at 79 chunks/sec (Qwen3-0.6B via TEI) → **~90 minutes**. The job is idempotent — it only embeds `WHERE embedding IS NULL`, so restart-on-fail is safe. Summary line at end reports `embed-speech-chunks: seen=N embedded=N batches=N errors=0`.

If you see a repeat of "All connection attempts failed": TEI likely fell back to CPU or crashed mid-run. Re-check step 2.

### 4. Refresh coverage + verify

```bash
docker compose run --rm scanner refresh-coverage-stats
```

Expected output line: `AB: speeches=439125 (was 439125) politicians=87 bills=11133 hansard=live`.

Then in a browser: https://canadianpoliticaldata.ca/coverage — AB's Hansard pill should show **Live** with 439,125 speeches.

### 5. Sanity-check semantic search

```bash
curl -s 'http://localhost:8088/api/v1/search/speeches?q=klein+era+energy+policy&province_territory=AB&limit=3' \
  | python3 -c "import json,sys;r=json.load(sys.stdin);print(f'mode={r[\"mode\"]} total={r[\"total\"]}'); [print(f\"  sim={h[\"similarity\"]:.3f} speaker={h.get(\"politician\",{}).get(\"name\",\"?\")} date={h[\"spoken_at\"][:10]}\") for h in r['items']]"
```

Expected: `mode=semantic`, top hits from Klein-era (L24–L25, 2000–2004) with high similarity. If the top results are all from 2025–2026, the embed didn't finish — not every L24–L30 chunk has a vector yet.

### 6. (Optional) Commit the AB Hansard pipeline

Nothing is committed from this session. When ready:

```bash
git add services/scanner/src/legislative/ab_hansard.py \
        services/scanner/Dockerfile \
        services/scanner/src/__main__.py \
        services/scanner/src/jobs_catalog.py \
        services/api/src/routes/admin.ts
git status -s                # confirm only AB-Hansard files staged
git commit -m "feat(scanner): ingest-ab-hansard — PDF-parsed Alberta Hansard"
```

The user's own in-flight changes (BC Hansard catalog entries in `admin.ts` and `jobs_catalog.py`, the many frontend search UI components, etc.) are intentionally *not* in this commit — those stay for the user to commit separately.

---

## What NOT to redo

- **Do not re-run `ingest-ab-hansard`.** It's already done for all 28 sessions. The idempotent ON CONFLICT would just rewrite rows at current sequences.
- **Do not re-run `chunk-speeches`.** 487,221 chunks exist; the command's `WHERE NOT EXISTS` guard means a second run finds nothing to do and exits in seconds.
- **Do not drop the `nvidia_uvm` module manually post-boot.** A clean boot reloads it cleanly; `rmmod` is only needed when the module is in a wedged state mid-session.

---

## References

- Prior GPU-wedge runbooks: `docs/runbooks/resume-after-reboot-2026-04-16.md`, `resume-after-reboot-2026-04-17-cudnn-fix.md`
- AB Hansard pipeline plan: `/home/bunker-admin/.claude/plans/okay-so-thinking-i-radiant-moler.md`
- Embedding-model background: `docs/plans/embedding-model-comparison.md`
- Coverage refresher code: `services/scanner/src/legislative/coverage_stats.py`
