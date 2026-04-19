# British Columbia — Legislative Data Research

> Standalone research dossier for British Columbia. Cross-cutting context (schema, scanner modules, probe hierarchy, research-handoff protocol) lives in [`overview.md`](./overview.md).

**Legislature:** Legislative Assembly of British Columbia | **Website:** https://www.leg.bc.ca | **Seats:** 93 | **Next election:** 2028-10-21

**Status snapshot (2026-04-19):** ✅ **Bills live** via LIMS PDMS JSON. ✅ **Hansard live** via LIMS HDMS debates JSON + HTML — full 23-session backfill P38-S4 → P43-S2 (2008-2026), **197,888 speeches** / 87.4% politician-linked, 464 distinct speakers. Historical MLA roster enriched (376 rows) from LIMS GraphQL. Both re-rated down from initial difficulty — Bills 5→2, Hansard 3→2. Votes / committees not yet built.

---

## Bills & Legislation

- **Source URL(s):** https://www.leg.bc.ca/parliamentary-business/bills-and-legislation ; https://www.bclaws.gov.bc.ca/civix/content/bills/
- **Format:** HTML on leg.bc.ca; enacted legislation on bclaws.gov.bc.ca under Queen's Printer License. **Real bills data lives at LIMS PDMS** (see below).
- **Fields captured upstream:** Bill number, title, reading stages, sponsor.
- **Terms/Licensing:** Crown copyright. BC Laws permits commercial + non-commercial use under Queen's Printer License. leg.bc.ca page content restricted to personal use without written consent.
- **Rate limits / auth:** None documented.
- **Difficulty (1–5):** **2** (re-rated 2026-04-15 — upgraded from initial 5). After discovering the React SPA, deeper probing turned up a structured JSON endpoint at `https://lims.leg.bc.ca/pdms/bills/progress-of-bills/{sessionId}` that returns the full bill table as JSON. No auth, no SPA rendering needed. The earlier-found LIMS GraphQL gives us session IDs. This makes BC the second-easiest bills source in Canada after NS Socrata.
- **Notes:** See "★ Bills API — LIMS PDMS" below for endpoint shape and integration plan. bclaws.gov.bc.ca is still authoritative for enacted bill text; PDMS `files[].path` links into `/ldp/{session}/{reading}/{name}.htm` which can be resolved via `lims.leg.bc.ca/hdms/file/...` (same file-serving pattern as Hansard).

## ★ Bills API — LIMS PDMS (discovered 2026-04-15)

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

**This downgrades BC from "blocked until we build Playwright" to "API-driven pipeline" — similar effort to NS Socrata, but with more structured data per bill.**

## Hansard / Debates

- **Source URL(s):** Discovery via `https://lims.leg.bc.ca/hdms/debates/{parl}{sess}` (JSON listing of every sitting). Transcripts: `https://lims.leg.bc.ca/hdms/file/Debates/{parl}{sess}/{YYYYMMDD}{am|pm}-{House-Blues.htm|Hansard-n{NNN}.html}`. The Drupal page at leg.bc.ca is a PDF-viewer wrapper — the real HTML lives on LIMS HDMS.
- **Format:** HTML with rich semantic markup (`SpeakerBegins`, `Speaker-Name`, `Time-Stamp`, `Proceedings-Group`, etc.). Both Blues (draft, ~1 hr post-adjournment) and Final HDMS variants share the same class taxonomy — Final hyphenates class names, Blues does not; single parser handles both.
- **Speaker identification:** By MLA name (no stable per-turn IDs). Sitting Speaker's name lives in the HTML header and is extracted per-sitting to resolve "The Speaker" attributions.
- **Difficulty (1–5):** **2** (re-rated 2026-04-19 — downgraded from initial 3). The JSON debate-index endpoint eliminates URL discovery entirely; markup is stable and class-driven.
- **Notes:** Archives from 1970 onward. Discovery endpoint covers every session LIMS has indexed. Deputy Speaker / Committee Chair attributions (~10% of rows) remain role-only in v1 — LIMS GraphQL's role data isn't reliably scoped to the current session.

## ★ Hansard pipeline — LIMS HDMS (live 2026-04-19)

Current scope: 43rd Parliament, 2nd Session. **40 House sittings, ~4,800 speeches, 89.5% politician-linked** (97.5% of named MLAs — the remainder are legitimate non-MLA guests).

**Upsert key strategy:** `speeches.source_url = hansard-bc.canonical/Debates/{parl}{sess}/{YYYYMMDD}{am|pm}-Hansard.html` — a synthesized canonical URL stable across Blues and Final. Real URLs live in `speeches.raw.bc_hansard.{blues_url, final_url, variant}`. Final overwrites Blues in place via `ON CONFLICT DO UPDATE` using this canonical key.

**Ingest commands:**

