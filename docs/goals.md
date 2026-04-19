# Canadian Political Data — Goals

**Last updated:** 2026-04-16
**Status:** Canonical. Revisit quarterly or when scope changes.

## North star

Become **the definitive source of Canadian political data**: who represents whom, what they've said, how they've voted, and where their infrastructure lives — across every level of government, every province and territory, as far back as the digital record goes.

The project is rooted in **access to information as a right** and takes explicit **progressive and democratic stances**. It is not, and will not pretend to be, apolitical.

## Audience (staged)

### Primary — engaged citizens (public, free)
- Postal-code "who represents me" lookup (shipped)
- Per-politician pages with socials, offices, hosting, and — new — parliamentary record
- Single-search-box semantic search over what politicians have said and done
- Map + change feed

### Secondary — lobbyists, journalists, academics, advocacy orgs (paid API tiers)
- Bulk export (CSV/Parquet)
- Programmatic semantic search
- Scheduled alerts on topic matches
- "Compare A vs. B on topic X" tooling

### Funding model
Free public UI + paid API tiers for institutional users + grant funding for long-term sustainability. Public side stays free forever.

## Positioning

- **Hosting sovereignty** is no longer the lede — it becomes one lens among many on the landing page and in politician detail views.
- **"Definitive political data of Canada"** is the brand framing going forward.
- The Alberta independence referendum (2026-10-19) is a soft forcing function for the sovereignty narrative but not for semantic search v1.
- Semantic search itself is the lure for wider adoption — the "you can finally search what your MP actually said" moment.

## What v1 of semantic search is

A single search box. Results are politician-attributed quotes with date, source, jurisdiction, and party-at-time. Filters by level / province / party / date range. Every result links to the exact source paragraph.

Everything else (topic dashboards, compare A/B, alerts, bulk export) is phase-2+.

## Data scope

### Priority 1 — ship first
- Hansard floor speeches
- Recorded votes / divisions

### Priority 2
- Committee transcripts
- Bill sponsorship + bill text (largely shipped for AB/BC/NS/ON/QC)
- Social media post content (not just handles)

### Priority 3
- Press releases from official politician websites
- Campaign materials / platforms
- Third-party media quotes (copyright-heavy; defer)

### Coverage
- Federal MPs + senators — first
- All 10 provinces + 3 territories — rolling
- Major municipal councils — later
- **Elected politicians only** for now; staffers / candidates / never-elected leaders deferred

### Historical depth
Back to digitization wherever possible. Openparliament.ca has federal from 1994; NS Hansard from 1994; SK/PEI from 1996. Current-session freshness (next-day / overnight) is the SLA. Historical backfill is opportunistic.

### Language
QC ships in FR; NB ships bilingual; NU ships EN + Inuktitut + Inuinnaqtun + FR. We embed in all source languages using a multilingual model so cross-lingual retrieval works by default. No machine translation in the critical path.

## Non-goals

- **Not apolitical.** We take stances on democratic values and information access.
- **Not a full public-records platform.** We don't index FOI filings, court dockets, or lobbying records (yet).
- **Not a social network / commentary platform.** No user accounts or posting in v1 beyond a corrections inbox.
- **Not a translation product.** We index source-language text; we don't generate English summaries of French speeches.
- **Not dependent on government partnerships.** We maintain operational independence from legislatures and ministries. Outreach for API allowlists is fine; funding from them is not.

## Governance

- **Non-commercial redistribution posture.** Full speech text with attribution is fine for the public UI. Paid API consumers get snippets + links, which sidesteps QC's stricter Crown-copyright clauses.
- **Takedown / correction policy.** Needs to be written. Will include an SMTP corrections inbox and a public "flag this" affordance on every surfaced claim.
- **PII in Hansard.** Non-politician names (petitioners, witnesses, victims) are kept in the source text but not surfaced as first-class entities. Search does not expand to "find everything about citizen X."
- **Accuracy baseline for v1.** Up to 5% misattribution acceptable; correctness improves as the corrections pipeline matures.

## Current baseline (2026-04-16)

- **Bills layer:** 9 of 13 sub-national legislatures live — NS, ON, BC, QC, AB, NB, NL, NT, NU. ~3,945 bills, 5,326 events, 394 sponsor rows (393 FK-linked = 99.7%). Federal bills partial via openparliament.ca mirror.
- **Remaining bills work:** 4 jurisdictions blocked in two pairs —
  - MB + SK (PDF-only, needs pdfplumber investment; same tooling unlocks AB Hansard)
  - PE + YT (WAF/CAPTCHA, needs Playwright track)
- **Semantic layer:** schema migrations 0014–0017 + 0019–0021 applied. `speeches` / `speech_chunks` / `speech_references` / `jurisdiction_sources` / `correction_submissions` tables exist. pgvector 0.8.2 + unaccent installed. `0018_votes.sql` held until real NT/NU data informs the consensus-gov't model.
- **Not yet:** zero speeches ingested. No embeddings generated. BGE-M3 not yet deployed.

## Success criteria for phase 1

1. Semantic search is live on the public site, covering federal Hansard (1994+).
2. Provincial Hansard live for at least 3 provinces including ON and QC (the two largest non-federal caucuses).
3. Every search result is traceable to a source URL + date + speaker.
4. A corrections inbox exists and has been used at least once.
5. A bootstrapped self-hosted embedding pipeline (BGE-M3 on local hardware) is stable enough to run daily.
6. API design for paid tiers is sketched (not necessarily launched).

## What's explicitly deferred

- Municipal council transcripts
- Non-elected figures
- Third-party media coverage
- Real-time / streaming ingest (next-day SLA is enough)
- Browser-automation infra for Cloudflare/Radware-blocked legislatures
- Machine translation of source languages
