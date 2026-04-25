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

## Research-handoff items (Bills + Hansard)

Per [overview.md](./overview.md) rule #5, PE pipelines are gated on user research. PE is paired with YT in the README's "WAF-blocked" tier. Specific questions to answer before any code is written:

- **Civic-transparency allowlist:** Has the user contacted the Legislative Assembly directly (clerk@assembly.pe.ca or equivalent) to request the same kind of allowlist that worked for the NS WAF conversation? An IP-whitelist or Bot Management exception is dramatically cheaper than browser automation per CLAUDE.md convention #6 (rate-limit + cache persistently).
- **Alternative LegCo feeds:** Does PE publish bills/Hansard through any third-party aggregator the user is aware of (Open North, opencivicdata, university repositories, princeedwardisland.ca open data portal)? Even a stale source is useful for backfill while the live source is blocked.
- **Browser-automation appetite:** PE + YT share the same blocker tier. The plan would be one Playwright-based scraper module reusable across both. Has the user committed to that infrastructure investment, or do we wait for an allowlist? It's a non-trivial dependency add (Playwright + Chromium binary in the scanner image).
- **Hansard-only fallback:** The dossier notes Hansard service began 1996 with both text and A/V records. If the bills page is genuinely permanently blocked but Hansard is reachable through a different subdomain or CDN, partial coverage is better than nothing.
- **Politician slug column:** PE roster has 27 MLAs. If/when a source unblocks, what's the canonical per-MLA ID? Add as `politicians.pe_assembly_slug` per CLAUDE.md convention #1 before building bills/Hansard pipelines.
