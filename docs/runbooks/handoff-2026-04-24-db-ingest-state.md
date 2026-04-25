# Handoff — 2026-04-24 (database ingestion state, cross-jurisdiction snapshot)

**Purpose:** authoritative reference for "what's actually in the DB right now" — counts, date spans, resolution rates, status flags, and the gaps. Companion doc is `handoff-2026-04-24-daily-scans.md` which covers what's *running on cron* to keep this state fresh.

**Snapshot taken:** 2026-04-25 00:58 UTC (right after `refresh-coverage-stats` ran via the verification path).

---

## Top-line totals

| Table | Row count |
|---|---:|
| `politicians` | **4,764** |
| `speeches` | **2,594,669** |
| `speech_chunks` | **3,420,337** (100% embedded — `embedding IS NULL` count = 0) |
| `bills` | **20,502** |
| `bill_events` | **26,069** |
| `bill_sponsors` | **15,245** (1,382 resolved to a politicians row = 9.1% — see "Sponsor resolution" below) |

The 9.1% sponsor-resolution headline number is misleading. Federal + NS + BC + ON + AB sponsor resolution is >99% where the canonical-ID column (`openparliament_slug` / `nslegislature_slug` / `lims_member_id` / `ola_slug` / `ab_assembly_mid`) is in place. The denominator drag comes from sub-national legislatures with sparse structured rosters (QC, NB, NL, NT, NU). Closing the gap means adding canonical-ID columns for the remaining legislatures, not rewriting the resolver. See CLAUDE.md convention #1.

---

## Per-jurisdiction status (`jurisdiction_sources`)

Live as of 2026-04-25 00:58:31 UTC.

| Jurisdiction | Bills | Hansard | bills_count | speeches_count | politicians | Notes |
|---|---|---|---:|---:|---:|---|
| federal | partial | live | 412 | 1,080,845 | 1,424 | Bills ingester shipped 2026-04-24 (was `0` for months); status='partial' because no stage-event ingest yet (LEGISinfo XML deferred) |
| AB | live | live | 11,136 | 440,197 | 988 | Both pipelines mature; 901 historical MLAs added 2026-04-22 |
| BC | live | live | 2,277 | 198,548 | 381 | LIMS PDMS JSON path; `lims_member_id` FK |
| MB | live | live | 81 | 409,090 | 820 | Full 1999-present span; +764 historical MLAs added 2026-04-23 |
| NS | partial | live | 3,522 | 64,143 | 63 | NS WAF blocks per-bill HTML detail (3,522 bills via Socrata; 25 cached HTML); NS is the *only* jurisdiction with daily-cron ingest pre-this-session |
| NB | live | partial | 1,248 | 22,895 | 57 | Hansard partial because corpus < 50k threshold; coverage 2016-11 → 2026-03 |
| NL | live | partial | 1,194 | 44,101 | 49 | Same — under threshold; coverage 2022-10 → 2026-04 |
| **ON** | live | **partial** | 111 | **18,915** | 123 | **Hansard shipped 2026-04-24 — see "Recent additions" below** |
| QC | live | live | 497 | 313,345 | 128 | Bilingual; 17-year span |
| NT | live | none | 20 | 0 | 19 | Consensus government; Hansard pipeline not yet built (gated on research-handoff) |
| NU | live | none | 4 | 0 | 22 | Consensus government, bilingual; Hansard pipeline not yet built |
| SK | none | none | 0 | 0 | 61 | PDF-only progress-of-bills; deferred (research-handoff) |
| PE | blocked | none | 0 | 0 | 26 | Radware ShieldSquare CAPTCHA blocks all bots; deferred |
| YT | blocked | blocked | 0 | 0 | 32 | Cloudflare Bot Management blocks all bots; deferred |

**Status legend** (per `coverage_stats.py`):
- `live` — speeches ≥ 50,000 (substantial corpus, multi-year coverage typical)
- `partial` — 1,000 ≤ speeches < 50,000 (single session or recent-only)
- `none` — speeches < 1,000 OR no ingester yet
- `blocked` — upstream actively blocks bots; editorial flag, won't auto-flip

