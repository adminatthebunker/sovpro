# New Brunswick — Legislative Data Research

> Standalone research dossier for New Brunswick. Cross-cutting context (schema, scanner modules, probe hierarchy, research-handoff protocol) lives in [`overview.md`](./overview.md).

**Legislature:** Legislative Assembly of New Brunswick | **Website:** https://www.legnb.ca | **Seats:** 49 | **Next election:** By 2028-10-16

**Status snapshot (2026-04-22):** ✅ **Bills historical backfill complete** — Legislatures 56 through 61 (2007–present, ~20 years). **1,248 bills / ~4.6k events / ~1,250 sponsors**, all with `raw_html` cached. Digital coverage on legnb.ca starts at Leg 56/1 (2007); earlier bills are paper-only via the Legislative Library (506-453-2338). FK-link rate ~100% on Leg 60-61, ~0-30% on Leg 56-58 pending historical-MLA enrichment (idempotent — a future `ingest-mlas` run fills in retroactively).

✅ **Hansard live** — bilingual PDF scrape of `/en/house-business/hansard/{L}/{S}` covering Leg 58/3 onward (Nov 2016 – present). **22,895 speeches across 312 sittings**. Earlier "available from 1900" claim on the site refers to paper records at the Legislative Library; digital Hansard begins at 58/3 in practice. Speaker resolution is name-based (no canonical MLA id); presiding-officer rows resolved by date range via the NB Speaker roster in `presiding_officer_resolver.SPEAKER_ROSTER["NB"]` — 4,131 of 4,208 "Mr./Madam Speaker" rows resolved (98%). Person-speaker resolution ranges 17% (Leg 59, 2018) → 77% (Leg 61, current) — the gap tracks NB MLA roster completeness.

The earlier hopeful note about `gnb.socrata.com` carrying NB legislative data turned out to be wrong — every "bill" hit on that portal is from another jurisdiction.

---

## Bills & Legislation ✅ LIVE (2026-04-16)

- **Primary source:** two-step HTML scrape of legnb.ca.
  - **List page** `/en/legislation/bills/{legislature}/{session}` — server-rendered HTML with every bill; each row links to the detail page via `/en/legislation/bills/{legl}/{session}/{number}/{slug}`.
  - **Detail page** — rich payload: Bill Type, Status, Sponsor (`<div class="member-card">` with name + party + constituency), Documents (PDF + HTML), **Progression Timeline** (a `<ul id="legislation-timeline">` with per-stage events listing date + action label like "Introduced", "Passed", "Adjourned").
- **Sponsor resolution:** name-based — legnb.ca exposes **no numeric MLA id** in sponsor links (portraits path carries session, not member). Sponsor names appear in all-caps-surname form ("Hon. Susan HOLT"); normalization strips honorifics and case-folds.
- **Scope:** current session discovered automatically by parsing `/en/legislation/bills` for the most recent `(legl, session)` pair. `--all-sessions-in-legislature L` backfills every session in L.
- **Historical coverage:** Legislatures 56–61 (2007–present, ~20 years) have digital bill data. The bills index dropdown lists sessions back to Leg 53 (1995), but direct probes of Leg 54/1 and 53/1 returned zero bills — only Leg 56+ actually ship bill content on legnb.ca. Pre-2007 bills exist only in paper form via the Legislative Library.
- **Open data portal (`gnb.socrata.com`):** earlier research note was **wrong** — the catalog has ~48 results for "bill" queries but every single one is from **other jurisdictions** (NS, CT, Iowa, etc.). NB publishes no legislative-business datasets on the portal. HTML scrape is the only viable route.
- **Terms/Licensing:** Open Government Licence (NB). Civic-transparency use case is well-covered.
- **Rate limits / auth:** None observed. HTTP 302 "not found" behavior on unmapped paths (no catch-all 200 trap). Per-bill detail cost: ~35 bills × 1.5 s = ~1 min per session.
- **Difficulty (1–5):** 2 — server-rendered HTML with clean class names.
- **Scanner module:** `services/scanner/src/legislative/nb_bills.py`.
- **CLI:** `ingest-nb-bills [--legislature N --session S | --all-sessions-in-legislature N]`. In `--all-sessions-in-legislature` mode the scanner probes S=1..6 directly (the main bills index only exposes current-session links, not historical ones).
- **Raw HTML cache:** Each bill's detail-page HTML is persisted to `bills.raw_html` + `bills.html_fetched_at` so re-parses after a template change are network-free. Columns come from migration `0007_bill_html_cache.sql`.
- **Stages captured:** First Reading, Second Reading, Committee, Third Reading, Royal Assent — with action outcomes (Introduced, Passed, Adjourned, etc.) stored in `bill_events.event_type`.
- **Bill types normalized:** `government`, `private_member`, `private`.
- **Results on first run (Legislature 61, Session 2):** 33 bills / 111 events / 33 sponsors / **33 FK-linked (100%)**.
- **Historical backfill (2026-04-22, Leg 56–61):** ~1.2k bills / ~4k events / ~1.2k sponsors. FK-link rate ~100% on Leg 60/61 (current roster present in `politicians`), and close to 0% for Leg 56–58 where historical MLAs are not yet ingested. The ingester is idempotent — re-running after enriching the historical-MLA roster retroactively links prior sponsors.

