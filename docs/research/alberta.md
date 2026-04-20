# Alberta — Legislative Data Research

> Standalone research dossier for Alberta. Cross-cutting context (schema, scanner modules, probe hierarchy, research-handoff protocol) lives in [`overview.md`](./overview.md).

**Legislature:** Legislative Assembly of Alberta | **Website:** https://www.assembly.ab.ca | **Seats:** 87 | **Next election:** 2027-10-18

**Status snapshot (2026-04-20):** ✅ **Bills live** for Legislature 31, sessions 1+2 (114 bills / 551 events / 114 sponsors / **100% FK-linked**). ✅ **Hansard live** via PDF pipeline — 439,125 speeches (2000-02-17 → 2026-04-16), 487,221 chunks, 100% Qwen3-embedded. ✅ **Speaker (presiding officer) resolution live** — 114,450 'The Speaker' rows tied to the correct sitting Speaker by date (Kowalski/Zwozdesky/Wanner/Cooper/McIver). Committees pre-existing (`ingest_ab_committees`). Overall speaker resolution still gated by the roster gap — only 91 AB politicians are in `politicians` against a 26-year corpus (~57% of speeches remain `politician_id IS NULL`); historical MLA enrichment is the outstanding work, not a resolver bug.

---

## User research (handoff URLs)

The user's initial Alberta research handoff:

- **Alberta Assembly Dashboard:** https://www.assembly.ab.ca/assembly-business/assembly-dashboard
- **Votes and proceedings page:** https://www.assembly.ab.ca/assembly-business/assembly-records/votes-and-proceedings
- **Open API:** https://open.alberta.ca/api/3 *(don't think they publish assembly business here — confirmed: it's ministry-of-government publications only)*
- **Hansard:** https://www.assembly.ab.ca/assembly-business/transcripts/transcripts-by-type

## Bills & Legislation ✅ LIVE (2026-04-16)

- **Primary source — Assembly Dashboard:** `https://www.assembly.ab.ca/assembly-business/assembly-dashboard?legl={L}&session={S}`. One server-rendered HTML page per session (~600 KB) embeds **every bill** plus its full stage history. Densest single-page roster we've found — no per-bill fetches needed.
- **MLA roster:** `/members/members-of-the-legislative-assembly` — 91 MLAs linked with zero-padded 4-char `mid=NNNN` in each profile href. One scrape → `politicians.ab_assembly_mid` → sponsor resolution becomes exact FK lookup. CSV download at `/txt/mla_home/contacts.csv` lacks the mid column — skip it.
- **Stage vocabulary (extended):** 1R / 2R / CW / 3R / RA / **CF (Comes into Force)**. CF is unique to Alberta — captured as a new canonical stage `comes_into_force` (no constraint to update; `bill_events.stage` is TEXT). CF lets us surface the operational effective date, which is often weeks after Royal Assent.
- **Historical backfill:** parameter-addressable back to Legislature 1 Session 1 — **137 total sessions** discoverable from the current dashboard's session-picker nav. One HTTP GET per session = trivial full backfill.
- **Session-nav params deciphered:**
  - `?legl=L&session=S` with no `rx` — full dashboard with all bills for that session.
  - `rx=455` — deep-link to a single bill anchor on the same dashboard.
  - `rx=225` — votes/proceedings day-anchor view (separate page, deferred).
