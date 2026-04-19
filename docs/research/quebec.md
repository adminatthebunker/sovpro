# Quebec — Legislative Data Research

> Standalone research dossier for Quebec. Cross-cutting context (schema, scanner modules, probe hierarchy, research-handoff protocol) lives in [`overview.md`](./overview.md).

**Legislature:** National Assembly of Quebec (Assemblée nationale du Québec) | **Website:** https://www.assnat.qc.ca | **Seats:** 125 | **Next election:** 2026-10-05

**Status snapshot (2026-04-19):** ✅ **Bills live** (102 / 115 / 95 — **94 / 95 sponsors FK-linked, 99%**) via donneesquebec.ca CSV + RSS + bill-detail HTML. Hansard / votes / committees not yet built. Private bills and votes registry deferred.

---

## User research (handoff URLs)

These URLs were the user's initial research handoff for QC and seeded the pipeline:

- https://www.assnat.qc.ca/en/travaux-parlementaires/index.html — parliamentary work hub
- https://www.assnat.qc.ca/en/deputes/index.html#listeDeputes — assembly members roster
- https://www.assnat.qc.ca/fr/fils-rss.html — RSS feed catalog (where the bills RSS came from)
- https://www.assnat.qc.ca/en/travaux-parlementaires/projets-loi/projets-loi-43-2.html — bills index for the current session

## Bills & Legislation ✅ LIVE (2026-04-16)

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

## Hansard / Debates

- **Source URL(s):** https://www.assnat.qc.ca/fr/travaux-parlementaires/journaux-debats/ ; https://www.assnat.qc.ca/en/travaux-parlementaires/journaux-debats/
- **Format:** HTML searchable archive from 1963.
- **Granularity:** Per-session daily transcripts (Journal des débats).
- **Speaker identification:** By MNA name; searchable.
- **Difficulty (1–5):** 3.
- **Notes:** Bilingual (FR primary). Committee-level Hansard (Journal des débats) per committee.

## Voting Records / Divisions

- **Source URL(s):** https://www.assnat.qc.ca/fr/lien/12779.html (Register of Recorded Divisions); also embedded in Journal des débats and bill pages
- **Format:** HTML scattered across multiple pages.
- **Roll-call availability:** Yes; member names and votes.
- **Difficulty (1–5):** 4.
- **Notes:** No dedicated voting API. Registry page is **ASP.NET postback** — needs form-aware scrape or Playwright. Requires navigating bill/session structure.

## Committee Activity

- **Source URL(s):** https://www.assnat.qc.ca/fr/travaux-parlementaires/commissions/index.html ; https://www.assnat.qc.ca/en/deputes/fonctions-parlementaires-ministerielles/composition-commissions.html ; individual committee pages at `/travaux-parlementaires/commissions/{committee-code}/`
- **Format:** HTML + PDF reports; committee Hansard in HTML.
- **Data available:** Memberships, meetings, reports, transcripts (Journal des débats per committee).
- **Overlap with existing scanner:** None.
- **Difficulty (1–5):** 4.
- **Notes:** Committees (commissions) organized by legislature/session code. Bilingual.

## Existing third-party scrapers

- **opencivicdata/scrapers-ca:** `ca_qc` module exists.
- Other: None identified.

## Status

- [x] Research complete
- [x] Schema drafted (migration `0012_politician_qc_assnat_id.sql`)
- [x] Ingestion prototyped
- [x] Production ingestion live (2026-04-16) — bills + events + sponsors
- [ ] Hansard / Journaux des débats
- [ ] Voting records (registry page is ASP.NET postback — needs form-aware scrape or Playwright)
- [ ] Committee meetings + reports
- [ ] Private-bill URL scheme
