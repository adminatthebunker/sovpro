# Handoff — 2026-04-24 (ON Hansard live + verifying tomorrow's first cron-driven daily ingest)

**Session arc:** built ON Hansard ingester end-to-end via probe-yourself research (CLAUDE.md §5 was waived for this jurisdiction in-session), then ran the full P44-S1 walk (61 sittings → **18,915 speeches**, 18,731 EN + 184 FR) + chunk + embed. Wrote 2 new modules, 2 new Click commands, added ON to the presiding-officer resolver, and registered 3 new schedule rows in the 18:00 UTC slot. Discovered + fixed a missing partial index on `speech_chunks` that was making `embed-speech-chunks` time out at the asyncpg 60s ceiling.

**Working tree is dirty** with the user's parallel work (premium-reports phase 1b: openrouter, reports.ts, AdminReports, ReportViewerPage, etc.) plus my ON Hansard changes — **nothing committed this session**. The user said "commit later once other agents done work" — so the handoff target is the user reviewing tomorrow's cron run, not a downstream operator picking up half-state.

**TL;DR — verify tomorrow at ~18:55 UTC (after the 18:50 ON presiding-speaker tick):**

```bash
# 1. Confirm all 6 ON schedule rows fired today
docker exec sw-db psql -U sw -d sovereignwatch -c "
SELECT command, status, started_at, exit_code,
       LEFT(COALESCE(stderr_tail, ''), 80) AS err_tail
  FROM scanner_jobs
 WHERE queued_at::date = current_date
   AND (command LIKE '%on-hansard%'
        OR command LIKE '%on-bill%'
        OR (command='resolve-presiding-speakers' AND args::text LIKE '%ON%'))
 ORDER BY started_at;"
# Expected: 6 rows, all status='succeeded', exit_code=0
# (ingest-on-bills, fetch-on-bill-pages, parse-on-bill-pages,
#  ingest-on-hansard, resolve-on-speakers, resolve-presiding-speakers)

# 2. Confirm new sittings landed (any post-2026-04-14 = today's increment)
docker exec sw-db psql -U sw -d sovereignwatch -c "
SELECT MIN(spoken_at::date), MAX(spoken_at::date), COUNT(*) AS speeches,
       COUNT(DISTINCT spoken_at::date) AS sittings
  FROM speeches WHERE source_system='hansard-on';"
# Baseline: speeches=18915, sittings=61, MAX=2026-04-14
# Expected: same OR (speeches > 18915 AND MAX > 2026-04-14)
```

If both queries pass, the daily-ingest cycle works end-to-end. If anything's "queued" rather than "succeeded" by 19:00 UTC, see "If something goes wrong" below.

---

## What shipped this session (uncommitted, working tree)

### New files

- `services/scanner/src/legislative/on_hansard.py` — orchestrator (~520 lines). Discovery via session-index HTML, fetch via `?_format=json`, name-based speaker resolution with parens-first cascade, speeches upsert, post-pass chunk sync, `resolve_on_speakers` re-resolver.
- `services/scanner/src/legislative/on_hansard_parse.py` — parser (~310 lines). Matches `<p class="speakerStart"><strong>{ATTR}:</strong>{TEXT}</p>` shape. Distinguishes role+parens-person ("The Speaker (Hon. Donna Skelly)") from person+parens-metadata ("Hon. Edith Dumont (Lieutenant Governor)"). Per-speech French detection via stopword heuristic — tags 1% of corpus as `language='fr'`.

### Modified files

- `services/scanner/src/__main__.py` — added `ingest-on-hansard` + `resolve-on-speakers` Click commands; added `"ON"` to the `--province` choice list of `resolve-presiding-speakers`.
- `services/scanner/src/jobs_catalog.py` — 2 new entries (`ingest-on-hansard`, `resolve-on-speakers`).
- `services/scanner/src/legislative/presiding_officer_resolver.py` — `SPEAKER_ROSTER["ON"]` seeded with current Speaker (Hon. Donna Skelly, started 2025-04-15); `_SPEAKER_ROLE_BY_PROVINCE["ON"] = ("The Speaker",)`.
- `scripts/seed-daily-ingest-schedules.sql` — packed all 6 ON commands into the 18:00 UTC slot (existing 3 ON bills rows shifted to offsets 0/05/10; 3 new Hansard chain rows at 20/35/50).
- `docs/research/ontario.md` — flipped "Research-handoff items (Hansard)" section to "Hansard pipeline ✅ LIVE (2026-04-24)" with the probe-result table + bilingual-content note.
- `docs/research/README.md` — ON status flipped to "✅ Bills + Hansard live".
- `CLAUDE.md` — Click command count 117 → 123, daily-ingest schedule note bumped (39 rows, 10 jurisdictions), ON added to live-Hansards list.

