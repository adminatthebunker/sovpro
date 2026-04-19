# Ontario — Legislative Data Research

> Standalone research dossier for Ontario. Cross-cutting context (schema, scanner modules, probe hierarchy, research-handoff protocol) lives in [`overview.md`](./overview.md).

**Legislature:** Legislative Assembly of Ontario | **Website:** https://www.ola.org | **Seats:** 124 | **Next election:** 2030-04-11

**Status snapshot (2026-04-19):** ✅ **Bills live** for Parliament 44, Session 1 (102 bills, 595 events, 102 sponsors — 100% FK-linked). Hansard / votes / committees not yet built. Drupal `?_format=json` upgrade path identified but not migrated to (HTML pipeline works for current scope).

---

## Bills & Legislation

- **Source URL(s):** https://www.ola.org/en/legislative-business/bills/current ; https://www.ola.org/en/legislative-business/bills/all
- **Format:** HTML web pages; no structured API. Per-bill PDFs available.
- **Fields captured upstream:** Bill number, title, status (reading stages), sponsoring MPP.
- **Terms/Licensing:** Crown copyright (Queen's Printer for Ontario). Non-commercial reproduction permitted with attribution. Legislative text freely reproducible.
- **Rate limits / auth:** None documented.
- **Difficulty (1–5):** 3.
- **Notes:** Bills indexed by Parliament and session. URL structure is predictable. No JSON/XML export at the URLs we ingest from.

## Hansard / Debates

- **Source URL(s):** https://www.ola.org/en/legislative-business/hansard-search ; https://www.ola.org/en/legislative-business/house-hansard-index
- **Format:** HTML searchable archive; no API.
- **Granularity:** Per-session daily transcripts (Hansard volumes).
- **Speaker identification:** By MPP name; searchable.
- **Difficulty (1–5):** 3.
- **Notes:** Full-text searchable from 1974-03-05 onward.

## Voting Records / Divisions

- **Source URL(s):** https://www.ola.org/en/legislative-business/house-documents/parliament-44/session-1 (Votes and Proceedings)
- **Format:** HTML Votes and Proceedings; also PDF downloads.
- **Roll-call availability:** Yes, from 43rd Parliament forward, with member names and votes.
- **Difficulty (1–5):** 3.
- **Notes:** Divisions embedded in daily Votes and Proceedings. Consistent URL structure by Parliament/session/date.

## Committee Activity

- **Source URL(s):** https://www.ola.org/en/legislative-business/committees ; https://www.ola.org/en/legislative-business/committees/documents
- **Format:** HTML transcripts; some committees publish CSV exports (e.g. Standing Committee on Finance and Economic Affairs).
- **Data available:** Memberships, meetings (transcripts by date), reports (PDF/HTML), transcripts (HTML).
- **Overlap with existing scanner:** None.
- **Difficulty (1–5):** 3.
- **Notes:** 9 Standing Committees. Transcripts include member remarks, votes, and staff lists.

## Existing third-party scrapers

- **opencivicdata/scrapers-ca:** `ca_on` module exists ([github.com/opencivicdata/scrapers-ca](https://github.com/opencivicdata/scrapers-ca)).
- **Open North Represent API** — reps only, not legislative activity.

## ★ Drupal JSON serializer (discovered 2026-04-15, after initial HTML pipeline shipped)

Every node on `www.ola.org` supports `?_format=json` — the Drupal core REST serializer. This turns the entire bills / sponsors / members graph into a queryable JSON API without any auth:

```
https://www.ola.org/en/legislative-business/bills/parliament-44/session-1/bill-104?_format=json
https://www.ola.org/en/node/9608366?_format=json          # sponsor node
https://www.ola.org/en/members/all/john-fraser?_format=json # member node
```

**Fields available on a bill node** (superset of what we scrape):

- `field_bill_number`, `field_long_title`, `field_short_title`, `field_current_status`
- `field_sponsor` → reference to a bill_sponsor node (which has `field_member` → member node, with `field_member_id` — a stable **integer ID** we can store on politicians for exact-match linking, same trick as BC's `lims_member_id`)
- `field_status_table` — same malformed HTML table we parse, but now arriving inside JSON (still needs the tr-split fix)
- `field_has_divisions` — boolean, signals whether vote roll-calls exist
- `field_debates` — array of Hansard debate node refs
- `field_acts`, `field_acts_affected` — ties into legislation graph
- `field_versions` — bill-text version history
- `field_type` → taxonomy term (government vs. private member's bill)
- `field_parliament`, `field_parliament_sessions`
- `field_latest_activity_date`

**Member node also exposes `field_member_id`** (integer, stable) plus riding, party, dates of service, gender, contact group, expense disclosure links.

**Why it matters going forward:**
- Richer data for free — divisions boolean, type taxonomy, acts-affected graph — that HTML scraping made awkward to get.
- Integer `field_member_id` enables exact sponsor→politician joins (same pattern as BC's LIMS `memberId`). Replace slug-fuzz resolution with a single-column FK.
- Likely applies to **Saskatchewan, Manitoba, PEI, NL** too if they're Drupal-backed — worth probing `?_format=json` on the first bill page of each as a fast triage before writing HTML scrapers. (Result of that probe pass on 2026-04-15: none of the four are Drupal. The serializer trick is Ontario-specific.)

**Not migrating the current ON pipeline** (102 bills, 595 events, sponsors all linked) because the HTML pipeline works and the data is already good. Switch to the JSON serializer when we:
  (a) backfill earlier ON Parliaments, or
  (b) want the divisions / acts-affected / versions data we skipped.

## Open issues

- **Historical ON sponsors** — only current-Parliament MPPs are in our politicians table, so any pre-2024 ON bill would name-match poorly. Not a problem at P44-S1 scope, but will be when we backfill.

## Status

- [x] Research complete
- [x] Schema drafted (0006 — shared across jurisdictions)
- [x] Ingestion prototyped (`ingest-on-bills` P44-S1: 102 bills, 595 events, 102 sponsors)
- [x] Production ingestion live (current session; backfill earlier Parliaments deferred)
- [x] Sponsor→politician resolver working (102/102 linked)
- [ ] JSON-serializer pipeline (optional rewrite; HTML pipeline works fine for current scope)
- [ ] Hansard
- [ ] Votes
- [ ] Committees