- **Bill ID model:** every bill carries a stable integer `billinfoid` (e.g. 12086) in the accordion anchor (`id="g12086"`). Stored in `bills.raw.billinfoid` — candidate integer key if we later need one.
- **Data per bill:** number, title, sponsor (mid + last name), type (Government / Private / Private Member), amendments flag, money-bill flag, bill-text PDF url, per-stage date + status ("passed" / "adjourned" / "defeated" / "outside of House sitting") + Hansard PDF link with page range. `event_type` stores the status so same-stage same-day rows with different outcomes stay distinct.
- **Catch-all 404 gotcha:** `www.assembly.ab.ca` serves a 200-status HTML page for every unmapped URL. Don't trust HTTP codes when probing for endpoints — compare response bodies.
- **Open Data portal (`open.alberta.ca`):** has two Municipal Government Act feedback datasets and nothing else for the Assembly — `open.alberta.ca` is ministry-of-government publications only.
- **Scanner modules:** `ab_mlas.py` (roster), `ab_bills.py` (dashboard scrape + parser).
- **CLI:** `enrich-ab-mla-ids`, `ingest-ab-bills [--legislature --session | --all-sessions-in-legislature L | --all-sessions]`.
- **Terms/Licensing:** Crown copyright; publicly accessible. Civic-transparency use case is standard.
- **Rate limits / auth:** None observed. ASP.NET/IIS/Sitefinity stack — no WAF signals.
- **Difficulty (1–5):** **2** — the dashboard does almost all the work.
- **Results on first run (Legislature 31, sessions 1+2):** 114 bills / 551 events / 114 sponsors / **114 FK-linked to politicians (100%)**. 6 stages seen: first_reading (119), second_reading (194), committee (82), third_reading (61), royal_assent (81), comes_into_force (14).
- **Outstanding:** Historical backfill (`--all-sessions` = ~137 GETs, free to run); votes/proceedings page scraping (needs day-anchor iteration); Hansard PDF pipeline (broader PDF tooling investment).

## Hansard / Debates ✅ LIVE

- **Source URL(s):** https://www.assembly.ab.ca/assembly-business/transcripts/hansard-transcripts/compiled-volumes
- **Format:** PDF compiled volumes per Legislature/Session (paper publication ceased 2016-01-01).
- **Granularity:** Session-level volumes; digitized from 1972 forward; searchable from 1986.
- **Speaker identification:** Yes; speaker names in PDF. `ab_hansard.py` parses `_PERSON_SPEAKER_RE` (honorific + surname) and `_ROLE_SPEAKER_RE` (presiding-officer titles) from pdftotext output.
- **Difficulty (1–5):** 4 on first build; 2 once the PDF stack exists.
- **Source system:** `source_system='assembly.ab.ca'`.
- **Scanner modules:** `ab_hansard.py` (parse + upsert), plus shared `presiding_officer_resolver.py`.
- **CLI:** `ingest-ab-hansard`, `chunk-speeches`, `embed-speech-chunks`, `resolve-presiding-speakers --province AB`.

## ★ Speaker (presiding-officer) resolution — live 2026-04-20

AB Hansard "The Speaker" lines carry only the role, never a name. Resolution is **date-ranged**: the sitting Speaker on any given day is knowable from the Legislature's public records, so we seed a small hand-curated roster and join by `spoken_at`.

**Data model:**
- Roster is a Python constant (`SPEAKER_ROSTER["AB"]` in `services/scanner/src/legislative/presiding_officer_resolver.py`) — one tuple per Speaker with `started_at` / `ended_at`. Kept in code so changes are PR-reviewable.
- Roster → seeds `politicians` (inserts retired Speakers as minimal rows with `is_active=false` and `source_id='presiding-officer-seed:AB:<surname>'`).
- Roster → seeds `politician_terms` with `office='Speaker'`, `source='presiding_officer_seed'`. Idempotent via DELETE-then-INSERT on that source tag (no unique constraint required on the table).

**Resolver:**
```bash
docker compose run --rm scanner resolve-presiding-speakers --province AB
# → roster=5 terms=5 scanned=114450 resolved=114450 chunks_updated=~52k
```
One pass links every `speaker_role='The Speaker'` row whose `politician_id` is NULL. Also updates `speech_chunks.politician_id` (denormalised copy) in the same transaction. Batched in 5,000-row UPDATEs to avoid asyncpg timeouts on the 100k+-row buckets.

**Roster (as of 2026-04-20):**

| Speaker | Start | End | Speeches linked |
|---|---|---|---:|
| Ken Kowalski | 1997-04-14 | 2012-05-23 | 49,264 |
| Gene Zwozdesky | 2012-05-23 | 2015-06-11 | 11,065 |
| Bob Wanner | 2015-06-11 | 2019-05-20 | 21,628 |
| Nathan Cooper | 2019-05-21 | 2025-05-13 | 27,956 |
| Ric McIver | 2025-05-13 | — | 4,537 |

