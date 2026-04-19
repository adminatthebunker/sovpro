# Prince Edward Island — Legislative Data Research

> Standalone research dossier for Prince Edward Island. Cross-cutting context (schema, scanner modules, probe hierarchy, research-handoff protocol) lives in [`overview.md`](./overview.md).

**Legislature:** Legislative Assembly of Prince Edward Island | **Website:** https://www.assembly.pe.ca | **Seats:** 27 | **Next election:** 2027-10-04

**Status snapshot (2026-04-19):** ⛔ **Bills blocked.** Server header `server: rdwr`, redirects to `captcha.perfdrive.com` — Radware ShieldSquare CAPTCHA. Same tier as Yukon (Cloudflare). **Re-rated to difficulty 5**. Bill content URLs themselves look ingestible if we can clear the bot challenge.

---

## Bills & Legislation

- **Source URL(s):** https://www.assembly.pe.ca/legislative-business/house-records/bills
- **Format:** HTML with full text; searchable.
- **Fields captured upstream:** Bill number, title, sponsor, status, date.
- **Terms/Licensing:** Crown copyright.
- **Rate limits / auth:** **Radware ShieldSquare CAPTCHA blocks all non-browser requests.** Confirmed during the 2026-04-15 CMS fingerprint pass.
- **Difficulty (1–5):** **5** (re-rated up from initial 3).
- **Notes:** Predictable URL structure — would be easy to ingest if we could get past the CAPTCHA. Same tooling investment (Playwright with realistic User-Agent + cookie handling) would unlock both PEI and Yukon.

## Hansard / Debates

- **Source URL(s):** https://www.assembly.pe.ca/legislative-business/house-records/debates ; legacy http://www.gov.pe.ca/paroatom/index.php/hansard
- **Format:** Searchable HTML + audio/video archives.
- **Granularity:** Speaker, statement, timestamp.
- **Speaker identification:** Name + riding.
- **Difficulty (1–5):** 3 (assuming CAPTCHA is bypassable; otherwise 5).
- **Notes:** Hansard service began 1996. Both text and A/V records maintained.

## Voting Records / Divisions

- **Source URL(s):** Embedded in Hansard
- **Format:** Text tables in debate transcripts.
- **Roll-call availability:** Named votes during divisions.
- **Difficulty (1–5):** 3 (assuming CAPTCHA is bypassable; otherwise 5).
- **Notes:** Partisan (Liberals, PCs, Greens).

## Committee Activity

- **Source URL(s):** https://www.assembly.pe.ca/legislative-business/house-records — Committee Documents; Calendar of Committee Meetings
- **Format:** HTML agendas; video/audio archives.
- **Data available:** Standing committees (Education & Economic Growth, Health & Social Development, Natural Resources, Public Accounts, Legislative Management, Rules/Regulations); minutes; video transcripts.
- **Overlap with existing scanner:** None.
- **Difficulty (1–5):** 3 (assuming CAPTCHA is bypassable; otherwise 5).
- **Notes:** Committee voting not always explicitly recorded in accessible form.

## Existing third-party scrapers

- **opencivicdata/scrapers-ca:** PE scraper status — verify in repo.
- Other: https://data.princeedwardisland.ca/ — legislative datasets not prominent.

## Status

- [x] Research complete (Radware ShieldSquare blockade confirmed)
- [ ] Schema drafted
- [ ] Ingestion prototyped — **deferred until browser automation or alternative data source viable**
- [ ] Production ingestion live
