# Yukon — Legislative Data Research

> Standalone research dossier for Yukon. Cross-cutting context (schema, scanner modules, probe hierarchy, research-handoff protocol) lives in [`overview.md`](./overview.md).

**Legislature:** Yukon Legislative Assembly | **Website:** https://yukonassembly.ca | **Seats:** 21 | **Next election:** 2029-11

**Status snapshot (2026-04-19):** ⛔ **Bills blocked.** Cloudflare Bot Management returns HTTP 403 to all non-browser requests, confirmed April 2026. Requires Playwright/Selenium with realistic User-Agent + challenge-cookie handling. Alternative for bill text only: scrape `yukon.ca` government legislation portal.

---

## Bills & Legislation

- **Source URL(s):** https://yukonassembly.ca/house-business/progress-bills ; alternative: https://yukon.ca/en/your-government/legislation/order-legislative-documents
- **Format:** HTML bill pages; PDF text on yukon.ca government portal.
- **Fields captured upstream:** Bill number, sponsor, reading stage, dates.
- **Terms/Licensing:** Crown copyright (Yukon).
- **Rate limits / auth:** **Cloudflare Bot Management returns HTTP 403 to non-browser requests.** Confirmed 2026-04-15 via HEAD request; `cf-mitigated` header present.
- **Difficulty (1–5):** **5**.
- **Notes:** Requires browser automation (Playwright/Selenium) with realistic User-Agent + challenge-cookie handling. Alternative path: scrape legislation text from yukon.ca government portal — bypasses the Assembly site for at least the bill text layer but doesn't help with activity data.

## Hansard / Debates

- **Source URL(s):** https://yukonassembly.ca/travaux-de-lassemblee/hansard ; https://yukonassembly.ca/debates-and-proceedings
- **Format:** HTML full-text searchable (1987+); PDF historical indexes.
- **Granularity:** Speaker, statement, session/date.
- **Speaker identification:** Name + party.
- **Difficulty (1–5):** **5** — same Cloudflare blockade.
- **Notes:** Blues during session, Official Hansard after. Inaccessible to standard HTTP.

## Voting Records / Divisions

- **Source URL(s):** Embedded in Hansard + Orders of the Day
- **Format:** Text summaries within Hansard transcripts.
- **Roll-call availability:** Expected but not independently verifiable due to Cloudflare.
- **Difficulty (1–5):** **5**.
- **Notes:** Partisan legislature but data inaccessible.

## Committee Activity

- **Source URL(s):** yukonassembly.ca committees section (not reachable via curl)
- **Format:** HTML.
- **Data available:** Standing committee listings; meeting dates.
- **Overlap with existing scanner:** None.
- **Difficulty (1–5):** **5**.
- **Notes:** No alternative open-data source identified.

## Existing third-party scrapers

- **opencivicdata/scrapers-ca:** YT scraper may exist but is likely affected by Cloudflare.
- Other: https://open.yukon.ca/data/ — does not host legislative data.

## Status

- [x] Research complete (Cloudflare blockade confirmed)
- [ ] Schema drafted
- [ ] Ingestion prototyped — **deferred until browser automation or alternative data source viable**
- [ ] Production ingestion live

## Research-handoff items (Bills + Hansard)

Per [overview.md](./overview.md) rule #5, YT pipelines are gated on user research. YT is paired with PE in the README's "WAF-blocked" tier. Specific questions to answer before any code is written:

- **Civic-transparency allowlist:** Has the user contacted the Yukon Legislative Assembly (clerk@yla.gov.yk.ca or via the assembly's general contact) about a Cloudflare Bot Management exception for civic-transparency use? CF-mitigated headers are the symptom; the operator can lift the rule if they understand the use case.
- **yukon.ca legislation portal as bill-text fallback:** The dossier flags `yukon.ca/en/your-government/legislation/order-legislative-documents` as an alternative for bill *text* (bypasses the Assembly site). Has the user verified that portal is *not* also Cloudflare-protected, and what fields it exposes vs. what the Assembly site does? Even text-only coverage with no procedural data lets us at least populate `bills.title` + `bills.raw_html`.
- **opencivicdata `ca_yt`:** The dossier notes this scraper "may exist but is likely affected by Cloudflare." Has anyone confirmed this directly? If their scraper has solved the challenge-cookie problem, mining the approach is faster than building from scratch.
- **Browser-automation appetite:** Same question as PE — one Playwright module covering both. If the user wants a lightweight one-jurisdiction trial first, YT (21 MLAs, smaller surface area) is a good test bed.
- **Politician slug column:** When any source unblocks, what's the canonical per-MLA ID? Yukon Legislative Assembly publishes member pages — once reachable, harvest the slug and add `politicians.yt_assembly_slug` per CLAUDE.md convention #1 before building bills/Hansard pipelines.
