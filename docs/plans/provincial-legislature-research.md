# Provincial Legislature Data Research

**Status:** Active — NS + ON + BC + QC + AB + NB + NL + **NT + NU** bills layer in production (9 of 13 Canadian sub-national legislatures); MB and SK deferred (PDF-only pair); PEI and YT deferred (CAPTCHA/Cloudflare).
**Last updated:** 2026-04-16

## Implementation Log

Tracks what's built so far. See per-jurisdiction "Status" sections for
granular progress.

### Schema (normalized, API-facing)
- `0006_legislative_bills.sql` — `legislative_sessions`, `bills`,
  `bill_events`, `bill_sponsors`. All carry `level` +
  `province_territory`.
- `0007_bill_html_cache.sql` — `bills.raw_html` + fetched/error columns.
- `0008_bill_sponsor_slug.sql` — sponsor slug/role on `bill_sponsors`;
  `politicians.nslegislature_slug`.
- `0009_bill_events_rich.sql` — `bill_events.event_type`, `outcome`,
  `committee_name`; second HTML slot `bills.raw_status_html`;
  `UNIQUE NULLS NOT DISTINCT` key for dedup.
- `0010_politician_ola_slug.sql` — Ontario profile slug on politicians.
- `0011_politician_lims_member_id.sql` — BC LIMS integer memberId.
- `0012_politician_qc_assnat_id.sql` — Quebec Assemblée nationale integer MNA id.
- `0013_politician_ab_assembly_mid.sql` — Alberta zero-padded text MLA mid.

### Scanner modules (added 2026-04-16)
- `legislative/nb_bills.py` — legnb.ca two-step HTML scrape (list + detail).
  Sponsor resolution inline, name-based (no numeric MLA id upstream).
- `legislative/nl_bills.py` — single-page bills table at
  `/HouseBusiness/Bills/ga{GA}session{S}/`. Stages only (sponsor not
  exposed by any HTML page on assembly.nl.ca).
- `legislative/nt_bills.py` — Drupal 9 list + per-bill detail on
  ntassembly.ca. Rich 9-field stage vocabulary (includes Standing
  Committee / Committee of the Whole distinction). No sponsor
  (consensus government).
- `legislative/nu_bills.py` — single Drupal 9 view at
  `/bills-and-legislation` with typed `<time>` elements per stage.
  Small roster (4 bills). Assembly/session via CLI flags.

### Scanner modules
- `legislative/ns_bills.py` — Socrata → bills (phase 1)
- `legislative/ns_bill_pages.py` — HTML fetcher w/ WAF detection (phase 2)
- `legislative/ns_bill_parse.py` — regex parser (phase 3)
- `legislative/on_bills.py` — discovery + fetcher + parser (ON; one
  module because all three sources are scraped)
- `legislative/sponsor_resolver.py` — bill_sponsors → politicians via
  slug join + name match; backfills politician slug columns. Jurisdiction-
  agnostic: add a row to `SOURCE_SYSTEM_TO_SLUG_COL` per province.

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

\* NS sponsors derived from HTML; only 25/3,522 bill pages cached — see
blocker below.
\* NL publishes sponsor nowhere on the bills-list or per-bill page — would
require Order Papers / Hansard scrape; deferred.
\* NT and NU are consensus-government territories with no political
parties or partisan sponsor model — "sponsor" concept doesn't apply in
the traditional sense. Bills and stages are ingested; sponsor table
intentionally empty for these two.

### CMS fingerprint pass (2026-04-15)

Followed up the ola.org `?_format=json` discovery with a quick probe of
MB / SK / PEI / NL to see whether the Drupal trick generalized. **It
didn't.** None of the four are Drupal, and two surfaced unexpected
infrastructure findings:

| Jurisdiction | CMS / backend | `?_format=json` | Notes |
|---|---|---|---|
| **MB** | Hand-coded static HTML + PHP on `web2.gov.mb.ca` | No | Bill URLs are `/bills/{P}-{S}/b{NNN}e.php` — predictable, scrapable. Page serves **bill text**, not progression metadata. Status metadata is in `billstatus.pdf` only. |
| **SK** | Bootstrap 5 static site on Azure | No | Primary bill artifact is `progress-of-bills.pdf`. Probing didn't find per-bill HTML URLs; likely PDF-only metadata. Re-rate bills to difficulty 4. |
| **PEI** | **Radware ShieldSquare CAPTCHA** | N/A — blocked | Server header `server: rdwr`, redirects to captcha.perfdrive.com. Same tier as Yukon (Cloudflare) — needs browser automation. **Re-rate to difficulty 5**. |
| **NL** | IIS 5.0 + bootstrap static HTML | No | Very old stack. Worth separate probe for XML/JSON feeds but generic `?_format=` is not meaningful. |

Takeaway: the **Drupal serializer trick is an Ontario-specific win**,
not a general shortcut. Going forward, the probe hierarchy before
building any scraper is:

  1. **RSS feeds** — check `/rss`, `/feed`, `/feed.xml`, `/rss.xml` at
     the legislative-business root. NS surfaced a 253-item current-
     session feed at `/legislative-business/bills-statutes/rss` that
     gives richer commencement/status text than Socrata in one 120 KB
     request. Even where a legislature has a better primary API, RSS
     can complement it for ongoing freshness updates without hitting
     rate limits or WAFs.
  2. **Drupal `?_format=json`** (ola.org pattern) — every node
     becomes queryable JSON if the REST module is enabled.
  3. **Iframe-backed content servers** (leg.bc.ca → lims.leg.bc.ca
     pattern — check both `www.` and other subdomains) — "wrapper"
     sites often proxy real content from a separate infra tier with
     its own APIs.
  4. **Open GraphQL endpoints referenced in JS bundles** — search the
     main SPA bundle for `graphql` / `uri:` / `baseURL`. React/Apollo
     sites often expose introspectable public schemas.
  5. **Fall back to HTML scraping** only after 1–4 come up empty.

### Research handoff protocol (enforced)

**Before starting any pipeline for MB, SK, NB, NL, QC, AB, NT, or NU,
the assistant MUST pause and ask the user for their research pass.**
No probing, no migration, no code until the user has either:

  (a) shared their findings (upstream URLs, subdomains, iframe hints,
      known endpoints), or
  (b) explicitly said "go ahead and probe yourself."

Rationale — two consecutive cases where user-led research beat
assistant-driven probing:

  - Ontario: assistant shipped an HTML scraper. User asked "did we
    look more?" — `?_format=json` on every ola.org node returns JSON
    (a superset of what we scraped from HTML).
  - BC: assistant probed 30 min, concluded "blocked, needs
    Playwright." User shared one Hansard URL; that revealed
    `lims.leg.bc.ca/hdms/file/…` and then
    `lims.leg.bc.ca/pdms/bills/progress-of-bills/{id}` — re-rating
    bills from difficulty 5 to 2.

Running through the remaining list is **deferred pending each
province's research pass**. This applies even if momentum is
towards "just start scraping" — pause and prompt every time.

