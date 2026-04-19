# Alberta — Legislative Data Research

> Standalone research dossier for Alberta. Cross-cutting context (schema, scanner modules, probe hierarchy, research-handoff protocol) lives in [`overview.md`](./overview.md).

**Legislature:** Legislative Assembly of Alberta | **Website:** https://www.assembly.ab.ca | **Seats:** 87 | **Next election:** 2027-10-18

**Status snapshot (2026-04-19):** ✅ **Bills live** for Legislature 31, sessions 1+2 (114 bills / 551 events / 114 sponsors / **100% FK-linked**). Committees pre-existing (`ingest_ab_committees`). Hansard PDF-only — pending PDF tooling investment.

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

## Hansard / Debates

- **Source URL(s):** https://www.assembly.ab.ca/assembly-business/transcripts/hansard-transcripts/compiled-volumes
- **Format:** PDF compiled volumes per Legislature/Session.
- **Granularity:** Session-level volumes; digitized from 1972 forward; searchable from 1986.
- **Speaker identification:** Yes; speaker names in PDF.
- **Difficulty (1–5):** 4.
- **Notes:** Paper publication ceased 2016-01-01 — now PDF-only. **This is the first PDF-heavy jurisdiction we'll hit; investment here in PDF-to-structured-data tooling pays off for other jurisdictions (QC committee reports, NB archives, etc.).**

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
- [ ] Historical backfill (`--all-sessions` covers Legislature 1 onward, ~137 sessions; trivial but not yet run)
- [ ] Hansard PDF parsing
- [ ] Votes/proceedings per-day scrape
