# Handoff — 2026-04-24 (daily-scan schedules: architecture, current state, tomorrow's verification)

**Purpose:** authoritative reference for *what's running on cron* — the schedule rows, the auto-detect mechanism, the bash cron loop that coexists, and how to verify tomorrow's first full-day cron-driven cycle. Companion doc is `handoff-2026-04-24-db-ingest-state.md` which covers what these scans have *put into the DB*.

**Snapshot taken:** 2026-04-25 00:58 UTC. **45 schedule rows total.**

---

## Two scheduling systems coexist

The repo has **two** scheduling mechanisms running side-by-side. CLAUDE.md flags consolidating them as a deferred task; for now both must be kept in mind.

### 1. DB-driven `scanner_schedules` table (the modern path, 45 rows)

The `scanner-jobs` Python daemon polls the table every `JOBS_POLL_INTERVAL` seconds. Enabled rows whose `next_run_at <= now()` get an enqueued job in `scanner_jobs`; `next_run_at` is then advanced via `croniter`. Each command runs through a `jobs_catalog.py` allowlist (`unknown command in catalog` is the failure when a row references an unregistered command).

Three `created_by` buckets in the table:

| created_by | rows | scope |
|---|---:|---|
| `daily-ingest-rollout` | 36 | Federal + 9 provinces (AB BC QC MB ON NB NL NT NU), the full bills + Hansard + resolver chain wired 2026-04-24. The ON additions (3 rows) shipped today. |
| `ns-hansard-bootstrap` | 4 | The original NS-only daily schedules, pre-existing before the rollout. **Hardcoded args** (parliament=65, session=1) — not auto-detect. Functioning fine; intentionally untouched by the rollout. |
| `admin` | 1 | Daily politician-photo backfill at 03:00 UTC. National scope. |

Plus 4 NS rows under `ns-hansard-bootstrap` that include the weekly Sunday roster + speaker re-resolve.

### 2. Bash cron loop (`scripts/scanner-cron.sh`, runs in `scanner-cron` container)

Hourly + daily wall-clock loop, NOT in `scanner_schedules`. What it covers (none of which is in the DB-driven table):
- **Hourly quick scan** for sites stale > 6h
- **Daily 06:00 UTC full scan** of all sites
- **Sunday 02:00 UTC weekly Open North re-ingest** — federal MPs, all MLAs, all councils, ingest-legislatures, ingest-all-councils, seed-orgs (this is what keeps `politicians` fresh for every jurisdiction)
- **Sunday 04:00 UTC** — normalize-socials, enrich-legislatures, enrich-mps
- **Monday 03:00 UTC** — verify-socials (5000 limit, 168h stale)

These bash-cron items are infrastructure / roster maintenance, NOT bills/Hansard ingest. The rollout intentionally leaves them in the bash loop because they were already proven and migrating them would require schema additions to express their multi-step shape.

---

## Auto-detect current session pattern (new this session)

Every Hansard ingest command + the two MB bills commands now accept optional `--parliament/--session` (`--legislature/--session` for AB; `--ga/--session` for NL). When absent, the command calls `services/scanner/src/legislative/current_session.py::current_session(db, level=…, province_territory=…)`, which reads the latest `(parliament_number, session_number)` from `legislative_sessions` for the jurisdiction.

**Why DB-backed and not upstream-probed:** the bills ingester for each jurisdiction already does its own upstream current-session detection (BC GraphQL `allSessions`, AB dashboard render, ON canonical URL parse, etc.) and writes the result into `legislative_sessions`. By the time the Hansard chain runs (intra-hour offset), the DB row reflects whatever upstream considers current. This is the operational ordering invariant that keeps the auto-detect honest:

```
within each jurisdiction's UTC slot:
  bills (creates/updates legislative_sessions row)  →  Hansard (reads it)  →  resolvers
```

If a brand-new jurisdiction is added with an empty `legislative_sessions` table, the resolver raises `ValueError("No legislative_sessions row for {scope}. Run the bills ingester for this jurisdiction first…")`. Schedule rows with empty `args={}` will then surface this as a `failed` job with a clear message — easy to recover.

---

## Full schedule table (`scanner_schedules`, all 45 rows)

