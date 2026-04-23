# Newfoundland & Labrador — Legislative Data Research

> Standalone research dossier for Newfoundland and Labrador. Cross-cutting context (schema, scanner modules, probe hierarchy, research-handoff protocol) lives in [`overview.md`](./overview.md).

**Legislature:** House of Assembly | **Website:** https://www.assembly.nl.ca | **Seats:** 40 | **Next election:** 2029-10

**Status snapshot (2026-04-22):** ✅ **Bills live + backfilled** — 1,193 bills / 3,677 events across 24 sessions (parliaments 44–51), full historical backfill via `--all-sessions`. Sponsors remain 0 by upstream design (not exposed on any inspected page). Hansard research pass complete (below); pipeline build in progress.

---

## Bills & Legislation ✅ LIVE (2026-04-16)

- **Primary source:** single-page session table at `/HouseBusiness/Bills/ga{GA}session{S}/`. The page is server-rendered HTML with exactly one `<table>` whose rows carry **the full stage timeline for every bill in the session** — columns: No., Bill (title + link to bill text), First Reading, Second Reading, Committee, Amendments (Yes/No), Third Reading, Royal Assent, Act chapter.
- **One HTTP GET per session** captures every stage date. No per-bill detail fetch needed for timeline data. (Per-bill `.htm` pages exist but serve bill text only.)
- **Sponsor data: NOT IN THE PROGRESS TABLE OR PER-BILL HTML.** Sponsor would need to come from Order Papers, Journals, or Hansard — deferred. Pipeline writes `bill_sponsors` = 0 for NL; stages + titles are the MVP.
- **MHA roster:** at `/Members/members.aspx`; no numeric member id in URLs. Would require name-based matching if/when sponsor data surfaces.
- **Historical coverage:** every session back to GA 44 (≈40 sessions) addressable via `ga{GA}session{S}`. `--all-sessions-in-ga` + `--all-sessions` flags available.
- **Quirk:** per-bill `.htm` pages are **Windows-1252 encoded** (not UTF-8). Bill list pages + **Hansard transcripts** are UTF-8 cleanly (verified for modern Word-exported and legacy FrontPage-exported Hansard HTML, 2026-04-22). The encoding trap is scoped to per-bill text pages only.
- **Catch-all 404 gotcha:** `assembly.nl.ca` serves a 200 status with a styled error page (`<title>House of Asembly - NL - Error Page</title>` — typo "Asembly" in upstream template) for every unmapped URL. Content-compare is mandatory for probes; status-code alone lies.
- **Terms/Licensing:** Crown copyright. Civic-transparency use is standard.
- **Rate limits / auth:** None observed.
- **Difficulty (1–5):** 2 for stages (single table), 4+ for sponsor (not exposed).
- **Scanner module:** `services/scanner/src/legislative/nl_bills.py`.
- **CLI:** `ingest-nl-bills [--ga G --session S | --all-sessions-in-ga G | --all-sessions]`.
- **Stages captured:** First Reading, Second Reading, Committee (with `outcome='amended'` when Amendments=Yes), Third Reading, Royal Assent.
- **Current state (2026-04-22):** 1,193 bills / 3,677 events across GA 44–51 (24 sessions), 0 sponsors by design.

## Hansard / Debates — research pass complete (2026-04-22)

- **Source URL(s):** `https://www.assembly.nl.ca/HouseBusiness/Hansard/` — the landing page lists every session from GA 21 (1909) to the current GA 51.
- **URL taxonomy:**
  - Hansard landing: `/HouseBusiness/Hansard/`
  - Session directory (modern, GA 34+): `/HouseBusiness/Hansard/ga{GA}session{S}/`
  - Sitting day: `ga{GA}session{S}/{YY}-{MM}-{DD}.htm[l]` — one canonical URL per sitting
  - Special days: `ga{GA}session{S}/{YY}-{MM}-{DD}{Label}.htm[l]` — e.g. `25-11-25SwearingIn.html`, `25-11-03ElectionofSpeaker.htm`
  - Legacy (GA 21–33, 1909–1971): single PDF per volume — `{GA}GASession{S}_{YEAR}_vol{N}.pdf`
- **Content formats — TWO HTML eras plus PDF archive:**
  - **Modern (Word-exported, GA 45+):** `<p class="MsoNormal"><strong><span>SPEAKER:</span></strong>` speaker blocks. Clean Word HTML, UTF-8, `MsoNormal` / `MsoHeader` classes.
  - **Legacy (FrontPage 3.0, GA 44 and earlier HTML era):** malformed markup — opening `<b>` on one `<p>`, closing `</b>` mid-line of the next. Requires BeautifulSoup + lenient parser (`html5lib` or `lxml` with recover).
  - **PDF archive (GA 21–33):** scanned print volumes — out of scope for HTML pipeline v1.
