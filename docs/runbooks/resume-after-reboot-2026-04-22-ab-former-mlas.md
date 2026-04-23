# Resume after reboot — 2026-04-22 (AB former-MLAs shipped, MB/QC next)

**Status when paused:** AB historical-roster backfill **shipped** (991 MLAs from 1906–present, 2,249 terms). AB speaker re-resolution **shipped on `speeches` table**: 42.5% → **83.5%** FK-linked (+180k speeches rescued). AB **`speech_chunks` propagation is NOT complete** — 234,663 chunks still have stale `politician_id`; the 30-minute UPDATE rolled back due to contention with autovacuum on `speech_chunks`. Reboot is a clean slate. MB and QC historical-roster ingests are **built-and-scaffolded** in my head but **not yet implemented** — this session stopped after AB. MB Hansard 2000→present backfill (the user's original ask) is **still pending** — gated on MB former-MLAs.

**TL;DR to resume:**

```bash
# After reboot, confirm baseline:
docker compose up -d tei
docker compose logs tei --tail 20 | grep -iE "cuda|cpu|warming|ready"
# Required: "Starting ... on Cuda". If "on Cpu", reboot again.

# 1) Finish AB chunk propagation — batched per legl this time so
#    we don't hit the 30-min autovacuum-contended transaction.
docker compose run --rm scanner resolve-ab-speakers         # speeches idempotent, will re-populate chunks in smaller txns
# If chunk propagation still stalls, see §"Chunk propagation options".

# 2) Refresh coverage stats so /coverage reflects the AB lift.
docker compose run --rm scanner refresh-coverage-stats

# 3) Commit the AB former-MLAs feature (4 files new/modified,
#    see §"Uncommitted work" for the exact list).

# 4) Build MB former-MLAs (~900 historical MLAs from
#    gov.mb.ca/legislature/members/mla_bio_{living,deceased}.html).
#    See §"MB former-MLAs plan".

# 5) Build QC former-MNAs (~2,374 from assnat Dictionnaire, letter-paginated).
#    See §"QC former-MNAs plan".

# 6) Then the original user ask: MB Hansard 2000→present backfill,
#    which at that point has full historical roster in place.
```

---

## What shipped this session

### Commits landed on `main`

| SHA | Title | Files | Why |
|---|---|---:|---|
| `5a27a2f` | `feat(auth): collapse admin ADMIN_TOKEN into user-session is_admin flag` | 9 | ADMIN_TOKEN in localStorage was XSS-amplifier; collapse to requireAdmin + CSRF. Already referenced as done in CLAUDE.md. |
| `ffcfad5` | `feat(scanner): manitoba bills + hansard live (43-1/2/3, 30k speeches); nb/ns scaffolding` | 20 | Runbook `2026-04-20-mb-hansard.md` work + historical sessions 43-1/43-2 added afterwards. Bundles NB/NS hansard scaffolding because their catalog entries were interleaved with MB in admin.ts / __main__.py / jobs_catalog.py. |
| `0c424ab` | `feat(frontend): "In Beta" badge + modal on lander` | (not mine — landed while I was working on AB) | Not relevant to this runbook. |

### AB historical-roster backfill — NOT YET COMMITTED

New file `services/scanner/src/legislative/ab_former_mlas.py` holds both the ingest (`ingest_ab_former_mlas`) and the post-pass resolver (`resolve_ab_speakers`). Migration `0031_unique_ab_assembly_mid.sql` tightened the partial index to UNIQUE so the ingest can use `ON CONFLICT (ab_assembly_mid) DO UPDATE`. `__main__.py` got 2 new Click commands (`ingest-ab-former-mlas`, `resolve-ab-speakers`) + one import line.

**DB verification at pause time:**

| Metric | Pre-session | Post-session | Δ |
|---|---:|---:|---:|
| AB provincial politicians | 91 | **992** | +901 |
| — with `ab_assembly_mid` | 87 | **988** | +901 |
| — marked `is_active=true` | 87 | 91 | +4 (net: 4 current MLAs that had been marked inactive got promoted; 901 historicals landed as inactive) |
| `politician_terms` from historical backfill (`source LIKE 'assembly.ab.ca:legl-%'`) | 0 | **2,249** | +2,249 |
| AB speeches with `politician_id` | 186,832 | **366,576** | +179,744 |
| AB speech resolution % | 42.5 % | **83.5 %** | +41.0 pp |
| AB speech_chunks still stale (politician_id mismatch) | n/a | **234,663** | — (see §"Chunk propagation options") |

Per-legislature speech resolution (of speeches where the parser extracted a surname):

| Legl | Before | After |
|---|---:|---:|
| 24 (1997-01) | 2% | 99.3% |
| 25 (2001-04) | 4% | 99.8% |
| 26 (2005-08) | 4% | 95.5% |
| 27 (2008-12) | 2% | 99.8% |
| 28 (2012-15) | 15% | 99.6% |
| 29 (2015-19) | 37% | 98.4% |
| 30 (2019-23) | 58% | 97.3% |
| 31 (2023–) | 94% | 95.8% |

---

## Chunk propagation options

The final step in `resolve-ab-speakers` is one big UPDATE that copies `speeches.politician_id` onto `speech_chunks.politician_id` wherever they disagree. At pause time it has ~234k rows to update across the 487k-chunk AB corpus. Attempted twice:

1. `asyncpg` default `command_timeout=60` — hit at 60s, rolled back.
2. `db.pool.execute(..., timeout=600)` — hit at 600s, rolled back.
3. Direct `psql` with `SET statement_timeout='30min'` — ran the full 30 min in `wait_event=WALWrite` contending with an autovacuum on the same table, then statement_timeout cancelled it.

Root cause: a single atomic UPDATE that touches 234k rows inside a 487k-row table while autovacuum is rewriting the same table is pathological. The transaction holds exclusive row locks + competes for WAL writes + competes with autovacuum's buffer pool.

### Recommended fix: batch by legl

The per-legl UPDATEs in `resolve_ab_speakers` already work fine (each <10s). Wrap the chunk propagation the same way — one UPDATE per legl, 8 total:

```python
# In ab_former_mlas.py resolve_ab_speakers, replace Step 3 with:
for legl in legls:
    await db.pool.execute(
        """
        UPDATE speech_chunks sc
           SET politician_id = s.politician_id
          FROM speeches s
         WHERE sc.speech_id = s.id
           AND s.source_system = 'assembly.ab.ca'
           AND s.politician_id IS NOT NULL
           AND sc.politician_id IS DISTINCT FROM s.politician_id
           AND (s.raw->'ab_hansard'->>'legislature')::int = $1
        """,
        legl, timeout=600,
    )
```

Each legl has ~25-50k chunks to update → ~30-60s per. Eight legls sequentially = ~5 minutes total.

Because `resolve-ab-speakers` on speeches is already idempotent (committed via the per-legl batches), re-running it post-fix only touches chunks.

### Defer option

If speech_chunks propagation isn't on today's critical path, it can run later — overnight via a cron, or whenever the next chunk-rebuild pass runs. The coverage page / speech listing / politician profile pages all read from `speeches.politician_id`, which is already correct. Only semantic search's "filter by politician" chip degrades, and even there the failure mode is "some chunks don't appear when filtered," not "wrong results."

---

## Uncommitted work on disk

### AB former-MLAs (ready to commit as its own commit):

- `db/migrations/0031_unique_ab_assembly_mid.sql` — UNIQUE partial index (applied to live DB already; re-running is idempotent).
- `services/scanner/src/legislative/ab_former_mlas.py` — new file, ingest + resolver.
- `services/scanner/src/__main__.py` — one import line added at L715, two Click commands added at L1229 and L1274.

```bash
git add db/migrations/0031_unique_ab_assembly_mid.sql \
        services/scanner/src/legislative/ab_former_mlas.py \
        services/scanner/src/__main__.py
# Commit message suggestion:
git commit -m "feat(scanner): ab historical mla roster + speaker re-resolution (42.5% → 83.5%)"
```

### Everything else (not touched this session, just inherited)

82 other files modified/deleted/added, spanning:
- AI contradictions feature (OpenRouter-backed, new API route + frontend components)
- `safeHttpHref` XSS-prevention for admin pages
- Email-domain-reserved rejection in `/auth/request-link` (uses new `email-domain.ts` lib)
- Email-bounce handling in alerts worker (migration `0028`)
- Multi-politician pin on saved searches (`politician_ids` array instead of singular `politician_id`)
- Lots of frontend UI polish across search/politician-detail/map pages
- New blog post `2026-04-20-accounts-alerts-corrections.mdx`
- Various docs touchups

These are out of scope for the AB/MB/QC historical-roster project. Leave them for a separate slicing session — the user was already cautious about scope creep when we started.

---

## MB former-MLAs plan (next)

**Source:** `https://www.gov.mb.ca/legislature/members/mla_bio_living.html` (181 KB) + `mla_bio_deceased.html` (416 KB). Both are Word-exported static HTML.

**Shape:**
```html
<strong>ADAMS, Charles</strong>
<strong>October 18, 2013 - November 3, 2014</strong>
<strong>November 3, 2014 - April 29, 2015</strong>
```

Counts: deceased page has 587 name-shaped `LASTNAME, Firstname` entries across 471 unique surnames; living page likely adds ~300 more. **Estimated total: ~900 historical MLAs.**

**Shape of ingest:**

- Parse `<strong>` tags; classify each as "name" (matches `^[A-Z]{2,},\s+[A-Z][a-z]+`) or "term" (matches date-range `Month DD, YYYY - Month DD, YYYY`).
- For each name, consume the following consecutive date-range bolds as that person's terms.
- Slug: `mb_assembly_slug` = `lastname-firstname` lowercased, accent-stripped (matches the existing current-MLA convention from `mb_mlas.py`).
- Collision risk: ~900 historicals + 56 current over 160 years, some surnames common (Adams, Smith). First-pass collision handling: if the generated slug already exists for a politician whose first-term end date doesn't overlap, append `-N` suffix starting at 2. Log every collision so we can manually audit them.
- `is_active = false` for everyone on `mla_bio_deceased`; on `mla_bio_living`, set `true` iff their most recent term has no end date. Most "living" entries are former MLAs still alive, not currently seated.

**Resolver post-pass:** MB speeches don't carry `legislature` in `raw->'mb_hansard'` the way AB does. The existing `resolve-mb-speakers` matches by surname full_name globally across the MB roster. Adding 900 historicals will raise surname-ambiguity rates. Date-filtering via `politician_terms` is the right fix — a new `resolve-mb-speakers-v2` command that joins speeches with terms by date range would mirror AB's legl-keyed approach but keyed on `spoken_at ∈ [started_at, ended_at]`.

---

## QC former-MNAs plan (next)

**Source:** `https://www.assnat.qc.ca/fr/membres/notices/index.html` and 15 letter-paginated siblings (`index-b.html`, `index-c.html`, … `index-vz.html`). This is the official **Dictionnaire des parlementaires du Québec de 1764 à nos jours** published by the National Assembly Library.

**Counts:** 2,374 MNAs total across 16 pages (53 / 321 / 222 / 217 / 102 / 174 / 81 / 54 / 269 / 188 / 52 / 182 / 136 / 122 / 136 / 65).

**Shape:** every MNA is linked as `/fr/deputes/{slug-lastname-firstname-middlename}-{qc_assnat_id}/index.html` (or `/biographie.html`) — **same URL pattern as current MNAs**. So `qc_assnat_id` (the integer at the end) is already the canonical ID and extends cleanly to historical. No new schema.

**Shape of ingest:**

- Iterate 16 letter-pages, extract all `(slug, qc_assnat_id)` pairs via regex `/fr/deputes/[a-z0-9-]+-(\d+)/(index|biographie)\.html`.
- For each unique `qc_assnat_id` not yet in `politicians`, INSERT stub with name derived from slug (convert `duplessis-maurice-le-noblet` → `Maurice Le Noblet Duplessis`), `province_territory='QC'`, `level='provincial'`, `qc_assnat_id=N`, `is_active=false`.
- Do NOT fetch individual bio pages on first pass — they're 90 KB each × 2374 = 210 MB of fetches and the bios are prose paragraphs, not structured data. Defer prose-parsing as a separate follow-up.
- Terms: we don't get them from the index pages, so leave empty initially. Resolver date-filtering will fall back to "no term overlap = use anyway" until prose-parsing lands.

**Resolver post-pass:** same story as MB. Current `resolve-qc-speakers` isn't date-aware; adding 2,374 historicals will spike ambiguity. Either build a date-aware v2 resolver, or run prose-parsing first and populate terms before re-resolving.

---

## MB Hansard 2000→present (the original ask)

Gated on MB former-MLAs (so pre-2023 speakers resolve). Once that lands:

- MB Hansard URL pattern `https://www.gov.mb.ca/legislature/hansard/{leg}_{sess}/{leg}_{sess}.html` is verified to hold back to the 25th Legislature (1958). 2000→present means legl 37 (1999-2003) onward — 6 legislatures, ~20 sessions.
- Existing `ingest-mb-hansard --parliament N --session M --limit-sittings 1` supports per-legl smoke tests; expect format drift at legislature boundaries (Word template changes). Probe one sitting per legl before full run.
- Post-ingest: `resolve-mb-speakers` → `embed-speech-chunks` drain → `refresh-coverage-stats` → browser check /coverage and /search?q=x&province=MB.

Speaker roster expansion required for accurate presiding-officer resolution:
- Myrna Driedger (PC Speaker, 2016-10 → 2023-10)
- Daryl Reid (NDP Speaker, 2012-11 → 2016-04)
- George Hickes (NDP Speaker, 1999-10 → 2012-10)

Add to `SPEAKER_ROSTER["MB"]` in `services/scanner/src/legislative/presiding_officer_resolver.py`.

---

## Known quirks carried into this state

- **TEI CUDA-wedge** remains a reboot-fix-only failure mode. If `docker compose logs tei` shows "Starting Qwen3 model on Cpu" after the reboot, reboot again. Do not drain any embed backlog on CPU.
- **Autovacuum on `speech_chunks`** has been running off-and-on all session — 487k AB chunks, 2.7 M total. If it's still running post-reboot, wait for it to settle before running any big UPDATE on `speech_chunks`. The lock contention is the single biggest perf hazard here.
- **asyncpg `command_timeout=60`** on the pool is hardcoded in `services/scanner/src/db.py`. Any scanner operation that runs a bulk UPDATE needs to use `db.pool.execute(..., timeout=N)` to override. Consider bumping the pool default to 300s if bulk ops keep hitting it.
- **`CLAUDE.md` was touched** by a linter during the session (NS-integration status update at L53). The change is intentional — leave it.
- **Database has a concurrent INSERT INTO speeches** running at pause time from an unknown source (no `scanner_jobs` row, no obvious process). Pre-reboot `pg_stat_activity` shows it's short-lived (150ms). Probably a cron-triggered ingest; reboot will kill it and nothing persistent depends on it.

---

## Verification SQL for post-reboot sanity check

```sql
-- AB politicians should still be 992 (ingest is idempotent anyway)
SELECT COUNT(*) FROM politicians
 WHERE province_territory='AB' AND level='provincial';
-- Expected: 992

-- AB resolution should still be 83.5% (committed)
SELECT ROUND(100.0*COUNT(politician_id)::numeric/COUNT(*),1) AS pct
  FROM speeches WHERE source_system='assembly.ab.ca';
-- Expected: 83.5

-- AB terms from historical backfill
SELECT COUNT(*) FROM politician_terms WHERE source LIKE 'assembly.ab.ca:legl-%';
-- Expected: 2249

-- Chunks pending propagation (the stuck thing from pause time)
SELECT COUNT(*) FROM speech_chunks sc
  JOIN speeches s ON s.id=sc.speech_id
 WHERE s.source_system='assembly.ab.ca' AND s.politician_id IS NOT NULL
   AND sc.politician_id IS DISTINCT FROM s.politician_id;
-- Expected pre-fix: 234663
-- Expected post-fix (batched update lands): 0

-- Migration 0031 should be visible in pg_indexes
SELECT indexdef FROM pg_indexes
 WHERE indexname='idx_politicians_ab_assembly_mid';
-- Expected: "CREATE UNIQUE INDEX ..."
```
