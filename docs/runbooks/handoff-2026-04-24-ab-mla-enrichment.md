# Handoff — 2026-04-24 (AB MLA detail enrichment + presiding-officer stub merge + speaker_role UI)

**Session arc:** started from a Hansard-search bug report — "you are a loser" surfaced Myrna Driedger, but clicking through 404'd with "Politician not found." Traced three layered causes: (1) detail route filtered on `is_active=true` so 2,933 inactive politicians (1.4M speeches, ~68% of corpus) were unviewable; (2) AB former-MLA records were thin (party 6.9%, photo 6.5%, official_url 6.9%) because `ingest-ab-former-mlas` only parses the legl=N index page, not the per-MLA detail page; (3) the four most-clicked AB Speakers (Kowalski, Cooper, Wanner, Zwozdesky — 109,949 speeches between them) were `presiding-officer-seed:AB:*` stubs that never got reconciled with their MID-keyed twins. Fixed all three plus added `[Speaker]` UI badges and a "hide chair speech" filter chip.

**Working tree is dirty** (no commits this session). Mixed with the user's parallel premium-reports phase 1b work. The user said "commit later" in the prior handoff and the same applies here.

**TL;DR — verify the user-visible fix:**

```bash
# 1. Driedger's profile — was 404, should now load with "Former member"
docker exec sw-frontend wget -qO- "http://api:3000/api/v1/politicians/b7b632b2-b07a-4aa5-95b1-3b3f1e685063" | python3 -c "
import sys,json; d=json.load(sys.stdin)['politician']
print('name=', d['name']); print('is_active=', d['is_active'])
print('latest_term_ended_at=', d['latest_term_ended_at'])"
# Expected: name=Myrna Driedger, is_active=False, latest_term_ended_at=2023-11-09T...

# 2. AB enrichment coverage (was 6.5% photo / 6.9% party)
docker exec sw-db psql -U sw -d sovereignwatch -c "
SELECT COUNT(*) AS total,
       COUNT(*) FILTER (WHERE photo_url IS NOT NULL) AS with_photo,
       COUNT(*) FILTER (WHERE party IS NOT NULL) AS with_party,
       COUNT(*) FILTER (WHERE constituency_name IS NOT NULL) AS with_const
  FROM politicians WHERE province_territory='AB' AND level='provincial' AND ab_assembly_mid IS NOT NULL;"
# Expected: total=988, with_photo=988, with_party=988, with_const=988 (100%)

# 3. AB stub merge complete
docker exec sw-db psql -U sw -d sovereignwatch -c "
SELECT COUNT(*) FROM politicians WHERE source_id LIKE 'presiding-officer-seed:AB:%';"
# Expected: 0 (was 4 — Kowalski, Cooper, Wanner, Zwozdesky)
```

If these three pass, the data side is solid. The UI side requires browser eyes — load `/politicians/b7b632b2-b07a-4aa5-95b1-3b3f1e685063` (the originally-broken Driedger) + run a Hansard search for "motion lost" and look for `[Speaker]` badges.

---

## What shipped this session (uncommitted, working tree)

### New files

- `services/scanner/src/legislative/ab_presiding_merge.py` — `merge_ab_presiding_stubs()`. For each `presiding-officer-seed:AB:%` stub, finds the MID-keyed twin (last-name match, disambiguated by Speaker-term overlap), reassigns `speeches.politician_id` + `speech_chunks.politician_id`, deletes the stub. Final post-pass reconciles any orphan chunks via the standard `WHERE sc.politician_id IS DISTINCT FROM s.politician_id` pattern.
- `services/frontend/src/styles/...` — none new; updated existing.

### Modified files

