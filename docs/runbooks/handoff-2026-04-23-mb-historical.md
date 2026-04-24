# Handoff — 2026-04-23 (MB historical MLAs + MB Hansard 1999-present live)

**Session arc:** continued the post-reboot digest from 2026-04-22 (commits `7c765e2`, `20aaaa9`, `73e7b9c`, `6efbcd1`), took MB from "current session only" (30k speeches) to **"legs 37-43 live, full 27-year span"** (407,695 speeches, 1999-11-26 → 2026-04-16). Four commits landed on `main`; working tree is still dirty with unrelated in-progress work (billing rail shipped by `233634c` in a parallel workstream, AI contradictions, etc.) that is **not** scoped to this handoff.

**TL;DR resume path (if you pick this up):**

```bash
# 1. Confirm state hasn't drifted
docker exec sw-db psql -U sw -d sovereignwatch -At -c "
  SELECT count(*), ROUND(100.0*count(politician_id)::numeric/count(*),1)
    FROM speeches WHERE source_system='hansard-mb';"
# Expected: 407695 | 79.7

# 2. Next-steps menu (choose one):
#   a) Pre-1999 MB (legs 25-36, 1958-1999): same Word-97 parser
#      should work; session-index format not yet probed for pre-37.
#   b) MB admin.ts catalog mirror (deferred with commit 73e7b9c —
#      see below; note: billing-rail commit 233634c may have
#      already added adjacent entries to admin.ts).
#   c) Another province's Hansard (ON is the big remaining one —
#      research-handoff rule still applies per CLAUDE.md §5).
```

---

## What shipped this session

### Commits on `main`

| SHA | Title | Files | Lines |
|---|---|---:|---:|
| `7c765e2` | feat(scanner): post-reboot digest — ab historical mlas + nb/nl hansard live + ns speaker roster | 15 | +2977 −30 |
| `20aaaa9` | feat(scanner): mb historical mla roster — 764 former mlas across 1870–present | 6 | +612 −1 |
| `73e7b9c` | feat(scanner): mb hansard backfill — legs 39-43 live (292k speeches) | 6 | +157 −7 |
| `6efbcd1` | feat(scanner): mb hansard word97 era — legs 37-38 live (+115k speeches, 1999-2007) | 3 | +420 −9 |

Ordering reflects dependency: MB historical MLAs (`20aaaa9`) is a prerequisite for the MB Hansard backfills' date-windowed speaker resolution (`73e7b9c`, `6efbcd1`). The Word-97 parser (`6efbcd1`) shipped *after* `73e7b9c` because the legs 37-38 markup needed a separate parser module — it was initially deferred and later landed in the same session.

### Data shipped

| Artifact | Before session | After session |
|---|---:|---:|
| NL Jim/James Dinn duplicate | 2 rows (both active) | merged → 1 (645 speeches re-attributed) |
| MB politicians (provincial) | 56 | **820** (+764 historical MLAs 1870–present) |
| MB `politician_terms` (historical backfill source) | 0 | **1,723** |
| MB speeches | 30,649 | **407,695** |
| MB sittings | ~160 | **2,325** |
| MB speech_chunks (Qwen3-embedded) | ~40k | **510,237** (100% embedded) |
| MB person-resolution | 81.3% | **79.7%** (see resolution note below) |
| MB date range | 2023-11 → 2026-04 | **1999-11-26 → 2026-04-16** (+24 yr) |
| MB coverage status | partial | **live** |

The resolution rate dip is real but deceptive: the absolute **count** of resolved speeches jumped from 24,912 to 324,736 (13.3×). The %-drop is because the denominator grew faster than the numerator in absolute terms — the historical-era speeches are harder to resolve than current-session ones because Tom Lindsey is canonical in 2024 but "McFadyen" in 2009 could be any of several MLAs with that surname. The dated resolver (below) rescues what it can.

---

## Files touched (code, not data)

### New