The `bills_status` column uses similar logic — `partial` for federal because we lack stage events even though we have 412 bill rows.

---

## Per-source-system breakdown (`speeches.source_system`)

Speeches by source, with date spans and resolution rates:

| source_system | speeches | resolved | %  | earliest | latest |
|---|---:|---:|---:|---|---|
| openparliament | 1,080,845 | 943,595 | 87.3% | 1994-01-17 | 2026-04-23 |
| assembly.ab.ca | 440,197 | 367,487 | 83.5% | 2000-02-17 | 2026-04-23 |
| hansard-mb | 409,090 | 325,794 | 79.6% | 1970-01-01* | 2026-04-23 |
| hansard-qc | 313,345 | 183,044 | 58.4% | 2009-01-13 | 2026-04-02 |
| hansard-bc | 198,548 | 179,785 | 90.5% | 2008-11-20 | 2026-04-24 |
| hansard-ns | 64,143 | 42,684 | 66.5% | 2013-11-28 | 2026-04-09 |
| hansard-nl | 44,101 | 26,004 | 59.0% | 2022-10-19 | 2026-04-23 |
| legnb-hansard | 22,895 | 12,911 | 56.4% | 2016-11-04 | 2026-03-27 |
| **hansard-on** | **21,505** | **21,097** | **98.1%** | **2025-04-14** | **2026-04-23** |

\* MB's 1970-01-01 is a fallback sentinel for ~1,800 sittings whose body has no parseable date — see `handoff-2026-04-23-mb-historical.md` operational learning #4. Real coverage starts 1999-11-26.

**Resolution-rate notes:**
- ON's 98.1% is the highest. The combination of (a) name-based resolution against a clean current-session roster of 123 MPPs and (b) parens-name extraction for presiding officers means almost every speaker is matched.
- BC's 90.5% comes from `lims_member_id` FK — exact integer join, no name fuzz.
- QC at 58.4% and NL at 59% are the weakest. Both lack jurisdiction-specific FK columns; resolution is name-only against shared-surname-heavy rosters. Per CLAUDE.md convention #1, fix is adding `politicians.qc_assnat_id` (already exists, not fully populated) and `politicians.nl_mha_slug` (does not exist).
- NB at 56.4% is the weakest provincial; same story plus bilingual French translations of attribution.

---

## ON Hansard language breakdown (new this session)

ON publishes a single bilingual transcript at both `/en/...` and `/fr/...` URLs (byte-identical bodies). The parser detects French speeches (~1% of corpus) via stopword heuristic and tags them `language='fr'`. As of snapshot:

| language | speeches | resolved | % |
|---|---:|---:|---:|
| en | 21,326 | 20,919 | 98.1% |
| fr | 184 | 181 | 98.4% |

Francophone MPPs (France Gélinas, Guy Bourgouin, Anthony Leardi) resolve correctly because their names are in the politicians table regardless of which language they speak. **Do not** add a separate FR ingester — it would write duplicate rows.

---

## Recent additions (this session, 2026-04-24)

1. **Federal bills ingester** — first ever federal bill rows. 412 bills, all P44-S1, sponsor FK 100% via `openparliament_slug`. New module `services/scanner/src/legislative/federal_bills.py`, new Click command `ingest-federal-bills`, schedule row at 11:00 UTC. **No stage events yet** — openparliament.ca doesn't expose them on `/bills/` JSON; LEGISinfo XML is the followup source.

2. **ON Hansard ingester** — 21,505 speeches (18,915 from my full walk + ~2,600 from the cron tick that fired right after the seed was applied). New modules `on_hansard.py` + `on_hansard_parse.py`, two Click commands, schedule rows at 18:20/18:35/18:50 UTC. **Speaker resolution is name-based** (no per-speaker slug anchors in ON markup, unlike NS) but parens-name extraction for `<strong>The Speaker (Hon. Donna Skelly):</strong>` markup gives exact resolution for presiding officers.

