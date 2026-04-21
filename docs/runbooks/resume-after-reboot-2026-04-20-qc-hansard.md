# Resume after reboot — 2026-04-20 (QC Hansard embed, blocked on CUDA wedge)

**Status when paused:** QC Hansard ingest **complete** for the 7-session backfill (43-2 + 43-1 + 42-2 + 42-1 + 41-1 + 40-1 + 39-2 + 39-1 — see DB-state table for final counts), and chunking **complete**. Embedding stalled mid-run: TEI's CUDA context hit `DriverError(CUDA_ERROR_UNKNOWN)`, fell back to CPU, and stayed there even after `docker compose rm -sf tei && up -d tei`. Same `nvidia_uvm`-wedged failure mode we saw on the AB and BC backfills earlier this week — **reboot is the fix**. No data loss; everything is safe in Postgres.

**TL;DR to resume:**

```bash
# After reboot:
docker compose up -d tei
docker compose logs tei --tail 30 | grep -iE "cuda|cpu|warming"
# Expected: "Starting Qwen3 model on Cuda" (NOT "on Cpu"). If on Cpu, repeat reboot.
docker compose run --rm scanner embed-speech-chunks --batch-size 32
docker compose run --rm scanner resolve-presiding-speakers --province QC
docker compose run --rm scanner refresh-coverage-stats
# Browser-check /coverage and /search?q=logement&province=QC
```

---

## Where we left off

### DB state (verified pre-reboot)

| Metric | Value |
|---|---:|
| QC speeches (`hansard-qc`) | **313,345** |
| QC sessions ingested | **8** (39-1 → 43-2, Jan 2009 → Apr 2026, 17-year span) |
| QC sittings | **1,278** |
| QC chunks total | **438,830** |
| QC chunks embedded (pre-stall) | **45,631** (~10 %) |
| QC chunks pending embed | **393,199** (this is what the resume drains) |
| QC politicians linked | 179,243 / 313,345 = **57.2 %** overall (range per session: 31–85 %) |
| `jurisdiction_sources.QC.hansard_status` | `live` (already refreshed) |

#### Per-session breakdown

| Session | Speeches | Sittings | Resolved | Range |
|---|---:|---:|---:|---|
| 43-2 | 14,784 | 51 | 84.9 % | 2025-09-30 → 2026-04-02 |
| 43-1 | 65,253 | 223 | 83.4 % | 2022-11-29 → 2025-06-06 |
| 42-2 | 18,944 | 70 | 72.2 % | 2021-10-19 → 2022-06-10 |
| 42-1 | 49,092 | 214 | 69.9 % | 2018-11-27 → 2021-10-07 |
| 41-1 | 45,546 | 352 | 39.8 % | 2014-05-20 → 2018-06-15 |
| 40-1 | 23,872 | 85 | 31.1 % | 2012-10-30 → 2014-02-20 |
| 39-2 | 38,246 | 117 | 40.3 % | 2011-02-23 → 2012-06-15 |
| 39-1 | 57,608 | 166 | 40.5 % | 2009-01-13 → 2011-02-21 |

Resolution drops on older sessions because retired MNAs aren't in `politicians` — same gap as AB historical. Tier 1 Speaker resolution (the `resolve-presiding-speakers --province QC` step in the resume procedure) will add another several-thousand rows on top of these figures by linking `Le Président` attributions to the date-correct sitting Speaker across Bissonnet / Vallières / Chagnon / Paradis / Roy.

Per-session breakdown query:

```sql
SELECT ls.parliament_number || '-' || ls.session_number AS sess,
       COUNT(*) AS speeches,
       COUNT(DISTINCT s.source_url) AS sittings,
       ROUND(100.0 * COUNT(politician_id)::numeric / COUNT(*), 1) AS pct_resolved
  FROM speeches s
  JOIN legislative_sessions ls ON ls.id = s.session_id
 WHERE s.source_system = 'hansard-qc'
 GROUP BY 1
 ORDER BY 1;
```

### What caused the stall

```
sw-tei logs:
  WARN  Could not find a compatible CUDA device on host: CUDA is not available
        DriverError(CUDA_ERROR_UNKNOWN, "unknown error")
  WARN  Using CPU instead
  INFO  Starting Qwen3 model on Cpu
```

Same wedge as the AB and BC sessions earlier this week. `nvidia-smi` showed GPU healthy (15 MiB / 6141 MiB, idle). Diagnostic CUDA container worked:

```bash
docker run --rm --gpus all nvidia/cuda:12.4.0-base-ubuntu22.04 nvidia-smi   # ← prints normal GPU summary
```

…confirming the driver itself is fine. The CUDA context state inside TEI's runtime is wedged and a container recreate doesn't clear it. `docker compose rm -sf tei && docker compose up -d tei` produced the same "Using CPU instead" log. **Reboot is the only reliable reset.**

The pre-stall QC embed pass succeeded for ~26 k chunks at ~50 chunks/sec on GPU before TEI errored on a batch around 16:01. The remaining backlog will drain in one pass once GPU is restored.

### Background processes that may still be alive at reboot

These all die on reboot — no manual cleanup needed.

- **PID 670897** — `qc_chain_v2.sh`: sequential ingest + final chunk/embed/resolve. Should have completed before reboot. If it's still alive at reboot time, ingest didn't finish — check `tail -50 /tmp/qc_historical_backfill.log` to see where it left off, then resume by running the unfinished `ingest-qc-hansard` commands manually (idempotent).
- **PID 686416** — `qc_chunk_only_loop.sh`: chunks-only loop (embed paused). Will exit when chain v2 dies.

---

## Commits / scope touched this session

Already on `main` as of commit `193527a` (initial QC 43-2 ship). Subsequent historical-backfill work this session:

