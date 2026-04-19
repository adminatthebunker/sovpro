# Manitoba — Legislative Data Research

> Standalone research dossier for Manitoba. Cross-cutting context (schema, scanner modules, probe hierarchy, research-handoff protocol) lives in [`overview.md`](./overview.md).

**Legislature:** Legislative Assembly of Manitoba | **Website:** https://www.gov.mb.ca/legislature | **Seats:** 57 | **Next election:** By 2027-10-05

**Status snapshot (2026-04-19):** ⏸️ **Bills deferred** (PDF-dependent). Stage timeline locked behind `billstatus.pdf` — emitting proxy events with NULL dates would be materially weaker than every other shipped province. Unblock path: a `pdfplumber`-based extractor (also unlocks AB Hansard).

---

## User research (handoff URLs)

The user's initial Manitoba research handoff:

- **Bills search:** https://web2.gov.mb.ca/bills/search/search.php
- **Current session bills:** https://web2.gov.mb.ca/bills/43-3/index.php (PDFs)
- **Members:** https://www.gov.mb.ca/legislature/members/mla_list_constituency.html
- **Hansard:** https://www.gov.mb.ca/legislature/hansard/43rd_3rd/43rd_3rd.html#top (HTML / PDF versions)

## Bills & Legislation ⏸️ DEFERRED (PDF-dependent)

- **Probed 2026-04-16 and deferred** until PDF extraction is justified by ≥2 other PDF-only jurisdictions (currently only MB forces PDF parsing; NB/NL turned out HTML-native so the cross-cutting case hasn't materialized).
- **Bill roster source:** `/bills/{P}-{S}/index.php` — HTML table with bill number, sponsor-as-text (e.g. "Hon. Mr. Wiebe / Minister of Justice"), title, PDF link, and optional "amendment(s) adopted at Committee Stage" PDF. Bill list for current session (43-3) has ~80 bills.
- **Per-bill page** `/bills/{P}-{S}/b{NNN}e.php` returns the **bill text as distributed after First Reading** — no stage history, no sponsor block, no dates beyond a blank "Assented to ______" template.
- **Stage timeline source:** locked in `https://manitoba.ca/legislature/business/billstatus.pdf` (270 KB, one PDF per session). Without PDF extraction we can only emit proxy events (presence of committee-amendments PDF ⇒ reached committee, "As Enacted" link ⇒ royal assent) with NULL dates — materially weaker than every other province we've shipped.
- **Historical coverage via `/bills/sess/index.php`:** back to Legislature 37 Session 1 (1999) — same URL shape.
- **No open-data portal:** checked `data.gov.mb.ca`, `data.manitoba.ca`, `mbgov.socrata.com` — all 404 / no-connect.
- **No RSS, no feed, no JSON/XML endpoints.** Server sends `Server: na` header deliberately.
- **MLA roster:** 57 MLAs at `/legislature/members/mla_list_constituency.html` as HTML table. Per-MLA slug (e.g. `info/wiebe.html`) from surname — no numeric id; **sponsor resolution would be name-based** (honorific-heavy strings like "Hon. Mr. Wiebe").
- **Difficulty (1–5):** 3 for roster + titles; **4 for stage timeline** (requires PDF extraction).
- **Terms/Licensing:** Crown copyright.
- **Unblock path:** build `pdfplumber`-based extractor for `billstatus.pdf` once justified (same tooling will unlock AB Hansard, potential QC committee reports).

## Hansard / Debates

- **Source URL(s):** https://www.gov.mb.ca/legislature/hansard/ ; https://www.gov.mb.ca/legislature/hansard/index_homepage.html
- **Format:** HTML indexed by session/year; subject + member + public-presenter indexes.
- **Granularity:** Daily from 1958 to present.
- **Speaker identification:** Yes; speaker indexes available.
- **Difficulty (1–5):** 3.
- **Notes:** Transcripts available within 24 hours of sitting.

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
