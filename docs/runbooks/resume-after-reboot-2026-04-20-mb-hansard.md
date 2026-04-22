# Resume after reboot — 2026-04-20 (MB Hansard + bills shipped, embed blocked on CUDA wedge)

**Status when paused:** Manitoba bills + Hansard ingestion **complete** for session 43-3. Chunking **complete** (6,801 MB chunks). Embedding **stuck**: TEI hit the same `DriverError(CUDA_ERROR_UNKNOWN)` → "Using CPU instead" wedge we've seen on every prior provincial backfill. `docker compose restart tei` did not clear the CUDA context. **Reboot is the fix.** No data loss; everything is safe in Postgres.

**TL;DR to resume:**

```bash
# After reboot:
docker compose up -d tei
docker compose logs tei --tail 30 | grep -iE "cuda|cpu|warming"
# Required: "Starting Qwen3 model on Cuda" (NOT "on Cpu"). If on Cpu, reboot again.
docker compose run --rm scanner embed-speech-chunks --batch-size 32
docker compose run --rm scanner refresh-coverage-stats
# Browser-check /coverage and /search?q=health+care&province=MB
# Then commit (see §"Commits" below).
```

---

## Where we left off

### DB state (verified pre-reboot)

| Metric | Value |
|---|---:|
| MB politicians with `mb_assembly_slug` | **56 / 56** seated MLAs |
| MB bills (session 43-3) | **81** (47 government + 34 PMB) |
| MB bill sponsors FK-linked | **81 / 81** (100 %, all via slug join) |
| MB bill events | **106** across 80 bills (80 first-reading, 17 second, 9 committee with committee_name) |
| MB speeches (`hansard-mb`) | **5,388** across 43 sittings |
| MB speeches resolved to politicians | **4,793 / 5,388 = 89.0 %** (after presiding-officer post-pass) |
|  └ via MLA slug match (inline)  | 2,571 |
|  └ via Tom Lindsey "The Speaker" post-pass | 2,222 |
| MB speeches with only `speaker_role` (not yet linked) | 555 (mostly "The Attorney General", "The Clerk", "The Chairperson" — out of presiding-officer scope) |
| MB speeches unresolved (no role, no match) | 40 (Lt. Governor, Sergeant-at-Arms, "An Honourable Member", a few section-header false-positives) |
| MB chunks total | **6,801** (4,418 speeches produced chunks; 970 speeches too short to chunk at 8-token minimum) |
| MB chunks embedded (pre-stall) | **0** — TEI never reached GPU this run |
| MB chunks pending embed | **6,801** (this is what the resume drains) |

Verification SQL:

```sql
-- Bills + sponsor-resolution
SELECT b.bill_type, COUNT(*) AS bills,
       COUNT(bs.politician_id) AS sponsors_linked
  FROM bills b
  JOIN legislative_sessions s ON s.id = b.session_id
  LEFT JOIN bill_sponsors bs ON bs.bill_id = b.id
 WHERE s.province_territory='MB' AND s.parliament_number=43 AND s.session_number=3
 GROUP BY b.bill_type;

-- Stage events from billstatus.pdf
SELECT e.stage, COUNT(*)
  FROM bill_events e JOIN bills b ON b.id=e.bill_id
  JOIN legislative_sessions s ON s.id=b.session_id
 WHERE s.province_territory='MB' AND s.parliament_number=43 AND s.session_number=3
 GROUP BY 1 ORDER BY 2 DESC;

-- Hansard totals
SELECT COUNT(*) AS speeches,
       COUNT(politician_id) AS resolved,
       ROUND(100.0 * COUNT(politician_id)::numeric / COUNT(*), 1) AS pct
  FROM speeches WHERE source_system='hansard-mb';

-- Chunks pending embed
SELECT COUNT(*) FROM speech_chunks sc
  JOIN speeches s ON s.id=sc.speech_id
 WHERE s.source_system='hansard-mb' AND sc.embedding IS NULL;
```

### What caused the stall

Same symptom as the AB / BC / QC backfills earlier this week:

```
sw-tei logs:
  WARN  Could not find a compatible CUDA device on host: CUDA is not available
        DriverError(CUDA_ERROR_UNKNOWN, "unknown error")
  WARN  Using CPU instead
  INFO  Starting Qwen3 model on Cpu
```

`nvidia-smi` on the host shows GPU healthy (15 MiB / 6141 MiB, idle). The CUDA context state inside TEI's runtime is wedged and `docker compose rm -sf tei && docker compose up -d tei` does not clear it — **only a host reboot resets `nvidia_uvm`.** This is a known recurring failure mode; see the prior runbooks for AB, BC, and QC if you want the identical diagnostic trail.

---

## Commits / scope touched this session

