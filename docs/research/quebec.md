# Quebec — Legislative Data Research

> Standalone research dossier for Quebec. Cross-cutting context (schema, scanner modules, probe hierarchy, research-handoff protocol) lives in [`overview.md`](./overview.md).

**Legislature:** National Assembly of Quebec (Assemblée nationale du Québec) | **Website:** https://www.assnat.qc.ca | **Seats:** 125 | **Next election:** 2026-10-05

**Status snapshot (2026-04-20):** ✅ **Bills live** (102 / 115 / 95 — **94 / 95 sponsors FK-linked, 99%**) via donneesquebec.ca CSV + RSS + bill-detail HTML. ✅ **Hansard live** for 43-2 (current session) via Journal des débats HTML — ASP.NET ViewState-paginated discovery + per-sitting HTML scrape + surname-with-riding-disambiguation resolver. ✅ **Tier 1 Speaker resolution live** — "Le Président" rows tied to the sitting Speaker by date (Paradis 2018–2022, Roy 2022–present). Votes / committees not yet built. Private bills and votes registry deferred.

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

## Hansard / Debates ✅ LIVE (2026-04-20, session 43-2)

- **Primary source:** Journal des débats daily HTML transcripts at `https://www.assnat.qc.ca/fr/travaux-parlementaires/assemblee-nationale/{parl}-{sess}/journal-debats/{YYYYMMDD}/{doc_id}.html` — one per sitting day. French is primary; English versions often 500 and are not ingested.
- **Discovery:** ASP.NET WebForms listing at `/fr/travaux-parlementaires/journaux-debats/`. Session filter `ddlSessionLegislature` (e.g. 1617 = 43-2, 1611 = 43-1) + page size `ddlNombreParPage=100` + debate-type `rblOptionTypeDebat=1` (Assemblée nationale only — committees excluded in v1). Pagination by `__EVENTTARGET=…lkbPageSuivante` POSTs carrying `__VIEWSTATE` / `__EVENTVALIDATION` / `__VIEWSTATEGENERATOR`. One initial GET + one filter POST + N page-next POSTs covers a full session (~20 POSTs for 43-2).
- **Parser markup:** Speaker turns are `<p style="text-align: justify"><b>Honorific Surname :</b> speech text…</p>` with continuation paragraphs in plain `<p>`s (no `SpeakerContinues` class). Heading-vs-speaker disambiguated by *trailing colon* inside the `<b>` — centered bold without a colon is a section heading. NBSP (`\xa0`) between tokens is common. The parse module lives at `services/scanner/src/legislative/qc_hansard_parse.py` — pure-offline, importable for fixture testing.
- **Attribution shapes observed:**
  - Person: `M. Ciccone` / `Mme Charest` — honorific + surname only (no given name).
  - Role + person: `La Vice-Présidente (Mme Soucy)` — resolved via the parenthetical name.
  - Role + riding: `M. Lévesque (Chapleau)` — riding used to disambiguate shared surnames (Lévesque, Bélanger, Roy). The scanner stores the riding as `raw.qc_hansard.constituency_hint` and the SpeakerLookup indexes `(surname, constituency) → politician` so these resolve cleanly.
  - Pure role: `Le Président` / `La Vice-Présidente` / `Le Premier ministre` / `Le Ministre de X` / `Le Secrétaire` / `Des voix` / `Une voix`.
- **Speaker resolution:** `politicians.qc_assnat_id` carries 124/124 active MNAs (enriched by `enrich-qc-mna-ids`). The SpeakerLookup builds four indexes from the politicians table: `by_full_name`, `by_surname` (with compound-surname + name-tail keys — e.g. "Boivin Roy" indexes both "Karine Boivin Roy" and "Roy"), and `by_riding_surname`. Presiding-officer rows (`speaker_role='Le Président'`) are left NULL at ingest and resolved in a post-pass by `presiding_officer_resolver.py` using the QC SPEAKER_ROSTER (Paradis / Roy).
- **Source system:** `source_system='hansard-qc'`. Upsert key `UNIQUE NULLS NOT DISTINCT (source_system, source_url, sequence)` — idempotent.
- **Scanner modules:** `qc_hansard.py` (discovery + fetch + upsert + post-pass), `qc_hansard_parse.py` (HTML → ParsedSpeech).
- **CLI:** `ingest-qc-hansard --parliament 43 --session 2 [--since/--until/--limit-sittings/--limit-speeches/--url]`, `resolve-qc-speakers`, `resolve-presiding-speakers --province=QC`, `chunk-speeches`, `embed-speech-chunks`.
- **Difficulty (1–5):** **3**. ASP.NET postback pagination is the only wrinkle; the per-sitting markup is clean semantic HTML.
- **Terms/Licensing:** Crown copyright. Civic-transparency / non-commercial use fits the stated terms.
- **Rate limits / auth:** None observed; 1.5 s delay between sittings for politeness.
- **Known limitations:**
  - *Shared-surname ambiguity without a riding hint* — e.g. "Mme Bélanger" when two Bélanger MNAs are active and the transcript doesn't include the riding. ~15–20 rows per sitting fall here; they land `confidence=0.0 politician_id=NULL` and don't resolve until we add context tracking (next-speech inference).
  - *Le Secrétaire / Des voix / Une voix* — structurally non-resolvable (Le Secrétaire is assembly staff, not an MNA; the voices are anonymous). Expect ~60 rows per sitting to remain `politician_id=NULL`.
  - *Historical sessions* — the 43-1 and earlier backfill will resolve less cleanly because retired MNAs aren't in `politicians` yet (same roster gap as AB). V1 scopes to current session.
  - *Sections* — `raw.qc_hansard.section` is not yet populated (heading markup varies across eras). Speech text still includes the section heading words, so retrieval is unaffected.

## ★ Tier 1 Speaker resolution — live 2026-04-20

"Le Président" / "La Présidente" attributions carry only the role, not a name. Resolution is date-ranged against `politician_terms.office='Speaker'`, seeded from a small hand-curated roster in `presiding_officer_resolver.py::SPEAKER_ROSTER["QC"]`:

| Speaker | Start | End |
|---|---|---|
| François Paradis | 2018-11-28 | 2022-11-29 |
| Nathalie Roy | 2022-11-29 | — |

Run with:

```bash
docker compose run --rm scanner resolve-presiding-speakers --province QC
```

Idempotent. DELETE-then-INSERT of Speaker terms on each run. Updates `speeches.politician_id` **and** `speech_chunks.politician_id` (denormalised copy) in the same transaction. Adding a new Speaker is a 3-line PR: append a `SpeakerTerm(…)`, bump the prior Speaker's `ended_at`, re-run the command.

**Scope note:** Tier 1 covers only "Le Président" (single-person-at-a-time, date-determinable). Tier 2 would extend to "Le Vice-Président" / "La Vice-Présidente" — which is partially auto-resolved already because the Journal des débats uses the `(Mme Soucy)` parenthetical form that names the Vice-Président directly, so most Vice-Président rows resolve at ingest without needing a term-based post-pass.

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
