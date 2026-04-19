# Resume after reboot — 2026-04-19 (BC Hansard embed, blocked on CUDA UVM)

**Status when paused:** BC Hansard historical backfill **ingest + chunk finished cleanly** (23 sessions P38-S4 → P43-S2, 197,888 speeches → 173,401 chunks → 34,857 embedded). Embedding stalled mid-run: TEI's CUDA context hit `CUDA_ERROR_UNKNOWN`, fell back to CPU, and stayed there across container recreate. Same host-level `nvidia_uvm`-wedged failure mode as the AB Hansard run earlier today (see `resume-after-reboot-2026-04-19-ab-hansard.md` — both runbooks may now apply; reboot clears both). **Reboot is the fix.** No data loss — everything is safe in Postgres.

**TL;DR to resume:** reboot → `docker compose up -d tei` → verify logs say `Starting Qwen3 model on Cuda` → `docker compose run --rm scanner embed-speech-chunks` (this drains AB's 418k + BC's 138k in one pass) → `docker compose run --rm scanner refresh-coverage-stats` → browser-check `/coverage`.

---

## Where we left off

### DB state (verified pre-reboot)

| Metric | Value |
|---|---:|
| BC speeches (hansard-bc) | **197,888** |
| BC sessions indexed | **23** (P38-S4 → P43-S2, 2008-2026) |
| BC chunks total | **173,401** |
| BC chunks embedded | 34,857 (**20.1 %**) |
| BC chunks pending | **138,544** |
| BC politicians linked | 172,890 (**87.4 %**) |
| BC distinct speakers | 464 (93 current MLAs + 371 historical/presiding) |
| `jurisdiction_sources.BC.hansard_status` | `live` (refreshed 2026-04-19 19:50 UTC) |
| Global chunks pending embed | **556,555** (BC 138k + AB 418k from earlier run) |

Semantic search semantics: `/api/v1/search/speeches?...&province_territory=BC` works on the 34 k embedded chunks. Full-text (`sc.tsv @@ websearch_to_tsquery(...)`) works across all 173 k. Once embed resumes, semantic coverage will be complete for BC.

### What caused the stall

```
sw-tei logs:
  DriverError(CUDA_ERROR_UNKNOWN, "unknown error")
  WARN  Using CPU instead
  INFO  Starting Qwen3 model on Cpu
```

Same wedge as the AB session earlier. `nvidia-smi` showed GPU idle (15 MiB/6141 MiB, 0% util), but any CUDA-context init failed. `docker compose rm -sf tei && docker compose up -d tei` did **not** clear it — the kernel-side `nvidia_uvm` state persists across container recreates. Reboot is the only reliable reset.

The first ~8 k BC chunks embedded successfully on GPU (~125 chunks/sec). Then TEI errored on a batch, retried, then came back up in CPU mode for the remainder. 34,857 chunks are embedded with the canonical Qwen3-0.6B fp16 vectors; the rest are NULL.

### Commits / scope touched this session

**Not yet committed** — all changes staged in the working tree.

**New files:**