```bash
# Full session backfill
python -m src ingest-bc-hansard --parliament 43 --session 2

# Smoke-test one URL
python -m src ingest-bc-hansard --parliament 43 --session 2 \
    --url https://lims.leg.bc.ca/hdms/file/Debates/43rd2nd/20260415pm-House-Blues.htm

# Post-pass resolver (after expanding BC MLA roster or fixing name-normalisation)
python -m src resolve-bc-speakers
```

**Module layout:**

- `services/scanner/src/legislative/bc_hansard.py` — discovery, fetch, upsert orchestrator, speaker lookup, post-pass resolver
- `services/scanner/src/legislative/bc_hansard_parse.py` — pure-offline HTML parser (stdlib `re` + `html`), handles Blues + Final variants

## Voting Records / Divisions

- **Source URL(s):** https://www.leg.bc.ca/parliamentary-business/overview/43rd-parliament/2nd-session/votes-and-proceedings
- **Format:** HTML Votes and Proceedings.
- **Roll-call availability:** Yes, recorded divisions with member names.
- **Difficulty (1–5):** 3.
- **Notes:** No dedicated voting API. Consistent URL structure per Parliament/session.

## Committee Activity

- **Source URL(s):** https://www.leg.bc.ca/parliamentary-business/committees ; https://www.leg.bc.ca/parliamentary-business/committees/committee-meetings
- **Format:** HTML agendas + transcripts; Hansard Blues + Official Report PDF; audio/video webcasts.
- **Data available:** Memberships, meetings (schedules + transcripts), reports, transcripts, webcasts.
- **Overlap with existing scanner:** None.
- **Difficulty (1–5):** 3.
- **Notes:** Select Standing + Special Committees. Memberships set at session start by Committee of Selection.

## Existing third-party scrapers

- **opencivicdata/scrapers-ca:** `ca_bc` module exists.
- Other: None identified.

## ★ Member Data — LIMS GraphQL (discovered 2026-04-15)

Independent of the bills question, BC exposes a **public, fully-introspectable GraphQL API** at `https://lims.leg.bc.ca/graphql` (POST). No auth, no documented rate limit, CORS permissive. Discovered by mining the `dyn.leg.bc.ca` React SPA bundle for an Apollo client `uri`.

**Schema scope:** 110 root query fields covering members, parliaments, sessions, constituencies, parties, ministers, executive councils, clerks, legislative assistants. Notable `all*` entry points:

- `allMembers`, `allMemberParliaments`, `allMemberElections`, `allMemberRoles`, `allMemberResignations`, `allMemberTypes`, `allMemberConstituencies`
- `allParliaments`, `allSessions`, `allParties`
- `allConstituencies`, `allConstituencyOffices`
- `allExecutiveCouncils`, `allExecutiveStaffs`, `allMinisters`
- `allClerks`, `allLegislativeAssistants`, `allRoles`, `allRoleTypes`
- `allSocialMediaLinks`

**What it does NOT expose:** bills, Hansard, divisions, committees — this is a member/role/org data API, not a legislative-activity one.

**Why it's valuable anyway:**

1. Richer than Open North for BC — includes role history (minister → critic → private member transitions), executive council membership over time, committee postings.
2. Single query fetches what Open North's Represent API returns plus ~10× more structured metadata.
3. Can replace / augment our BC gap filler (`gap_fillers/bc.py`) once we decide how to fold this into our politicians table.
4. Introspection means no schema guessing — `__schema { queryType { fields { name } } }` returns everything.

**Minimum probe query:**

```bash
curl -s -X POST -H "Content-Type: application/json" \
  --data '{"query":"{ allMembers(first: 5) { nodes { id firstName lastName } } }"}' \
  https://lims.leg.bc.ca/graphql
```

**Later-work to capture:** a BC-members enrichment that hits this API to populate politician role history + constituency-office detail in our DB. Independent of the bills pipeline; could be done at any time.

## Status

- [x] Research complete — partially superseded 2026-04-15/19 (see re-ratings)
- [x] Schema drafted — shared schema applies; no new migration needed beyond `0011_politician_lims_member_id.sql`
- [x] Ingestion prototyped (LIMS PDMS pipeline)
- [x] Production ingestion live (bills: 43-2 current, 36 bills / 92 events / 36 sponsors / 36 FK-linked)
- [x] Production ingestion live (Hansard: 43-2 current, 40 sittings / 4,800 speeches / 89.5% politician-linked)
- [ ] Historical backfill — bills (PDMS serves every session back to 1872) + Hansard (HDMS archives from 1970)
- [ ] Hansard scheduler cron (Blues poller + Final sweep)
- [ ] Deputy Speaker / Committee Chair resolution (~10% of role-only Hansard rows)
- [ ] Committee transcripts (`CommitteeA-Blues.htm` / `CommitteeC-Blues.htm` — skipped in v1)
- [ ] Votes
- [ ] LIMS GraphQL member-enrichment workstream (optional, independent of bills)