UTC time is in chronological order through the day. **Tomorrow (2026-04-25) is the first full-day cron-driven cycle.** All 36 `daily-ingest-rollout` rows have `next_run_at = 2026-04-25 [time]:00+00`.

### National

| UTC | Command | Args | Source |
|---|---|---|---|
| 03:00 | `backfill-politician-photos` | `{stale_days:30, concurrency:4}` | admin |
| 03:00 Sun | `ingest-ns-mlas` | `{parliament:65, session:1, sample_sittings:10}` | ns-hansard-bootstrap |
| 03:15 Sun | `resolve-ns-speakers` | `{}` | ns-hansard-bootstrap |

### Federal — 11:00 UTC slot

| UTC | Command |
|---|---|
| 11:00 | `ingest-federal-bills` |
| 11:15 | `ingest-federal-hansard` |

### NS — 12:00-13:30 UTC (legacy NS-only schedules — hardcoded args)

| UTC | Command | Args |
|---|---|---|
| 12:00 | `ingest-ns-bills-rss` | `{}` |
| 13:00 | `ingest-ns-hansard` | `{parliament:65, session:1}` *(hardcoded — needs operator update on prorogation)* |
| 13:30 | `resolve-presiding-speakers` | `{province:NS}` |

### BC — 14:00 UTC slot

| UTC | Command |
|---|---|
| 14:00 | `ingest-bc-bills` |
| 14:15 | `ingest-bc-hansard` |
| 14:30 | `resolve-bc-speakers` |
| 14:45 | `resolve-presiding-speakers` *(BC)* |

### AB — 15:00 UTC slot

| UTC | Command |
|---|---|
| 15:00 | `ingest-ab-bills` |
| 15:15 | `ingest-ab-hansard` |
| 15:30 | `resolve-ab-speakers` |
| 15:45 | `resolve-presiding-speakers` *(AB)* |

### QC — 16:00 UTC slot

| UTC | Command |
|---|---|
| 16:00 | `ingest-qc-bills` |
| 16:05 | `ingest-qc-bills-rss` |
| 16:15 | `ingest-qc-hansard` |
| 16:30 | `resolve-qc-speakers` |
| 16:45 | `resolve-presiding-speakers` *(QC)* |

### MB — 17:00 UTC slot (longest chain — 8 commands)

| UTC | Command |
|---|---|
| 17:00 | `ingest-mb-bills` |
| 17:05 | `fetch-mb-billstatus-pdf` |
| 17:10 | `parse-mb-bill-events` |
| 17:15 | `ingest-mb-hansard` |
| 17:25 | `resolve-mb-bill-sponsors` |
| 17:30 | `resolve-mb-speakers` |
| 17:35 | `resolve-mb-speakers-dated` |
| 17:45 | `resolve-presiding-speakers` *(MB)* |

### ON — 18:00 UTC slot (new this session — Hansard chain added)

| UTC | Command |
|---|---|
| 18:00 | `ingest-on-bills` |
| 18:05 | `fetch-on-bill-pages` |
| 18:10 | `parse-on-bill-pages` |
| 18:20 | `ingest-on-hansard` *(new)* |
| 18:35 | `resolve-on-speakers` *(new)* |
| 18:50 | `resolve-presiding-speakers` *(ON — new)* |

### NB — 19:00 UTC slot

| UTC | Command |
|---|---|
| 19:00 | `ingest-nb-bills` |
| 19:15 | `ingest-nb-hansard` |
| 19:30 | `resolve-nb-speakers` |
| 19:45 | `resolve-presiding-speakers` *(NB)* |

### NL — 20:00 UTC slot

| UTC | Command |
|---|---|
| 20:00 | `ingest-nl-bills` |
| 20:15 | `ingest-nl-hansard` |
| 20:30 | `resolve-nl-speakers` |
| 20:45 | `resolve-presiding-speakers` *(NL)* |

### NT + NU — 21:00 UTC slot

| UTC | Command |
|---|---|
| 21:00 | `ingest-nt-bills` |
| 21:15 | `ingest-nu-bills` |

