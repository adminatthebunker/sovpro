# Legislative Data Research — Cross-Cutting Overview

> Shared methodology, schema log, and progress tracking for the Canadian Political Data legislative-data effort. Per-jurisdiction dossiers (federal + 13 provinces/territories) are siblings of this file in `docs/research/`. See [`README.md`](./README.md) for the index.

**Status:** Active — federal Hansard + NS + ON + BC + QC + AB + NB + NL + NT + NU + **MB** bills layer in production (11 of 14 Canadian legislatures including federal); **QC Hansard live** for 8 sessions (39-1 → 43-2, Jan 2009 → Apr 2026 — **313,345 speeches / 1,278 sittings / 17-year span**) via origin HTML + Wayback CDX fallback for historical URL discovery; **MB Hansard live end-to-end for legs 37-43 (1999-11 → 2026-04, 407,695 speeches across 2,325 sittings / 27-year span)** — era-dispatching parser covers both modern MsoNormal (legs 39+) and Word-97 HTML export (legs 37-38); SK deferred (PDF-only, single-province investment); PEI and YT deferred (CAPTCHA / Cloudflare pair). **Historical-MLA roster backfills** landed for AB (+901 MLAs back to 1906) and MB (+764 MLAs back to 1870), enabling date-windowed speaker resolution across the full Hansard span. Corpus-wide: **2,568,359 speeches / 3,398,197 Qwen3 chunks, 100% embedded.**
**Last updated:** 2026-04-23

## Implementation Log

Tracks what's built so far. See per-jurisdiction "Status" sections for granular progress.

### Schema (normalized, API-facing)
- `0006_legislative_bills.sql` — `legislative_sessions`, `bills`, `bill_events`, `bill_sponsors`. All carry `level` + `province_territory`.
- `0007_bill_html_cache.sql` — `bills.raw_html` + fetched/error columns.
- `0008_bill_sponsor_slug.sql` — sponsor slug/role on `bill_sponsors`; `politicians.nslegislature_slug`.
- `0009_bill_events_rich.sql` — `bill_events.event_type`, `outcome`, `committee_name`; second HTML slot `bills.raw_status_html`; `UNIQUE NULLS NOT DISTINCT` key for dedup.
- `0010_politician_ola_slug.sql` — Ontario profile slug on politicians.
- `0011_politician_lims_member_id.sql` — BC LIMS integer memberId.
- `0012_politician_qc_assnat_id.sql` — Quebec Assemblée nationale integer MNA id.
- `0013_politician_ab_assembly_mid.sql` — Alberta zero-padded text MLA mid.

### Scanner modules (added 2026-04-16)
- `legislative/nb_bills.py` — legnb.ca two-step HTML scrape (list + detail). Sponsor resolution inline, name-based (no numeric MLA id upstream).
- `legislative/nl_bills.py` — single-page bills table at `/HouseBusiness/Bills/ga{GA}session{S}/`. Stages only (sponsor not exposed by any HTML page on assembly.nl.ca).
- `legislative/nt_bills.py` — Drupal 9 list + per-bill detail on ntassembly.ca. Rich 9-field stage vocabulary (includes Standing Committee / Committee of the Whole distinction). No sponsor (consensus government).
- `legislative/nu_bills.py` — single Drupal 9 view at `/bills-and-legislation` with typed `<time>` elements per stage. Small roster (4 bills). Assembly/session via CLI flags.

### Scanner modules
- `legislative/ns_bills.py` — Socrata → bills (phase 1)
- `legislative/ns_bill_pages.py` — HTML fetcher w/ WAF detection (phase 2)
- `legislative/ns_bill_parse.py` — regex parser (phase 3)
- `legislative/on_bills.py` — discovery + fetcher + parser (ON; one module because all three sources are scraped)
- `legislative/sponsor_resolver.py` — bill_sponsors → politicians via slug join + name match; backfills politician slug columns. Jurisdiction-agnostic: add a row to `SOURCE_SYSTEM_TO_SLUG_COL` per province.