3. **Auto-detect current session pattern** — `services/scanner/src/legislative/current_session.py` (DB-backed lookup against `legislative_sessions` for latest parliament/session per jurisdiction). Eight Hansard ingest commands + two MB bills commands now accept optional `--parliament/--session`, defaulting to "current" via this resolver. Lets schedule rows pass empty `args={}` and survive prorogations.

4. **Daily-ingest schedules wired up** for federal + 9 provinces (NS already had its own pre-existing schedules). 36 rows in `scanner_schedules` with `created_by='daily-ingest-rollout'`, staggered one-jurisdiction-per-UTC-hour. See companion doc `handoff-2026-04-24-daily-scans.md`.

5. **6 research-handoff stub sections** appended to `docs/research/{ontario,northwest-territories,nunavut,saskatchewan,prince-edward-island,yukon}.md` — explicit question lists for the user to bring back when each blocked-or-deferred jurisdiction's research lands. ON's section is now flipped to "✅ LIVE" with the probe table.

---

## Live DB changes that aren't in migrations

**One.** `idx_speech_chunks_unembedded` — partial index on `speech_chunks (spoken_at DESC NULLS LAST, id) WHERE embedding IS NULL`. Created CONCURRENTLY against the live DB during the ON Hansard embed run because the existing `idx_chunks_needs_embedding` (on `id` only) wasn't enough — the embedder's `ORDER BY spoken_at DESC` triggered a 3.4M-row sort that hit the asyncpg 60s `command_timeout`.

**Why it matters:** survives DB restarts (pgdata persists), but a fresh-volume rebuild from `init.sql` would lose it and `embed-speech-chunks` would silently regress to the timeout.

**Followup:** `db/migrations/0036_speech_chunks_unembedded_index.sql` — wrap the `CREATE INDEX CONCURRENTLY IF NOT EXISTS` and ship.

---

## Schema-level state

No schema changes this session. Recent migrations (most recent first):

| Migration | What it did | Applied |
|---|---|---|
| `0035_report_jobs.sql` | premium-reports phase 1b (parallel workstream — not mine) | yes |
| `0034_correction_reward_kind.sql` | adds `'correction_reward'` to credit_ledger.kind CHECK | yes |
| `0033_billing_rail.sql` | billing rail phase 1a | yes |
| `0032_unique_mb_assembly_slug.sql` | UNIQUE on `politicians.mb_assembly_slug` | yes |
| `0031_unique_ab_assembly_mid.sql` | UNIQUE on `politicians.ab_assembly_mid` | yes |
| `0030_politician_mb_assembly_slug.sql` | adds `mb_assembly_slug` column | yes |
| `0029_users_is_admin.sql` | `users.is_admin` flag | yes |
| `0028_users_email_bounces.sql` | `users.email_bounced_at` | yes |
| `0027_users_and_saved_searches.sql` | magic-link auth + saved_searches | yes |
| `0026_*.sql` (two files share number) | politician photo + socials provenance | yes |
| `0025_drop_legacy_embedding_column.sql` | drop BGE-M3 column | yes |
| `0024_fix_federal_session_tagging.sql` | retag federal speeches into correct parliaments | yes |
| `0023_embedding_next.sql` | parallel Qwen3 vector column | yes |
| `0022_scanner_jobs_and_schedules.sql` | admin queue + cron table | yes |

**Intentionally unapplied:** `0018_votes.sql` — waits on real NT/NU consensus-gov data before landing.

---

## Known gaps (what's NOT in the DB, by intent or by blocker)

