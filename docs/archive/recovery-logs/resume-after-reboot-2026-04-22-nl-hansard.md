# Resume after reboot — 2026-04-22 (NL Hansard shipped, embed blocked on CUDA wedge)

**Status when paused:** Newfoundland & Labrador Hansard pipeline **built + validated end-to-end**. Ingestion **complete** for GA 51 Session 1 (18 sittings, 8,341 speeches) and GA 50 Session 2 (73 sittings, 35,390 speeches) — 43,731 NL speeches total across 2 sessions spanning 2022-10-19 → 2026-04-22. Chunking **complete** — **37,305 NL chunks** total (6,073 embedded from the GA 51 S1 pre-wedge run, **31,232 pending embed**). Embedding **stuck**: TEI fell into the same `DriverError(CUDA_ERROR_UNKNOWN)` → "Using CPU instead" wedge that hit MB/QC/AB/BC before. **Reboot is the fix.** No data loss; everything is safe in Postgres.

**TL;DR to resume:**

```bash
# After reboot:
docker compose up -d tei
sleep 10
docker compose logs tei --tail 30 | grep -iE "cuda|cpu|warming"
# Required: "Starting Qwen3 model on Cuda" (NOT "on Cpu"). If on Cpu, reboot again.

# Chunking is already done. Embed the 31k NL pending + 33k global carry-over.
# At 50 c/s end-to-end this takes ~20 minutes for the full 64k pending.
docker compose run --rm scanner embed-speech-chunks

docker compose run --rm scanner refresh-coverage-stats
# Expect NL.hansard_status stays 'partial' at 43.7k speeches (50k is the 'live' threshold).
# /search?q=future+fund&province=NL should return hits spanning 2022-2026.

# Then commit (see §"Commits" below).
```

---

## Where we left off

### DB state (verified pre-reboot, 2026-04-22 16:08)

| Metric | Value |
|---|---:|
| NL sessions covered | **2** (GA 51 Session 1 + GA 50 Session 2) |
| Date range | **2022-10-19 → 2026-04-22** |
| NL speeches total (`hansard-nl`) | **43,731** |
| NL speeches with `politician_id` | **25,107** (57.4 %) |
| &nbsp;&nbsp;└ via name-matched resolver (inline + post-pass) | ~14,000 |
| &nbsp;&nbsp;└ via presiding-officer seed (Lane + Bennett + Reid + Trimper + Osborne) | 11,090 |
| NL speeches `speech_type='group'` (SOME HON. MEMBERS) | **10,416** (NULL politician_id by design) |
| NL speeches unresolved (no role, no match, not group) | **2,666** |
| NL chunks total (chunker complete) | **37,305** |
| NL chunks embedded | **6,073** (all from GA 51 S1, pre-wedge) |
| NL chunks pending embed | **31,232** |
| Global chunks pending embed (all jurisdictions) | **63,894** (chunker processed everything pending globally, not just NL) |
| Coverage dashboard | `NL: hansard=partial, speeches=43731` |

Verification SQL:

```sql
-- Session + date span
SELECT COUNT(*) AS speeches,
       COUNT(DISTINCT session_id) AS sessions,
       MIN(spoken_at::date) AS earliest,
       MAX(spoken_at::date) AS latest
  FROM speeches WHERE province_territory = 'NL';

-- Resolution breakdown
SELECT COUNT(*) AS total,
       COUNT(politician_id) AS resolved,
       COUNT(*) FILTER (WHERE speech_type = 'group') AS group_chants,
       COUNT(*) FILTER (WHERE speaker_role = 'The Speaker') AS presiding,
       COUNT(*) FILTER (WHERE politician_id IS NULL
                          AND speech_type != 'group'
                          AND (speaker_role IS NULL OR speaker_role = ''))
         AS unresolved
  FROM speeches WHERE province_territory = 'NL';

-- Chunk / embed coverage
SELECT (SELECT COUNT(*) FROM speech_chunks sc JOIN speeches s ON s.id = sc.speech_id
         WHERE s.province_territory = 'NL') AS chunks,
       (SELECT COUNT(*) FROM speech_chunks sc JOIN speeches s ON s.id = sc.speech_id
         WHERE s.province_territory = 'NL' AND sc.embedding IS NULL) AS pending;
```

