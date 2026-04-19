# Newfoundland & Labrador — Legislative Data Research

> Standalone research dossier for Newfoundland and Labrador. Cross-cutting context (schema, scanner modules, probe hierarchy, research-handoff protocol) lives in [`overview.md`](./overview.md).

**Legislature:** House of Assembly | **Website:** https://www.assembly.nl.ca | **Seats:** 40 | **Next election:** 2029-10

**Status snapshot (2026-04-19):** ✅ **Bills live** for GA 51 Session 1 (12 bills / 31 events / **0 sponsors — by upstream design**). Stage timeline ingestion is trivial (single-page table per session); sponsor data is not exposed on any inspected HTML page and would require Order Papers / Hansard scrape.

---

## Bills & Legislation ✅ LIVE (2026-04-16)

- **Primary source:** single-page session table at `/HouseBusiness/Bills/ga{GA}session{S}/`. The page is server-rendered HTML with exactly one `<table>` whose rows carry **the full stage timeline for every bill in the session** — columns: No., Bill (title + link to bill text), First Reading, Second Reading, Committee, Amendments (Yes/No), Third Reading, Royal Assent, Act chapter.
- **One HTTP GET per session** captures every stage date. No per-bill detail fetch needed for timeline data. (Per-bill `.htm` pages exist but serve bill text only.)
- **Sponsor data: NOT IN THE PROGRESS TABLE OR PER-BILL HTML.** Sponsor would need to come from Order Papers, Journals, or Hansard — deferred. Pipeline writes `bill_sponsors` = 0 for NL; stages + titles are the MVP.
- **MHA roster:** at `/Members/members.aspx`; no numeric member id in URLs. Would require name-based matching if/when sponsor data surfaces.
- **Historical coverage:** every session back to GA 44 (≈40 sessions) addressable via `ga{GA}session{S}`. `--all-sessions-in-ga` + `--all-sessions` flags available.
- **Quirk:** per-bill `.htm` pages are **Windows-1252 encoded** (not UTF-8). List pages are UTF-8 cleanly, so the pipeline doesn't hit this — noted for anyone later adding bill-text ingestion.
- **Catch-all 404 gotcha:** `assembly.nl.ca` serves 200 for every unmapped URL — content-compare needed to confirm feed/API probes.
- **Terms/Licensing:** Crown copyright. Civic-transparency use is standard.
- **Rate limits / auth:** None observed.
- **Difficulty (1–5):** 2 for stages (single table), 4+ for sponsor (not exposed).
- **Scanner module:** `services/scanner/src/legislative/nl_bills.py`.
- **CLI:** `ingest-nl-bills [--ga G --session S | --all-sessions-in-ga G | --all-sessions]`.
- **Stages captured:** First Reading, Second Reading, Committee (with `outcome='amended'` when Amendments=Yes), Third Reading, Royal Assent.
- **Results on first run (GA 51, Session 1):** 12 bills / 31 events / 0 sponsors.

## Hansard / Debates

- **Source URL(s):** https://www.assembly.nl.ca (Hansard section)
- **Format:** HTML + PDF; searchable by keyword.
- **Granularity:** Speaker, statement, timing within session day.
- **Speaker identification:** Name + riding.
- **Difficulty (1–5):** 3.
- **Notes:** Both preliminary (Blues) and edited versions produced. Draft Subject + Speaker Indexes updated through 2025-01-09.

## Voting Records / Divisions

- **Source URL(s):** Embedded in Hansard; Order Papers; Journals
- **Format:** Text tables within Hansard; PDF Order Papers.
- **Roll-call availability:** Named divisions recorded (members called by name).
- **Difficulty (1–5):** 3.
- **Notes:** Partisan legislature (PC, Liberal, NDP, Independents).

## Committee Activity

- **Source URL(s):** https://www.assembly.nl.ca (Standing Committees section); Tabled Documents; Committee Reports
- **Format:** HTML committee pages; audio streaming.
- **Data available:** Membership, agendas, live audio.
- **Overlap with existing scanner:** None.
- **Difficulty (1–5):** 3.
- **Notes:** Committee-level voting not always publicly exposed.

## Existing third-party scrapers

- **opencivicdata/scrapers-ca:** NL scraper status — verify in repo during implementation.
- Other: https://opendata.gov.nl.ca/ — legislative data availability unclear.

## Status

- [x] Research complete
- [x] Schema (no new migration — no sponsor FK currently)
- [x] Ingestion prototyped
- [x] Production ingestion live (2026-04-16) — GA 51, Session 1, 12 bills, stages only
- [ ] Sponsor data (not exposed on any inspected HTML page — would need Order Papers / Hansard scrape)
- [ ] Historical backfill (`--all-sessions` covers ~40 sessions; free to run)
- [ ] Hansard
- [ ] Votes