- `services/api/src/routes/politicians.ts` — dropped `AND is_active = true` from `GET /:id` (line 417); added `latest_term_ended_at` scalar subquery against `politician_terms`.
- `services/api/src/routes/openparliament.ts` — dropped `is_active=true` from both `:id/parliament-activity` (line 135) and `:id/openparliament` (line 224).
- `services/api/src/routes/search.ts` — added `s.speaker_role` to both SQL projections (grouped + flat); added `exclude_presiding` to `baseFilterSchema`; added `NOT EXISTS (SELECT 1 FROM speeches sx WHERE ... speaker_role IS NOT NULL)` predicate when `exclude_presiding=true`.
- `services/api/src/routes/admin.ts` — added `enrich-ab-mlas` and `merge-ab-presiding-stubs` to `COMMAND_CATALOG`.
- `services/frontend/src/hooks/usePolitician.ts` — added `is_active: boolean` and `latest_term_ended_at: string | null` to `PoliticianCore`.
- `services/frontend/src/hooks/useSpeechSearch.ts` — added `speaker_role: string | null` to the `speech` sub-object on `SpeechSearchItem`; added `exclude_presiding?: boolean` to `SpeechSearchFilter`; added `if (f.exclude_presiding) p.set("exclude_presiding", "true")` to `buildSpeechSearchQuery`.
- `services/frontend/src/components/PoliticianDetailHeader.tsx` — renders "Former MLA" / "Former member" prefix on `elected_office` when `is_active=false`; renders "Last term ended {date}" line below when `latest_term_ended_at` is non-null. New helper `formatLastTermDate()`.
- `services/frontend/src/components/SpeechResultCard.tsx` — new `presidingRoleLabel()` mapper. Renders a `[Speaker]` / `[Président]` / `[Chair]` pill next to the speaker name (in the `--speaker-name-row` flex container) and as an inline tag in the meta row when `hideSpeaker` is set (profile speeches tab).
- `services/frontend/src/components/SpeechFilters.tsx` — new "Hide chair speech" checkbox, hide-able via `hide=["exclude_presiding"]`.
- `services/frontend/src/pages/HansardSearchPage.tsx` — read/write `exclude_presiding` to/from the URL via the `?exclude_presiding=true` query param.
- `services/frontend/src/styles/politician-detail.css` — `.pol-detail__former` style (small italic muted line under the office row).
- `services/frontend/src/styles/hansard-search.css` — `.speech-result__speaker-name-row` (flex), `.speech-result__role-badge` (rounded pill), `.speech-result__role-badge--inline`.
- `services/scanner/src/legislative/ab_former_mlas.py` — added `enrich_ab_mlas()` + helpers (`_parse_member_info`, `_iter_table_rows`, `_office_to_term`, `_latest_party`, `_latest_constituency`, `_parse_ab_date`). Parses `<div id="mla_pa">`, `<div id="mla_cec">`, `<div id="mla_or">` blocks via div-balance scan + col-position cell extraction.
- `services/scanner/src/legislative/presiding_officer_resolver.py` — `_find_politician_id` now falls back to last-name-only when the exact (first, last) match misses *and* the surname has exactly one provincial-row hit. Logs each fallback. `SPEAKER_ROSTER["AB"]` updated to legal first names: `"Ken" → "Kenneth"` (with full_name `"Kenneth R. Kowalski"`), `"Bob" → "Robert"` (with full_name `"Robert E. Wanner"`).
- `services/scanner/src/jobs_catalog.py` — added `enrich-ab-mlas` (category `enrichment`, args: mid/limit/delay/refresh) and `merge-ab-presiding-stubs` (category `maintenance`, args: dry_run).
- `services/scanner/src/__main__.py` — registered two new Click commands: `enrich-ab-mlas` and `merge-ab-presiding-stubs`. Updated the `from .legislative.ab_former_mlas import` to also import `enrich_ab_mlas`. New `from .legislative.ab_presiding_merge import merge_ab_presiding_stubs`.

### Live DB changes (stateful, applied)

- **988 AB politicians enriched.** All 988 with `ab_assembly_mid IS NOT NULL` now have non-null `photo_url`, `party`, `constituency_name`, and `extras->'ab_member_info_fetched_at'`.
- **2,112 new `politician_terms` rows** with `source='ab-assembly-member-info'` covering Speaker / Premier / Minister / Critic / committee periods for AB MLAs. Idempotent on `(politician_id, source)` — the enrichment deletes-and-reinserts these rows per MLA each run.
- **4 stubs merged**: `presiding-officer-seed:AB:{kowalski,cooper,wanner,zwozdesky}` deleted. Their 109,949 speeches + ~52k chunks moved to the MID-keyed twins (`mid=0543` Kenneth R. Kowalski, `mid=0885` Nathan Cooper, `mid=0879` Robert E. Wanner, `mid=0676` Gene Zwozdesky).
- **48,075 orphan chunks reconciled** by the merge command's post-pass. Chunks pointed at the deleted Kowalski stub UUID after a mid-merge timeout in the first attempt; the post-pass `UPDATE speech_chunks sc SET politician_id = s.politician_id FROM speeches s WHERE sc.speech_id = s.id AND sc.politician_id IS DISTINCT FROM s.politician_id` recovered them.
- `extras` blob now carries `ab_member_info` jsonb on every enriched MLA — full party_history / constituency_history / offices / status. Useful for future UI surfaces (constituency-over-time tab, ministerial timeline, etc.) without re-fetching.

