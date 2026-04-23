# Resume after reboot — 2026-04-22 (NB bills + Hansard shipped, embed blocked on CUDA wedge)

**Status when paused:** New Brunswick bills historical backfill **complete** (Leg 56–61, 1,248 bills). NB Hansard ingestion **complete** (Leg 58/3 → 61/2, 22,895 speeches, 78,923 chunks). Embedding **stuck at 58.6 %**: TEI hit the familiar `DriverError(CUDA_ERROR_UNKNOWN)` → "Using CPU instead" wedge we've seen on every prior provincial backfill (AB, BC, QC, MB). **Reboot is the fix.** No data loss; everything is safe in Postgres.

**TL;DR to resume:**

```bash
# After reboot:
docker compose up -d tei
sleep 10
docker compose logs tei --tail 30 | grep -iE "cuda|cpu|warming"
# Required: "Starting Qwen3 model on Cuda" (NOT "on Cpu"). If on Cpu, reboot again.
docker compose run --rm scanner embed-speech-chunks --batch-size 32
docker compose run --rm scanner resolve-nb-speakers        # idempotent
docker compose run --rm scanner refresh-coverage-stats
# Browser-check /coverage and /search?q=housing&province=NB
# Then commit (see §"Commits" below).
```

---

## Where we left off

### DB state (verified pre-reboot)

#### Bills layer

| Legislature | Years | Bills | raw_html | FK-linked sponsors |
|---|---|---:|---:|---:|
| 56 | 2007–2010 | 324 | 324 | 0 (0%) |
| 57 | 2010–2014 | 288 | 288 | 1 (~0%) |
| 58 | 2014–2018 | 241 | 241 | 31 (13%) |
| 59 | 2018–2020 | 97 | 97 | 14 (14%) |
| 60 | 2020–2024 | 229 | 229 | 72 (31%) |
| 61 | 2024– | 69 | 69 | 69 (100%) |
| **Total** | | **1,248** | **1,248** | **187** |