### What caused the stall

Same fingerprint as the MB/QC/AB/BC backfills:

```
sw-tei logs:
  WARN  Could not find a compatible CUDA device on host: CUDA is not available
        DriverError(CUDA_ERROR_UNKNOWN, "unknown error")
  WARN  Using CPU instead
  INFO  Starting Qwen3 model on Cpu
```

The embed command that fired mid-wedge reported `seen=15000 embedded=0 batches=0 errors=469` — every batch call came back with "All connection attempts failed" because TEI was mid-restart. It then came back on CPU and stayed there. `nvidia-smi` on the host shows the RTX 4050 healthy and idle — the CUDA context inside the TEI container is wedged and `docker compose restart tei` does NOT clear it. **Only a host reboot resets `nvidia_uvm`** (confirmed on every prior provincial backfill; see the MB / QC / AB runbooks for identical traces).

### Background jobs still running (check before reboot)

```bash
docker ps --filter "name=scanner-run" --format "{{.Names}} {{.Status}}"
```

At pause time the chunker had finished (`chunk-speeches: seen=100000 chunked=25923 skipped=74077 chunks=31232`). No scanner jobs should still be running. If one is (defunct `scanner-run-*` containers sometimes linger), `docker compose down` before reboot — or just reboot; nothing is mid-transaction.

---

## Commits / scope touched this session

Not yet committed at reboot time. All files below should still be on the working tree; run `git status` to confirm.