### Live DB changes (stateful, applied)

- `idx_speech_chunks_unembedded` — new partial index on `speech_chunks (spoken_at DESC NULLS LAST, id) WHERE embedding IS NULL`. **Created CONCURRENTLY** to avoid table lock; not in any migration file. Should be promoted to a numbered migration (followup #4 below) since it's now load-bearing for the ongoing daily ingest cycle.
- 6 rows in `scanner_schedules` with `created_by='daily-ingest-rollout'`, all enabled, all `next_run_at = 2026-04-25 18:xx:00+00:00`.
- 18,915 ON speeches in `speeches` (source_system='hansard-on'), 15,095 ON chunks in `speech_chunks` (100% embedded), `legislative_sessions` row for ('provincial', 'ON', 44, 1).
- `jurisdiction_sources.ON.hansard_status='partial'` (would be `'live'` at ≥ 50k speeches; the threshold is correct — single-session corpus is not yet "live"-tier).
- `politician_terms` row for Donna Skelly with `office='Speaker'`, `started_at='2025-04-15'`, `ended_at=NULL` (seeded by the presiding-officer resolver smoke test).

---

## What runs tomorrow

The 18:00 UTC ON slot has 6 schedule rows; here's the chain in order:

| UTC | Command | Args | What it does |
|---|---|---|---|
| 18:00 | `ingest-on-bills` | `{}` | Discover ON bills (P44-S1 current). Idempotent — touches existing rows + adds any new bill numbers. |
| 18:05 | `fetch-on-bill-pages` | `{}` | Fetch HTML for any bill not yet cached. Polite delay, will skip already-cached. |
| 18:10 | `parse-on-bill-pages` | `{}` | Parse cached HTML → bill_sponsors + bill_events. |
| 18:20 | `ingest-on-hansard` | `{}` | **The new one.** Auto-resolves current session via DB-backed `current_session()`, walks every sitting URL on `house-documents/parliament-44/session-1/`. ON CONFLICT DO UPDATE on `(source_system, source_url, sequence)` makes re-walks idempotent — only NEW sittings (since 2026-04-14) will produce inserted=N>0; existing sittings show updated=N. |
| 18:35 | `resolve-on-speakers` | `{}` | Re-resolve any NULL-`politician_id` ON speeches against the current ON roster. Should be a near-noop on a fresh ingest (the orchestrator already resolves at insert time). |
| 18:50 | `resolve-presiding-speakers` | `{"province":"ON"}` | Find any bare-`The Speaker:` rows (no inline parens) and stamp Donna Skelly's politician_id from the SPEAKER_ROSTER+politician_terms join. Expected `scanned=0` for modern transcripts — the parens-name path in the parser already resolves these inline. |

Total worker time per chain ≈ 5-30 min depending on how many new sittings exist. Each Hansard sitting is ~250-400 speeches × 2-5 chunks each = ~1000 chunks per new sitting. The chunker + embedder are NOT scheduled — they'll catch up on the next manual run, or you can wire them in later (followup #5).

---

## Verification SQL (post-cron sanity, 2026-04-25 ~19:00 UTC)

```sql
-- Schedule fired correctly
SELECT command, status, EXTRACT(EPOCH FROM (finished_at - started_at))::int AS duration_s,
       exit_code, LEFT(COALESCE(stderr_tail, ''), 100) AS err
  FROM scanner_jobs
 WHERE queued_at::date = current_date
   AND (command LIKE '%on-hansard%' OR command LIKE '%on-bill%'
        OR (command='resolve-presiding-speakers' AND args::text LIKE '%ON%'))
 ORDER BY started_at;
-- Expected: 6 rows, all succeeded, durations: bills 1-3s each, hansard 30-300s
-- depending on new sittings, resolvers <5s.
```

```sql
-- Did new sittings land?
SELECT COUNT(*) AS new_sittings_today
  FROM speeches
 WHERE source_system='hansard-on'
   AND created_at::date = current_date
   AND spoken_at::date NOT IN (
     SELECT DISTINCT spoken_at::date FROM speeches
      WHERE source_system='hansard-on' AND created_at::date < current_date
   );
-- Expected: 0 if no new sittings published since 2026-04-14, else N > 0.
-- Either is OK — depends on whether ON had a sitting between 04-15 and 04-25.
```

```sql
-- Resolution rate stayed healthy
SELECT language,
       COUNT(*) AS speeches,
       ROUND(COUNT(*) FILTER (WHERE politician_id IS NOT NULL) * 100.0 / COUNT(*), 1) AS pct_resolved
  FROM speeches WHERE source_system='hansard-on'
 GROUP BY language ORDER BY speeches DESC;
-- Baseline 2026-04-24: en=18731 (98.1%), fr=184 (98.4%).
-- Tomorrow expected: same OR slightly higher (new sittings, maybe new MPPs
-- being sworn in could dip resolution by a few points until ingest-ontario-mpps
-- catches up the roster).
```

```sql
-- New chunks pending embedding (downstream catch-up, not part of cron)
SELECT COUNT(*) AS pending_embed
  FROM speech_chunks sc JOIN speeches s ON sc.speech_id = s.id
 WHERE s.source_system='hansard-on' AND sc.embedding IS NULL;
-- Expected: 0 today's baseline; tomorrow >0 if new sittings landed.
-- Run `chunk-speeches` then `embed-speech-chunks` manually to drain.
```

```sql
-- Confirm the new index is still there (paranoia check)
SELECT indexdef FROM pg_indexes
 WHERE indexname='idx_speech_chunks_unembedded';
-- Expected one row: CREATE INDEX ... USING btree (spoken_at DESC NULLS LAST, id) WHERE (embedding IS NULL)
```

---

## What "bad" looks like — and how to recover

- **`ingest-on-hansard` shows `status='queued'` past 18:30**: worker is stuck on a prior job. Check `docker compose logs scanner-jobs --tail 50` and inspect what's running. The MB chain (17:00 UTC) is the most likely upstream blocker — MB Hansard re-walks have a 60s asyncpg timeout failure mode (orthogonal pre-existing issue, see followup #2).

- **`status='failed'` with `error='unknown command in catalog'`**: the worker was running an older image. `docker compose build scanner-jobs && docker compose up -d scanner-jobs` and re-enqueue manually:
  ```sql
  INSERT INTO scanner_jobs (command, args, status, requested_by, priority)
       VALUES ('ingest-on-hansard', '{}'::jsonb, 'queued', 'manual-recover', 10);
  ```

- **`status='failed'` with `bad args: missing required arg`**: jobs_catalog.py was reverted somewhere. Confirm `'ingest-on-hansard'` is in `COMMANDS` and its `parliament`/`session` args have `"required": False`.

- **`status='failed'` with `ValueError: No legislative_sessions row for provincial/ON`**: ingest-on-bills didn't run before ingest-on-hansard. Check the 18:00 row finished. If it failed too, run it manually and then ingest-on-hansard.

- **Hansard finished but resolution rate dropped**: probably a new MPP was sworn in but `ingest-ontario-mpps` hasn't refreshed the roster (that's the weekly Sunday 02:00 UTC Open North job). Run `docker compose run --rm scanner ingest-ontario-mpps` then `resolve-on-speakers` to recover.

