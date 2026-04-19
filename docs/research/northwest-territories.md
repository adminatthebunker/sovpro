# Northwest Territories — Legislative Data Research

> Standalone research dossier for the Northwest Territories. Cross-cutting context (schema, scanner modules, probe hierarchy, research-handoff protocol) lives in [`overview.md`](./overview.md).

**Legislature:** Legislative Assembly of the Northwest Territories | **Website:** https://www.ntlegislativeassembly.ca | **Seats:** 19 | **Next election:** 2027-10

**Status snapshot (2026-04-19):** ✅ **Bills live** for 20th Assembly, 1st Session (20 bills / 82 events / **0 sponsors — by design, NT is consensus government**). Hansard deferred (evaluate opennwt.ca mirror first). Votes blocked on schema design for consensus-government model.

---

## Why NT (and NU) are different

NT has **consensus government** — no political parties, no party whips. All MLAs are elected as independents and decisions are made by consensus or standing count. The "sponsor" concept that the bills schema is built around doesn't apply in the partisan sense. The pipeline ingests bills + stage events but writes zero `bill_sponsors` rows — that's not a bug, it's faithful to the source.

The same goes for "voting records" in the partisan sense: there are no party-line tallies because there are no party lines. Migration `0018_votes.sql` sits unapplied while we figure out how to model consensus / acclamation votes alongside partisan ones.

## Bills & Legislation ✅ LIVE (2026-04-16)

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

## Hansard / Debates

- **Source URL(s):** https://www.ntlegislativeassembly.ca/documents-proceedings/hansard (official) ; https://hansard.opennwt.ca/ (third-party friendlier interface) ; https://lanwt.i8.dgicloud.com/hansard (Legislative Library)
- **Format:** Searchable HTML + PDF archives.
- **Granularity:** Speaker, statement, date, session.
- **Speaker identification:** Name (all MLAs non-partisan in consensus model).
- **Difficulty (1–5):** 2.
- **Notes:** **OpenNWT is a pre-existing third-party Hansard portal — evaluate whether it's easier to mirror than the official site.**

## Voting Records / Divisions

- **Source URL(s):** https://www.ntlegislativeassembly.ca/documents-proceedings/proceedings (Votes and Proceedings)
- **Format:** HTML/PDF Votes and Proceedings summaries.
- **Roll-call availability:** Format TBD (may be summary-only given consensus model).
- **Difficulty (1–5):** 3.
- **Notes:** **CONSENSUS GOVERNMENT — all MLAs independent; no party whips; decisions by consensus or standing count. Traditional partisan "voting records" do not apply.** Schema must model this (e.g., `voting_model` column on a legislative_session table, or skip votes table entirely for NT/NU).

## Committee Activity

- **Source URL(s):** https://www.ntlegislativeassembly.ca/documents-proceedings/committees-reports ; standing committee listings
- **Format:** HTML/PDF reports; agendas; dates.
- **Data available:** Seven standing committees; reports, schedules, rosters.
- **Overlap with existing scanner:** None.
- **Difficulty (1–5):** 3.
- **Notes:** Committee work is collaborative.

## Existing third-party scrapers

- **opencivicdata/scrapers-ca:** NT scraper status — verify in repo.
- Other: OpenNWT (https://opennwt.ca/) — possible upstream, evaluate data model.

## Status

- [x] Research complete
- [x] Schema (no new migration — no sponsor FK)
- [x] Ingestion prototyped
- [x] Production ingestion live (2026-04-16) — 20th Assembly, 1st Session, 20 bills
- [ ] Historical backfill (assemblies 16–19 visible in nav; URL routing not mapped)
- [ ] Hansard (deferred — evaluate opennwt.ca mirror)
- [ ] Votes (consensus-government model — different schema needed)
