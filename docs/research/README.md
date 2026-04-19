# Jurisdiction Research

This directory holds **one self-contained research dossier per jurisdiction** — federal plus all 13 Canadian provinces and territories. Each file is reviewable on its own; you do not need to read the others (or this index) to understand what one jurisdiction looks like.

For the cross-cutting context — schema migrations, scanner-module conventions, the probe hierarchy, the research-handoff protocol, the comparison matrix, licensing notes, and shared blockers — see [`overview.md`](./overview.md). That file is the authority on *how* we approach research; the per-jurisdiction files are *what we found*, per place.

## Index

### Overview
- [Cross-cutting overview](./overview.md) — schema log, scanner-module conventions, probe hierarchy, research-handoff protocol, comparison matrix, licensing, known blockers, next steps.

### Federal
- [Federal](./federal.md) — House of Commons via openparliament.ca mirror; only Canadian legislature with a comprehensive third-party portal we can lean on.

### Provinces (by region, west to east)
- [British Columbia](./british-columbia.md) — ✅ **Bills live** via LIMS PDMS JSON. GraphQL member API also available.
- [Alberta](./alberta.md) — ✅ **Bills live** via Assembly Dashboard server-rendered HTML. Committees pre-existing. Hansard PDF-only.
- [Saskatchewan](./saskatchewan.md) — ⏸️ **Deferred** (PDF-only progress-of-bills). Hansard well-indexed.
- [Manitoba](./manitoba.md) — ⏸️ **Deferred** (PDF-only billstatus). Stage timeline locked behind `billstatus.pdf`.
- [Ontario](./ontario.md) — ✅ **Bills live** via HTML scrape; Drupal `?_format=json` pipeline available as later upgrade.
- [Quebec](./quebec.md) — ✅ **Bills live** via donneesquebec.ca CSV + RSS + detail HTML. Bilingual.
- [New Brunswick](./new-brunswick.md) — ✅ **Bills live** via two-step legnb.ca HTML scrape.
- [Nova Scotia](./nova-scotia.md) — ✅ **Bills live** via Socrata API (easiest source in country); per-bill HTML cache blocked by WAF budget.
- [Prince Edward Island](./prince-edward-island.md) — ⏸️ **Deferred** (Radware ShieldSquare CAPTCHA).
- [Newfoundland & Labrador](./newfoundland-labrador.md) — ✅ **Bills live** via single-page session table; sponsor data not exposed.

### Territories
- [Yukon](./yukon.md) — ⏸️ **Deferred** (Cloudflare Bot Management blockade).
- [Northwest Territories](./northwest-territories.md) — ✅ **Bills live** via ntassembly.ca Drupal 9. Consensus government — no sponsors by design.
- [Nunavut](./nunavut.md) — ✅ **Bills live** via Drupal 9 single-page view. Consensus government — no sponsors by design.

## Status legend

- ✅ **Live** — production ingestion running; data in `bills` / `bill_events` / `bill_sponsors`.
- 🚧 **In progress** — schema or ingester partially built.
- ⏸️ **Deferred** — research complete; ingestion blocked on tooling, infra, or upstream changes.
- ⛔ **Blocked** — upstream is hostile or absent; needs alternative path.

## Scope

These dossiers cover four legislative-data layers per jurisdiction:

1. **Bills & Legislation** — proposed laws and their stage timelines.
2. **Hansard / Debates** — verbatim transcripts of chamber proceedings.
3. **Voting Records / Divisions** — recorded roll-call votes.
4. **Committee Activity** — memberships, meetings, reports.

Plus standard front-matter (legislature name, seats, next election) and a Status checklist.

## Editing convention

When you complete or change something for a jurisdiction, update its file's "Status" section and (if material) its difficulty rating or blocker note. Keep one fact in one file — if a finding affects every jurisdiction, write it in the cross-cutting plan doc, not per-jurisdiction.