### Scanner modules (added 2026-04-22 / 2026-04-23)
- `legislative/ab_former_mlas.py` — AB historical-MLA roster ingester. Iterates `assembly.ab.ca/members/...?legl=N` for N in 1..31, upserts politicians keyed on `ab_assembly_mid`, and creates `politician_terms` per (politician, legislature). Post-pass `resolve_ab_speakers` re-resolves existing speeches via legislature-scoped UPDATE, with `speech_chunks` propagation batched per-legl (mandatory — single-shot UPDATE wedges on autovacuum contention at the 234k-row scale). Migration **0031** tightens `ab_assembly_mid` to a UNIQUE partial index.
- `legislative/mb_former_mlas.py` — MB historical-MLA roster ingester. Parses both the deceased- and living-MLA bio pages with a `<tr>`-based walker that handles two data shapes per page: the deceased-page `<strong>Month DD, YYYY - Month DD, YYYY</strong>` term-range tags and the living-page narrative-event format ("Elected g.e. DATE" / "Resigned DATE"). Name-matches existing MB politicians before inserting so current-roster rows receive historical terms rather than duplicating. Migration **0032** tightens `mb_assembly_slug` to a UNIQUE partial index.
- `legislative/mb_hansard_parse_w97.py` — Manitoba Word-97 HTML-export parser (legs 37-38, 1999-10 → 2007-05). Sister module to `mb_hansard_parse`; `mb_hansard.py` dispatches on `is_word97()` (checks for "Microsoft Word 97" generator meta + absence of `MsoNormal`). Handles uppercase-tag markup, `<B><P>Name:</B>body</P>` speaker pattern (bold wraps across paragraph boundary), split-sitting discovery (`h002_1.html` + `h002_2.html`), CENTER-aligned section headers, and sitting-date extraction from the "Day-of-week, Month DD, YYYY" header. Output-compatible with the modern parser's `ParsedSpeech` so downstream code doesn't care which era it processed.
- `mb_hansard.resolve_mb_speakers_dated` — date-windowed MB speaker resolver. Joins unresolved speeches by normalized surname AND `politician_terms` by `spoken_at ∈ [started_at, ended_at]`, attributes when exactly one politician matches. MB analog of AB's legislature-keyed resolver; unblocks historical surnames (Driedger/Friesen/McFadyen era) that the name-only resolver now rejects as ambiguous after the 820-MLA roster expansion.

### Data on hand (as of 2026-04-16)
| Jurisdiction | Bills | Events | Sponsors | Linked to politicians |
|---|---:|---:|---:|---:|
| NS (all sessions 1995+)  | 3,522 | 3,725 |  14* |  14 |
| ON (P44-S1 only)         |   102 |   595 | 102  | 102 |
| BC (43-2 current)        |    36 |    92 |  36  |  36 |
| QC (43-2 current)        |   102 |   115 |  95  |  94 |
| AB (Legislature 31)      |   114 |   551 | 114  | 114 |
| NB (61-2 current)        |    33 |   111 |  33  |  33 |
| NL (GA 51 S1)            |    12 |    31 |   0\* |   0\* |
| **NT (20-1 current)**    |  **20** |  **82** |   **0\*** |   **0\*** |
| **NU (7-1 current)**     |   **4** |  **24** |   **0\*** |   **0\*** |

\* NS sponsors derived from HTML; only 25/3,522 bill pages cached — see blocker below.
\* NL publishes sponsor nowhere on the bills-list or per-bill page — would require Order Papers / Hansard scrape; deferred.
\* NT and NU are consensus-government territories with no political parties or partisan sponsor model — "sponsor" concept doesn't apply in the traditional sense. Bills and stages are ingested; sponsor table intentionally empty for these two.

### CMS fingerprint pass (2026-04-15)

Followed up the ola.org `?_format=json` discovery with a quick probe of MB / SK / PEI / NL to see whether the Drupal trick generalized. **It didn't.** None of the four are Drupal, and two surfaced unexpected infrastructure findings:

| Jurisdiction | CMS / backend | `?_format=json` | Notes |
|---|---|---|---|
| **MB** | Hand-coded static HTML + PHP on `web2.gov.mb.ca` | No | Bill URLs are `/bills/{P}-{S}/b{NNN}e.php` — predictable, scrapable. Page serves **bill text**, not progression metadata. Status metadata is in `billstatus.pdf` only. |
| **SK** | Bootstrap 5 static site on Azure | No | Primary bill artifact is `progress-of-bills.pdf`. Probing didn't find per-bill HTML URLs; likely PDF-only metadata. Re-rate bills to difficulty 4. |
| **PEI** | **Radware ShieldSquare CAPTCHA** | N/A — blocked | Server header `server: rdwr`, redirects to captcha.perfdrive.com. Same tier as Yukon (Cloudflare) — needs browser automation. **Re-rate to difficulty 5**. |
| **NL** | IIS 5.0 + bootstrap static HTML | No | Very old stack. Worth separate probe for XML/JSON feeds but generic `?_format=` is not meaningful. |