NT / NU consensus-government jurisdictions ingest bills only — no Hansard yet (research-handoff blocked, see `docs/research/{northwest-territories,nunavut}.md`).

---

## What's NOT scheduled but probably should be

1. **`chunk-speeches`** — converts new `speeches` rows into `speech_chunks` (paragraph splitter, ~480 tokens, 50 overlap). Currently runs only on demand. New speeches sit unchunked until manually triggered.
2. **`embed-speech-chunks`** — sends chunks through TEI for Qwen3 embeddings. Same on-demand pattern. Without this, new speeches don't surface in semantic search.
3. **`refresh-coverage-stats`** — keeps `jurisdiction_sources` (which feeds the public `/coverage` page) in sync with reality. Currently stale until manually run.

**Why deferred:** the embed pipeline and the daily ingest pipeline contend on `speech_chunks` (one writes, one reads/updates). The MB Hansard 60s-asyncpg-timeout failure mode at 17:15 UTC suggests serializing them is wise. Two reasonable wirings:

- **(A) Append to each chain:** add `chunk-speeches` at `:55` of each jurisdiction's hour, then `embed-speech-chunks` at `:58`. Concurrency risk: if a jurisdiction's Hansard ingest runs long, it overlaps the embed.
- **(B) Single national catch-up:** add `chunk-speeches` + `embed-speech-chunks` at 22:00 / 22:15 UTC after every jurisdiction is done. Simpler, slightly stale-er. Probably the better default.
- **(C) Keep manual:** explicit operator step keeps the user in the loop. Lowest-risk.

Choose one and add to `scripts/seed-daily-ingest-schedules.sql` when ready.

---

## Verification path for tomorrow (2026-04-25)

The first cron-driven full day. Expected timeline:

| UTC | Expect to see |
|---|---|
| 03:00 | `backfill-politician-photos` succeed (national) |
| 11:00 | `ingest-federal-bills` succeed |
| 11:15 | `ingest-federal-hansard` succeed |
| 12:00-13:30 | NS chain (existing schedules) |
| 14:00-21:15 | Each jurisdiction's chain in its UTC slot |

**The single best probe** — run after 21:30 UTC (end of NU at 21:15 + buffer):

```sql
SELECT
  date_trunc('hour', started_at) AS hour,
  COUNT(*) FILTER (WHERE status='succeeded') AS ok,
  COUNT(*) FILTER (WHERE status='failed') AS failed,
  STRING_AGG(DISTINCT command, ', ' ORDER BY command) FILTER (WHERE status='failed') AS failed_cmds
  FROM scanner_jobs
 WHERE queued_at::date = current_date
 GROUP BY date_trunc('hour', started_at)
 ORDER BY hour;
```

Expected: one row per UTC hour from 03:00 through 21:00, mostly all-succeeded.

**Per-jurisdiction freshness check** — confirm each Hansard ran and re-walked its session:

```sql
SELECT
  source_system,
  COUNT(*) FILTER (WHERE updated_at::date = current_date) AS rows_touched_today,
  MAX(updated_at) AS last_updated
  FROM speeches
 GROUP BY source_system
 ORDER BY last_updated DESC NULLS LAST;
```