Not yet committed at reboot time. All files below should still be on the working tree; if not, regenerate from the references in `docs/research/manitoba.md` and `memory/project_provincial_bills_layer.md`.

**New files (8):**
- `db/migrations/0030_politician_mb_assembly_slug.sql` — `mb_assembly_slug TEXT` + partial unique index. Already applied.
- `services/scanner/src/legislative/pdf_utils.py` — shared Poppler `pdftotext` primitive (`layout=True/False`, `raw=True/False`). Hoisted from `ab_hansard.py:201`.
- `services/scanner/src/legislative/mb_mlas.py` — slug-stamp + insert-missing against OpenNorth's existing MB roster.
- `services/scanner/src/legislative/mb_bills.py` — HTML roster ingest from `web2.gov.mb.ca/bills/{P}-{S}/index.php` (Government + Private Members' tables).
- `services/scanner/src/legislative/mb_billstatus.py` — fetch + parse `billstatus.pdf` into real-dated `bill_events` via `pdftotext -raw`.
- `services/scanner/src/legislative/mb_bill_sponsors.py` — post-pass sponsor resolver (safety net; 100 % of current-session sponsors already resolve inline).
- `services/scanner/src/legislative/mb_hansard_parse.py` — Word-exported-HTML → `ParsedSpeech` list. Handles `<b>Name:</b>` speaker markup, `(HH:MM)` timestamp markers, windows-1252 encoding.
- `services/scanner/src/legislative/mb_hansard.py` — Hansard orchestrator + `SpeakerLookup` (slug-first, surname fallback) + `resolve_mb_speakers` post-pass.

**Modified (7):**
- `services/scanner/src/__main__.py` — 7 new Click commands: `ingest-mb-mlas`, `ingest-mb-bills`, `fetch-mb-billstatus-pdf`, `parse-mb-bill-events`, `resolve-mb-bill-sponsors`, `ingest-mb-hansard`, `resolve-mb-speakers`. Plus MB added to the `--province` choices on `resolve-presiding-speakers`.
- `services/scanner/src/jobs_catalog.py` — 7 new entries (mirror of the Click commands).
- `services/api/src/routes/admin.ts` — 7 new `COMMAND_CATALOG` entries (mirror; must stay in sync with jobs_catalog per CLAUDE.md).
- `services/scanner/src/legislative/ab_hansard.py` — `_pdftotext` now `from .pdf_utils import pdftotext`. Removed local `subprocess` import; behaviour unchanged (still `layout=False`, reading-order).
- `services/scanner/src/legislative/presiding_officer_resolver.py` — added `SPEAKER_ROSTER["MB"] = [SpeakerTerm("Tom Lindsey", "Tom", "Lindsey", date(2023, 11, 21), None)]` and `_SPEAKER_ROLE_BY_PROVINCE["MB"] = ("The Speaker",)`.
- `docs/research/manitoba.md` — status flipped from ⏸️ deferred to 🟢 live, counts filled in.
- `docs/plans/semantic-layer.md` — bills coverage bumped to 10/13, PDF-extraction row rewritten to reference the shared `pdf_utils.pdftotext` helper, phase-3 list notes MB Hansard shipped.

**Memory files (at `/home/bunker-admin/.claude/projects/-home-bunker-admin-sovpro/memory/`):**
- `project_provincial_bills_layer.md` — updated to 10/13 coverage, added MB row + MB-specific details (raw-mode PDF parsing, compound-surname handling, shared `pdf_utils`).

---

## Resume procedure

### 1. Verify CUDA is sound

```bash
docker run --rm --gpus all nvidia/cuda:12.4.0-base-ubuntu22.04 nvidia-smi
# Expected: prints GPU summary with "NVIDIA GeForce RTX 4050 Laptop GPU" idle.
```

If this errors, the host driver is broken — try `sudo nvidia-smi -r` once before re-rebooting.

### 2. Bring TEI up on GPU

```bash
docker compose up -d tei
sleep 10
docker compose logs tei --tail 30 | grep -iE "cuda|cpu|warming|ready"
```

**Required line:** `Starting Qwen3 model on Cuda`. If it says `on Cpu`, the CUDA context is still wedged — reboot again. Don't proceed to embedding on CPU; 6.8 k chunks at ~5 c/s would still finish in ~25 min but blocks the GPU recovery investigation.

### 3. Drain the MB embed backlog

```bash
docker compose run --rm scanner embed-speech-chunks --batch-size 32
```

Expected: ~50 chunks/sec end-to-end. For the MB backlog of 6,801 chunks, allow **~2–3 minutes**. The command only touches rows where `embedding IS NULL`, so if anything else has accumulated (unlikely in this window) it'll drain that too — not a concern.

Verify:

```sql
SELECT COUNT(*) AS embedded
  FROM speech_chunks sc JOIN speeches s ON s.id=sc.speech_id
 WHERE s.source_system='hansard-mb' AND sc.embedding IS NOT NULL;
-- Expected: 6801
```

### 4. Refresh coverage stats

```bash
docker compose run --rm scanner refresh-coverage-stats
```

This flips `jurisdiction_sources.MB.hansard_status = 'live'` based on the real speech count, and updates the `/coverage` page figures. No args needed.

### 5. Browser sanity-check

- **`/coverage`** — Manitoba should show Hansard live with 5,388 speeches, and bills up to 81.
- **`/search?q=health+care&level=provincial&province=MB`** — should return multiple hits with proper speaker attribution and constituency.
- Spot-check a random MB speech page — confirm Wab Kinew / Nahanni Fontaine / Matt Wiebe attributions render correctly (accented characters from windows-1252 decoding).

### 6. Commit

```bash
git add db/migrations/0030_politician_mb_assembly_slug.sql \
        services/scanner/src/legislative/pdf_utils.py \
        services/scanner/src/legislative/mb_mlas.py \
        services/scanner/src/legislative/mb_bills.py \
        services/scanner/src/legislative/mb_billstatus.py \
        services/scanner/src/legislative/mb_bill_sponsors.py \
        services/scanner/src/legislative/mb_hansard_parse.py \
        services/scanner/src/legislative/mb_hansard.py \
        services/scanner/src/__main__.py \
        services/scanner/src/jobs_catalog.py \
        services/api/src/routes/admin.ts \
        services/scanner/src/legislative/ab_hansard.py \
        services/scanner/src/legislative/presiding_officer_resolver.py \
        docs/research/manitoba.md \
        docs/research/overview.md \
        docs/plans/semantic-layer.md \
        docs/runbooks/resume-after-reboot-2026-04-20-mb-hansard.md
git commit -m "feat(scanner): manitoba bills + hansard — session 43-3 live (81 bills, 5,388 speeches)"
```

---

## Known limitations carried into this state

- **Historical sessions not yet ingested.** Current-session (43-3) only. The URL pattern holds back to the 25th Legislature (1958), so a `--since/--until/--limit-sittings` walk of prior sessions is straightforward whenever the user wants historical. Pre-2023 resolution will degrade once we leave the NDP-era roster the same way it does on AB/QC historical backfills — `politicians` only carries current MB MLAs.
- **Speaker roster is Tom Lindsey only.** The 43rd Legislature elected Tom Lindsey on 2023-11-21 and the whole MB Hansard corpus we have is in his tenure — so every "The Speaker" row resolves cleanly to him. When historical sessions are backfilled, add Myrna Driedger (PC, 2018-2023) and earlier entries to `SPEAKER_ROSTER["MB"]`; the resolver is idempotent on re-run.
- **Non-Speaker role attributions not resolved.** 555 speeches carry `speaker_role IN ('The Attorney General','The Clerk','The Chairperson','The Sergeant-at-Arms',…)` with `politician_id IS NULL`. These are real ministerial/officer titles that the parser extracted but the resolver ignores because they're not covered by the presiding-officer roster. Linking them would require either (a) cabinet-portfolio date ranges on `politician_terms`, or (b) parsing the `<b>Hon. Mr. Wiebe (Attorney General):</b>`-style compound attributions that some speeches do have. Not a regression — consistent with how AB/BC/QC handle cabinet roles today.
- **`raw_html` storage:** ~400 KB per sitting × 43 sittings ≈ 17 MB in `speeches.raw_html`, stored only on `sequence=1` per sitting (same write-amplification avoidance as QC/BC/AB).
- **PDF cache is ephemeral.** `billstatus.pdf` is cached in `/tmp/mb_pdf_cache/billstatus_YYYYMMDD.pdf` — wiped on each container restart. Fetch cost is ~270 KB / ~1s so this is fine; `MB_PDF_CACHE_DIR` env var can point at a writable mount later if diffing historical PDFs matters.

---

## If something goes wrong

- **Embedding fails with `ConnectError: All connection attempts failed`**: TEI isn't listening. Check `docker logs sw-tei` — if the model is still warming up, wait 30 s and retry. If it's on CPU, see §2.
- **`parse-mb-bill-events` fails with "No legislative_sessions row for MB 43-3"**: `ingest-mb-bills` hasn't been run for that session yet. Run it first (idempotent): `docker compose run --rm scanner ingest-mb-bills --parliament 43 --session 3`.
- **Sponsor resolution regresses**: `docker compose run --rm scanner resolve-mb-bill-sponsors` is idempotent; re-run.
- **Want to re-ingest from scratch**: `DELETE FROM speeches WHERE source_system='hansard-mb';` then rerun `ingest-mb-hansard`. All the `ingest-mb-*` commands are idempotent via their respective unique constraints.