## Hansard / Debates ✅ LIVE (2026-04-22)

- **Primary source:** HTML listing at `/en/house-business/hansard/{L}/{S}` → per-sitting PDFs at `/content/house_business\{L}\{S}\hansard\{seq} {YYYY-MM-DD}{b|bil}.pdf`.
  - Path literal backslashes (`\`) are served verbatim; the scanner URL-encodes them as `%5C` on fetch.
  - Filename suffix drifts between `b.pdf` (older) and `bil.pdf` (newer). Listing regex matches both.
- **Format:** Bilingual two-column PDF (English left, French right). Default `pdftotext` reading-order mode produces alternating EN/FR paragraphs which the parser then walks as paragraphs (blank-line separated).
- **Digital coverage:** Legislature 58/3 onwards (2016-09+). 58/1 and 58/2 + all prior return "There are no hansard transcripts for the selected legislative session." on the listing page.
- **Speaker identification:**
  - English speaker lines trigger new speeches: `Hon. Susan Holt:`, `Hon. Ms. Holt:`, `Mr. Coon:`, `Ms Mitton:`, `Mrs. Petrovic:`, `Member Arseneau:`. Long attributions (`Mr. Monahan, resuming the adjourned debate on Motion 24:`) handled via paragraph-level regex.
  - Role-only: `Mr. Speaker`, `Madam Speaker`, `Mr. Chair`, `Madam Chair`, `Her Honour`, `Hon. Members`, etc. — left `politician_id=NULL` at ingest and resolved by the NB Speaker roster in `presiding_officer_resolver.SPEAKER_ROSTER["NB"]` via `resolve-presiding-speakers --province NB`.
  - French speaker labels (`L'hon. Mme Holt :`, `Le président :`, `Des voix`) do NOT create new speech rows — they become body text of the preceding English turn (the French is the translation).
- **Resolution strategy:** Name-based against `politicians` WHERE level='provincial' AND province_territory='NB'. No canonical MLA id exists on legnb.ca — confirmed by inspecting sponsor links and member portraits.
- **Scanner module:** `services/scanner/src/legislative/nb_hansard.py`. Clones `ab_hansard.py`'s PDF recipe with NB-specific listing regex + bilingual-aware parser.
- **CLI:** `ingest-nb-hansard [--legislature L --session S | --all-sessions-in-legislature L]` plus `resolve-nb-speakers` for post-pass resolution.
- **Difficulty (1–5):** 3 — bilingual PDF + long attribution lines + French-label suppression are the non-trivial bits.
- **Notes:** Committee Hansards on request via Legislative Library (506-453-2338). Paper records for pre-2016 Hansard also there.

## Voting Records / Divisions

- **Source URL(s):** https://www.legnb.ca/en (House Business section; embedded in Journals)
- **Format:** Embedded in Journals/House proceedings.
- **Roll-call availability:** In minutes when recorded.
- **Difficulty (1–5):** 4.
- **Notes:** No dedicated voting export. Extract from Journals or Hansard.

## Committee Activity

- **Source URL(s):** https://www.legnb.ca/en/committees ; https://www.legnb.ca/en/committees/{id}
- **Format:** HTML pages with meeting schedules, reports, membership.
- **Data available:** Standing committees (Procedure/Privileges, Law Amendments, Social Policy, Economic Policy, Estimates and Fiscal Policy, Public Accounts); no active select committees.
- **Overlap with existing scanner:** None.
- **Difficulty (1–5):** 2.
- **Notes:** Standing committees meet year-round.

## Existing third-party scrapers

- **opencivicdata/scrapers-ca:** `ca_nb` module exists (provincial + Fredericton, Moncton, Saint John).
- Other: https://www1.gnb.ca/leglibbib (Legislative Library reference).

## Status

- [x] Research complete
- [x] Schema (no new migration — name-based resolution against existing `politicians` rows)
- [x] Ingestion prototyped
- [x] Production ingestion live (2026-04-16) — Legislature 61, Session 2, 33 bills
- [x] Historical backfill (2026-04-22) — Leg 56–61, ~1.2k bills, raw_html captured
- [x] Hansard (2026-04-22) — Leg 58/3 onward, bilingual PDF scrape
- [ ] Historical MLA enrichment (pre-2020 MLAs not yet in `politicians`; blocks sponsor + speaker resolution for older sessions)
- [ ] Votes / Journals
