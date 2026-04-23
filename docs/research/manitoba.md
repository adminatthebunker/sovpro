# Manitoba — Legislative Data Research

> Standalone research dossier for Manitoba. Cross-cutting context (schema, scanner modules, probe hierarchy, research-handoff protocol) lives in [`overview.md`](./overview.md).

**Legislature:** Legislative Assembly of Manitoba | **Website:** https://www.gov.mb.ca/legislature | **Seats:** 57 | **Next election:** By 2027-10-05

**Status snapshot (2026-04-23):** 🟢 **Live.** Bills roster (81 bills, 81/81 sponsors FK-linked) + bill stage events from `billstatus.pdf` (106 events across 80 bills) + Hansard for **legislatures 39-43** — 19 sessions, 2007-09-11 → 2026-04-16 (legs 37-38 deferred: Microsoft Word 97 HTML export, different markup shape). **292,647 speeches, 78.8% resolved to politicians, 368,028 Qwen3 chunks 100% embedded.** Resolution mix: name-matched (59.5%) + date-windowed presiding speakers (Hickes/Reid/Driedger/Lindsey; +47,998 rows via `resolve-presiding-speakers --province MB`) + date-windowed historical surnames (+8,481 rows via new `resolve-mb-speakers-dated` using `politician_terms` spans). Politicians table holds the full **820-MLA historical roster** back to 1870 (56 current + 764 historical, 1,723 terms with 97% coverage from `ingest-mb-former-mlas`). All via `ingest-mb-mlas` / `ingest-mb-former-mlas` / `ingest-mb-bills` / `parse-mb-bill-events` / `ingest-mb-hansard` / `resolve-mb-speakers-dated` / `resolve-presiding-speakers`. PDF extraction uses the shared `pdf_utils.pdftotext` helper (Poppler, `-raw` mode) that also backs AB Hansard — no new dependency.

---

## User research (handoff URLs)

The user's initial Manitoba research handoff:

- **Bills search:** https://web2.gov.mb.ca/bills/search/search.php
- **Current session bills:** https://web2.gov.mb.ca/bills/43-3/index.php (PDFs)
- **Members:** https://www.gov.mb.ca/legislature/members/mla_list_constituency.html
- **Hansard:** https://www.gov.mb.ca/legislature/hansard/43rd_3rd/43rd_3rd.html#top (HTML / PDF versions)

## Bills & Legislation 🟢 LIVE (2026-04-20)

- **Roster from `/bills/{P}-{S}/index.php`** via `ingest-mb-bills` — parses the Government Bills + Private Members' Bills tables on a single page. Current session 43-3: **81 bills** (47 government + 34 PMB), all sponsors FK-linked to politicians via the slug join.
- **Per-bill pages** (`b{NNN}e.php`) are bill-text-only as predicted — no sponsor, no dates. We never fetch them; the index has all the metadata we need.
- **Stage timeline from `billstatus.pdf`** via `fetch-mb-billstatus-pdf` + `parse-mb-bill-events` — 106 events across 80 bills (bill 235 is pre-first-reading and not yet in the PDF). Dates span first reading / second reading / committee (with committee name like "Justice", "Social and Economic Development"). PDF parsed via Poppler's `pdftotext -raw` mode (the `-layout` mode wrapped dates awkwardly across lines).
- **Canonical ID:** `politicians.mb_assembly_slug` (surname slug from `info/<surname>.html`) added in migration `0030`. 56/56 seated MLAs have it stamped via `ingest-mb-mlas`. Compound surnames ("Dela Cruz" → slug `delacruz`) handled by slug-candidate ordering in the parser.
- **No open-data portal, no RSS, no JSON endpoints** (as probed). Scraping is the only path.

## Hansard / Debates 🟢 LIVE (43rd Legislature complete, 2026-04-21)

- **Source URL pattern:** `/hansard/{leg}_{sess}/vol_NN[letter]/hNN[letter].html` — Word-exported HTML served as windows-1252 (force encoding on fetch, otherwise accented characters mojibake).
- **Full 43rd Legislature ingested:** 3 sessions, 184 sitting-days, **30,649 speeches**, 81.3% resolved to politicians (24,912 / 30,649), span 2023-11-09 → 2026-04-16. Per-session breakdown:
    - **43-1:** 12,379 speeches, 75 days (2023-11-09 → 2024-11-08), 77.5% resolved
    - **43-2:** 12,882 speeches, 75 days (2024-11-19 → 2025-11-07), 81.7% resolved
    - **43-3:** 5,388 speeches, 34 days (2025-11-18 → 2026-04-16), 89.0% resolved
- **Resolution pipeline:** inline name match via `mb_assembly_slug` → `resolve-mb-speakers` post-pass → `resolve-presiding-speakers --province MB` (links "The Speaker" rows to Tom Lindsey across his 2023-11-21–present term). The 18.7% unresolved is mostly role-only attributions ("The Attorney General", "The Clerk", "The Acting Speaker", "Sergeant-at-Arms") and a small set of pre-Lindsey-tenure speeches in the first ~12 days of 43-1 (2023-11-09 → 2023-11-20). 43-1's lower rate reflects both factors plus more ceremonial early-legislature content.
- **Parser quirks:** timestamp markers are `<b>*</b> (HH:MM)` between speech blocks — we use them to set per-speech `spoken_at` accurately rather than defaulting to sitting-start time. Speaker attribution uses `<b>Hon./Mr./Mrs./Ms./MLA Surname:</b>` with the full person's first+last name spelled out only on throne-speech / formal introductions.
- **Pre-43rd-Legislature backfill deferred.** The URL pattern holds back to the 25th Legislature (1958). Going further would require `SPEAKER_ROSTER["MB"]` expansion (Driedger 2018-2023, etc.) and would degrade resolution rate (current `politicians` table only carries seated MLAs). Worth doing eventually but not gating any current work.
- **Resolution lift candidates:** (a) seed an Acting Speaker entry or expand the Speaker resolver to handle `The Acting Speaker` + `The Speaker pro tempore`, (b) add a pre-Lindsey acting Speaker for the 2023-11-09 → 2023-11-20 window of 43-1.

## Voting Records / Divisions

- **Source URL(s):** https://www.gov.mb.ca/legislature/business/votes_proceedings.html
- **Format:** Votes and Proceedings documents; typically embedded in daily records.
- **Roll-call availability:** Variable format.
- **Difficulty (1–5):** 4.
- **Notes:** No standalone export.

## Committee Activity

- **Source URL(s):** https://www.gov.mb.ca/legislature/committees/ ; https://www.gov.mb.ca/legislature/committees/membership.html
- **Format:** HTML pages with meeting notices, broadcasts, reports, clerk contacts.
- **Data available:** Non-permanent rotating membership; broadcasts; reports.
- **Overlap with existing scanner:** None.
- **Difficulty (1–5):** 2.
- **Notes:** Meetings via Zoom Webinar. Standing committees can't meet Jan–Aug except Public Accounts.

## Existing third-party scrapers

- **opencivicdata/scrapers-ca:** `ca_mb` module exists (provincial + Winnipeg municipal).
- Other: None identified.

## Status

- [x] Research complete
- [ ] Schema drafted
- [ ] Ingestion prototyped
- [ ] Production ingestion live
