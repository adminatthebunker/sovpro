# Nova Scotia — Legislative Data Research

> Standalone research dossier for Nova Scotia. Cross-cutting context (schema, scanner modules, probe hierarchy, research-handoff protocol) lives in [`overview.md`](./overview.md).

**Legislature:** House of Assembly | **Website:** https://nslegislature.ca | **Seats:** 55 | **Next election:** By 2029-12-07

**Status snapshot (2026-04-19):** ✅ **Bill rows live** via Socrata — easiest source in the country (3,522 bills across 24 sessions). Per-bill HTML cache **partially blocked by WAF budget** (~25/3,522 cached). RSS-feed pivot or email allowlist pending. NS is the **reference implementation** that other provincial pipelines follow.

---

## Bills & Legislation

- **Source URL(s):** https://nslegislature.ca/legislative-business/ ; https://data.novascotia.ca/Government-Administration/Bills-introduced-in-the-Nova-Scotia-Legislature/iz5x-dzyf
- **Format:** **Socrata API** (JSON, CSV, SoQL queries) via data.novascotia.ca.
- **Fields captured upstream:** Bill title, status, first/assented-to versions (1995–96 to present), bill types.
- **Terms/Licensing:** **Open Government Licence (Nova Scotia)** — permissive, attribution only.
- **Rate limits / auth:** Public app token recommended but not required. Rate limits generous; documented at dev.socrata.com.
- **Difficulty (1–5):** **2** — easiest bills source in the country.
- **Notes:** **The NS reference implementation.** Socrata's SoQL query language is a JSON/REST API — the closest provincial analog to federal LEGISinfo. The shared `bills` schema was built against this source first.

## ★ RSS feed (discovered 2026-04-15)

Complement to Socrata: `https://nslegislature.ca/legislative-business/bills-statutes/rss` serves an RSS 2.0 feed of every bill in the current session (253 items for 65-1, ~122 KB, single request). Delivers richer status text than Socrata — commencement clauses, exceptions, effective-date caveats in the `<description>` field.

**What RSS gives us:**

- Status text: `"Royal Assent - October 2, 2025; Commencement: October 3, 2025 except:..."` — commencement + exception detail that Socrata's terse `description` field never had.
- pubDate on each status change.
- Single-request polling suitable for a daily cron.

**What RSS doesn't give us:**

- Historical bills (current session only).
- Sponsor slug (still needs HTML bill-page fetch).

**Integration:** `legislative/ns_rss.py` + CLI `ingest-ns-bills-rss`. Matches RSS items to existing Socrata-ingested bills via the canonical source_id; merges RSS payload into `bills.raw.rss`; refreshes `bills.status` and `bills.status_changed_at`; appends `bill_events` rows for the current stage. Fully idempotent, no WAF impact.

## Known blocker: NS WAF daily budget

The per-bill HTML detail-page fetcher hits a per-IP request budget (~11–14 reqs / window). Delay-tuning does **not** help — the counter is per successful request, not per unit time. Two open paths:

- **(a)** Switch phase-2 fetcher to the `/bill-N/rss` endpoint (served from a different CDN path in probe tests).
- **(b)** Email `legcomm@novascotia.ca` for a civic-transparency allowlist.

Neither has been started. Meanwhile the existing 25-bill cache is sufficient to prove the pipeline. The 3,500+ re-fetches we've done so far were waste — the same headers re-trigger the WAF every time.

## Hansard / Debates

- **Source URL(s):** https://nslegislature.ca/legislative-business/hansard-debates ; https://nslegislature.ca/about/supporting-offices/hansard-reporting-services
- **Format:** HTML transcripts from 1994 forward; PDF index; video/audio webcasts.
- **Granularity:** Daily; includes committee Hansards.
- **Speaker identification:** Yes.
- **Difficulty (1–5):** 3.
- **Notes:** Transcripts published next morning after sitting. Contact: Hansard Reporting Services, 902-424-7990.

## Voting Records / Divisions

- **Source URL(s):** https://nslegislature.ca/ruling-topics/votes ; https://nslegislature.ca/legislative-business/hansard-dates/
- **Format:** House Journals with voice votes and recorded roll calls.
- **Roll-call availability:** Yes when roll call is demanded (two members required per rules).
- **Difficulty (1–5):** 3.
- **Notes:** Divisions entered in minutes. No standalone export.

## Committee Activity

- **Source URL(s):** https://nslegislature.ca/legislative-business/committees/standing ; https://nslegislature.ca/about/supporting-offices/legislative-committees-office
- **Format:** HTML pages with meeting archives, membership, public submissions.
- **Data available:** Standing committees (Community Services, Health, Human Resources, Natural Resources, Public Accounts, Veterans Affairs); schedules, transcripts.
- **Overlap with existing scanner:** None.
- **Difficulty (1–5):** 2.
- **Notes:** Contact: legcomm@novascotia.ca, 902-424-4432.

## Existing third-party scrapers

- **opencivicdata/scrapers-ca:** `ca_ns` module exists (provincial + Halifax, Cape Breton).
- Other: None identified.

## Status

- [x] Research complete
- [x] Schema drafted (0006 — same as ON)
- [x] Ingestion prototyped (Socrata → 3,522 bills across 24 sessions)
- [~] Production ingestion partial — bill rows complete; per-bill HTML fetch blocked by WAF budget (25/3,522 cached). RSS-feed pivot or email allowlist pending.
- [x] Sponsor→politician resolver working (14/14 parsed sponsors linked)
- [ ] Hansard
- [ ] Votes
- [ ] Committees
