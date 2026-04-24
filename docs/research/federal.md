# Federal — Legislative Data Research

> Standalone research dossier for the **Parliament of Canada**. Cross-cutting context (schema, scanner modules, probe hierarchy, research-handoff protocol) lives in [`overview.md`](./overview.md).

**Legislature:** House of Commons of Canada | **Website:** https://www.ourcommons.ca | **Seats:** 338 (rising to 343 with 2024 redistribution) | **Next election:** By 2029-10-20

**Status snapshot (2026-04-19):** ✅ Politicians + bills + Hansard live. We mirror openparliament.ca rather than scraping `ourcommons.ca` or `parl.ca` directly — federal is the only Canadian legislature with a comprehensive third-party portal we can lean on.

---

## Why federal is structurally different

Every province and territory ships its own legislative-business site with its own format quirks. Federal is the one jurisdiction where a long-running civic-tech project (**openparliament.ca**, by Michael Mulley) has already done the unification work — bills, Hansard, votes, committees, member metadata — and exposes it via a JSON API.

We mirror openparliament rather than building federal-specific scrapers because:
1. The data is already cleanly normalized (debates, statements, bills, committees, votes).
2. openparliament has historical depth back to **1994** for Hansard, plus rich biographical detail.
3. Building a parallel `ourcommons.ca` scraper would duplicate work that's already maintained.

The trade-off: we depend on openparliament's continued operation. If it goes down or changes its API, federal ingestion stops. As a hedge, we persist `raw` payloads on every speech / bill / member row so re-parsing without re-fetching is possible.

## Bills & Legislation