Takeaway: the **Drupal serializer trick is an Ontario-specific win**, not a general shortcut. Going forward, the probe hierarchy before building any scraper is:

  1. **RSS feeds** — check `/rss`, `/feed`, `/feed.xml`, `/rss.xml` at the legislative-business root. NS surfaced a 253-item current-session feed at `/legislative-business/bills-statutes/rss` that gives richer commencement/status text than Socrata in one 120 KB request. Even where a legislature has a better primary API, RSS can complement it for ongoing freshness updates without hitting rate limits or WAFs.
  2. **Drupal `?_format=json`** (ola.org pattern) — every node becomes queryable JSON if the REST module is enabled.
  3. **Iframe-backed content servers** (leg.bc.ca → lims.leg.bc.ca pattern — check both `www.` and other subdomains) — "wrapper" sites often proxy real content from a separate infra tier with its own APIs.
  4. **Open GraphQL endpoints referenced in JS bundles** — search the main SPA bundle for `graphql` / `uri:` / `baseURL`. React/Apollo sites often expose introspectable public schemas.
  5. **Fall back to HTML scraping** only after 1–4 come up empty.

### Research handoff protocol (enforced)

**Before starting any pipeline for MB, SK, NB, NL, QC, AB, NT, or NU, the assistant MUST pause and ask the user for their research pass.** No probing, no migration, no code until the user has either:

  (a) shared their findings (upstream URLs, subdomains, iframe hints, known endpoints), or
  (b) explicitly said "go ahead and probe yourself."

Rationale — two consecutive cases where user-led research beat assistant-driven probing:

  - Ontario: assistant shipped an HTML scraper. User asked "did we look more?" — `?_format=json` on every ola.org node returns JSON (a superset of what we scraped from HTML).
  - BC: assistant probed 30 min, concluded "blocked, needs Playwright." User shared one Hansard URL; that revealed `lims.leg.bc.ca/hdms/file/…` and then `lims.leg.bc.ca/pdms/bills/progress-of-bills/{id}` — re-rating bills from difficulty 5 to 2.

Running through the remaining list is **deferred pending each province's research pass**. This applies even if momentum is towards "just start scraping" — pause and prompt every time.

### Known blockers
- **NS WAF daily budget (~11–14 reqs/IP/window).** Delay-tuning does not help; the counter is per successful request, not per unit time. Two open paths: (a) switch phase-2 fetcher to the `/bill-N/rss` endpoint (served from a different CDN path in probe tests), (b) email `legcomm@novascotia.ca` for a civic-transparency allowlist. Neither started yet. Meanwhile the existing 25-bill cache is sufficient to prove the pipeline. (Per-jurisdiction detail: [`nova-scotia.md`](./nova-scotia.md).)
- ~~**BC bills require browser automation.**~~ **RESOLVED 2026-04-15** — deeper probing found a JSON endpoint at `lims.leg.bc.ca/pdms/bills/progress-of-bills/{sessionId}` that returns the full bill table for a session. Combined with LIMS GraphQL for member/session IDs, BC is now difficulty 2. See [`british-columbia.md`](./british-columbia.md) for the full API shape.
- **Historical ON sponsors** — only current-Parliament MPPs are in our politicians table, so any pre-2024 ON bill would name-match poorly. Not yet a problem (P44-S1 scope) but will be when we backfill.

---

## Context

Canadian Political Data already ingests **basic representative data** (names, parties, ridings, contact info, social media) for all 13 provinces and territories via `services/scanner/src/opennorth.py` (using the Open North Represent API) plus per-province gap fillers in `services/scanner/src/gap_fillers/` for BC, NB, NL, ON, YT, and NU.

For **federal** MPs we additionally mirror rich legislative activity — sponsored bills, recent speeches, biographical detail — from **openparliament.ca** into the `politician_openparliament_cache` table (see `db/migrations/0004_openparliament_cache.sql` and `0005_openparliament_activity.sql`). This is surfaced in the frontend via `PoliticianOpenparliamentTab.tsx` and `PoliticianParliamentTimeline.tsx`. Full federal-pipeline notes: [`federal.md`](./federal.md).

**The gap:** there is no Canadian equivalent to openparliament.ca for any province or territory. Each jurisdiction publishes its own legislative activity data (bills, Hansard, divisions, committees) in its own format — some via APIs, some as structured HTML, some only as PDFs. There is no unified provincial legislative API.