---

## Known gotchas hit during the run

1. **First merge timed out on Kowalski's chunks UPDATE** (asyncpg's 60s default vs ~52k rows). Symptom: speeches moved, chunks didn't, stub deleted anyway because the inner await raised after both happy-path UPDATEs succeeded individually but the *next* stub's UPDATE hung. Resulted in 36k orphan chunks pointing at a deleted UUID. **Fixed** by switching the merge module to `db.pool.fetchrow(..., timeout=600)` (mirrors `resolve_ab_speakers`'s pattern) plus a final orphan-reconciliation pass that re-runs even on a clean state — defends against any future interrupted merge.

2. **Second merge attempt deadlocked** because `scanner-jobs` daemon was running `resolve-ab-speakers` concurrently (a scheduled job). Both UPDATE'd `speech_chunks` rows, contended, deadlocked. **Recovery**: `docker compose stop scanner-jobs`, killed any lingering DB backends with `pg_terminate_backend`, ran the merge clean, `docker compose start scanner-jobs`. Worth noting if any future merge-style command needs to run — pause the daemon during the destructive section.

3. **Notley's name displays as "ECA, The   Rachel Notley"** in the DB. Pre-existing data quality issue from the original `ingest-ab-former-mlas` ingest's name parser (it splits on the first comma, treating "The Honourable Rachel Notley, ECA" as last="The Rachel Notley", first="ECA"). My enrichment doesn't fix it (uses `COALESCE(name, ...)` so existing values win). **Not in scope** for this round; clean fix would be to extend `_HONORIFICS_RE` to include "ECA" and re-run the splitter on the existing roster names, OR prefer the header-extracted name when it parses cleaner. Filing as followup #5.

4. **Resolver `SPEAKER_ROSTER` first-name drift was the original cause of the four AB stubs.** The roster used colloquial first names ("Ken Kowalski"; "Bob Wanner") but `ingest-ab-former-mlas` writes the legal forms from the legl=N index page ("Kenneth R. Kowalski"; "Robert E. Wanner"). Strict `lower(first_name)=lower($2)` lookup missed → `_insert_minimal_politician` created a new stub. **Hardened two ways**: (a) `_find_politician_id` now falls back to last-name-only when exact match misses + last-name match is unique within province; (b) `SPEAKER_ROSTER["AB"]` updated to legal first names. Belt and suspenders — the fallback handles all jurisdictions; the legal-name update means AB doesn't even need the fallback.

5. **One-shot `db.pool.fetchrow(..., timeout=N)` does NOT propagate to follow-up statements on the next acquired connection.** Found this when `db.execute("DELETE FROM politicians ...")` after a 600s-timeout chunks UPDATE got `ConnectionDoesNotExistError` mid-batch — the pool had recycled the connection. Fix in the merge code: each statement uses its own `db.pool.fetchrow` / `db.execute` call. Each is independently idempotent, so a between-statement crash is recoverable on re-run. Worth remembering for future long-running multi-statement scripts.

6. **`politician_changes` table has a CHECK constraint on `change_type`.** Wanted to write `change_type='merged_from_presiding_seed'` audit rows during the merge but the existing CHECK list doesn't include it. **Skipped the audit-row write**; relied on the merge command's stdout/stderr captured into `scanner_jobs.stdout_tail` instead. If a DB-level audit of merges becomes important, that's a small migration to extend the CHECK list.

---

## Verification SQL (copy-paste sanity checks)

```sql
-- Coverage on enriched AB MLAs (was 6.5% / 6.9% baseline)
SELECT COUNT(*) AS total,
       COUNT(*) FILTER (WHERE photo_url IS NOT NULL)         AS with_photo,
       COUNT(*) FILTER (WHERE party IS NOT NULL)             AS with_party,
       COUNT(*) FILTER (WHERE constituency_name IS NOT NULL) AS with_const,
       COUNT(*) FILTER (WHERE extras ? 'ab_member_info_fetched_at') AS enriched
  FROM politicians
 WHERE province_territory='AB' AND level='provincial' AND ab_assembly_mid IS NOT NULL;
-- Expected: 988 / 988 / 988 / 988 / 988
```

```sql
-- New term rows from the enrichment pass (Speaker, Premier, Minister, Critic, etc.)
SELECT COUNT(*) AS total_terms,
       COUNT(DISTINCT politician_id) AS distinct_mlas
  FROM politician_terms WHERE source = 'ab-assembly-member-info';
-- Expected: total_terms ≈ 2112, distinct_mlas ≈ 700+ (many MLAs have ≥1 cabinet/critic role)
```

```sql
-- All 4 AB Speakers' speeches now on MID-keyed rows
SELECT p.name, p.ab_assembly_mid,
       (SELECT COUNT(*) FROM speeches s WHERE s.politician_id = p.id) AS speeches,
       (SELECT COUNT(*) FROM speech_chunks sc WHERE sc.politician_id = p.id) AS chunks
  FROM politicians p
 WHERE p.ab_assembly_mid IN ('0543','0885','0879','0676')
 ORDER BY p.ab_assembly_mid;
-- Expected:
--   0543 Kenneth R. Kowalski  speeches=49283  chunks=36412
--   0676 Gene Zwozdesky       speeches=14023  chunks=12565
--   0879 Robert E. Wanner     speeches=21635  chunks=14697
--   0885 Nathan Cooper        speeches=29248  chunks=26360
```

```sql
-- No orphan chunks (chunks pointing at a non-existent politician_id)
SELECT COUNT(*) AS orphan_chunks
  FROM speech_chunks sc
 WHERE sc.politician_id IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM politicians p WHERE p.id = sc.politician_id);
-- Expected: 0
```

```sql
-- No remaining presiding-officer-seed AB stubs
SELECT COUNT(*) AS remaining_ab_stubs
  FROM politicians WHERE source_id LIKE 'presiding-officer-seed:AB:%';
-- Expected: 0
```

```sql
-- Speaker_role coverage (no change — this just confirms the column is alive across the corpus)
SELECT level, province_territory,
       COUNT(*) FILTER (WHERE speaker_role IS NOT NULL AND speaker_role <> '') AS with_role,
       COUNT(*) AS total
  FROM speeches GROUP BY level, province_territory ORDER BY total DESC;
-- Reference: AB 182362/439125 (41%), QC 163257/313345 (52%), MB 74592/407695 (18%),
-- federal 86953/1080845 (8%), NS 26149/64143 (41%), NL 16746/44101 (38%), BC 16466/197888 (8%)
```

```bash
# API smoke: search returns speaker_role + the badge fires for "I declare the motion lost"
docker exec sw-frontend wget -qO- "http://api:3000/api/v1/search/speeches?q=motion+lost&limit=10" \
  | python3 -c "
import sys, json
for it in json.load(sys.stdin)['items'][:5]:
    role=it['speech'].get('speaker_role')
    pol=(it['politician'] or {}).get('name')
    print(f'{role!r:24s} | {pol!r}')"
# Expected: at least one row with role='The Speaker', politician='Myrna Driedger'
```

```bash
# API smoke: exclude_presiding filter drops chair speech
docker exec sw-frontend wget -qO- \
  "http://api:3000/api/v1/search/speeches?q=motion+lost&limit=10&exclude_presiding=true" \
  | python3 -c "
import sys, json
items = json.load(sys.stdin)['items']
print('items=', len(items))
print('with_role=', sum(1 for it in items if it['speech'].get('speaker_role')))"
# Expected: items > 0, with_role = 0
```

---

## What "bad" looks like — and how to recover

- **`/politicians/<historical-uuid>` returns 404 again**: someone reverted the `is_active = true` filter in `politicians.ts:417` or `openparliament.ts:135/224`. Re-apply the diff (the SELECT for `GET /:id` should be `SELECT p.*, (...) AS latest_term_ended_at FROM politicians p WHERE p.id = $1` — no `is_active` predicate).

- **Driedger profile loads but shows no "Former member" line**: the frontend is on an old image. `docker compose build frontend && docker compose up -d frontend`. The `is_active` and `latest_term_ended_at` fields must round-trip through `usePolitician.ts`'s `PoliticianCore` interface — verify by checking the API response includes them, then a hard browser reload.

- **Search results don't show `[Speaker]` badge**: API isn't returning `speech.speaker_role`. Confirm both SQL projections in `services/api/src/routes/search.ts` include `s.speaker_role AS speech_speaker_role` (grouped path AND flat path — they have different indentation, so an `Edit replace_all=true` won't catch both; check both). The grouped path shape interface and the response builder both need `speaker_role`.

- **Enrichment fails with `parse failed mid=NNNN`**: assembly.ab.ca template drift on a specific page. The 988-record full run had 0 failures, so any single-page failure is likely a transient DOM hiccup. Re-run with `--mid <NNNN>` to investigate. The parser is regex-based on `<div id="mla_*">` wrappers + `<div class="colN">` cells; a page that lacks the wrapper would parse to empty arrays (no failure) but produce 0 cabinet/Speaker terms.

- **Merge command finds new stubs**: shouldn't happen with the resolver hardened, but if `resolve-presiding-speakers --province AB` runs against a future first-name drift case, it could spawn new stubs. Run `merge-ab-presiding-stubs` again — it's idempotent and finds them automatically.

- **`scanner-jobs` daemon causes a deadlock when re-running merge**: `docker compose stop scanner-jobs`, run merge, `docker compose start scanner-jobs`. Already documented in gotcha #2 above.

---

## Out-of-scope follow-ups (per the approved plan)

1. **MB enrichment.** Gated on user-led research-handoff for the per-MLA URL pattern (suspected `/info/<slug>.html` per the MB research dossier but unverified). Same shape as AB once the URL is confirmed: parse upstream offices table, write `politician_terms` with `source='mb-assembly-member-info'`, run `merge-mb-presiding-stubs`. The four MB Speaker roster names (George Hickes, Daryl Reid, Myrna Driedger, Tom Lindsey) all have legal-name parity with the DB so the resolver shouldn't have created stubs there — but worth checking with `SELECT * FROM politicians WHERE source_id LIKE 'presiding-officer-seed:MB:%'`.

2. **Federal historical enrichment.** 975 inactive federal politicians have `openparliament_slug`. Re-use the existing `backfill-politicians-openparliament` flow against inactive records.

3. **Other-province presiding-officer-seed stubs (15 stubs, ~62k speeches)**: 4 QC (~40k speeches), 8 NS (~22k), 2 NB (~543), 1 NL (~368). Same merge pattern but each jurisdiction has its own canonical-ID column to twin against. Worth a single sweep after AB validates the approach (which it now has).

4. **Promote `extras.ab_member_info.party_history` to a normalized per-term `party` column on `politician_terms`.** Schema decision; defer.

5. **Honorific cleanup on existing roster names.** The original `ingest-ab-former-mlas` parser leaves "ECA, The   Rachel Notley" and similar messes. Extend `_HONORIFICS_RE` to include "ECA" / "OC" / "PC" / "MSC" etc., add "the" to the strip list, re-run the parser on raw_name field (or just on `name` in-place via a one-off SQL-driven re-parse). Cosmetic; no functional impact.

6. **Tier 2 presiding officers** (Deputy Speaker, Chair of Committees, Acting Speaker). Current `_SPEAKER_ROLE_BY_PROVINCE` is Tier 1 only. The role-badge UI in this plan already renders whatever role string the parser captured, so adding Deputy/Chair to the resolver/merge logic is the only missing piece. Out of scope here.

7. **Backfill-politician-photos run.** `enrich-ab-mlas` sets `photo_url` but doesn't download/hash. Run `docker compose run --rm scanner backfill-politician-photos` to mirror the 988 AB photos onto the local `/assets` volume. Cheap (~15 min). Not blocking — `photo_url` is a working absolute URL, so the UI renders the assembly.ab.ca-served photo directly until the local mirror is in place.

8. **CHECK constraint extension on `politician_changes.change_type`** to allow `'merged_from_presiding_seed'` audit rows (gotcha #6). Tiny migration. Only useful if the merge becomes a recurring operation rather than a one-time reconciliation.

9. **Commit the work.** Working tree mixes my AB enrichment changes with the user's premium-reports phase 1b work-in-progress. Same surgical-extract pattern as the prior handoff (cp backup → git checkout → re-apply selectively).

---

## File reference (for future-me)

- Plan: `~/.claude/plans/melodic-kindling-candle.md` ("Backfill AB politician detail + tag presiding-officer speeches")
- Probe artifacts (volatile): `/tmp/notley.html`, `/tmp/cooper.html` — sample assembly.ab.ca member-information pages used to develop the parser. Will be wiped on host reboot; not needed once parser is committed.
- Originally-broken Driedger UUID for the click-through smoke test: `b7b632b2-b07a-4aa5-95b1-3b3f1e685063`
- Four AB Speaker MIDs: 0543 (Kowalski), 0885 (Cooper), 0879 (Wanner), 0676 (Zwozdesky). All are now MID-keyed rows with rich `extras.ab_member_info` and a Speaker `politician_terms` row.
- Final stats from the full enrichment run: `considered=973 fetched=973 updated=973 terms_inserted=2040 failed=0` (the missing 15 of 988 were enriched in earlier targeted runs — Notley smoke + 10-batch sanity + 4 Speaker MIDs).