The low historical FK-link rate is expected: `politicians` only holds the 55-seat current NB roster (convention #1 deliberately doesn't apply — legnb.ca exposes no numeric MLA id). Historical enrichment is deferred; once `politicians` grows, re-run `ingest-nb-bills --all-sessions-in-legislature N` for each leg — idempotent, fills in retroactively via the sponsor UPSERT path.

#### Hansard layer

| Legislature | Sittings | Speeches | Resolved | % |
|---|---:|---:|---:|---:|
| 58/3–4 | 4 | 23 | 9 | 39% |
| 59 | 53 | 3,542 | ~1,450 | ~41% |
| 60 | 191 | 14,914 | ~8,200 | ~55% |
| 61 | 64 | 4,416 | ~3,380 | ~76% |
| **Total** | **312** | **22,895** | | |

- **Speaker-role (Mr./Madam Speaker) resolution:** 4,131 / 4,208 = **98%** via the NB entries in `presiding_officer_resolver.SPEAKER_ROSTER` (Collins, Guitard, Oliver, Landry).
- **Person resolution gradient:** 17% (Leg 59, 2018) → 77% (Leg 61, current). Gap tracks historical-MLA roster completeness — the same shape QC and AB showed post-backfill.

#### Chunks + embeddings

| Metric | Count |
|---|---:|
| NB chunks total | **78,923** |
| NB chunks embedded | **46,261 (58.6 %)** |
| NB chunks pending (this is what the resume drains) | **32,662** |
| Other-jurisdiction chunks pending | 13,476 (will also drain) |

### Verification SQL

```sql
-- Bills + raw_html capture
SELECT s.parliament_number, count(b.id) AS bills,
       count(*) FILTER (WHERE b.raw_html IS NOT NULL) AS with_html
  FROM legislative_sessions s
  LEFT JOIN bills b ON b.session_id = s.id
 WHERE s.province_territory='NB'
 GROUP BY 1 ORDER BY 1;

-- Hansard resolution by session
SELECT s.parliament_number, s.session_number, count(sp.id) AS total,
       count(*) FILTER (WHERE sp.politician_id IS NOT NULL) AS resolved,
       round(100.0 * count(*) FILTER (WHERE sp.politician_id IS NOT NULL)
             / NULLIF(count(sp.id),0), 1) AS pct
  FROM legislative_sessions s
  JOIN speeches sp ON sp.session_id = s.id AND sp.source_system='legnb-hansard'
 WHERE s.province_territory='NB'
 GROUP BY 1, 2 ORDER BY 1, 2;

-- Chunks pending embed (the reason we're rebooting)
SELECT count(*) FROM speech_chunks
 WHERE province_territory='NB' AND embedding IS NULL;
-- Expected before reboot: 32662; after resume: 0.
```

### What caused the stall

Identical symptom to the AB / BC / QC / MB backfills this month:

```
sw-tei logs:
  WARN  Could not find a compatible CUDA device on host: CUDA is not available
        Caused by: DriverError(CUDA_ERROR_UNKNOWN, "unknown error")
  WARN  Using CPU instead
  INFO  Starting Qwen3 model on Cpu
```

Contributing factor this time: **three embed workers ran concurrently** for a stretch (one from an earlier session plus two I spawned). That spiked TEI queue depth which appeared to correlate with the driver wedge — but the underlying driver fault is the same recurring pattern. `docker compose restart tei` won't clear it; `nvidia-smi` shows GPU healthy from the host but TEI's CUDA context is stuck. **Only a host reboot resets `nvidia_uvm`.** See the prior AB / BC / QC / MB runbooks if you want the identical diagnostic trail.

---

## Commits / scope touched this session

Not yet committed at pause time. Files on the working tree:

**New files:**
- `services/scanner/src/legislative/nb_hansard.py` — NB Hansard ingester. Clones `ab_hansard.py`'s PDF recipe but with NB-specific adaptations: literal-backslash PDF hrefs (URL-encoded to `%5C` on fetch), paragraph-level parsing (so multi-line speaker attributions like `Hon. Ms. Holt, resuming the adjourned debate on Motion 24:` match correctly), bilingual English-primary recognition with French speaker labels treated as body text.
- `docs/runbooks/resume-after-reboot-2026-04-22-nb-hansard.md` — this file.

**Modified:**
- `services/scanner/src/legislative/nb_bills.py` — two changes:
  1. Populate `bills.raw_html` + `html_fetched_at` in `_upsert_bill_and_events`. Required an **`$12::text` cast** in the INSERT statement because asyncpg can't infer the type when the value is NULL and the CASE branch is TIMESTAMPTZ (`AmbiguousParameterError`). The cast in the `CASE WHEN $12::text IS NULL` branch disambiguates.
  2. Fix `--all-sessions-in-legislature` discovery: the main bills index only exposes CURRENT-session bill-detail links, not historical. Replaced index-scraping with a direct probe of `S=1..6` within the requested legislature, keeping sessions that return non-empty list pages.
- `services/scanner/src/legislative/presiding_officer_resolver.py` — added `SPEAKER_ROSTER["NB"] = [Collins, Guitard, Oliver, Landry]` with exact tenure dates (2014-10-23 → present) and `_SPEAKER_ROLE_BY_PROVINCE["NB"] = ("Mr. Speaker", "Madam Speaker", "Madame Speaker", "The Speaker")`. NS was added in a parallel session.
- `services/scanner/src/__main__.py` — new Click commands `ingest-nb-hansard` and `resolve-nb-speakers`; NB added to the `--province` choices on `resolve-presiding-speakers`. Import block updated with `from .legislative.nb_hansard import ingest as ingest_nb_hansard, ingest_all_sessions_in_legislature as ingest_nb_hansard_all_sessions, resolve_nb_speakers as resolve_nb_hansard_speakers`.
- `services/scanner/src/jobs_catalog.py` — new entries for `ingest-nb-hansard`, `resolve-nb-speakers`; `resolve-presiding-speakers` choices extended with "NB". `ingest-nb-bills` entry gains `all_sessions_in_legislature` and `delay` args.
- `services/api/src/routes/admin.ts` (COMMAND_CATALOG) — mirrors the jobs_catalog changes exactly (per CLAUDE.md convention).
- `docs/research/new-brunswick.md` — corrected "legislatures 52–61" claim (real digital-bills depth is 56–61) and "Hansard 1900–present" claim (real digital-Hansard depth is 58/3 onward); added post-backfill counts + resolution rates; flipped Hansard status checkbox from `[ ]` to `[x]`.

**Plan file (outside repo):**
- `/home/bunker-admin/.claude/plans/we-want-to-do-inherited-dongarra.md` — the approved plan; all tasks complete except the final embed drain.

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
cd /home/bunker-admin/sovpro
docker compose up -d tei
sleep 10
docker compose logs tei --tail 30 | grep -iE "cuda|cpu|warming|ready"
```

**Required line:** `Starting Qwen3 model on Cuda`. If it says `on Cpu`, the CUDA context is still wedged — reboot again. Don't try to drain on CPU; 32 k NB chunks at ~0.5 c/s on CPU would take ~18 hours and blocks GPU recovery investigation.

### 3. Drain the NB embed backlog (and everything else pending)

```bash
docker compose run --rm scanner embed-speech-chunks --batch-size 32
```

Expected: ~50 chunks/sec end-to-end (per CLAUDE.md baseline). For the 32,662 NB backlog plus ~13,476 other-jurisdiction pending chunks = ~46,138 total to embed → **~15 min wall-clock**. The command only touches rows where `embedding IS NULL`, so a single worker drains the whole pending queue.

**Run one worker, not three.** The pre-reboot stall correlated with three concurrent workers contending on `UPDATE ... FROM UNNEST($ids, $vecs)` + stressing TEI's queue. One worker is the supported configuration.

Verify:

```sql
SELECT count(*) FILTER (WHERE embedding IS NOT NULL) AS embedded,
       count(*) FILTER (WHERE embedding IS NULL) AS pending
  FROM speech_chunks
 WHERE province_territory='NB';
-- Expected: embedded=78923, pending=0
```

### 4. Re-run the NB speaker post-pass (idempotent)

```bash
docker compose run --rm scanner resolve-nb-speakers
```

Expected: ~9,400 scanned, ~80 additional resolutions. This was already run pre-reboot (scanned=9554, updated=82). Re-running is harmless — it only touches rows where `politician_id IS NULL`. Worth running after any `politicians` enrichment pass in the future.

### 5. Refresh coverage stats

```bash
docker compose run --rm scanner refresh-coverage-stats
```

This flips `jurisdiction_sources.NB.hansard_status` based on the real speech count (currently stamps `partial` because pre-2016 Hansard isn't digitized on legnb.ca — that's mechanically correct and desirable). `/coverage` page picks up the new numbers.

### 6. Browser sanity-check

- **`/coverage`** — New Brunswick should show bills=1248, speeches=22895, hansard=partial.
- **`/search?q=housing&province=NB&level=provincial`** — should return hits with proper speaker attribution (Susan Holt / David Coon / Chris Collins era MLAs depending on the query).
- Spot-check a random NB speech page — confirm bilingual content renders (body text contains both English and French paragraphs, since the parser stores them together as one speech row per English speaker turn).

### 7. Commit

```bash
cd /home/bunker-admin/sovpro
git add services/scanner/src/legislative/nb_hansard.py \
        services/scanner/src/legislative/nb_bills.py \
        services/scanner/src/legislative/presiding_officer_resolver.py \
        services/scanner/src/__main__.py \
        services/scanner/src/jobs_catalog.py \
        services/api/src/routes/admin.ts \
        docs/research/new-brunswick.md \
        docs/runbooks/resume-after-reboot-2026-04-22-nb-hansard.md

git commit -m "$(cat <<'EOF'
feat(scanner): new brunswick bills historical backfill + hansard live

- nb_bills: 33 → 1,248 bills across leg 56–61 (2007–present).
  raw_html now captured for every detail-page fetch (convention #3).
  --all-sessions-in-legislature now probes S=1..6 directly since
  legnb.ca's main bills index only exposes current-session links.
- nb_hansard (new): bilingual PDF ingester for legnb.ca's
  /en/house-business/hansard/{L}/{S} listings. Handles literal-
  backslash URLs (%5C-encoded on fetch), multi-line speaker
  attributions, and English-first bilingual turns with French
  labels treated as translation body text.
- 22,895 NB speeches across 312 sittings (leg 58/3 onward).
  98% of "Mr./Madam Speaker" rows resolved via the NB entry in
  presiding_officer_resolver.SPEAKER_ROSTER (Collins/Guitard/Oliver/
  Landry). Person resolution 17%→77% across leg 59→61, gap tracks
  historical-MLA roster completeness.

CPD
EOF
)"
```

---

## Known limitations carried into this state

- **Historical MLA roster gap.** NB politicians table holds only the 55-seat current roster. This caps sponsor-FK rate on Leg 56–59 bills (0–14%) and person-speaker resolution on Leg 58–60 Hansard (~17–55%). Both are idempotent — re-run `ingest-nb-bills --all-sessions-in-legislature N` and `resolve-nb-speakers` after any NB roster enrichment and they'll fill in retroactively.
- **Pre-2016 Hansard not ingestable.** legnb.ca's digital Hansard starts at Leg 58/3 (Nov 2016); 58/1, 58/2, and all earlier sessions return "no transcripts" with a pointer to the Legislative Library (506-453-2338) for paper records. Coverage dashboard correctly marks NB as `hansard=partial`.
- **Non-Speaker role attributions not resolved.** NB Hansard speakers appear as `Mr. Chair`, `Madam Chair`, `Her Honour`, `Hon. Members` etc. — currently left with `politician_id=NULL` and `speaker_role` populated. These are out of scope for the presiding-officer resolver (Tier-1-only). Consistent with how AB/BC/QC handle cabinet-role attributions today.
- **Non-breaking-space character in nb_hansard.py.** `_FR_SPEAKER_RE` contains a U+00A0 non-breaking space inside a character class. When Editing that regex, read the exact bytes first — `sed -n '<line>,<line>p' file | cat -A` — because visual-equivalent ASCII strings won't match. (This bit us once during the session.)
- **Bills Leg 59/3 skipped one bill** at `/59/3/1/an-act-to-perpetuate-a-certain-ancient-right` due to a transient upstream 500. Re-running `ingest-nb-bills --all-sessions-in-legislature 59` will retry it; idempotent UPSERTs mean no duplicate rows.
- **Three-worker contention.** Running multiple `embed-speech-chunks` workers concurrently against the same pending queue does not parallelise cleanly — all workers `SELECT ... WHERE embedding IS NULL LIMIT N` and fight for the same rows. One worker is the supported mode.

---

## If something goes wrong

- **Embedding fails with `ConnectError: All connection attempts failed`**: TEI isn't listening (or crashed). Check `docker logs sw-tei` — if it says "Starting Qwen3 model on Cpu", back to step 2. If it's still warming up, wait 30 s and retry.
- **`resolve-nb-speakers` updates 0 rows**: Expected — the pre-reboot run already processed everything. Not a regression.
- **`ingest-nb-hansard --all-sessions-in-legislature L` finds 0 sittings** for a legislature we confirmed exists: the `_LISTING_PDF_RE` regex expects literal backslashes in hrefs. If legnb.ca switches to forward slashes in a future redesign, update the regex.
- **`ingest-nb-bills --all-sessions-in-legislature N` returns `sessions=0 bills=0`** for a legislature we confirmed exists: the list-page probe (`_LIST_HREF_RE.search(r.text)`) didn't find any bill-detail links. This can happen if legnb.ca serves a 200 with a "no bills" stub instead of 302ing. Re-check manually via `curl -sL https://www.legnb.ca/en/legislation/bills/{L}/{S} | grep -c "/en/legislation/bills/"`.
- **Want to re-ingest NB Hansard from scratch**: `DELETE FROM speech_chunks WHERE province_territory='NB'; DELETE FROM speeches WHERE province_territory='NB' AND source_system='legnb-hansard';` then rerun the ingest. Bills unaffected. All commands idempotent via their unique constraints.

---

## When this is done

NB is the 6th jurisdiction with Hansard live (federal + QC + AB + BC + MB + NS + **NB**), and the 10th with bills-layer coverage. Remaining provincial Hansard pipelines: ON, NL, PE, YT, SK, NT, NU — all gated on convention #5 (research-handoff rule). Remaining bills pipelines: MB (done in an earlier session), SK, PE, YT — MB now also done, so SK/PE/YT are the holdouts.

After the embed drain + commit, the immediate follow-ups per the plan:
1. Run `ingest-mlas` (if not recently run) to refresh the NB MLA roster — may include newer members missed in earlier runs.
2. (Deferred) Historical NB MLA enrichment — would lift Leg 56–59 sponsor FK rate and Leg 59–60 Hansard person-speaker resolution, both retroactively via idempotent re-runs.
3. (Deferred) Committee Hansards — on request from the Legislative Library, not scraped online.
4. (Deferred) Votes / Journals — embedded in Journals, no dedicated export.