- **`embed-speech-chunks` times out again** (60s asyncpg): confirm `idx_speech_chunks_unembedded` exists (last paranoia query above). If missing, recreate:
  ```sql
  CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_speech_chunks_unembedded
    ON speech_chunks (spoken_at DESC NULLS LAST, id) WHERE embedding IS NULL;
  ```

---

## Known gotchas carried into this state

1. **Bilingual body, not separate FR ingest.** ON publishes a single transcript at both `/en/...` and `/fr/...` URLs — they return byte-identical bodies. Francophone MPPs' speeches appear in French interleaved with the English majority (~1% of corpus). Per-speech `language` tag handles this. **Do not** add a separate FR ingester — it would duplicate-write every row.

2. **Parens-name extraction is the resolution unlock for presiding officers.** The on_hansard parser pulls "Hon. Donna Skelly" out of `<strong>The Speaker (Hon. Donna Skelly):</strong>` directly. The SPEAKER_ROSTER fallback only matters for bare `The Speaker:` rows (rare in modern transcripts). When you backfill historical sessions, the older markup *might* lack the parens — at that point the SPEAKER_ROSTER will need historical Speakers added (currently only Donna Skelly seeded).

3. **`politicians.ola_slug` is NOT in the ON Hansard FK chain.** It's used for ON bills sponsor resolution (52 of 123 current MPPs have it populated), but Hansard markup has no `/members/<slug>` anchors — only inline names. Don't try to "improve" the resolver by adding a slug join; it'll match nothing.