Sources: Wikipedia "Speaker of the Legislative Assembly of Alberta" + assembly.ab.ca.

**Extending to a new Speaker** (next election, resignation, etc.):
1. Append a new `SpeakerTerm(...)` to `SPEAKER_ROSTER["AB"]`.
2. Update the prior Speaker's `ended_at` to the new Speaker's `started_at`.
3. Re-run `resolve-presiding-speakers --province AB`. Idempotent — it re-seeds terms and backfills any new Hansard that's landed since.

**Known edge case:** Single-day transitions attribute the entire day to the incoming Speaker. Worst case ~1 day drift per transition across 26 years. Fixing would require sub-day precision (sequence-based split on the election-of-Speaker proceedings). Not worth it unless these rows start surfacing prominently in search UX.

**Out of scope (Tier 2/3):** Deputy Speaker (19,302 rows), Acting Speaker (12,615), The Chair / Deputy Chair (28,636), The Chairman (1,223). Tier 2 needs the same date-range approach but with per-parliament Deputy Speaker terms (harder — changes mid-parliament; no Wikipedia list; AB Journals scrape needed). Tier 3 (Committee of the Whole Chair) is parser-level — chair identity appears in HTML ("R. Leonard in the chair") and needs a two-pass parse.

## Voting Records / Divisions

- **Source URL(s):** https://www.assembly.ab.ca/assembly-business/assembly-dashboard (order paper / daily records)
- **Format:** Embedded in daily order papers and Hansard — no standalone interface.
- **Roll-call availability:** Recorded votes appear in Hansard when divisions occur.
- **Difficulty (1–5):** 4.
- **Notes:** Likely requires extracting from Hansard PDFs once Hansard pipeline exists.

## Committee Activity

- **Source URL(s):** https://www.assembly.ab.ca/assembly-business/committees ; https://www.assembly.ab.ca/assembly-business/committees/committee-reports
- **Format:** HTML committee pages with reports; minutes via Legislature Library (librarysearch.assembly.ab.ca).
- **Data available:** Memberships, standing committee list, committee reports.
- **Overlap with existing scanner:** **`ingest_ab_committees` already implemented** — this is our one existing provincial asset in the legislative-activity layer pre-2026. Any AB work here extends that.
- **Difficulty (1–5):** 2 (already scraped).
- **Notes:** Contact: library.requests@assembly.ab.ca, 780-427-2473.

## Existing third-party scrapers

- **opencivicdata/scrapers-ca:** `ca_ab` module exists (provincial + municipal: Calgary, Edmonton, Grande Prairie, Lethbridge, Strathcona, Wood Buffalo).
- Other: None identified.

## Status

- [x] Research complete
- [x] Schema drafted (migration `0013_politician_ab_assembly_mid.sql`)
- [x] Ingestion prototyped
- [x] Production ingestion live (2026-04-16) — Legislature 31 sessions 1+2, 114 bills
- [x] Committees (pre-existing `ingest_ab_committees`)
- [x] Hansard PDF parsing (live 2026-04 — 439,125 speeches, 2000-2026)
- [x] Speaker (Tier 1 presiding-officer) resolution (live 2026-04-20 — 114,450 rows linked)
- [ ] Historical AB MLA roster enrichment (91 politicians vs 26 years of corpus; ~57% of speeches remain role-only or unmatched due to absent retired MLAs — separate workstream from the resolver)
- [ ] Historical bills backfill (`--all-sessions` covers Legislature 1 onward, ~137 sessions; trivial but not yet run)
- [ ] Tier 2 presiding officers — Deputy Speaker / Acting Speaker (~32k rows combined)
- [ ] Tier 3 presiding officers — Committee of the Whole Chair / Deputy Chair (~29k rows)
- [ ] Votes/proceedings per-day scrape