- **Charset:** both HTML eras declare `<meta charset="utf-8">`. Curly apostrophes appear as `&rsquo;`. No Windows-1252 on Hansard (contrast with per-bill pages).
- **Speaker attribution format — compact initial+surname:**
  - Modern: `S. O'LEARY:`, `J. HOGAN:`, `K. WHITE:` (first initial + surname), `PREMIER WAKEHAM:` (title + surname), `SPEAKER:` / `SPEAKER (Lane):` (role, optionally with parens-name disambiguator), `SOME HON. MEMBERS:` (group chant)
  - Legacy: `MR. MATTHEWS:` (title + surname), `MR. SPEAKER (Snow):` — no initial
  - **No riding inline. No party inline.** Speaker resolution relies on (initial, surname) or (title, surname) matched against the date-windowed NL politicians table.
- **Preliminary vs edited:** ONE canonical URL per sitting day. The body carries `"PARTIALLY EDITED transcript of the House of Assembly sitting for …"` at the top while partial; it's replaced with the complete edited version in place. Strategy: track `Last-Modified` / `ETag` and re-parse on change.
- **Section headings:** centered + bold + underlined (`<p align="center"><strong><u>Statements by Members</u></strong></p>` modern; `<p ALIGN="CENTER">Statements by Ministers</u></p>` legacy). Detect as centered + `<u>` wrapped.
- **Volume estimate:** a completed full session runs ~70 sitting days (GA 50 Session 2 = 73 sittings across 2022–2025). Modern HTML era ≈ 40 sessions × ~70 days × ~60 turns/day ≈ **120–180 k speeches** — same order as MB, smaller than federal.
- **MHA canonical id:** **not exposed on assembly.nl.ca.** `/Members/members.aspx` is a postal-code "Your Member" lookup, not a per-member profile roster. No `memberId=N`, no `/Members/Smith`, no JSON endpoint. Convention #1 (add a `{jurisdiction}_slug` column) does NOT apply here — speaker resolution relies on existing Open North roster + `(initial, surname)` matching.
- **Probe hierarchy:** RSS (`/rss`, `/feed`, `/feed.xml`) all hit the 200/error-template. Site is ASP.NET/IIS, not Drupal — no `?_format=json`. No iframes on Hansard pages. No JS bundle / GraphQL. **HTML scrape is the only viable path.**
- **Rate limits / auth:** none observed. Cookie `cookiesession1` is set but not required.
- **Subject + Speaker Indexes** (`/HouseBusiness/Hansard/Index/`) — mentioned on the landing page; not deeply probed. Deferred.
- **Difficulty (1–5):** 3 — compact attribution format + era-branching parser raises the bar above a simple MsoNormal clone.
- **Source system tag:** `hansard-nl`. Upsert key: `UNIQUE NULLS NOT DISTINCT (source_system, source_url, sequence)`.

## Voting Records / Divisions

- **Source URL(s):** Embedded in Hansard; Order Papers; Journals
- **Format:** Text tables within Hansard; PDF Order Papers.
- **Roll-call availability:** Named divisions recorded (members called by name).
- **Difficulty (1–5):** 3.
- **Notes:** Partisan legislature (PC, Liberal, NDP, Independents).

## Committee Activity

- **Source URL(s):** https://www.assembly.nl.ca (Standing Committees section); Tabled Documents; Committee Reports
- **Format:** HTML committee pages; audio streaming.
- **Data available:** Membership, agendas, live audio.
- **Overlap with existing scanner:** None.
- **Difficulty (1–5):** 3.
- **Notes:** Committee-level voting not always publicly exposed.

## Existing third-party scrapers

- **opencivicdata/scrapers-ca:** NL scraper status — verify in repo during implementation.
- Other: https://opendata.gov.nl.ca/ — legislative data availability unclear.

## Status

- [x] Research complete
- [x] Schema (no new migration — no sponsor FK currently)
- [x] Ingestion prototyped
- [x] Production ingestion live (2026-04-16) — GA 51, Session 1, 12 bills, stages only
- [ ] Sponsor data (not exposed on any inspected HTML page — would need Order Papers / Hansard scrape)
- [x] Historical backfill (1,193 bills across parliaments 44–51, backfilled 2026-04-16)
- [x] Hansard — GA 51 Session 1 live (2026-04-22): 8,341 speeches / 18 sittings, era-branching parser validated against both modern (Word-exported) and legacy (FrontPage) samples. ~90% speaker-attributed after presiding-resolver pass (33% name-matched, 32% group markers, 25% presiding role via Paul Lane roster entry). Known data-quality gaps: Jim/James Dinn duplicate (168 rows ambiguous) and "H. Conway Ottenheimer" compound-surname mismatch (43 rows) — closable by dedup + resolver slug-candidate enhancement, not parser work.
- [ ] Hansard historical backfill — GA 50 Session 2 (73 sittings, 2022–2025) is the obvious next session; earlier GAs + legacy-era parser verification still to run.
- [ ] Votes