- **Source URL(s):** openparliament.ca JSON API endpoints (`/bills/`, `/bills/{session}/{number}/`); LEGISinfo as upstream fallback (https://www.parl.ca/legisinfo/).
- **Format:** JSON via openparliament.ca; LEGISinfo provides XML and CSV exports as the official upstream.
- **Fields captured upstream:** Bill number, title (EN + FR), introduced date, status, sponsor (linked to politician), text url, related debates, session.
- **Terms/Licensing:** Crown copyright. House of Commons publications are reproducible for non-commercial / civic-transparency use. openparliament's API is freely usable.
- **Rate limits / auth:** openparliament asks for a polite User-Agent and a contact email. No hard rate-limit documented but we delay between requests.
- **Difficulty (1–5):** **2** — a JSON API plus the LEGISinfo backstop.
- **Notes:** Federal bills are stored in the same `bills` / `bill_events` / `bill_sponsors` schema as provincial bills, with `level='federal'`.

## Hansard / Debates

- **Source URL(s):** openparliament.ca `/debates/` walk; canonical upstream is https://www.ourcommons.ca/DocumentViewer/en/house/latest/hansard
- **Format:** JSON per debate day with structured `Statement` records (one per speaker turn). Each statement has `time`, `politician`, `attribution` (party / constituency at-time-of-speech), and `content_en` / `content_fr`.
- **Granularity:** One row in `speeches` per spoken turn. Chunked further into `speech_chunks` for embedding.
- **Speaker identification:** **By openparliament `politician_slug`** → `politicians.openparliament_slug` FK. Presiding-officer turns ("The Speaker", "Acting Speaker", "Deputy Speaker") are resolved to the actual seated MP via `legislative/acting_speaker_resolver.py` (added in commit `50eb5ec`).
- **Difficulty (1–5):** **2**.
- **Coverage:** Hansard back to **1994-01-17** (35th Parliament onward) is on openparliament.

### Pipeline: `ingest-federal-hansard`

```
docker compose run --rm scanner python -m src ingest-federal-hansard \
    --parliament 44 --session 1 [--since YYYY-MM-DD] [--until YYYY-MM-DD]
```

- Tags every speech with the `(parliament, session)` passed in.
- **Auto-derives `--since` / `--until`** from the parliament/session bounds when neither flag is provided. Without this guard, the underlying `/debates/` walk enumerates *every* sitting day openparliament has indexed (back to 1994) and tags them all with whichever session was named — exactly how 896k speeches were mis-labeled as P43-S2 on 2026-04-18 before commit `08c3644` fixed it.
- Idempotent via `UNIQUE (source_system, source_url, sequence)` on `speeches`. Re-runs over the same date range are safe.
- Migration `0024_fix_federal_session_tagging.sql` repaired previously mis-tagged rows.

### Pipeline: `legislative/speech_chunker.py` + `legislative/speech_embedder.py`

- Chunker splits each speech into ~512-token windows with overlap, lands in `speech_chunks`.
- Embedder calls `embed:8000` (BGE-M3, 1024-dim, fp16 on RTX 4050 Mobile) for each chunk and writes the vector back to `speech_chunks.embedding`.
- Single canonical `embedding` column after migration `0025_drop_legacy_embedding_column.sql` collapsed the dual-column transition.
- Throughput on RTX 4050: **~125 texts/sec at batch=64**, **~205 at batch=128**. 1M federal speeches embed in ~80 min of continuous compute.

## Voting Records / Divisions

- **Source URL(s):** openparliament.ca `/votes/` API; canonical upstream https://www.ourcommons.ca/Members/en/votes (also has CSV export per session).
- **Format:** JSON via openparliament; CSV at source. Each vote includes party-line tally + per-MP position.
- **Roll-call availability:** Yes, every recorded division back to 1994 is named-MP-resolved.
- **Difficulty (1–5):** **2**.
- **Status:** Migration `0018_votes.sql` exists on disk but **intentionally unapplied** — waiting until we model NT/NU consensus-government decisions cleanly so the same schema works for partisan and non-partisan jurisdictions. Federal voting ingestion is not blocked by code, only by that schema decision.

## Committee Activity

- **Source URL(s):** openparliament.ca `/committees/` walk; canonical upstream https://www.ourcommons.ca/Committees/en/Home.
- **Format:** JSON per committee meeting (memberships, transcripts, reports).
- **Data available:** All standing committees + standing joint committees + special committees with member rosters and meeting minutes.
- **Overlap with existing scanner:** `ingest-committees-federal` CLI already exists.
- **Difficulty (1–5):** **2**.
- **Notes:** Committee Hansard ("Evidence") is a separate document type from chamber Hansard — same pipeline-able shape, but currently de-prioritized while we get chamber speeches end-to-end.

## Existing third-party scrapers / data sources

- **openparliament.ca** — primary upstream. JSON API mirrors federal legislative business since 1994.
- **opencivicdata/scrapers-ca** — `ca_federal` representative metadata (rep contact info, photos). We use Open North Represent for the same purpose.
- **Open North Represent API** (`represent.opennorth.ca`) — reps + ridings; complementary to openparliament's deeper legislative data.

## Files involved (federal-specific)

| Concern | Path |
|---|---|
| Politician cache table | `db/migrations/0004_openparliament_cache.sql` |
| Activity feed table | `db/migrations/0005_openparliament_activity.sql` |
| Federal session-tag fix | `db/migrations/0024_fix_federal_session_tagging.sql` |
| Hansard ingester | `services/scanner/src/legislative/federal_hansard.py` |
| Politician backfill | `services/scanner/src/legislative/politicians_op_backfill.py` |
| Acting-speaker resolver | `services/scanner/src/legislative/acting_speaker_resolver.py` |
| Speech chunker | `services/scanner/src/legislative/speech_chunker.py` |
| Speech embedder | `services/scanner/src/legislative/speech_embedder.py` |
| Slug resolver | `services/scanner/src/resolve_openparliament.py` |
| API routes | `services/api/src/routes/openparliament.ts` |
| Frontend tab | `services/frontend/src/components/PoliticianOpenparliamentTab.tsx` |
| Frontend timeline | `services/frontend/src/components/PoliticianParliamentTimeline.tsx` |

## CLI reference

| Command | Purpose |
|---|---|
| `ingest-federal-hansard --parliament N --session S` | Pull speeches for one session. Auto-derives date bounds. |
| `backfill-politicians-openparliament` | Upsert federal MPs from openparliament; populate `politicians.openparliament_slug`. Re-runs speech/chunk resolution by default. |
| `backfill-politician-terms-openparliament` | Populate `politician_terms` from openparliament's role history. |
| `resolve-openparliament-slugs` | Re-link orphaned speech rows to politicians by slug. |
| `enrich-socials-openparl` | Pull MP social handles from openparliament profile pages. |
| `ingest-committees-federal` | Pull committee meetings + memberships. |

## Status

- [x] Politicians ingested (all current federal MPs + historical roster from openparliament)
- [x] `politicians.openparliament_slug` populated and FK-joined to speeches
- [x] Hansard ingester operational (`ingest-federal-hansard`)
- [x] Session-tagging fixed (commit `08c3644` + migration `0024`)
- [x] Presiding-officer attribution resolved (commit `50eb5ec`)
- [x] Speech chunker + embedder running on GPU
- [x] Frontend surfaces (tab + timeline) showing per-politician federal activity
- [ ] Bills ingestion via openparliament JSON (LEGISinfo-only at present)
- [ ] Votes ingestion (gated on `0018_votes.sql` apply, which is gated on consensus-gov't modeling)
- [ ] Committees full pipeline (`ingest-committees-federal` exists; not on a schedule)

## Open issues

- **Embed-service regression noted 2026-04-17** (memory `project_embed_regression.md`): a recent run dropped from ~71k chunks to ~448. Suspected to be a code regression in `speech_embedder.py` or its caller, not hardware. Worth checking commit `bc46b7d` first if/when revisiting.
- **Embedding throughput** is GPU-bound on the RTX 4050; if we move embedding off-host we lose the 80-minutes-for-1M-speeches benchmark. Document the trade-off before any infra change.