### Known blockers
- **NS WAF daily budget (~11–14 reqs/IP/window).** Delay-tuning does not
  help; the counter is per successful request, not per unit time. Two
  open paths: (a) switch phase-2 fetcher to the `/bill-N/rss` endpoint
  (served from a different CDN path in probe tests), (b) email
  `legcomm@novascotia.ca` for a civic-transparency allowlist. Neither
  started yet. Meanwhile the existing 25-bill cache is sufficient to
  prove the pipeline.
- ~~**BC bills require browser automation.**~~ **RESOLVED 2026-04-15** —
  deeper probing found a JSON endpoint at `lims.leg.bc.ca/pdms/bills/
  progress-of-bills/{sessionId}` that returns the full bill table for a
  session. Combined with LIMS GraphQL for member/session IDs, BC is now
  difficulty 2. See the BC section for the full API shape.
- **Historical ON sponsors** — only current-Parliament MPPs are in our
  politicians table, so any pre-2024 ON bill would name-match poorly.
  Not yet a problem (P44-S1 scope) but will be when we backfill.

---

## Context

SovereignWatch already ingests **basic representative data** (names, parties, ridings, contact info, social media) for all 13 provinces and territories via `services/scanner/src/opennorth.py` (using the Open North Represent API) plus per-province gap fillers in `services/scanner/src/gap_fillers/` for BC, NB, NL, ON, YT, and NU.

For **federal** MPs we additionally mirror rich legislative activity — sponsored bills, recent speeches, biographical detail — from **openparliament.ca** into the `politician_openparliament_cache` table (see `db/migrations/0004_openparliament_cache.sql` and `0005_openparliament_activity.sql`). This is surfaced in the frontend via `PoliticianOpenparliamentTab.tsx` and `PoliticianParliamentTimeline.tsx`.

**The gap:** there is no Canadian equivalent to openparliament.ca for any province or territory. Each jurisdiction publishes its own legislative activity data (bills, Hansard, divisions, committees) in its own format — some via APIs, some as structured HTML, some only as PDFs. There is no unified provincial legislative API.

**Purpose of this doc:** catalog per-jurisdiction data sources for four legislative data layers — **bills & legislation, Hansard/debates, voting records, committee activity** — so future sessions can design a schema and begin per-province ingestion in priority order.

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

---

## Ontario

**Legislature:** Legislative Assembly of Ontario | **Website:** https://www.ola.org | **Seats:** 124 | **Next election:** 2030-04-11

