# New Brunswick — Legislative Data Research

> Standalone research dossier for New Brunswick. Cross-cutting context (schema, scanner modules, probe hierarchy, research-handoff protocol) lives in [`overview.md`](./overview.md).

**Legislature:** Legislative Assembly of New Brunswick | **Website:** https://www.legnb.ca | **Seats:** 49 | **Next election:** By 2028-10-16

**Status snapshot (2026-04-19):** ✅ **Bills live** for Legislature 61, Session 2 (33 bills / 111 events / **33 sponsors / 100% FK-linked**) via two-step legnb.ca HTML scrape. The earlier hopeful note about `gnb.socrata.com` carrying NB legislative data turned out to be wrong — every "bill" hit on that portal is from another jurisdiction.

---

## Bills & Legislation ✅ LIVE (2026-04-16)

- **Primary source:** two-step HTML scrape of legnb.ca.
  - **List page** `/en/legislation/bills/{legislature}/{session}` — server-rendered HTML with every bill; each row links to the detail page via `/en/legislation/bills/{legl}/{session}/{number}/{slug}`.
  - **Detail page** — rich payload: Bill Type, Status, Sponsor (`<div class="member-card">` with name + party + constituency), Documents (PDF + HTML), **Progression Timeline** (a `<ul id="legislation-timeline">` with per-stage events listing date + action label like "Introduced", "Passed", "Adjourned").
- **Sponsor resolution:** name-based — legnb.ca exposes **no numeric MLA id** in sponsor links (portraits path carries session, not member). Sponsor names appear in all-caps-surname form ("Hon. Susan HOLT"); normalization strips honorifics and case-folds.
- **Scope:** current session discovered automatically by parsing `/en/legislation/bills` for the most recent `(legl, session)` pair. `--all-sessions-in-legislature L` backfills every session in L.
- **Historical coverage:** at least legislatures 52–61 (i.e. ~20 years) accessible via URL parameters.
- **Open data portal (`gnb.socrata.com`):** earlier research note was **wrong** — the catalog has ~48 results for "bill" queries but every single one is from **other jurisdictions** (NS, CT, Iowa, etc.). NB publishes no legislative-business datasets on the portal. HTML scrape is the only viable route.
- **Terms/Licensing:** Open Government Licence (NB). Civic-transparency use case is well-covered.
- **Rate limits / auth:** None observed. HTTP 302 "not found" behavior on unmapped paths (no catch-all 200 trap). Per-bill detail cost: ~35 bills × 1.5 s = ~1 min per session.
- **Difficulty (1–5):** 2 — server-rendered HTML with clean class names.
- **Scanner module:** `services/scanner/src/legislative/nb_bills.py`.
- **CLI:** `ingest-nb-bills [--legislature N --session S | --all-sessions-in-legislature N]`.
- **Stages captured:** First Reading, Second Reading, Committee, Third Reading, Royal Assent — with action outcomes (Introduced, Passed, Adjourned, etc.) stored in `bill_events.event_type`.
- **Bill types normalized:** `government`, `private_member`, `private`.
- **Results on first run (Legislature 61, Session 2):** 33 bills / 111 events / 33 sponsors / **33 FK-linked (100%)**.

## Hansard / Debates

- **Source URL(s):** https://www.legnb.ca/en/house-business/hansard
- **Format:** HTML + PDF; archives from 1900 to present.
- **Granularity:** Daily; includes committee proceedings.
- **Speaker identification:** Yes.
- **Difficulty (1–5):** 3.
- **Notes:** Committee Hansards on request via Legislative Library. Contact: 506-453-2338.

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
- [ ] Historical backfill (`--all-sessions-in-legislature` works; not yet run)
- [ ] Hansard
- [ ] Votes / Journals