**Purpose of the per-jurisdiction docs:** catalog data sources for four legislative data layers — **bills & legislation, Hansard/debates, voting records, committee activity** — so future sessions can extend per-province ingestion in priority order.

---

## Coverage Matrix

Difficulty rating: **1** = documented API, **2** = undocumented but structured (JSON/XML/Socrata), **3** = predictable HTML scrape, **4** = messy HTML/PDF, **5** = blocked or data unavailable.

| Jurisdiction | Seats | Bills | Hansard | Votes | Committees |
|---|---:|---|---|---|---|
| Ontario | 124 | HTML (3) | HTML (3) | HTML (3) | HTML + some CSV (3) |
| British Columbia | 93 | **LIMS JSON API (2)** | HTML + PDF (3) | HTML (3) | HTML + PDF (3) |
| Quebec | 125 | HTML bilingual (4) | HTML (3) | HTML (4) | HTML (4) |
| Alberta | 87 | HTML (3) | PDF-only (4) | Embedded in Hansard (4) | **HTML (2) — already scraped** |
| Nova Scotia | 55 | **Socrata API (2)** | HTML (3) | HTML Journals (3) | HTML (2) |
| Manitoba | 57 | HTML (3) | HTML (3) | HTML Votes/Proceedings (4) | HTML (2) |
| Saskatchewan | 61 | PDF-only (4) | HTML + PDF (2) | HTML (3) | HTML (2) |
| New Brunswick | 49 | **Socrata + HTML (2)** | HTML + PDF (3) | Embedded in Journals (4) | HTML (2) |
| Newfoundland & Labrador | 40 | HTML (3) | HTML (3) | Embedded in Hansard (3) | HTML (3) |
| Prince Edward Island | 27 | **Radware CAPTCHA (5)** | HTML + A/V (3)* | Embedded in Hansard (3)* | HTML + A/V (3)* |
| Yukon | 21 | **Cloudflare blocked (5)** | **Cloudflare blocked (5)** | **Cloudflare blocked (5)** | **Cloudflare blocked (5)** |
| Northwest Territories | 19 | HTML (3) | HTML + OpenNWT (2) | HTML — **consensus govt** (3) | HTML (3) |
| Nunavut | 22 | HTML (3) | HTML (2) | **N/A — consensus govt, no parties** (4) | HTML (3) |

Notes on the matrix:
- **Alberta committees** are already partially ingested via `ingest_ab_committees` in the current scanner — an existing asset, not a greenfield build.
- **Nova Scotia** and **New Brunswick** have **Socrata-backed open data portals** (`data.novascotia.ca`, `gnb.socrata.com`) which provide SoQL/REST APIs — the lowest-friction starting points in the country.
- **Yukon** is behind Cloudflare Bot Management and returns HTTP 403 to all non-browser requests, confirmed April 2026. Requires Playwright/Selenium or an alternative source (`yukon.ca` government legislation portal may be an option for bill text only).
- **NT and NU** are consensus governments — non-partisan MLAs, no party lines, and "voting records" in the partisan sense largely don't apply. Schema will need to accommodate this.

---

## Priority Ranking

Recommended implementation order, balancing data accessibility against political/civic impact:

1. **Nova Scotia** — Socrata bills API is the easiest wedge in the country. Small assembly (55 seats) lets us iterate the full 4-layer schema end-to-end quickly. Use as the **reference implementation** that other provinces follow.
2. **Ontario** — 124 MPPs makes it the largest civic impact. HTML-only but well-structured. Pair well after NS prototype so we can see how the schema stretches from "API-driven" to "scrape-driven".
3. **British Columbia** — Third largest by population. Solid Hansard archives (1970+) and structured Votes and Proceedings. Similar difficulty profile to ON.
4. **Alberta** — Can build on existing `ingest_ab_committees` and extend to bills/Hansard/votes. Hansard is PDF-only, so this forces us to solve PDF extraction before tackling other PDF-heavy jurisdictions.
5. **Saskatchewan** — Well-indexed Hansard (subject + speaker indexes from 1996) and structured Votes and Proceedings. Moderate assembly size (61).
6. **New Brunswick** — Socrata portal means bills are cheap; rest of data is moderate HTML. Complements NS in proving the Socrata ingestion code generalizes.
7. **Quebec** — High civic salience but bilingual complexity (FR primary, EN translations) adds implementation cost. Worth tackling once the pipeline has been hardened elsewhere.
8. **Manitoba** — Standard HTML scrape. No special difficulties or opportunities.
9. **Newfoundland & Labrador** — Small (40 seats), standard HTML.
10. **Prince Edward Island** — Smallest partisan assembly (27). Good data but low civic impact; save for last among partisan legislatures.
11. **Northwest Territories** — Requires schema changes for consensus government. Hansard is excellent (OpenNWT portal already exists as a third-party interface — possibly reusable).
12. **Nunavut** — Same consensus-govt schema concerns as NT. Smallest political footprint.
13. **Yukon** — Deferred until we either invest in browser automation or find an alternative data source. Cloudflare will not be solved by changing scraper headers.