Expected: `rows_touched_today > 0` for every jurisdiction whose Hansard schedule fired (the ON CONFLICT DO UPDATE bumps `updated_at` even when the speech text doesn't change). `hansard-on` and `hansard-mb` should always show high numbers because their re-walks touch every speech in the session.

**Schedule advancement check** — confirm `next_run_at` advanced to 2026-04-26:

```sql
SELECT name, next_run_at, last_enqueued_at FROM scanner_schedules
 WHERE created_by='daily-ingest-rollout'
   AND next_run_at::date < current_date + 1
 ORDER BY name;
```

Expected: zero rows. Any row returned is a schedule that didn't fire (worker likely stuck on something earlier in the day).

---

## Known failure modes (from this session + prior runs)

### 1. asyncpg `command_timeout` (60s default)

The `Database` class in `services/scanner/src/db.py` sets `command_timeout=60` per query. Two operations have hit this:

- **MB Hansard post-pass `UPDATE speech_chunks`** (17:15 UTC slot) — UPDATE over 407k MB chunks to sync `politician_id`. Pre-existing issue; the schedule fails intermittently. Workaround: re-run resolvers manually after the failure (`resolve-mb-bill-sponsors`, `resolve-mb-speakers`, `resolve-mb-speakers-dated`, `resolve-presiding-speakers --province MB`). Real fix: batch the UPDATE or per-statement `SET LOCAL statement_timeout`.
- **`embed-speech-chunks` initial SELECT** — fixed this session by adding `idx_speech_chunks_unembedded`. **The index is not in any migration file** — see `handoff-2026-04-24-db-ingest-state.md` "Live DB changes". A fresh-volume rebuild loses it; promote to migration `0036_speech_chunks_unembedded_index.sql`.

### 2. Hansard ingesters re-walk full sessions (no `--since` heuristic)

Every Hansard schedule re-walks the entire current session. With `ON CONFLICT DO UPDATE` this is correct (idempotent) but increasingly heavy as sessions grow:
- Federal: 1.08M speeches re-walked daily
- AB: 440k
- MB: 409k
- QC: 313k
- BC: 198k
- NS: 64k
- NL: 44k
- NB: 23k
- ON: 19k

Followup: add `--since=last_spoken_at - 1d` heuristic to each Hansard ingester. ~10x daily-traffic reduction at the upstream + DB level. Worth filing.

### 3. ON CONFLICT DO UPDATE clobbers prior resolver work

This is the gotcha that bit MB Hansard (handoff-2026-04-23 operational learning #3) and is now baked into ON Hansard too. The ingester's UPSERT sets `politician_id = EXCLUDED.politician_id`, so re-ingesting overwrites whatever the post-pass resolvers wrote. Pattern: **re-ingest first, then run resolvers**. Never resolve, then re-ingest.

The schedule chain enforces this ordering (Hansard at `:15` or `:20`, resolvers at `:30`+). Manual operator runs need to remember.

### 4. Worker contention on `speech_chunks`

Three commands write to `speech_chunks` and can deadlock:
- The chunker (INSERT)
- The embedder (UPDATE)
- The Hansard post-pass `UPDATE speech_chunks SET politician_id = …` at end of each ingest

If two are running simultaneously, postgres can pick a deadlock victim and abort one. Pattern: **embed → resolvers/ingest**, not parallel.

### 5. Old worker drains stale jobs after restart

When `scanner-jobs` is recreated (`docker compose up -d scanner-jobs`), the old container takes a few seconds to fully die. Any jobs claimed in those few seconds run with the **old code/catalog** and fail with `unknown command in catalog` or `bad args: missing required arg` if the catalog or Click signatures changed. Fix: ignore the stale failures (they'll re-enqueue at the next cron tick); confirm new worker is healthy via fresh job submission.

---

## Bootstrap status (the very first cron-driven cycle)

The `daily-ingest-rollout` schedules were applied at 2026-04-25 00:28 UTC. That immediately enqueued the first run of every row (because `next_run_at` defaulted to NULL and the worker treats NULL as "due now"). The worker has been chewing through that backlog since.

Status as of snapshot (00:58 UTC):
- 45 schedule rows registered
- All `next_run_at` advanced to 2026-04-25 [their cron time] +00:00 (correct croniter advancement)
- Backlog still draining (heavy Hansard re-walks for federal, AB, MB are slow)
- ON chain ran cleanly (proof point: ON speeches grew from 18,915 to 21,505 between my full walk and the cron tick)

Tomorrow (2026-04-25) the schedules fire on their proper cron times. That's the first "real" cycle.

---

## File reference

- Schedule seed (idempotent): `scripts/seed-daily-ingest-schedules.sql`
- Auto-detect resolver: `services/scanner/src/legislative/current_session.py`
- Catalog (allowlist): `services/scanner/src/jobs_catalog.py`
- Worker daemon: `services/scanner/src/jobs_worker.py`
- Bash cron loop (parallel system): `scripts/scanner-cron.sh`
- Companion handoff (DB state): `docs/runbooks/handoff-2026-04-24-db-ingest-state.md`
- Prior context: CLAUDE.md "Daily-ingest schedule" section (under Admin panel → Execution pipeline)
