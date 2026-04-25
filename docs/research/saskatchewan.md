# Saskatchewan — Legislative Data Research

> Standalone research dossier for Saskatchewan. Cross-cutting context (schema, scanner modules, probe hierarchy, research-handoff protocol) lives in [`overview.md`](./overview.md).

**Legislature:** Legislative Assembly of Saskatchewan | **Website:** https://www.legassembly.sk.ca | **Seats:** 61 | **Next election:** By 2028-10

**Status snapshot (2026-04-19):** ⏸️ **Deferred (PDF-only).** Probing on 2026-04-15 found primary bill artifact is `progress-of-bills.pdf` with no per-bill HTML URLs. Re-rated to difficulty 4. Unlike most provinces, Hansard is **lower difficulty** than bills here (well-indexed back to 1996). No `ca_sk` scraper in opencivicdata.

---

## Bills & Legislation

- **Source URL(s):** https://www.legassembly.sk.ca/legislative-business/bills/
- **Format:** HTML, by Legislature and session. **Primary bill artifact is `progress-of-bills.pdf`.** Probing didn't find per-bill HTML URLs; likely PDF-only metadata.
- **Fields captured upstream:** Bill title, status, process info (First Reading, Specified Bills, Regulations).
- **Terms/Licensing:** Crown copyright.
- **Rate limits / auth:** None documented.
- **Difficulty (1–5):** **4** (re-rated up from 3 after CMS fingerprint pass — Bootstrap 5 static site on Azure, no `?_format=json` support).
- **Notes:** Alternative legislation source: freelaws.gov.sk.ca (bills and acts text, not procedural data).

## Hansard / Debates

- **Source URL(s):** https://www.legassembly.sk.ca/legislative-business/debates-hansard/ ; https://docs.legassembly.sk.ca
- **Format:** HTML + PDF; digitized back to 1947.
- **Granularity:** Daily.
- **Speaker identification:** Yes; subject + speaker indexes for 1996 forward.
- **Difficulty (1–5):** 2.
- **Notes:** Contact: hansard@legassembly.sk.ca, 306-787-1175. Downloadable indexes are a major asset — would let us link speeches to MLAs without name-fuzz.

## Voting Records / Divisions

- **Source URL(s):** https://www.legassembly.sk.ca/legislative-business/minutes-votes/
- **Format:** HTML Minutes (Votes and Proceedings); digitized March 2003 forward.
- **Roll-call availability:** Yes.
- **Difficulty (1–5):** 3.
- **Notes:** Contact: journals@legassembly.sk.ca, 306-787-0421.

## Committee Activity

- **Source URL(s):** https://www.legassembly.sk.ca/legislative-business/legislative-committees/ ; https://docs.legassembly.sk.ca
- **Format:** HTML; committee docs on docs.legassembly.sk.ca.
- **Data available:** Four standing committees (Crown and Central Agencies, Economy, Intergovernmental Affairs and Justice, Public Accounts); House Management, Private Bills, Privileges.
- **Overlap with existing scanner:** None.
- **Difficulty (1–5):** 2.
- **Notes:** Contact: committees_branch@legassembly.sk.ca, 306-787-9930.

## Existing third-party scrapers

- **opencivicdata/scrapers-ca:** **No `ca_sk` module currently active** (disabled or never built).
- Other: freelaws.gov.sk.ca (acts + bills text, not procedural).

## Status

- [x] Research complete
- [ ] Schema drafted
- [ ] Ingestion prototyped
- [ ] Production ingestion live

## Research-handoff items (Bills + Hansard)

Per [overview.md](./overview.md) rule #5, SK pipelines are gated on user research. SK is the only "no Hansard ingester anywhere" jurisdiction where Hansard is rated *easier* than bills (difficulty 2 vs 4) — likely the right place to start. Specific questions to answer before any code is written:

**Bills:**
- **PDF parsing in scope?** The current README marks SK as ⏸️ Deferred specifically because the primary bill artifact is `progress-of-bills.pdf` with no per-bill HTML URLs. Has the user confirmed PDF parsing (similar to the MB `billstatus.pdf` pattern) is acceptable, or is the team holding out for an HTML/JSON source?
- **freelaws.gov.sk.ca alternative:** That site has bill *text* but not procedural data. Has the user evaluated whether stage-event coverage is essential for SK, or whether bill text + minimal status is enough for a first pass?

**Hansard:**
- **Speaker/subject indexes:** The dossier flags downloadable indexes from 1996 forward as a "major asset — would let us link speeches to MLAs without name-fuzz." Has the user inspected the index format (CSV? PDF? structured HTML?) and confirmed it's machine-readable?
- **Transcript URL pattern:** `docs.legassembly.sk.ca` is named as the document repository. What's the per-sitting URL pattern? PDF or HTML transcripts? The contact (`hansard@legassembly.sk.ca`) may be worth emailing for an authoritative URL template before reverse-engineering.
- **Era boundary:** Digitized back to 1947, but format almost certainly changes between eras. Where does HTML start? An era-branching parser (NL/MB pattern) is likely needed.
- **Politician slug column:** Does any SK roster source publish a stable per-MLA ID/slug? If yes, add it as `politicians.sk_assembly_slug` per CLAUDE.md convention #1 *before* building the speaker resolver.