**Modified:**

- `services/scanner/src/legislative/qc_hansard.py` — added `discover_via_wayback()` fallback. The assnat.qc.ca ASP.NET search form returns HTTP 500 for every session except the current one (server-side bug, reproducible from multiple IPs and inside the container). The fallback queries the Wayback Machine CDX API for transcript URLs matching `assnat.qc.ca/.../{parl}-{sess}/journal-debats/*`, dedupes, and returns SittingRefs pointing at the **origin URLs**. Wayback is a URL-discovery crutch only — every actual transcript fetch goes straight to assnat.qc.ca. Confirm post-reboot:

  ```sql
  SELECT CASE WHEN source_url LIKE 'https://www.assnat.qc.ca/%' THEN 'origin'
              WHEN source_url LIKE 'https://web.archive.org/%' THEN 'wayback'
              ELSE 'other' END AS host, COUNT(*)
    FROM speeches WHERE source_system='hansard-qc' GROUP BY 1;
  ```
  Expected: 100% origin.

- `services/scanner/src/legislative/presiding_officer_resolver.py` — extended `SPEAKER_ROSTER["QC"]` from 2 to 5 entries to cover historical sessions:

  | Speaker | Term |
  |---|---|
  | Michel Bissonnet  | 2003-05-13 → 2008-04-08 |
  | Yvon Vallières    | 2008-04-08 → 2011-04-05 |
  | Jacques Chagnon   | 2011-04-05 → 2018-10-01 |
  | François Paradis  | 2018-11-28 → 2022-11-29 |
  | Nathalie Roy      | 2022-11-29 → present |

  These cover everything from 38-1 (2007) onward.

**Not yet committed at reboot time:**

- `docs/research/quebec.md` — historical-coverage update (counts, year span, Wayback fallback note).
- `docs/research/overview.md` — bumped QC Hansard count + year-range parity claim.
- This runbook (`docs/runbooks/resume-after-reboot-2026-04-20-qc-hansard.md`).

If those edits aren't on the working tree post-reboot, regenerate them from the per-session counts above.

---

## Resume procedure

### 1. Verify CUDA is sound

```bash
docker run --rm --gpus all nvidia/cuda:12.4.0-base-ubuntu22.04 nvidia-smi
# Expected: prints GPU summary with "NVIDIA GeForce RTX 4050 Laptop GPU" and "0 MiB / 6141 MiB" idle.
```

If this errors, the host driver is broken — try `sudo nvidia-smi -r` once before re-rebooting.

### 2. Bring TEI up on GPU

```bash
docker compose up -d tei
sleep 10
docker compose logs tei --tail 30 | grep -iE "cuda|cpu|warming|ready"
```

**Required line:** `Starting Qwen3 model on Cuda`. If it says `on Cpu`, the CUDA context is still wedged — reboot again. Don't proceed to embedding on CPU; ~700 k chunks at 5 c/s would take 30+ hours.

### 3. Drain the embed backlog

```bash
docker compose run --rm scanner embed-speech-chunks --batch-size 32
```

Expected: ~50 chunks/sec end-to-end. For a backlog of ~700 k chunks, allow ~4 hours.

If TEI errors out partway again (same `CUDA_ERROR_UNKNOWN`), restart the embed step — it picks up where it left off (only embeds rows where `embedding IS NULL`).

### 4. Resolve presiding officers

```bash
docker compose run --rm scanner resolve-presiding-speakers --province QC
```

Expected: links every `speaker_role='Le Président'` row with `politician_id IS NULL` to the date-correct Speaker via `politician_terms`. The QC roster (5 Speakers) covers 2003+. Idempotent — safe to re-run after adding new Speaker terms.

### 5. Refresh coverage + commit

```bash
docker compose run --rm scanner refresh-coverage-stats
git add docs/research/quebec.md docs/research/overview.md \
        docs/runbooks/resume-after-reboot-2026-04-20-qc-hansard.md \
        services/scanner/src/legislative/qc_hansard.py \
        services/scanner/src/legislative/presiding_officer_resolver.py
git commit -m "feat(scanner): qc hansard historical backfill (39-1 → 43-2, 8 sessions)"
```

### 6. Browser sanity-check

- `/coverage` — QC Hansard should show updated speech counts and year span.
- `/search?q=logement&level=provincial&province=QC` — should return hits across multiple parliaments, not just 43-2.
- Spot-check an old transcript URL (e.g. one from 39-1, 2009) — confirm it renders with proper attribution.

---

## Known limitations carried into this state

- **Historical politician resolution is sparse.** Only current (2022+) MNAs are in the `politicians` table. For sessions 39-x → 42-x, attribution will resolve roughly 30–60 % of speeches via surname matches that overlap with current names; the rest land `politician_id=NULL` with `speaker_role` set when extractable. Same gap as the AB historical backfill — fixable later by enriching `politicians` with retired QC MNAs.
- **Wayback CDX coverage is a hard ceiling on URL discovery.** Per-session counts:
  - 43-1: 223  -  42-2: 70  -  42-1: 215  -  41-1: 354  -  40-1: 107
  - 39-2: 117  -  39-1: 166  -  38-1: 97 (deferred)  -  37-x: <100 each (deferred)
  Real sitting counts may be 5–15 % higher than what Wayback indexed; fixable later if/when the assnat.qc.ca search form gets fixed.
- **Bilingual scope:** French-only (`/fr/`). English versions 500 on most sittings.
- **`raw_html` storage:** ~500 KB per sitting × ~1450 sittings = ~700 MB of duplicated HTML in the `speeches.raw_html` column. Consider a follow-up migration to compress or store only on the first row per sitting.