4. **`field_associated_bill_multi`** is captured into `raw->'on_hansard'->'field_associated_bills'` on every speech but not yet promoted to a normalised join table. When the bill ↔ debate cross-reference UI ships, that's where the data lives.

5. **Re-running `ingest-on-hansard` with no args** auto-resolves to the current session via `current_session()`. Re-runs are idempotent (`ON CONFLICT DO UPDATE` on speeches), but **the post-pass `UPDATE speech_chunks` overwrites politician_id from the parent speech** — same gotcha as MB Hansard. Pattern: re-ingest → resolvers (in that order). Don't run `resolve-on-speakers` then re-ingest — the second ingest would clobber the resolver's work via the chunk-sync UPDATE.

6. **Embed throughput is currently ~35-44 chunks/sec, not the CLAUDE.md reference of 50.9.** Cause: HNSW index write cost grew with corpus size (3.4M chunks now vs 242k at the reference benchmark) plus concurrent worker activity. Not a bug, just degraded headroom. If it gets worse, the fix is index tuning or a separate embed-only worker.

---

## Next-steps menu (for whenever you pick this up)

1. **Commit the work.** Working tree has my ON Hansard files mixed with your premium-reports work-in-progress. The clean files (new `on_hansard*.py`, new schedule SQL, the 6 docs/research stubs from the prior session) are unambiguously mine. The modified files (`__main__.py`, `jobs_catalog.py`, `presiding_officer_resolver.py`, `CLAUDE.md`, `docs/research/ontario.md`, `docs/research/README.md`) have my hunks intermingled with yours. The cleanest path: surgical extract via `cp` backup + `git checkout HEAD --` revert + re-apply (last session's plan walked through this — needs `git checkout HEAD -- <file>` Bash permission).

2. **Promote the new index to a migration.** `idx_speech_chunks_unembedded` was created with `CREATE INDEX CONCURRENTLY` directly against the live DB. It survives DB rebuilds because pgdata persists, but a fresh-volume rebuild would lose it and `embed-speech-chunks` would silently regress to the 60s timeout. Add `db/migrations/0036_speech_chunks_unembedded_index.sql` so it gets recreated on `init.sql` runs.

3. **Verify tomorrow** (the whole point of this handoff). Use the queries above. If clean, the daily-ingest cycle is fully proven across federal + 10 jurisdictions.

4. **Historical ON backfill (P43, P42, ...)**. The same `on_hansard.py` should walk older session-index URLs unchanged — but resolution rate would tank because `politicians` only carries current MPPs (52 of 123 with `ola_slug`). Need an `ingest-ontario-former-mpps` analog of `ingest-mb-former-mlas` first; until then, backfilling would land thousands of speeches with `politician_id=NULL`.

5. **Wire chunking + embedding into the daily chain.** Currently the daily ON chain stops at `resolve-presiding-speakers`. New speeches sit unchunked + unembedded until manually triggered. Two options: (a) add `chunk-speeches` and `embed-speech-chunks` rows to the seed SQL at offsets `:55` of each jurisdiction's hour, or (b) keep them as catch-up runs to avoid contention with the embed pipeline (current pattern). The MB Hansard 60s timeout failure suggests (b) is safer, but worth a one-line SQL trial during a low-traffic window.

6. **Bill ↔ Hansard cross-references.** `raw->'on_hansard'->'field_associated_bills'` is captured but not promoted. A small denormalised `bill_speeches` join table would unlock "show every speech that references this bill" UI.

---

## File reference (for future-me when context is gone)

- Plan: `~/.claude/plans/can-you-tell-me-transient-muffin.md` (the "ON Hansard ingester" plan, supersedes the earlier daily-ingest plan)
- Probe artifacts (volatile): `/tmp/probe[1-10].html`, `/tmp/probe[5,10].json`, `/tmp/probe_en.json`, `/tmp/probe_fr.json` — used to develop the parser and language detection. Will be wiped on container restart; not needed once parser is committed.
- Last session's daily-ingest handoff context: see CLAUDE.md "Daily-ingest schedule" section.
- ON dossier with full probe result table: `docs/research/ontario.md` (Hansard pipeline section).