### Bills & Legislation
- **Source URL(s):** https://www.ola.org/en/legislative-business/bills/current ; https://www.ola.org/en/legislative-business/bills/all
- **Format:** HTML web pages; no structured API. Per-bill PDFs available.
- **Fields captured upstream:** Bill number, title, status (reading stages), sponsoring MPP
- **Terms/Licensing:** Crown copyright (Queen's Printer for Ontario). Non-commercial reproduction permitted with attribution. Legislative text freely reproducible.
- **Rate limits / auth:** None documented
- **Difficulty (1–5):** 3
- **Notes:** Bills indexed by Parliament and session. URL structure is predictable. No JSON/XML export.

### Hansard / Debates
- **Source URL(s):** https://www.ola.org/en/legislative-business/hansard-search ; https://www.ola.org/en/legislative-business/house-hansard-index
- **Format:** HTML searchable archive; no API
- **Granularity:** Per-session daily transcripts (Hansard volumes)
- **Speaker identification:** By MPP name; searchable
- **Difficulty (1–5):** 3
- **Notes:** Full-text searchable from 1974-03-05 onward.

### Voting Records / Divisions
- **Source URL(s):** https://www.ola.org/en/legislative-business/house-documents/parliament-44/session-1 (Votes and Proceedings)
- **Format:** HTML Votes and Proceedings; also PDF downloads
- **Roll-call availability:** Yes, from 43rd Parliament forward, with member names and votes
- **Difficulty (1–5):** 3
- **Notes:** Divisions embedded in daily Votes and Proceedings. Consistent URL structure by Parliament/session/date.

### Committee Activity
- **Source URL(s):** https://www.ola.org/en/legislative-business/committees ; https://www.ola.org/en/legislative-business/committees/documents
- **Format:** HTML transcripts; some committees publish CSV exports (e.g. Standing Committee on Finance and Economic Affairs)
- **Data available:** Memberships, meetings (transcripts by date), reports (PDF/HTML), transcripts (HTML)
- **Overlap with existing scanner:** None
- **Difficulty (1–5):** 3
- **Notes:** 9 Standing Committees. Transcripts include member remarks, votes, and staff lists.

### Existing Third-Party Scrapers
- opencivicdata/scrapers-ca: `ca_on` module exists ([github.com/opencivicdata/scrapers-ca](https://github.com/opencivicdata/scrapers-ca))
- Other: Open North Represent API (reps only, not legislative activity)

### ★ Drupal JSON serializer (discovered 2026-04-15, after initial HTML pipeline shipped)

Every node on `www.ola.org` supports `?_format=json` — the Drupal core
REST serializer. This turns the entire bills / sponsors / members graph
into a queryable JSON API without any auth:

```
https://www.ola.org/en/legislative-business/bills/parliament-44/session-1/bill-104?_format=json
https://www.ola.org/en/node/9608366?_format=json          # sponsor node
https://www.ola.org/en/members/all/john-fraser?_format=json # member node
```

**Fields available on a bill node** (superset of what we scrape):

- `field_bill_number`, `field_long_title`, `field_short_title`,
  `field_current_status`
- `field_sponsor` → reference to a bill_sponsor node (which has
  `field_member` → member node, with `field_member_id` — a stable
  **integer ID** we can store on politicians for exact-match linking,
  same trick as BC's `lims_member_id`)
- `field_status_table` — same malformed HTML table we parse, but now
  arriving inside JSON (still needs the tr-split fix)
- `field_has_divisions` — boolean, signals whether vote roll-calls exist
- `field_debates` — array of Hansard debate node refs
- `field_acts`, `field_acts_affected` — ties into legislation graph
- `field_versions` — bill-text version history
- `field_type` → taxonomy term (government vs. private member's bill)
- `field_parliament`, `field_parliament_sessions`
- `field_latest_activity_date`

**Member node also exposes `field_member_id`** (integer, stable) plus
riding, party, dates of service, gender, contact group, expense
disclosure links.

**Why it matters going forward:**
- Richer data for free — divisions boolean, type taxonomy, acts-affected
  graph — that HTML scraping made awkward to get.
- Integer `field_member_id` enables exact sponsor→politician joins
  (same pattern as BC's LIMS `memberId`). Replace slug-fuzz resolution
  with a single-column FK.
- Likely applies to **Saskatchewan, Manitoba, PEI, NL** too if they're
  Drupal-backed — worth probing `?_format=json` on the first bill page
  of each as a fast triage before writing HTML scrapers.

**Not migrating the current ON pipeline** (102 bills, 595 events,
sponsors all linked) because the HTML pipeline works and the data is
already good. Switch to the JSON serializer when we:
  (a) backfill earlier ON Parliaments, or
  (b) want the divisions / acts-affected / versions data we skipped.

### Status
- [x] Research complete
- [x] Schema drafted (0006 — shared across jurisdictions)
- [x] Ingestion prototyped (`ingest-on-bills` P44-S1: 102 bills, 595 events, 102 sponsors)
- [x] Production ingestion live (current session; backfill earlier Parliaments deferred)
- [x] Sponsor→politician resolver working (102/102 linked)
- [ ] JSON-serializer pipeline (optional rewrite; HTML pipeline works fine for current scope)

---

## British Columbia

**Legislature:** Legislative Assembly of British Columbia | **Website:** https://www.leg.bc.ca | **Seats:** 93 | **Next election:** 2028-10-21

### Bills & Legislation
- **Source URL(s):** https://www.leg.bc.ca/parliamentary-business/bills-and-legislation ; https://www.bclaws.gov.bc.ca/civix/content/bills/
- **Format:** HTML (leg.bc.ca); enacted legislation on bclaws.gov.bc.ca under Queen's Printer License
- **Fields captured upstream:** Bill number, title, reading stages, sponsor
- **Terms/Licensing:** Crown copyright. BC Laws permits commercial + non-commercial use under Queen's Printer License. leg.bc.ca page content restricted to personal use without written consent.
- **Rate limits / auth:** None documented
- **Difficulty (1–5):** **2 (re-rated 2026-04-15 — upgraded from initial 5).** After discovering the React SPA, deeper probing turned up a **structured JSON endpoint** at `https://lims.leg.bc.ca/pdms/bills/progress-of-bills/{sessionId}` that returns the full bill table as JSON. No auth, no SPA rendering needed. The earlier-found LIMS GraphQL gives us session IDs. This makes BC the second-easiest bills source in Canada after NS Socrata.
- **Notes:** See "★ Bills API — LIMS PDMS" subsection below for endpoint shape and integration plan. bclaws.gov.bc.ca is still authoritative for enacted bill text; PDMS `files[].path` links into `/ldp/{session}/{reading}/{name}.htm` which can be resolved via `lims.leg.bc.ca/hdms/file/...` (same file-serving pattern as Hansard).

### ★ Bills API — LIMS PDMS (discovered 2026-04-15)

Root endpoint: `GET https://lims.leg.bc.ca/pdms/bills/progress-of-bills/{sessionId}` → JSON array of bills for that session. Session IDs come from LIMS GraphQL `allSessions`.

**Sample record shape:**
```json
{
  "billId": 1028,
  "billNumber": 1,
  "title": "An Act to Ensure the Supremacy of Parliament",
  "firstReading": "2026-02-14",
  "secondReading": null,
  "committeeReading": null,
  "thirdReading": null,
  "reportReading": null,
  "royalAssent": null,
  "chapterNumber": null,
  "billTypeId": 1,
  "memberId": 236,
  "memberAlias": null,
  "titleChanged": false,
  "reinstated": false,
  "ruledOutOfOrder": false,
  "files": { "nodes": [
    { "readingTypeName": "1st Reading", "readingTypeId": 1,
      "readingDate": "2026-02-14",
      "fileName": "gov01-1.htm",
      "path": "/ldp/38th2nd/1st_read/gov01-1.htm" }
  ] }
}
```

**What it gives us directly into our schema:**
- `bills.bill_number` ← `billNumber`
- `bills.title` ← `title`
- `bills.status` / `bills.status_changed_at` ← derived from latest non-null reading date
- `bill_events` rows ← one per non-null reading date (first/second/committee/third/report/royal_assent)
- `bill_sponsors.politician_id` ← **already resolved** via `memberId`, which is the integer LIMS member ID. We can ingest BC members via LIMS GraphQL `allMembers` and store `lims_member_id INT` on politicians → exact-int join replaces slug/name fuzz entirely.

**Session enumeration:**
- Current session: ID 206 = 43rd Parliament, 2nd Session, 36 bills as of 2026-04-15.
- Previous session: ID 173 = 43rd-1st (2025), ~185 bills.
- Entire BC historical: `allSessions` returns every session back to 1872 (id 171). PDMS appears to serve all of them.

**Retrieval characteristics:**
- Single request per session (no paging; 36 bills ≈ 5 KB, 500-bill sessions ≈ 50 KB).
- Polite pacing still recommended (~1 req/sec) but total traffic to cover all BC history is tiny — 140 sessions × ~50 KB ≈ 7 MB.
- No WAF observed on `lims.leg.bc.ca` across probe traffic.

**This downgrades BC from "blocked until we build Playwright" to "API-driven pipeline" — similar effort to NS Socrata, but with **more** structured data per bill.**

### Hansard / Debates
- **Source URL(s):** https://www.leg.bc.ca/parliamentary-business/legislation-debates-proceedings ; https://www.leg.bc.ca/learn/discover-your-legislature/house-documents/hansard
- **Format:** HTML; certified PDF Official Reports; "Blues" preliminary drafts within ~1 hour of speaking
- **Granularity:** Per-session daily debates
- **Speaker identification:** By MLA name; searchable
- **Difficulty (1–5):** 3
- **Notes:** Archives from 1970 onward.

### Voting Records / Divisions
- **Source URL(s):** https://www.leg.bc.ca/parliamentary-business/overview/43rd-parliament/2nd-session/votes-and-proceedings
- **Format:** HTML Votes and Proceedings
- **Roll-call availability:** Yes, recorded divisions with member names
- **Difficulty (1–5):** 3
- **Notes:** No dedicated voting API. Consistent URL structure per Parliament/session.

### Committee Activity
- **Source URL(s):** https://www.leg.bc.ca/parliamentary-business/committees ; https://www.leg.bc.ca/parliamentary-business/committees/committee-meetings
- **Format:** HTML agendas + transcripts; Hansard Blues + Official Report PDF; audio/video webcasts
- **Data available:** Memberships, meetings (schedules + transcripts), reports, transcripts, webcasts
- **Overlap with existing scanner:** None
- **Difficulty (1–5):** 3
- **Notes:** Select Standing + Special Committees. Memberships set at session start by Committee of Selection.

### Existing Third-Party Scrapers
- opencivicdata/scrapers-ca: `ca_bc` module exists
- Other: None identified

### ★ Member Data — LIMS GraphQL (discovered 2026-04-15)

Independent of the bills question, BC exposes a **public, fully-introspectable
GraphQL API** at `https://lims.leg.bc.ca/graphql` (POST). No auth, no
documented rate limit, CORS permissive. Discovered by mining the
`dyn.leg.bc.ca` React SPA bundle for an Apollo client `uri`.

**Schema scope:** 110 root query fields covering members, parliaments,
sessions, constituencies, parties, ministers, executive councils, clerks,
legislative assistants. Notable `all*` entry points:

- `allMembers`, `allMemberParliaments`, `allMemberElections`,
  `allMemberRoles`, `allMemberResignations`, `allMemberTypes`,
  `allMemberConstituencies`
- `allParliaments`, `allSessions`, `allParties`
- `allConstituencies`, `allConstituencyOffices`
- `allExecutiveCouncils`, `allExecutiveStaffs`, `allMinisters`
- `allClerks`, `allLegislativeAssistants`, `allRoles`, `allRoleTypes`
- `allSocialMediaLinks`

**What it does NOT expose:** bills, Hansard, divisions, committees —
this is a member/role/org data API, not a legislative-activity one.

**Why it's valuable anyway:**
1. Richer than Open North for BC — includes role history
   (minister → critic → private member transitions), executive council
   membership over time, committee postings.
2. Single query fetches what Open North's Represent API returns
   plus ~10× more structured metadata.
3. Can replace / augment our BC gap filler (`gap_fillers/bc.py`) once
   we decide how to fold this into our politicians table.
4. Introspection means no schema guessing — `__schema { queryType
   { fields { name } } }` returns everything.

**Minimum probe query:**
```bash
curl -s -X POST -H "Content-Type: application/json" \
  --data '{"query":"{ allMembers(first: 5) { nodes { id firstName lastName } } }"}' \
  https://lims.leg.bc.ca/graphql
```

**Later-work to capture:** a BC-members enrichment that hits this API
to populate politician role history + constituency-office detail in
our DB. Independent of the bills pipeline; could be done at any time.

### Status
- [x] Research complete — partially superseded 2026-04-15 (see re-rating)
- [ ] Schema drafted — shared schema applies; no new migration needed
- [ ] Ingestion prototyped — **blocked on Playwright track** for bills
- [ ] Production ingestion live — bills blocked; member-data via LIMS
      GraphQL is available as a separate workstream

---

## Quebec

**Legislature:** National Assembly of Quebec (Assemblée nationale du Québec) | **Website:** https://www.assnat.qc.ca | **Seats:** 125 | **Next election:** 2026-10-05

### Bills & Legislation ✅ LIVE (2026-04-16)
- **Primary source — donneesquebec.ca CSV:** https://www.donneesquebec.ca/recherche/dataset/projets-de-loi — official open-data export, refreshed **daily**, CC-BY-NC-4.0. One HTTP GET returns all 613 bills across current + previous legislature. Columns: `Numero_projet_loi`, `Titre_projet_loi`, `Type_projet_loi`, `Derniere_etape_franchie`, `Date_derniere_etape`, `No_legislature`, `Date_debut_legislature`, `Date_fin_legislature`, `No_session`.
- **Stage timeline — RSS:** https://www.assnat.qc.ca/fr/rss/SyndicationRSS-210.html — XML feed fires on every stage transition in the current session. Same pattern as NS RSS (`ns_rss.py`). Parses ~25 items/day.
- **Sponsor resolution — bill detail HTML:** pattern `https://www.assnat.qc.ca/{en|fr}/travaux-parlementaires/projets-loi/projet-loi-{N}-{parl}-{session}.html`. Sponsor is one `<a href="/en/deputes/{slug}-{id}/index.html">` — numeric MNA id → `politicians.qc_assnat_id` FK lookup (**no name-fuzz**, same leverage as BC's `lims_member_id`).
- **MNA roster:** server-side HTML at `/en/deputes/index.html`. 125 MNAs embedded with numeric ids in URL slugs. Single-page scrape populates `politicians.qc_assnat_id` — run once, enables exact-match sponsor joins forever.
- **Session attribution caveat:** CSV tags carried-over bills with the *current* session (`No_session`) but bill-detail URLs use the *origin* session. The title always prefixes with "{parl}-{sess} PL {N} ..." — parse that prefix to decide the real session, else the detail URL 404s.
- **Private bills ("D'intérêt privé", 58/613, numbered 99x+):** different URL scheme we couldn't pin down. Pipeline skips them in the sponsor-fetch phase; they still get CSV bill rows but no sponsor.
- **Scanner modules:** `qc_mnas.py` (roster), `qc_bills.py` (CSV + RSS + detail HTML).
- **CLI:** `enrich-qc-mna-ids`, `ingest-qc-bills`, `ingest-qc-bills-rss`, `fetch-qc-bill-sponsors`.
- **Terms/Licensing:** CC-BY-NC-4.0 on the open-data CSV. Detail pages are Crown copyright. Civic-transparency use is non-commercial so both fit.
- **Rate limits / auth:** None observed. No WAF signals. 1.5s delay used for politeness in sponsor fetch.
- **Difficulty (1–5):** 2 (CSV makes it trivially easy; one 404 footgun from the session-origin quirk).
- **Results on first run:** 102 bills / 115 events / 95 sponsors (**94 / 95 FK-linked to politicians** = 99%).
- **Outstanding probes:** Private-bill URL scheme; votes registry (see Voting Records below — registry page is ASP.NET postback, deferred).

### Hansard / Debates
- **Source URL(s):** https://www.assnat.qc.ca/fr/travaux-parlementaires/journaux-debats/ ; https://www.assnat.qc.ca/en/travaux-parlementaires/journaux-debats/
- **Format:** HTML searchable archive from 1963
- **Granularity:** Per-session daily transcripts (Journal des débats)
- **Speaker identification:** By MNA name; searchable
- **Difficulty (1–5):** 3
- **Notes:** Bilingual (FR primary). Committee-level Hansard (Journal des débats) per committee.

### Voting Records / Divisions
- **Source URL(s):** https://www.assnat.qc.ca/fr/lien/12779.html (Register of Recorded Divisions); also embedded in Journal des débats and bill pages
- **Format:** HTML scattered across multiple pages
- **Roll-call availability:** Yes; member names and votes
- **Difficulty (1–5):** 4
- **Notes:** No dedicated voting API. Requires navigating bill/session structure.

### Committee Activity
- **Source URL(s):** https://www.assnat.qc.ca/fr/travaux-parlementaires/commissions/index.html ; https://www.assnat.qc.ca/en/deputes/fonctions-parlementaires-ministerielles/composition-commissions.html ; individual committee pages at `/travaux-parlementaires/commissions/{committee-code}/`
- **Format:** HTML + PDF reports; committee Hansard in HTML
- **Data available:** Memberships, meetings, reports, transcripts (Journal des débats per committee)
- **Overlap with existing scanner:** None
- **Difficulty (1–5):** 4
- **Notes:** Committees (commissions) organized by legislature/session code. Bilingual.

### Existing Third-Party Scrapers
- opencivicdata/scrapers-ca: `ca_qc` module exists
- Other: None identified

### Status
- [x] Research complete
- [x] Schema drafted (migration `0012_politician_qc_assnat_id.sql`)
- [x] Ingestion prototyped
- [x] Production ingestion live (2026-04-16) — bills + events + sponsors
- [ ] Hansard / Journaux des débats
- [ ] Voting records (registry page is ASP.NET postback — needs form-aware scrape or Playwright)
- [ ] Committee meetings + reports
- [ ] Private-bill URL scheme

---

## Alberta

**Legislature:** Legislative Assembly of Alberta | **Website:** https://www.assembly.ab.ca | **Seats:** 87 | **Next election:** 2027-10-18

### Bills & Legislation ✅ LIVE (2026-04-16)
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

### Hansard / Debates
- **Source URL(s):** https://www.assembly.ab.ca/assembly-business/transcripts/hansard-transcripts/compiled-volumes
- **Format:** PDF compiled volumes per Legislature/Session
- **Granularity:** Session-level volumes; digitized from 1972 forward; searchable from 1986
- **Speaker identification:** Yes; speaker names in PDF
- **Difficulty (1–5):** 4
- **Notes:** Paper publication ceased 2016-01-01 — now PDF-only. **This is the first PDF-heavy jurisdiction we'll hit; investment here in PDF-to-structured-data tooling pays off for other jurisdictions (QC committee reports, NB archives, etc.).**

### Voting Records / Divisions
- **Source URL(s):** https://www.assembly.ab.ca/assembly-business/assembly-dashboard (order paper / daily records)
- **Format:** Embedded in daily order papers and Hansard — no standalone interface
- **Roll-call availability:** Recorded votes appear in Hansard when divisions occur
- **Difficulty (1–5):** 4
- **Notes:** Likely requires extracting from Hansard PDFs once Hansard pipeline exists.

### Committee Activity
- **Source URL(s):** https://www.assembly.ab.ca/assembly-business/committees ; https://www.assembly.ab.ca/assembly-business/committees/committee-reports
- **Format:** HTML committee pages with reports; minutes via Legislature Library (librarysearch.assembly.ab.ca)
- **Data available:** Memberships, standing committee list, committee reports
- **Overlap with existing scanner:** **`ingest_ab_committees` already implemented** — this is our one existing provincial asset in the legislative-activity layer. Any AB work here extends that.
- **Difficulty (1–5):** 2 (already scraped)
- **Notes:** Contact: library.requests@assembly.ab.ca, 780-427-2473.

### Existing Third-Party Scrapers
- opencivicdata/scrapers-ca: `ca_ab` module exists (provincial + municipal: Calgary, Edmonton, Grande Prairie, Lethbridge, Strathcona, Wood Buffalo)
- Other: None identified

### Status
- [x] Research complete
- [x] Schema drafted (migration `0013_politician_ab_assembly_mid.sql`)
- [x] Ingestion prototyped
- [x] Production ingestion live (2026-04-16) — Legislature 31 sessions 1+2, 114 bills
- [x] Committees (pre-existing `ingest_ab_committees`)
- [ ] Historical backfill (`--all-sessions` covers Legislature 1 onward, ~137 sessions; trivial but not yet run)
- [ ] Hansard PDF parsing
- [ ] Votes/proceedings per-day scrape

---

## Nova Scotia

**Legislature:** House of Assembly | **Website:** https://nslegislature.ca | **Seats:** 55 | **Next election:** By 2029-12-07

### Bills & Legislation
- **Source URL(s):** https://nslegislature.ca/legislative-business/ ; https://data.novascotia.ca/Government-Administration/Bills-introduced-in-the-Nova-Scotia-Legislature/iz5x-dzyf
- **Format:** **Socrata API** (JSON, CSV, SoQL queries) via data.novascotia.ca
- **Fields captured upstream:** Bill title, status, first/assented-to versions (1995–96 to present), bill types
- **Terms/Licensing:** **Open Government Licence (Nova Scotia)** — permissive, attribution only
- **Rate limits / auth:** Public app token recommended but not required. Rate limits generous; documented at dev.socrata.com.
- **Difficulty (1–5):** **2** — easiest bills source in the country
- **Notes:** **Start here. Socrata's SoQL query language is a JSON/REST API — this is the closest provincial analog to federal LEGISinfo. Build the bills schema against this source first.**

### ★ RSS feed (discovered 2026-04-15)

Complement to Socrata: `https://nslegislature.ca/legislative-business/bills-statutes/rss` serves an RSS 2.0 feed of every bill in the current session (253 items for 65-1, ~122 KB, single request). Delivers richer status text than Socrata — commencement clauses, exceptions, effective-date caveats in the `<description>` field.

**What RSS gives us:**
- Status text: `"Royal Assent - October 2, 2025; Commencement: October 3, 2025 except:..."` — commencement + exception detail that Socrata's terse `description` field never had.
- pubDate on each status change.
- Single-request polling suitable for a daily cron.

**What RSS doesn't give us:**
- Historical bills (current session only).
- Sponsor slug (still needs HTML bill-page fetch).

**Integration:** `legislative/ns_rss.py` + CLI `ingest-ns-bills-rss`. Matches RSS items to existing Socrata-ingested bills via the canonical source_id; merges RSS payload into `bills.raw.rss`; refreshes `bills.status` and `bills.status_changed_at`; appends `bill_events` rows for the current stage. Fully idempotent, no WAF impact.

### Hansard / Debates
- **Source URL(s):** https://nslegislature.ca/legislative-business/hansard-debates ; https://nslegislature.ca/about/supporting-offices/hansard-reporting-services
- **Format:** HTML transcripts from 1994 forward; PDF index; video/audio webcasts
- **Granularity:** Daily; includes committee Hansards
- **Speaker identification:** Yes
- **Difficulty (1–5):** 3
- **Notes:** Transcripts published next morning after sitting. Contact: Hansard Reporting Services, 902-424-7990.

### Voting Records / Divisions
- **Source URL(s):** https://nslegislature.ca/ruling-topics/votes ; https://nslegislature.ca/legislative-business/hansard-dates/
- **Format:** House Journals with voice votes and recorded roll calls
- **Roll-call availability:** Yes when roll call is demanded (two members required per rules)
- **Difficulty (1–5):** 3
- **Notes:** Divisions entered in minutes. No standalone export.

### Committee Activity
- **Source URL(s):** https://nslegislature.ca/legislative-business/committees/standing ; https://nslegislature.ca/about/supporting-offices/legislative-committees-office
- **Format:** HTML pages with meeting archives, membership, public submissions
- **Data available:** Standing committees (Community Services, Health, Human Resources, Natural Resources, Public Accounts, Veterans Affairs); schedules, transcripts
- **Overlap with existing scanner:** None
- **Difficulty (1–5):** 2
- **Notes:** Contact: legcomm@novascotia.ca, 902-424-4432.

### Existing Third-Party Scrapers
- opencivicdata/scrapers-ca: `ca_ns` module exists (provincial + Halifax, Cape Breton)
- Other: None identified

### Status
- [x] Research complete
- [x] Schema drafted (0006 — same as ON)
- [x] Ingestion prototyped (Socrata → 3,522 bills across 24 sessions)
- [~] Production ingestion partial — bill rows complete; per-bill HTML
       fetch blocked by WAF budget (25/3,522 cached). RSS-feed pivot
       or email allowlist pending.
- [x] Sponsor→politician resolver working (14/14 parsed sponsors linked)

---

## Manitoba

**Legislature:** Legislative Assembly of Manitoba | **Website:** https://www.gov.mb.ca/legislature | **Seats:** 57 | **Next election:** By 2027-10-05

### Bills & Legislation ⏸️ DEFERRED (PDF-dependent)
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

### Hansard / Debates
- **Source URL(s):** https://www.gov.mb.ca/legislature/hansard/ ; https://www.gov.mb.ca/legislature/hansard/index_homepage.html
- **Format:** HTML indexed by session/year; subject + member + public-presenter indexes
- **Granularity:** Daily from 1958 to present
- **Speaker identification:** Yes; speaker indexes available
- **Difficulty (1–5):** 3
- **Notes:** Transcripts available within 24 hours of sitting.

### Voting Records / Divisions
- **Source URL(s):** https://www.gov.mb.ca/legislature/business/votes_proceedings.html
- **Format:** Votes and Proceedings documents; typically embedded in daily records
- **Roll-call availability:** Variable format
- **Difficulty (1–5):** 4
- **Notes:** No standalone export.

### Committee Activity
- **Source URL(s):** https://www.gov.mb.ca/legislature/committees/ ; https://www.gov.mb.ca/legislature/committees/membership.html
- **Format:** HTML pages with meeting notices, broadcasts, reports, clerk contacts
- **Data available:** Non-permanent rotating membership; broadcasts; reports
- **Overlap with existing scanner:** None
- **Difficulty (1–5):** 2
- **Notes:** Meetings via Zoom Webinar. Standing committees can't meet Jan–Aug except Public Accounts.

### Existing Third-Party Scrapers
- opencivicdata/scrapers-ca: `ca_mb` module exists (provincial + Winnipeg municipal)
- Other: None identified

### Status
- [x] Research complete
- [ ] Schema drafted
- [ ] Ingestion prototyped
- [ ] Production ingestion live

---

## Saskatchewan

**Legislature:** Legislative Assembly of Saskatchewan | **Website:** https://www.legassembly.sk.ca | **Seats:** 61 | **Next election:** By 2028-10

### Bills & Legislation
- **Source URL(s):** https://www.legassembly.sk.ca/legislative-business/bills/
- **Format:** HTML, by Legislature and session
- **Fields captured upstream:** Bill title, status, process info (First Reading, Specified Bills, Regulations)
- **Terms/Licensing:** Crown copyright
- **Rate limits / auth:** None documented
- **Difficulty (1–5):** 3
- **Notes:** Alternative legislation source: freelaws.gov.sk.ca (bills and acts, not activity).

### Hansard / Debates
- **Source URL(s):** https://www.legassembly.sk.ca/legislative-business/debates-hansard/ ; https://docs.legassembly.sk.ca
- **Format:** HTML + PDF; digitized back to 1947
- **Granularity:** Daily
- **Speaker identification:** Yes; subject + speaker indexes for 1996 forward
- **Difficulty (1–5):** 2
- **Notes:** Contact: hansard@legassembly.sk.ca, 306-787-1175. Downloadable indexes are a major asset.

### Voting Records / Divisions
- **Source URL(s):** https://www.legassembly.sk.ca/legislative-business/minutes-votes/
- **Format:** HTML Minutes (Votes and Proceedings); digitized March 2003 forward
- **Roll-call availability:** Yes
- **Difficulty (1–5):** 3
- **Notes:** Contact: journals@legassembly.sk.ca, 306-787-0421.

### Committee Activity
- **Source URL(s):** https://www.legassembly.sk.ca/legislative-business/legislative-committees/ ; https://docs.legassembly.sk.ca
- **Format:** HTML; committee docs on docs.legassembly.sk.ca
- **Data available:** Four standing committees (Crown and Central Agencies, Economy, Intergovernmental Affairs and Justice, Public Accounts); House Management, Private Bills, Privileges
- **Overlap with existing scanner:** None
- **Difficulty (1–5):** 2
- **Notes:** Contact: committees_branch@legassembly.sk.ca, 306-787-9930.

### Existing Third-Party Scrapers
- opencivicdata/scrapers-ca: **No `ca_sk` module currently active** (disabled or never built)
- Other: freelaws.gov.sk.ca (acts + bills text, not procedural)

### Status
- [x] Research complete
- [ ] Schema drafted
- [ ] Ingestion prototyped
- [ ] Production ingestion live

---

## New Brunswick

**Legislature:** Legislative Assembly of New Brunswick | **Website:** https://www.legnb.ca | **Seats:** 49 | **Next election:** By 2028-10-16

### Bills & Legislation ✅ LIVE (2026-04-16)
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

### Hansard / Debates
- **Source URL(s):** https://www.legnb.ca/en/house-business/hansard
- **Format:** HTML + PDF; archives from 1900 to present
- **Granularity:** Daily; includes committee proceedings
- **Speaker identification:** Yes
- **Difficulty (1–5):** 3
- **Notes:** Committee Hansards on request via Legislative Library. Contact: 506-453-2338.

### Voting Records / Divisions
- **Source URL(s):** https://www.legnb.ca/en (House Business section; embedded in Journals)
- **Format:** Embedded in Journals/House proceedings
- **Roll-call availability:** In minutes when recorded
- **Difficulty (1–5):** 4
- **Notes:** No dedicated voting export. Extract from Journals or Hansard.

### Committee Activity
- **Source URL(s):** https://www.legnb.ca/en/committees ; https://www.legnb.ca/en/committees/{id}
- **Format:** HTML pages with meeting schedules, reports, membership
- **Data available:** Standing committees (Procedure/Privileges, Law Amendments, Social Policy, Economic Policy, Estimates and Fiscal Policy, Public Accounts); no active select committees
- **Overlap with existing scanner:** None
- **Difficulty (1–5):** 2
- **Notes:** Standing committees meet year-round.

### Existing Third-Party Scrapers
- opencivicdata/scrapers-ca: `ca_nb` module exists (provincial + Fredericton, Moncton, Saint John)
- Other: https://www1.gnb.ca/leglibbib (Legislative Library reference)

### Status
- [x] Research complete
- [x] Schema (no new migration — name-based resolution against existing `politicians` rows)
- [x] Ingestion prototyped
- [x] Production ingestion live (2026-04-16) — Legislature 61, Session 2, 33 bills
- [ ] Historical backfill (`--all-sessions-in-legislature` works; not yet run)
- [ ] Hansard
- [ ] Votes / Journals

---

## Newfoundland & Labrador

**Legislature:** House of Assembly | **Website:** https://www.assembly.nl.ca | **Seats:** 40 | **Next election:** 2029-10

### Bills & Legislation ✅ LIVE (2026-04-16)
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

### Hansard / Debates
- **Source URL(s):** https://www.assembly.nl.ca (Hansard section)
- **Format:** HTML + PDF; searchable by keyword
- **Granularity:** Speaker, statement, timing within session day
- **Speaker identification:** Name + riding
- **Difficulty (1–5):** 3
- **Notes:** Both preliminary (Blues) and edited versions produced. Draft Subject + Speaker Indexes updated through 2025-01-09.

### Voting Records / Divisions
- **Source URL(s):** Embedded in Hansard; Order Papers; Journals
- **Format:** Text tables within Hansard; PDF Order Papers
- **Roll-call availability:** Named divisions recorded (members called by name)
- **Difficulty (1–5):** 3
- **Notes:** Partisan legislature (PC, Liberal, NDP, Independents).

### Committee Activity
- **Source URL(s):** https://www.assembly.nl.ca (Standing Committees section); Tabled Documents; Committee Reports
- **Format:** HTML committee pages; audio streaming
- **Data available:** Membership, agendas, live audio
- **Overlap with existing scanner:** None
- **Difficulty (1–5):** 3
- **Notes:** Committee-level voting not always publicly exposed.

### Existing Third-Party Scrapers
- opencivicdata/scrapers-ca: NL scraper status — verify in repo during implementation
- Other: https://opendata.gov.nl.ca/ — legislative data availability unclear

### Status
- [x] Research complete
- [x] Schema (no new migration — no sponsor FK currently)
- [x] Ingestion prototyped
- [x] Production ingestion live (2026-04-16) — GA 51, Session 1, 12 bills, stages only
- [ ] Sponsor data (not exposed on any inspected HTML page — would need Order Papers / Hansard scrape)
- [ ] Historical backfill (`--all-sessions` covers ~40 sessions; free to run)
- [ ] Hansard
- [ ] Votes

---

## Prince Edward Island

**Legislature:** Legislative Assembly of Prince Edward Island | **Website:** https://www.assembly.pe.ca | **Seats:** 27 | **Next election:** 2027-10-04

### Bills & Legislation
- **Source URL(s):** https://www.assembly.pe.ca/legislative-business/house-records/bills
- **Format:** HTML with full text; searchable
- **Fields captured upstream:** Bill number, title, sponsor, status, date
- **Terms/Licensing:** Crown copyright
- **Rate limits / auth:** None documented
- **Difficulty (1–5):** 3
- **Notes:** Predictable URL structure.

### Hansard / Debates
- **Source URL(s):** https://www.assembly.pe.ca/legislative-business/house-records/debates ; legacy http://www.gov.pe.ca/paroatom/index.php/hansard
- **Format:** Searchable HTML + audio/video archives
- **Granularity:** Speaker, statement, timestamp
- **Speaker identification:** Name + riding
- **Difficulty (1–5):** 3
- **Notes:** Hansard service began 1996. Both text and A/V records maintained.

### Voting Records / Divisions
- **Source URL(s):** Embedded in Hansard
- **Format:** Text tables in debate transcripts
- **Roll-call availability:** Named votes during divisions
- **Difficulty (1–5):** 3
- **Notes:** Partisan (Liberals, PCs, Greens).

### Committee Activity
- **Source URL(s):** https://www.assembly.pe.ca/legislative-business/house-records — Committee Documents; Calendar of Committee Meetings
- **Format:** HTML agendas; video/audio archives
- **Data available:** Standing committees (Education & Economic Growth, Health & Social Development, Natural Resources, Public Accounts, Legislative Management, Rules/Regulations); minutes; video transcripts
- **Overlap with existing scanner:** None
- **Difficulty (1–5):** 3
- **Notes:** Committee voting not always explicitly recorded in accessible form.

### Existing Third-Party Scrapers
- opencivicdata/scrapers-ca: PE scraper status — verify in repo
- Other: https://data.princeedwardisland.ca/ — legislative datasets not prominent

### Status
- [x] Research complete
- [ ] Schema drafted
- [ ] Ingestion prototyped
- [ ] Production ingestion live

---

## Yukon

**Legislature:** Yukon Legislative Assembly | **Website:** https://yukonassembly.ca | **Seats:** 21 | **Next election:** 2029-11

### Bills & Legislation
- **Source URL(s):** https://yukonassembly.ca/house-business/progress-bills ; alternative: https://yukon.ca/en/your-government/legislation/order-legislative-documents
- **Format:** HTML bill pages; PDF text on yukon.ca government portal
- **Fields captured upstream:** Bill number, sponsor, reading stage, dates
- **Terms/Licensing:** Crown copyright (Yukon)
- **Rate limits / auth:** **Cloudflare Bot Management returns HTTP 403 to non-browser requests.** Confirmed 2026-04-15 via HEAD request; `cf-mitigated` header present.
- **Difficulty (1–5):** **5**
- **Notes:** Requires browser automation (Playwright/Selenium) with realistic User-Agent + challenge-cookie handling. Alternative path: scrape legislation text from yukon.ca government portal — bypasses the Assembly site for at least the bill text layer but doesn't help with activity data.

### Hansard / Debates
- **Source URL(s):** https://yukonassembly.ca/travaux-de-lassemblee/hansard ; https://yukonassembly.ca/debates-and-proceedings
- **Format:** HTML full-text searchable (1987+); PDF historical indexes
- **Granularity:** Speaker, statement, session/date
- **Speaker identification:** Name + party
- **Difficulty (1–5):** **5** — same Cloudflare blockade
- **Notes:** Blues during session, Official Hansard after. Inaccessible to standard HTTP.

### Voting Records / Divisions
- **Source URL(s):** Embedded in Hansard + Orders of the Day
- **Format:** Text summaries within Hansard transcripts
- **Roll-call availability:** Expected but not independently verifiable due to Cloudflare
- **Difficulty (1–5):** **5**
- **Notes:** Partisan legislature but data inaccessible.

### Committee Activity
- **Source URL(s):** yukonassembly.ca committees section (not reachable via curl)
- **Format:** HTML
- **Data available:** Standing committee listings; meeting dates
- **Overlap with existing scanner:** None
- **Difficulty (1–5):** **5**
- **Notes:** No alternative open-data source identified.

### Existing Third-Party Scrapers
- opencivicdata/scrapers-ca: YT scraper may exist but is likely affected by Cloudflare
- Other: https://open.yukon.ca/data/ — does not host legislative data

### Status
- [x] Research complete (Cloudflare blockade confirmed)
- [ ] Schema drafted
- [ ] Ingestion prototyped — **deferred until browser automation or alternative data source viable**
- [ ] Production ingestion live

---

## Northwest Territories

**Legislature:** Legislative Assembly of the Northwest Territories | **Website:** https://www.ntlegislativeassembly.ca | **Seats:** 19 | **Next election:** 2027-10

### Bills & Legislation ✅ LIVE (2026-04-16)
- **Primary source:** Drupal 9 at `ntassembly.ca`.
  - **List page** `/documents-proceedings/bills` — current-assembly bills linked by slug.
  - **Detail page** `/documents-proceedings/bills/{slug}` — `node--type-bills-and-legislation` with `field--name-field-*-date` wrappers on every stage, each rendering a `<details>` with "Completed on {date}" status text.
- **Rich stage vocabulary (9 distinct date fields):** first-reading, second-reading, to-standing-comm, standing-comm-amend, to-whole-comm, whole-comm-amend, from-whole-comm, third-reading, assent. Distinguishes Standing Committee from Committee of the Whole (both mapped to canonical `committee` stage with distinct `committee_name` + `event_type` for dedup).
- **Assembly / session** parsed from `field--name-field-assembly-session` on each bill page — e.g. "20th Assembly, 1st Session" → session_id.
- **No sponsor data** (NT is consensus government — no partisan sponsor model). Pipeline writes bills + stage events only; no `bill_sponsors` rows.
- **Historical backfill** visible in list-page nav (16th–20th Assembly); per-assembly URL routing not yet mapped, so only current assembly ingested by default.
- **Canonical domain:** `www.ntassembly.ca` (redirects from `ntlegislativeassembly.ca`). Nginx/1.20.1, no WAF.
- **Scanner module:** `services/scanner/src/legislative/nt_bills.py`.
- **CLI:** `ingest-nt-bills [--delay S]`.
- **Results on first run (20th Assembly, 1st Session):** 20 bills / 82 events / 0 sponsors (by design).

### Hansard / Debates
- **Source URL(s):** https://www.ntlegislativeassembly.ca/documents-proceedings/hansard (official) ; https://hansard.opennwt.ca/ (third-party friendlier interface) ; https://lanwt.i8.dgicloud.com/hansard (Legislative Library)
- **Format:** Searchable HTML + PDF archives
- **Granularity:** Speaker, statement, date, session
- **Speaker identification:** Name (all MLAs non-partisan in consensus model)
- **Difficulty (1–5):** 2
- **Notes:** **OpenNWT is a pre-existing third-party Hansard portal — evaluate whether it's easier to mirror than the official site.**

### Voting Records / Divisions
- **Source URL(s):** https://www.ntlegislativeassembly.ca/documents-proceedings/proceedings (Votes and Proceedings)
- **Format:** HTML/PDF Votes and Proceedings summaries
- **Roll-call availability:** Format TBD (may be summary-only given consensus model)
- **Difficulty (1–5):** 3
- **Notes:** **CONSENSUS GOVERNMENT — all MLAs independent; no party whips; decisions by consensus or standing count. Traditional partisan "voting records" do not apply.** Schema must model this (e.g., `voting_model` column on a legislative_session table, or skip votes table entirely for NT/NU).

### Committee Activity
- **Source URL(s):** https://www.ntlegislativeassembly.ca/documents-proceedings/committees-reports ; standing committee listings
- **Format:** HTML/PDF reports; agendas; dates
- **Data available:** Seven standing committees; reports, schedules, rosters
- **Overlap with existing scanner:** None
- **Difficulty (1–5):** 3
- **Notes:** Committee work is collaborative.

### Existing Third-Party Scrapers
- opencivicdata/scrapers-ca: NT scraper status — verify in repo
- Other: OpenNWT (https://opennwt.ca/) — possible upstream, evaluate data model

### Status
- [x] Research complete
- [x] Schema (no new migration — no sponsor FK)
- [x] Ingestion prototyped
- [x] Production ingestion live (2026-04-16) — 20th Assembly, 1st Session, 20 bills
- [ ] Historical backfill (assemblies 16–19 visible in nav; URL routing not mapped)
- [ ] Hansard (deferred — evaluate opennwt.ca mirror)
- [ ] Votes (consensus-government model — different schema needed)

---

## Nunavut

**Legislature:** Legislative Assembly of Nunavut | **Website:** https://www.assembly.nu.ca | **Seats:** 22 | **Next election:** 2029-10

### Bills & Legislation ✅ LIVE (2026-04-16)
- **Primary source:** Drupal 9 view at `/bills-and-legislation` — single HTML table, one row per bill with typed `<time datetime="…">` elements in each stage column. Only 4 bills in current (7th Assembly, 1st Session) as of 2026-04.
- **Column vocabulary (Drupal `views-field-field-*`):** title, date-of-notice, first-reading, second-reading, reported (Standing Committee), reported-whole (Committee of the Whole), third-reading, date-of-assent.
- **No sponsor data** (consensus government, 22 non-partisan MLAs). Pipeline writes bills + events only.
- **Assembly/session absent from the HTML** — the Drupal view doesn't print it. CLI takes `--assembly N --session S` overrides; default = `7-1` (current as of 2026-04).
- **Drupal `?_format=json` is disabled** — returns 406 Not Acceptable with only `html` as supported format. Unlike Ontario, NU hasn't enabled the JSON serializer. HTML scrape is the only route.
- **Cost:** one HTTP GET for the whole current session.
- **Scanner module:** `services/scanner/src/legislative/nu_bills.py`.
- **CLI:** `ingest-nu-bills [--assembly N] [--session S]`.
- **Results on first run (7th Assembly, 1st Session):** 4 bills / 24 events / 0 sponsors (by design). All 4 are appropriation acts, all at Royal Assent.

### Hansard / Debates
- **Source URL(s):** https://www.assembly.nu.ca/hansard ; Legislative Library: library@assembly.nu.ca, 867-975-5132
- **Format:** Searchable HTML; "Blues" (unedited) available next morning
- **Granularity:** Speaker, statement, date
- **Speaker identification:** Name (all non-partisan)
- **Difficulty (1–5):** 2
- **Notes:** Bilingual publication (Inuktitut + English). Records from 1999-04-01.

### Voting Records / Divisions
- **Source URL(s):** Hansard + Legislative Library proceedings
- **Format:** Summary/textual within Hansard
- **Roll-call availability:** Unclear — **consensus government (no political parties, 22 non-partisan MLAs)** means partisan voting records don't exist in the traditional sense. Decisions often by consensus or acclamation.
- **Difficulty (1–5):** 4 (conceptual rather than technical difficulty)
- **Notes:** Schema design question: do we skip the votes table for NU, or model consensus/acclamation as a vote type? Recommend the latter for completeness. Contact Legislative Library to clarify formal division procedures.

### Committee Activity
- **Source URL(s):** https://www.assembly.nu.ca (Standing and Special Committees)
- **Format:** HTML committee pages; reports
- **Data available:** Memberships, schedules, reports
- **Overlap with existing scanner:** None
- **Difficulty (1–5):** 3
- **Notes:** Committees fulfill legislation review, policy exam, spending review. More procedural flexibility than Assembly floor.

### Existing Third-Party Scrapers
- opencivicdata/scrapers-ca: NU scraper status — verify in repo; may lack vote coverage due to consensus model
- Other: https://www.gov.nu.ca/ — general government, not legislative-specific open data

### Status
- [x] Research complete
- [x] Schema (no new migration — no sponsor FK)
- [x] Ingestion prototyped
- [x] Production ingestion live (2026-04-16) — 7th Assembly, 1st Session, 4 bills
- [ ] Assembly/session auto-detection (currently hard-coded default via CLI flag)
- [ ] Hansard
- [ ] Votes (consensus-government modeling question remains open)

---

## Next Steps

After this doc is reviewed, the natural follow-up sessions are:

1. **Schema design session** — propose SQL migrations for `bills`, `speeches`, `votes`, `committees`, `committee_memberships` tables with `level` + `province_territory` discriminators, informed by the `politician_openparliament_cache` precedent. Decide consensus-government modeling for NT/NU.
2. **Nova Scotia reference-implementation session** — prototype ingestion against the Socrata bills API + HTML Hansard/committees, end-to-end including frontend display. This validates the schema under real data before scaling out.
3. **Ontario rollout session** — second implementation, stressing the HTML-scraping path of the schema.
4. **Cross-cutting tooling session** — decide PDF-to-structured-data approach (needed for AB Hansard; beneficial for BC, NB archival material, QC committee reports).

Each jurisdiction's Status checklist above should be updated in-place as work progresses.