- `services/scanner/src/legislative/mb_former_mlas.py` — ingester + dual-format parser (deceased-style strong-tag term ranges + living-style narrative "Elected g.e. DATE"/"Resigned DATE" events). Name-matches existing MB politicians before inserting so current-roster rows (slug `byram`) receive historical terms rather than duplicating as `byram-jodie`.
- `db/migrations/0032_unique_mb_assembly_slug.sql` — tighten `politicians.mb_assembly_slug` partial index to UNIQUE (parallel to AB's 0031). Verified zero collisions at migration time.

### Modified

- `services/scanner/src/legislative/mb_hansard_parse.py` — `extract_sitting_date` gains a body-scan fallback for "letter-variant" volumes (`vol_NN[a-z]`) whose `<title>` is a bare `VOL`. Strips tags + collapses whitespace before regex matching (sitting-date header is wrapped in `<b><span>Thursday,</span></b><b><span>April 24, 2008</span></b>` in Word-exported HTML). No byte limit on the fallback — early-session pages have ~30 KB of CSS font-face definitions before body content.
- `services/scanner/src/legislative/mb_hansard.py` — new `resolve_mb_speakers_dated` function: joins unresolved MB speeches by normalized surname AND `politician_terms` by `spoken_at ∈ [started_at, ended_at]`, attributes when exactly one politician emerges. MB analog of AB's legl-keyed resolver.
- `services/scanner/src/legislative/presiding_officer_resolver.py` — MB `SPEAKER_ROSTER` expanded from Lindsey-only (2023+) to Hickes/Reid/Driedger/Lindsey (1999-10-06 → present). Transition dates are election-boundary approximations; date-windowed resolver uses them for "The Speaker" attribution across the 1999-2023 span.
- `services/scanner/src/__main__.py` — Click commands `ingest-mb-former-mlas` + `resolve-mb-speakers-dated`.
- `services/scanner/src/jobs_catalog.py` — entries for both new commands.
- `docs/research/manitoba.md` — status snapshot updated to reflect 292k speeches, 820 politicians, live status.

### Deferred from this session's commits

- `services/api/src/routes/admin.ts` — **has my `resolve-mb-speakers-dated` catalog mirror in the working tree, uncommitted**. The same file also has ~220 lines of unrelated billing-rail code from a parallel workstream. Staging only my hunk non-interactively is awkward, so I left it for the billing commit to include the MB mirror alongside its own additions. Worst-case drift: admin UI won't show the command in the Jobs form picker; the worker + CLI still honor it. Not a blocker.

---

## Operational learnings

### 1. Legs 37-38 Word-97 parser landed (commit `6efbcd1`)

Discovered during the smoke-test phase, shipped later in the same session. Sessions 37-1 through 38-6 (1999-10 → 2007-05) use:

```html
<HTML><HEAD>
<META NAME="Generator" CONTENT="Microsoft Word 97">
<TITLE>Daily</TITLE>
</HEAD><BODY LINK="#0000ff" VLINK="#800080">
<B><P ALIGN="JUSTIFY">Hon. Diane McGifford (Minister of Culture, ...): </B>Mr. Speaker, ...
```

No `MsoNormal` classes, no `class` attributes, uppercase tags; the `<B>` wrapper opens *before* `<P>` and closes after the attribution colon (so the bold run spans the paragraph boundary). The new `mb_hansard_parse_w97.py` module handles this shape end-to-end; `mb_hansard.py` dispatches on format at parse time (`is_word97()` checks for the Microsoft-Word-97 generator meta tag + absence of `MsoNormal`).

Two additional complications:
  * Session index for 37-38 uses `vol_NN/index.html` (not `summary.html`), and directory names are mixed-case (`Vol_002` alongside `vol_005`). `_VOL_HREF_RE` was extended to preserve case so URL reconstruction stays correct.
  * **Split sittings:** `Vol_002/index.html` links out to `h002_1.html` AND `h002_2.html` (typically morning + afternoon transcripts for one calendar day). Each split lands as its own SittingRef. Added `_W97_TRANSCRIPT_HREF_RE` to enumerate the per-volume transcript filenames by fetching each vol's index page.

Net: +115,048 speeches across 567 sittings, zero parse errors. Full 27-year MB Hansard corpus now live.

### 2. Living-page vs deceased-page format split in `mla_bio_*.html`

Important for the former-MLAs ingester:
- **Deceased page:** `<strong>Month DD, YYYY - Month DD, YYYY</strong>` term-range tags. Structured. ~590 MLAs.
- **Living page:** narrative events in `<p>` tags — "Elected g.e. DATE", "Re-elected g.e. DATE", "Resigned DATE", "Not a candidate g.e. DATE", "Died DATE", etc. ~220 MLAs (mostly modern).

Single-format parsers capture ~34% of terms; the `<tr>`-based dual-format parser captures 97% (742 / 764 historical rows have ≥1 term). The 22 without terms are pre-1900 edge cases with unparseable date formatting; chasing them isn't worth it.

### 3. `ON CONFLICT DO UPDATE` resets `politician_id`

The MB Hansard ingester's UPSERT includes `politician_id = EXCLUDED.politician_id` in its `DO UPDATE SET` clause. If you re-ingest a session to fix a parser bug (like the 1970-date issue), the UPDATE overwrites `politician_id` with whatever the fresh ingest-time resolver produced — **undoing any prior `resolve-*` pass work on those rows**. Pattern: always `re-ingest → resolve`, never `resolve → re-ingest`.

Lost ~29k resolutions this way mid-session; recovered by re-running `resolve-presiding-speakers --province MB` + `resolve-mb-speakers-dated` after the repair was done.

### 4. 1970-01-01 as a failed-fallback sentinel

`mb_hansard_parse.py::_parse_sitting` had `date(1970, 1, 1)` as its fallback when `_TITLE_DATE_RE` missed. Silent failure mode: those speeches ingest fine, embed fine, search fine, but date-windowed resolvers find zero candidate MLAs (no politician served on 1970-01-01 of any Canadian legislature), and date filters exclude them silently. Spotted via year-histogram SQL — 22,641 rows mapped to 1970.

Fix landed in the parser. Post-W97 backfill, 1,803 rows (0.4% of 407k) remain with epoch fallback where even the body text has no parseable date (procedural openings / stub sittings).

**Durable lesson:** sentinel dates should be `NULL`, not valid-but-wrong. `spoken_at NULL` would have broken the downstream queries loudly at first use.

### 5. Three-worker contention wedges the GPU

Known from prior runbooks but reconfirmed: running `resolve-mb-speakers-dated` during an active `embed-speech-chunks` drain triggered a Postgres deadlock on `speech_chunks` (both UPDATE the same table). Retry succeeded once the embed worker moved to different batches, but the safer pattern is sequential: **embed drain → resolvers**, not parallel. Applies to any per-legl speech/chunk propagation step.

---

## Known limitations carried into this state

- **Pre-1999 MB Hansard (legs 25-36, 1958-1999) not yet ingested.** The same Word-97 parser should work (markup presumably similar), but `_VOL_HREF_RE` hasn't been probed against those older session-index pages and discovery may hit a third format variant. Unsized scope.
- **1,803 MB speeches with `spoken_at = 1970-01-01`** (0.4% of corpus) — sittings with truly no body-date. Search by content works; date filter excludes silently. Fix requires either deeper parser work or flipping to `spoken_at = NULL`.
- **2,963 MB speeches still ambiguous after dated v2 resolver** (pre-W97 number; post-W97 the ambiguous count after re-resolving is 3,872) — surnames with term-overlap across multiple MLAs (father/son seat successions, two MLAs with the same surname serving simultaneously). Requires honorific/constituency disambiguation, not date windows.
- **MB admin UI catalog mirror missing** for `resolve-mb-speakers-dated` (see "Deferred" above). Worker + CLI honor it; only the Jobs-form picker is blind.
- **`speech_chunks.spoken_at` was manually propagated once** after the re-ingest pass. If more re-ingests land, re-run:
  ```sql
  UPDATE speech_chunks sc SET spoken_at = s.spoken_at
    FROM speeches s WHERE sc.speech_id = s.id
     AND s.source_system = 'hansard-mb'
     AND sc.spoken_at IS DISTINCT FROM s.spoken_at;
  ```
  Consider wiring this into a post-repair step of the MB ingester itself.
- **~24k MB rows are "unresolvable by role"**: `Mr. Chairperson`, `Some Honourable Members`, procedural officers. Correct `speaker_role` set; `politician_id = NULL` by design.

---

## Verification SQL (post-reboot sanity)

```sql
-- MB state snapshot (expected 2026-04-23 post-W97)
SELECT
  (SELECT count(*) FROM speeches WHERE source_system='hansard-mb') AS speeches,
  (SELECT count(*) FROM speeches WHERE source_system='hansard-mb' AND politician_id IS NOT NULL) AS resolved,
  (SELECT count(*) FROM politicians WHERE province_territory='MB' AND level='provincial') AS politicians,
  (SELECT count(*) FROM speech_chunks sc JOIN speeches s ON s.id=sc.speech_id
    WHERE s.source_system='hansard-mb' AND sc.embedding IS NULL) AS pending_embed,
  (SELECT count(*) FROM speeches WHERE source_system='hansard-mb' AND spoken_at::date='1970-01-01') AS date_fallbacks;
-- Expected: speeches=407695, resolved=~324736, politicians=820, pending_embed=0, date_fallbacks=1803
```

```sql
-- Year spread should be continuous 1999-2026 plus the 1970 fallback pool
SELECT extract(year from spoken_at)::int AS yr, count(*) AS n
  FROM speeches WHERE source_system='hansard-mb' GROUP BY 1 ORDER BY 1;
-- Expected: 1970 (fallback pool), then continuous 1999 … 2026.
```

```sql
-- Migration 0032 applied
SELECT indexdef FROM pg_indexes WHERE indexname='idx_politicians_mb_assembly_slug';
-- Expected: CREATE UNIQUE INDEX ...
```

---

## Next-steps menu

1. **Pre-1999 MB Hansard (legs 25-36, 1958-1999)** — the `mb_hansard_parse_w97` module should handle the markup, but the session-index shape for pre-37 isn't probed. Unknown whether legs 25-36 use the same `vol_NN/index.html` pattern or a third format. Would potentially add another 40 years of corpus; needs one half-day probe pass to scope.

2. **MB admin.ts catalog mirror** (5 min). Stage only the 6-line `resolve-mb-speakers-dated` hunk when the billing commit lands. Billing rail shipped as `233634c` in a parallel workstream — check whether that commit already added adjacent `ingest-*` entries that would need the MB mirror to ride along.

3. **Honorific/constituency disambiguation for the 2,963 ambiguous dated-resolver rows**. Requires extending `raw->'mb_hansard'->>'paren_role'` lookups in the resolver. Small incremental lift.

4. **Another province's Hansard.** Remaining pipelines: ON, SK, PE, YT, NT, NU. ON is the biggest prize (20-year corpus). Research-handoff rule per CLAUDE.md still applies — pause + ask for user's research pass before probing.

5. **Legacy-era NL parser exercise (GA 44 S1, 1999)** — from the 2026-04-22 NL runbook's "next session" list. Parallel to the MB Word 97 work.

---

## If something goes wrong

- **Search returns zero MB results**: check API is up (`curl -s canadianpoliticaldata.ca/api/v1/coverage | jq '.jurisdictions[] | select(.jurisdiction=="MB")'`). Then check search endpoint uses `items` key not `results` (known gotcha). If API side looks fine, confirm embeddings loaded: `SELECT count(*) FROM speech_chunks sc JOIN speeches s ON s.id=sc.speech_id WHERE s.source_system='hansard-mb' AND sc.embedding IS NOT NULL;` should be 368,028.
- **`resolve-mb-speakers-dated` deadlocks**: the embed worker is contending on `speech_chunks`. Wait for embed to finish, then retry. Idempotent.
- **Re-running `ingest-mb-hansard --parliament N --session M` erases prior resolver work**: expected (see "Operational learnings #3"). Always follow with `resolve-presiding-speakers --province MB` + `resolve-mb-speakers-dated`.
- **Want to re-ingest a session from scratch**: `DELETE FROM speech_chunks WHERE speech_id IN (SELECT id FROM speeches WHERE source_system='hansard-mb' AND source_url LIKE '...')` then `DELETE FROM speeches WHERE ...` then `ingest-mb-hansard --parliament N --session M`. Chunker + embedder will re-process the new speeches on their next run.