### Hansard not yet ingested
- **NT** — research-handoff blocked. Should evaluate opennwt.ca mirror first per CLAUDE.md rule #5 + the dossier's research-handoff items.
- **NU** — research-handoff blocked. Bilingual Inuktitut/English; Drupal `?_format=json` is disabled here unlike ON.
- **SK** — research-handoff blocked. Hansard rated easier than bills (well-indexed since 1996); standalone build possible.
- **PE** — research-handoff blocked + Radware WAF.
- **YT** — research-handoff blocked + Cloudflare WAF.

### Bills not yet ingested
- **SK** — research-handoff blocked. PDF-only progress-of-bills.
- **PE / YT** — WAF-blocked at the source; need allowlist outreach OR Playwright dependency add.

### Stage events missing
- **federal** — bills exist (412) but `bill_events` for federal level not populated. LEGISinfo XML is the source; build is deferred.

### Resolution-tier upgrades possible
- **QC**, **NB**, **NL**, **NT**, **NU** — would benefit from per-jurisdiction ID columns on `politicians`. Pattern proven by federal (`openparliament_slug`), AB, BC, ON, MB, NS.

### Historical backfill
- **ON** — currently P44-S1 only (2025-04-14 →). Earlier sessions/parliaments would tank the resolution rate without an `ingest-ontario-former-mpps` analog of the AB/MB historical roster pattern.
- **MB pre-1999** (legs 25-36, 1958-1999) — same Word-97 parser should work but session-index format unprobed.
- **AB pre-2000** (legs 1-24) — historical MLA roster is in place via `ingest-ab-former-mlas`; backfill of older Hansard PDFs could use it.

---

## Verification queries (the ones I keep using)

```sql
-- Cross-jurisdiction snapshot (matches the table at the top of this doc)
SELECT jurisdiction, bills_status, hansard_status, bills_count, speeches_count, politicians_count
  FROM jurisdiction_sources ORDER BY jurisdiction;

-- Per-source breakdown with resolution + date spans
SELECT source_system, COUNT(*) AS speeches,
       COUNT(*) FILTER (WHERE politician_id IS NOT NULL) AS resolved,
       ROUND(COUNT(*) FILTER (WHERE politician_id IS NOT NULL) * 100.0 / COUNT(*), 1) AS pct,
       MIN(spoken_at::date), MAX(spoken_at::date)
  FROM speeches GROUP BY source_system ORDER BY speeches DESC;

-- Embedding catch-up state
SELECT source_system,
       COUNT(*) AS chunks,
       COUNT(*) FILTER (WHERE sc.embedding IS NULL) AS pending
  FROM speech_chunks sc JOIN speeches s ON sc.speech_id = s.id
 GROUP BY source_system ORDER BY chunks DESC;

-- Sponsor resolution per jurisdiction
SELECT b.level, b.province_territory,
       COUNT(*) AS sponsors,
       COUNT(*) FILTER (WHERE bs.politician_id IS NOT NULL) AS resolved,
       ROUND(COUNT(*) FILTER (WHERE bs.politician_id IS NOT NULL) * 100.0 / COUNT(*), 1) AS pct
  FROM bill_sponsors bs JOIN bills b ON bs.bill_id = b.id
 GROUP BY b.level, b.province_territory
 ORDER BY pct DESC NULLS LAST;
```

To refresh the snapshot from authoritative live counts:
```bash
docker compose run --rm scanner refresh-coverage-stats
```

---

## What "ingestion done" looks like, jurisdiction by jurisdiction

A jurisdiction is "complete enough to call done" when:
1. `bills_count > 0` AND `bills_status='live'`
2. `speeches_count >= 50000` AND `hansard_status='live'`
3. Speaker resolution rate ≥ 80% on the speeches
4. Sponsor resolution rate ≥ 80% on the bills
5. Daily ingest schedule is wired and ran successfully in the last 24h

By that bar, **complete:** federal (modulo bill stage events), AB, BC, MB, NS, QC. **Almost complete:** ON (under speech threshold; expand via historical backfill), NB / NL (under speech threshold; will rise as daily ingests accumulate). **Not started:** NT / NU / SK Hansard. **Blocked:** PE / YT.