Threshold decisions to revisit after NS + ON prototypes:
- Does our schema generalize cleanly from API (NS) to HTML (ON)?
- Do we need per-province adapter modules or a single configurable scraper?
- How do we model consensus-government jurisdictions (NT/NU) without forcing fake "party" columns?

---

## Cross-Cutting Resources

### Existing civic-tech infrastructure to reuse

- **[opencivicdata/scrapers-ca](https://github.com/opencivicdata/scrapers-ca)** — the group behind Open North maintains provincial scrapers here. Module coverage known:
  - `ca_on`, `ca_bc`, `ca_qc`, `ca_ab`, `ca_ns`, `ca_mb`, `ca_nb` — present
  - `ca_sk` — not currently active / disabled
  - NT/NU/YT/NL/PE — status TBD; verify per-province during implementation
  - **Action:** before writing any new scraper, check the corresponding `ca_XX` dir in this repo. Most of what we need for rep ingestion is already solved there; legislative-activity scrapers may or may not exist per province.
- **[OpenNWT](https://hansard.opennwt.ca/)** — third-party NWT Hansard interface that is already more user-friendly than the official site. Possibly suitable as our upstream for NT Hansard rather than scraping `ntlegislativeassembly.ca` directly.
- **Nova Scotia open data portal** — [data.novascotia.ca](https://data.novascotia.ca) (Socrata). Bills dataset: https://data.novascotia.ca/Government-Administration/Bills-introduced-in-the-Nova-Scotia-Legislature/iz5x-dzyf
- **New Brunswick open data portal** — https://gnb.socrata.com (Socrata).

### Legal / licensing considerations

All provincial legislative text is **Crown copyright**. Policies vary:

- **Ontario**: Non-commercial reproduction with attribution. Legislative text freely reproducible.
- **BC**: `bclaws.gov.bc.ca` under Queen's Printer License permits commercial and non-commercial use with attribution. `leg.bc.ca` page content is stricter (personal use only without written consent).
- **Quebec**: More restrictive — commercial use requires prior permission.
- **Nova Scotia, New Brunswick**: Open Government Licence — redistribution permitted with attribution.
- **Others**: standard Crown copyright; non-commercial, educational, and transparency uses widely accepted.

**Action before first ingestion:** confirm redistribution rights per-province, especially for QC. Our use case (civic transparency, non-commercial) is the cleanest case but should be documented per-province.

### Federal precedent to mirror

- `db/migrations/0004_openparliament_cache.sql` — federal cache table structure
- `db/migrations/0005_openparliament_activity.sql` — federal activity feed
- `services/api/src/routes/openparliament.ts` — detail + activity HTTP routes
- `services/frontend/src/components/PoliticianOpenparliamentTab.tsx` — frontend consumption pattern
- `services/frontend/src/components/PoliticianParliamentTimeline.tsx` — timeline/activity UI

A provincial solution could either (a) add per-province cache tables or (b) introduce normalized bills/votes/speeches tables with `level` + `province_territory` discriminators. Decision deferred to schema design session.

Federal pipeline detail: [`federal.md`](./federal.md).

---

## Next Steps

After this overview is reviewed, the natural follow-up sessions are:

1. **Schema design session** — propose SQL migrations for `bills`, `speeches`, `votes`, `committees`, `committee_memberships` tables with `level` + `province_territory` discriminators, informed by the `politician_openparliament_cache` precedent. Decide consensus-government modeling for NT/NU.
2. **Nova Scotia reference-implementation session** — prototype ingestion against the Socrata bills API + HTML Hansard/committees, end-to-end including frontend display. This validates the schema under real data before scaling out.
3. **Ontario rollout session** — second implementation, stressing the HTML-scraping path of the schema.
4. **Cross-cutting tooling session** — decide PDF-to-structured-data approach (needed for AB Hansard; beneficial for BC, NB archival material, QC committee reports).

Each jurisdiction's Status checklist (in its own dossier) should be updated in-place as work progresses.