**New files (2):**
- `services/scanner/src/legislative/nl_hansard_parse.py` — era-branching HTML parser. Modern (Word-exported MsoNormal + `<strong><span>`) and legacy (FrontPage malformed `<b>`) paths share the same `ParsedSpeech` output. Speaker-line regex, section-heading detection, initial-plus-surname + title-plus-surname attribution parsing, group-marker detection ("SOME HON. MEMBERS"), partial-transcript flag. Pure-offline (no network, no DB).
- `services/scanner/src/legislative/nl_hansard.py` — orchestrator. Session-index discovery from `/HouseBusiness/Hansard/ga{GA}session{S}/`, per-sitting fetch (UTF-8 forced), speaker resolver with `(first_initial, surname)` date-windowed lookup that trusts unique matches (sidesteps Open North's `started_at = now()` bug and presiding-seed / MP-term collisions), `_upsert_speech`, post-pass `resolve_nl_speakers`. Source system `hansard-nl`.
- `docs/runbooks/resume-after-reboot-2026-04-22-nl-hansard.md` — this file.

**Modified (5):**
- `services/scanner/src/__main__.py` — imports `ingest_nl_hansard` + `resolve_nl_speakers`, adds 2 Click commands: `ingest-nl-hansard`, `resolve-nl-speakers`. Extends `resolve-presiding-speakers --province` choices with `NL`.
- `services/scanner/src/jobs_catalog.py` — adds `ingest-nl-hansard` + `resolve-nl-speakers` entries. Extends `resolve-presiding-speakers` choices with `NL`.
- `services/api/src/routes/admin.ts` — mirrors the above two catalog entries + extends the presiding-speakers province choices. Must stay in sync with `jobs_catalog.py` per CLAUDE.md.
- `services/scanner/src/legislative/presiding_officer_resolver.py` — adds `SPEAKER_ROSTER["NL"]` with five entries (Osborne, Trimper, Reid, Bennett, Lane) covering GA 48 through current GA 51. Adds `_SPEAKER_ROLE_BY_PROVINCE["NL"] = ("The Speaker",)`.
- `docs/research/newfoundland-labrador.md` — reconciled with live state. Status snapshot updated to "1,193 bills / 3,677 events across 24 sessions"; Windows-1252 note scoped to per-bill pages (Hansard is UTF-8); Hansard section replaced with full probed findings (URL taxonomy, era-branching content formats, attribution shapes, MHA-canonical-ID absence, probe-hierarchy exhaustion); checklist shows Hansard as in-progress with live counts.

**No migration required** — `assembly.nl.ca` doesn't expose a canonical MHA id, so convention #1 (`{jurisdiction}_slug` column) doesn't apply here. Speaker resolution uses name matching against the existing Open North roster + date-windowed term filter.

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

**Required line:** `Starting Qwen3 model on Cuda`. If it says `on Cpu`, the CUDA context is still wedged — reboot again. Don't proceed to embedding on CPU; 16 k chunks at ~5 c/s on CPU would take ~50 minutes and blocks GPU-recovery investigation.

### 3. Drain the embed backlog

Chunking is already complete (37,305 NL chunks, 63,894 global pending at pause).

```bash
docker compose run --rm scanner embed-speech-chunks --batch-size 32
```

Expected: ~50 chunks/sec end-to-end. For 63,894 pending chunks, allow **~20 minutes**. If you want to cap this run to just the NL backlog so it finishes faster, pass `--limit 31232` — but there's no downside to draining everything in one go since all pending rows will need embedding eventually.

Verify:

```sql
SELECT COUNT(*) AS embedded, COUNT(*) FILTER (WHERE embedding IS NULL) AS pending
  FROM speech_chunks sc JOIN speeches s ON s.id = sc.speech_id
 WHERE s.province_territory = 'NL';
-- Expected: embedded = 37305, pending = 0
```

### 4. Refresh coverage stats

```bash
docker compose run --rm scanner refresh-coverage-stats
```

Flips `jurisdiction_sources.NL.hansard_status` based on the real speech count. **Thresholds:** ≥1 k → `partial`, ≥50 k → `live`. NL is at 43,731 so will stay `partial` — it'll flip to `live` once GA 49 or earlier backfills are added (next session after this runbook wraps).

### 5. Browser sanity-check

- **`/coverage`** — NL should show Hansard partial with 43,731 speeches, bills at 1,193.
- **`/search?q=future+fund&province=NL`** — should return hits from **both** 2023 and 2026 (cross-session verification). Paul Lane attributed correctly as "SPEAKER" for GA 51, Derek Bennett as Speaker for GA 50.
- **`/search?q=healthcare+funding&province=NL&date_from=2022-10-01&date_to=2023-12-31`** — should return GA 50 S2 hits only.
- Spot-check a random NL speech page — confirm Jim Hogan / Craig Pardy / Tony Wakeham / Helen Conway Ottenheimer attributions render. Curly apostrophes (O'Leary) should survive UTF-8 decoding.

### 6. Commit

```bash
git add services/scanner/src/legislative/nl_hansard.py \
        services/scanner/src/legislative/nl_hansard_parse.py \
        services/scanner/src/legislative/presiding_officer_resolver.py \
        services/scanner/src/__main__.py \
        services/scanner/src/jobs_catalog.py \
        services/api/src/routes/admin.ts \
        docs/research/newfoundland-labrador.md \
        docs/runbooks/resume-after-reboot-2026-04-22-nl-hansard.md

git commit -m "feat(scanner): newfoundland hansard — GA 50 s2 + 51 s1 live (43,731 speeches)"
```

---

## Known limitations carried into this state

- **No canonical MHA id on `politicians`.** `assembly.nl.ca/Members/members.aspx` is a postal-code "Your Member" lookup, not a per-member profile page — there's no stable slug or numeric id to persist. Convention #1 (`nl_assembly_slug` column) does **not** apply. Speaker resolution is (first_initial, surname) + date-windowed term fallback. This is a feature, not a bug — but it means unique-name coverage matters more than in MB/NS/BC.
- **Open North `politician_terms.started_at` is stamped to ingest time, not actual term start.** For NL that means current MHAs show `started_at=2026-04-14` even though they've served since 2021+. The NL resolver works around this by treating `(initial, surname)` as the primary key and only date-windowing when it would disambiguate between multiple politicians. Same fix probably wants propagating to MB/NS/BC if their resolvers tighten further.
- **Historical MHAs missing from `politicians`.** Names like `J. BROWN`, `A. FUREY`, `A. PARSONS`, `J. ABBOTT`, `D. BRAZIL` in GA 50 S2 are real NL MHAs but not in the current Open North roster (retired before the snapshot). These are the 2,666 unresolved rows. Fixable by adding a historical-roster ingester from `/Members/pastmembers.aspx` — separate workstream.
- **Jim vs James Dinn duplicate in `politicians`.** Open North has both `Jim Dinn` and `James Dinn` as distinct records; both match `(j, dinn)` → my resolver flags as ambiguous (645 `J. DINN:` rows). Real fix is to dedup the politicians table, not touch the resolver.
- **Compound-surname miss.** `H. CONWAY OTTENHEIMER:` (423 rows) — the parser extracts "Conway Ottenheimer" as a two-word surname (slug: `conwayottenheimer`), but the DB has `last_name="Ottenheimer"` (slug: `ottenheimer`). Fix would be to add slug candidates in `nl_hansard.py::_surname_slug` (also emit the last-token-only slug). MB has a similar helper `_slug_candidates`; port it.
- **Sergeant-at-Arms / Table Officer / Lt. Governor rows unresolved.** 11 rows total. These are procedural officers, not MHAs — out of scope for the presiding-officer resolver. Consistent with MB/AB/BC behaviour.
- **Legacy (FrontPage) era not yet exercised at scale.** The parser has been smoke-tested on one 1999 transcript (GA 44 S1). Pre-2004 sessions haven't been ingested. First big legacy-era run is a separate backfill and likely needs parser hardening — the 1999 sample had some malformed markup the regex tolerated, but a full-session pass will surface more edge cases.
- **GA 50 S2 ingest saw 120 sitting hrefs** vs the 73 sittings the dossier says exist. The extra 47 matched the date regex but either returned 0 speeches (53 parse errors) or were empty placeholder files. No data ingested from them, so the discrepancy is cosmetic — but worth noting if someone audits the "sittings scanned" number.

---

## If something goes wrong

- **Embedding fails with `ConnectError: All connection attempts failed`**: TEI isn't listening. Check `docker logs sw-tei` — if the model is still warming up, wait 30 s and retry. If it's on CPU, see §2.
- **TEI starts on CPU after first reboot**: Reboot a second time. On RTX 4050 Mobile this occasionally needs two resets to release `nvidia_uvm` cleanly.
- **`ingest-nl-hansard` reports `catch-all 404 template`**: `assembly.nl.ca` returns 200 for unmapped URLs with a styled error page (title "House of Asembly - NL - Error Page" — typo is upstream). The resolver compares body-content, not status. If this fires unexpectedly, verify the `ga{GA}session{S}/` URL exists on the Hansard landing page manually.
- **Speaker resolution regresses on re-run**: `resolve-nl-speakers` and `resolve-presiding-speakers --province NL` are both idempotent. Re-running is safe.
- **Want to re-ingest from scratch**: `DELETE FROM speeches WHERE source_system='hansard-nl';` then rerun `ingest-nl-hansard --ga 50 --session 2` and `--ga 51 --session 1`. All commands are idempotent via the `(source_system, source_url, sequence)` unique constraint.

---

## Next session after this one wraps

Per the "Do #1 then #3" plan from before the wedge:

1. **#3 from the menu: exercise legacy (FrontPage) parser at scale.** Ingest GA 44 Session 1 (1999), which is the first legacy-era full session. The parser has only been unit-tested on one file. Expect to iterate on the parser after the first real-session run.
2. **Then (optional) #2: close the 2.7 k unresolved gap.** Port MB's `_slug_candidates` to NL for compound-surname handling, dedup Jim/James Dinn in `politicians`, or add a historical-roster ingester from `pastmembers.aspx`.
3. **Then #4: next jurisdiction (ON/SK/PE/NT/NU/YT).** Research-handoff required per CLAUDE.md.