- `services/scanner/src/legislative/bc_hansard_parse.py` (~380 lines) — pure-offline parser. Handles all five BC HTML eras (P38→P43) via normalized class-name dispatch (strips hyphens, underscores, spaces so `SpeakerBegins` / `speaker-begins` / `speaker begins` all collapse to `speakerbegins`). Dual span handling for `Speaker-Name` / `SpeakerName` (modern) and `Attribution` (P39–P42 legacy). `ProceduralHeading` + `ProceedingsHeading` + `Proceedings` all treated as section markers. URL regex accepts `House-Blues.htm`, `Hansard-n{NNN}.html`, and `Hansard-v{VOL}n{NNN}.htm` (pre-P43).
- `services/scanner/src/legislative/bc_hansard.py` (~700 lines) — ingester with LIMS HDMS debate-index JSON discovery (`https://lims.leg.bc.ca/hdms/debates/{parl}{sess}`), canonical-URL upsert strategy (`hansard-bc.canonical/...`), and three-tier speaker resolver: `by_full_name` → `by_initial_last` (P42 "P. Milobar" style) → `by_surname`. Sitting-Speaker extraction pulls from HTML header, falls back to hardcoded `BC_PARLIAMENT_SPEAKER` map (43/42 Chouhan, 41 Plecas, 40 Reid, 39/38 Barisoff) for Final variants that strip the Speaker tag. Duplicate-`lims_member_id` dedup in the lookup collapses two-row MLAs (e.g. Claire Rattée).
- `scripts/bc-enrich-historical-mlas.py` — one-shot pull of `allMembers` from LIMS GraphQL. Inserted 284 retired BC MLAs (was 92 linked rows, now 376).
- `scripts/bc-hansard-backfill.sh` — per-session loop with chunk (embed is run out-of-band so GPU contention doesn't pace ingest).
- `services/scanner/tests/fixtures/bc_hansard/` — two canonical sittings (`20260415pm-House-Blues.htm`, `20260218pm-Hansard-n118.html`) + samples for older eras, used to iterate the parser offline.

**Modified:**

- `services/scanner/src/__main__.py` — `cmd_ingest_bc_hansard` (with `--url` smoke-test flag) and `cmd_resolve_bc_speakers`.
- `services/scanner/src/jobs_catalog.py` — added `ingest-bc-hansard` + `resolve-bc-speakers` under `category: "hansard"`.
- `services/api/src/routes/admin.ts` — mirrored those two entries into `COMMAND_CATALOG`.
- `docs/research/british-columbia.md` — status flipped to "live", difficulty 3→2, pipeline docs added.
- `docs/architecture.md` — documents the database-level HNSW tuning (see "DB changes" below).

**DB changes that persist across reboot (verify post-reboot):**

- `ALTER DATABASE sovereignwatch SET hnsw.iterative_scan = 'relaxed_order';`
- `ALTER DATABASE sovereignwatch SET hnsw.ef_search = 200;`
  — fixes filtered semantic search (e.g. `province_territory=BC`) which otherwise returned 0 hits because default HNSW top-K was dominated by 1.5M federal chunks. Verify with `SELECT unnest(setconfig) FROM pg_db_role_setting WHERE setdatabase = (SELECT oid FROM pg_database WHERE datname='sovereignwatch');` — should show both settings.
- 284 historical BC MLA rows inserted into `politicians` (total BC rows: 376, all `level='provincial' AND province_territory='BC'`).
- Claire Rattée duplicate politician rows merged: kept `610d2ee2-…` under canonical name `Claire Rattée`, deleted `6843d77b-…`.

---

## Resume procedure

### 1. Verify CUDA is sound

```bash
docker run --rm --gpus all pytorch/pytorch:2.5.1-cuda12.4-cudnn9-runtime \
  python -c "import torch; x = torch.randn(100, device='cuda'); print('cuda ok:', float((x*x).sum()))"
```

Expected: `cuda ok: <some float>`. If `CUDA-capable device(s) is/are busy or unavailable`, cold power-cycle (full shutdown, then start).

### 2. Bring TEI back with GPU

```bash
cd /home/bunker-admin/sovpro
docker compose up -d tei
sleep 20
docker logs sw-tei --tail 20 2>&1 | grep -E "Cuda|Cpu"
# Should print:  INFO  Starting Qwen3 model on Cuda
# If it prints   Starting Qwen3 model on Cpu  — STOP and investigate.
```

### 3. Drain the embed backlog

```bash
docker compose run --rm scanner embed-speech-chunks 2>&1 | tee /tmp/bc-embed-resume.log
```

At ~125 chunks/sec (Qwen3-0.6B fp16 on RTX 4050): **~75 minutes** for BC's 138 k alone, **~75 + 55 ≈ 130 minutes** if AB's 418 k backlog still exists. The command is idempotent (`WHERE embedding IS NULL`) — restart-on-fail is safe.

Summary line reports `embed-speech-chunks: seen=N embedded=N batches=N errors=0`. If `errors > 0`, TEI likely fell back to CPU mid-run — re-check step 2.

### 4. Verify HNSW tuning survived

```bash
docker compose exec db psql -U sw -d sovereignwatch -c "
  SELECT unnest(setconfig) FROM pg_db_role_setting
   WHERE setdatabase = (SELECT oid FROM pg_database WHERE datname='sovereignwatch');
"
# Expected lines include:
#   hnsw.iterative_scan=relaxed_order
#   hnsw.ef_search=200
```

If missing, re-apply with:

```bash
docker compose exec db psql -U sw -d sovereignwatch -c "
  ALTER DATABASE sovereignwatch SET hnsw.iterative_scan = 'relaxed_order';
  ALTER DATABASE sovereignwatch SET hnsw.ef_search = 200;
"
docker compose restart api  # pool picks up fresh connections
```

### 5. Refresh coverage + verify

```bash
docker compose run --rm scanner refresh-coverage-stats
```

Expected output line: `BC: speeches=197888 (was 197888) politicians=382 bills=2276 hansard=live`.

Then browser-check https://canadianpoliticaldata.ca/coverage — BC's Hansard pill should show **Live** with 197,888 speeches.

### 6. Sanity-check provincial-filtered semantic search

```bash
curl -s 'http://localhost:8088/api/v1/search/speeches?q=forestry+policy&province_territory=BC&limit=3' \
  | python3 -c "import json,sys;r=json.load(sys.stdin);print(f'mode={r[\"mode\"]} total={r[\"total\"]}'); [print(f\"  sim={h[\"similarity\"]:.3f} speaker={h.get(\"politician\",{}).get(\"name\",\"?\")} date={h[\"spoken_at\"][:10]}\") for h in r['items']]"
```

Expected: `mode=semantic`, 3 hits, BC speakers, similarity 0.6–0.8. If 0 items, HNSW tuning was lost — redo step 4.

### 7. (Optional) Commit the BC Hansard pipeline

Nothing is committed from this session. When ready:

```bash
git add services/scanner/src/legislative/bc_hansard.py \
        services/scanner/src/legislative/bc_hansard_parse.py \
        services/scanner/src/__main__.py \
        services/scanner/src/jobs_catalog.py \
        services/api/src/routes/admin.ts \
        services/scanner/tests/fixtures/bc_hansard/ \
        scripts/bc-enrich-historical-mlas.py \
        scripts/bc-hansard-backfill.sh \
        docs/research/british-columbia.md \
        docs/architecture.md
git status -s          # confirm only BC-Hansard / HNSW-doc files staged
git commit -m "feat(scanner): ingest-bc-hansard — LIMS HDMS Blues+Final HTML, 23-session backfill"
```

The AB Hansard pipeline from the earlier runbook is a separate commit.

---

## What NOT to redo

- **Do not re-run `ingest-bc-hansard` for sessions already done.** All 23 sessions are in the DB (P38-S4 → P43-S2). Idempotent upsert would just rewrite rows at current sequences (wasted bandwidth, no data change).
- **Do not re-run `chunk-speeches`.** 173,401 chunks exist; re-run finds nothing pending.
- **Do not re-run `bc-enrich-historical-mlas.py`.** It's idempotent (skips rows whose `lims_member_id` already exists) but there's no reason to — the 284 historical rows are in.
- **Do not drop `nvidia_uvm` manually post-boot.** Clean boot reloads it cleanly.

---

## Known follow-ups (not blocking the embed resume)

- **Deputy Speaker / The Chair role-only rows** — ~25 k BC speeches across all sessions have `politician_id=NULL` because these presiding roles don't map to a specific person at parse time. Needs a session-aware Deputy Speaker lookup (not in LIMS GraphQL reliably). Flagged in `docs/research/british-columbia.md`.
- **33 parse errors in P38-S4** — the oldest session had some sitting pages with older markup variants the parser didn't recognize. Acceptable loss for a ~2,600-speech session; can revisit if we need those speeches.
- **Committee transcripts** (`*-CommitteeA-Blues.htm`, `*-CommitteeC-Blues.htm`) — skipped by v1; a separate workstream.

---

## References

- AB Hansard runbook (same GPU issue, same day): `docs/runbooks/resume-after-reboot-2026-04-19-ab-hansard.md`
- Prior GPU-wedge runbooks: `docs/runbooks/resume-after-reboot-2026-04-16.md`, `resume-after-reboot-2026-04-17-cudnn-fix.md`
- BC research dossier (status, endpoint docs): `docs/research/british-columbia.md`
- BC Hansard plan file: `/home/bunker-admin/.claude/plans/can-we-start-working-hidden-deer.md`
- HNSW + filter fix background: `docs/architecture.md` § `db`
- Coverage refresher code: `services/scanner/src/legislative/coverage_stats.py`
