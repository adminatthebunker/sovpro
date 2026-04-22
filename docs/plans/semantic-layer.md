# Semantic Layer — Schema, Stack, and Phased Rollout

**Last updated:** 2026-04-19
**Status:** Approved architecture. Schema + chunking rules are still the authority. Embed-stack section updated post-Qwen3 migration; see `docs/plans/embedding-model-comparison.md` for the bake-off that drove the choice and `docs/plans/search-features-handoff.md` for the retrieval-side contract.

This doc translates `docs/plans/national-expansion-scoping.md` answers into a concrete plan. If it disagrees with that doc, that doc wins — come back and fix this one.

## Goals recap

- v1 is a single search box over what politicians have said.
- Local, bootstrapped, self-hosted. No paid APIs in the critical path.
- Next-day freshness. 5% misattribution acceptable at launch.
- Source-language embeddings, multilingual retrieval by default.
- Start federal + provincial. Municipal is phase-2.

## Infrastructure status (2026-04-19)

- **Database image:** built from `db/Dockerfile` extending `postgis/postgis:16-3.4` with `postgresql-16-pgvector` (v0.8.2). `docker compose build db` rebuilds; `pgdata` volume persists.
- **Applied migrations:** 0014 (vector + unaccent) · 0015 (speeches) · 0016 (speech_references) · 0017 (speech_chunks + HNSW/GIN indexes) · 0019 (jurisdiction_sources + seed) · 0020 (correction_submissions) · 0021 (constituency_boundaries temporal) · 0022 (scanner_jobs/schedules) · 0023 (embedding_next Qwen3 column) · 0024 (federal session re-tag) · 0025 (drop legacy BGE-M3 `embedding` column, rename `embedding_next` → `embedding`) · 0026 (politician photo local + socials provenance — two files share the 0026 number, both applied) · 0027 (users, login_tokens, saved_searches) · 0028 (users.email_bounced_at) · 0029 (users.is_admin).
- **Held back:** 0018 (votes + vote_positions) — sits on disk, apply in phase 4 after NT/NU consensus-gov't data informs revisions.
- **Bills coverage:** 10 of 13 sub-national legislatures live (NS, ON, BC, QC, AB, NB, NL, NT, NU, **MB**). SK (PDF-only, single-province investment) and PE + YT (WAF-blocked pair) remain.
- **Speeches coverage:** federal + QC + AB + BC + MB Hansard ingested — **2,035,283 speeches, 2,697,652 chunks, 100 % embedded** with Qwen3-Embedding-0.6B. ON / NS / NB / NL / NT / NU / SK / PE / YT Hansard pipelines are the remaining build-out.
- **Embed service:** HuggingFace **Text Embeddings Inference (TEI)** serving **Qwen3-Embedding-0.6B** at fp16 on the RTX 4050 Mobile. Reachable at `http://tei:80` via TEI-native `POST /embed` or OpenAI-compatible `POST /v1/embeddings`. Model cache in `embedmodels` (mounted at `/data`). Legacy BGE-M3 + BGE-reranker wrapper retired on 2026-04-19; its code still lives at `services/embed/` for rollback but no compose service references it. **Reranker is no longer in the critical path.**
- **Throughput (Qwen3-0.6B fp16 on RTX 4050 Mobile, 2026-04-18 re-embed):** ~75 chunks/sec pure GPU, **50.9 chunks/sec end-to-end** through the scanner's batched-UNNEST write path. 242 k chunks re-embedded in 1 h 19 m. The end-to-end number is the one worth capacity-planning against.
- **Retrieval contract (query-time):** Qwen3-Embedding requires an **instruction prefix on queries, not documents**. Format: `Instruct: Given a parliamentary search query, retrieve relevant Canadian Hansard speech excerpts\nQuery: {user query}`. Without it, NDCG@10 drops from ~0.43 to ~0.22. See `docs/plans/search-features-handoff.md`.
- **Not yet:** remaining provincial Hansard ingesters (ON / NS / NB / NL / NT / NU / SK / PE / YT); hybrid retrieval API endpoint (`/api/v1/search`); corrections-inbox email ingest.

## Stack decisions

| Layer | Choice | Why |
|---|---|---|
| Vector store | **pgvector in existing `sovereignwatch` Postgres** | Single DB, clean joins to `politicians` / `bills`, SQL-native filters. ~10M chunk ceiling with HNSW is above our full-scope estimate. |
| Embedding model | **Qwen3-Embedding-0.6B** (self-hosted, GPU fp16 via TEI) | Multilingual, 1024-dim native. Beat BGE-M3 by +13% NDCG@10 / +9% Recall@20 in the April 2026 bake-off; ~1.6× throughput. Apache 2.0. **Query-time instruction prefix is load-bearing.** |
| Reranker | **None in the critical path** | Qwen3-0.6B retrieval cleared the bar without a cross-encoder. If reintroduced, run as a separate service — don't resurrect the legacy FlagEmbedding wrapper just for it. |
| Sparse retrieval | **Postgres `tsvector`** with English + French configs | No new infra; catches bill numbers / act names that dense misses. |
| Chunking | **One chunk per speaker turn**, token-capped with overlap on long turns | Turns ≤ 512 tokens → one chunk. Longer → split with 50-token overlap. |
| Ingest | **Python async, extended existing scanner** | Same patterns as `legislative/*`. New `speeches/` subpackage. |
| PDF extraction | **Poppler `pdftotext` via `pdf_utils.pdftotext(raw=True/layout=True)`** | Shared helper hoisted from AB Hansard; MB billstatus.pdf landed 2026-04-20. `-raw` for tables whose columns wrap, `-layout` for cleanly-gridded PDFs, default for prose like AB Hansard. `pdfplumber` kept as documented fallback if Poppler ever can't crack a grid. |

## Schema (proposed migrations)

All migrations extend the existing `0006_legislative_bills.sql` pattern — `level` + `province_territory` discriminators, UUIDs, `raw JSONB` and `raw_html` where upstream text matters.

### Migration 0014 — pgvector + tsearch *(applied)*

```sql
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS unaccent; -- for FR tsvector
```

### Migration 0015 — `speeches`

```sql
CREATE TABLE speeches (
  id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  session_id         UUID NOT NULL REFERENCES legislative_sessions(id) ON DELETE CASCADE,
  politician_id      UUID REFERENCES politicians(id) ON DELETE SET NULL,
  level              TEXT NOT NULL CHECK (level IN ('federal','provincial','municipal')),
  province_territory TEXT,

  -- Attribution
  speaker_name_raw   TEXT NOT NULL,
  speaker_role       TEXT,              -- "Minister of Finance" at-time
  party_at_time      TEXT,              -- party at moment of speech
  constituency_at_time TEXT,            -- riding name at moment of speech
  confidence         REAL NOT NULL DEFAULT 1.0, -- speaker-id confidence 0..1

  -- Content
  speech_type        TEXT,              -- 'floor','committee','question_period','statement'
  spoken_at          TIMESTAMPTZ,       -- date/time speech was given
  sequence           INTEGER,           -- order within session-day
  language           TEXT NOT NULL,     -- ISO 639-1: en, fr, iu, ...
  text               TEXT NOT NULL,
  word_count         INTEGER,

  -- Provenance
  source_system      TEXT NOT NULL,     -- 'openparliament','hansard-ab','hansard-on',...
  source_url         TEXT NOT NULL,
  source_anchor      TEXT,              -- paragraph id / href fragment
  raw                JSONB NOT NULL DEFAULT '{}',
  content_hash       TEXT NOT NULL,     -- for dedup (sha256 of normalized text)

  created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),

  UNIQUE (source_system, source_url, sequence) -- natural dedup key
);

CREATE INDEX idx_speeches_politician ON speeches (politician_id, spoken_at DESC);
CREATE INDEX idx_speeches_level_prov ON speeches (level, province_territory);
CREATE INDEX idx_speeches_session    ON speeches (session_id);
CREATE INDEX idx_speeches_spoken_at  ON speeches (spoken_at DESC);
CREATE INDEX idx_speeches_content_hash ON speeches (content_hash); -- dedup lookup
CREATE INDEX idx_speeches_unresolved ON speeches (id) WHERE politician_id IS NULL;
```

### Migration 0016 — `speech_references` (speech→bill / speech→committee)

```sql
CREATE TABLE speech_references (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  speech_id    UUID NOT NULL REFERENCES speeches(id) ON DELETE CASCADE,
  ref_type     TEXT NOT NULL CHECK (ref_type IN ('bill','committee','act','motion')),
  bill_id      UUID REFERENCES bills(id) ON DELETE SET NULL,
  committee_name TEXT,
  mention_text TEXT,
  char_start   INTEGER,
  char_end     INTEGER
);
CREATE INDEX idx_speech_refs_speech ON speech_references (speech_id);
CREATE INDEX idx_speech_refs_bill   ON speech_references (bill_id) WHERE bill_id IS NOT NULL;
```

### Migration 0017 — `speech_chunks` + embeddings

```sql
CREATE TABLE speech_chunks (
  id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  speech_id      UUID NOT NULL REFERENCES speeches(id) ON DELETE CASCADE,
  chunk_index    INTEGER NOT NULL,
  text           TEXT NOT NULL,
  token_count    INTEGER NOT NULL,
  char_start     INTEGER NOT NULL,
  char_end       INTEGER NOT NULL,
  language       TEXT NOT NULL,

  -- Denormalized for fast WHERE-clause filtering before ANN scan
  politician_id      UUID,
  party_at_time      TEXT,
  level              TEXT,
  province_territory TEXT,
  spoken_at          TIMESTAMPTZ,
  session_id         UUID,

  -- Retrieval
  embedding      vector(1024), -- Qwen3-Embedding-0.6B dense (was BGE-M3 pre-0025)
  tsv            tsvector,     -- BM25 lexical
  tsv_config     TEXT,         -- 'english' / 'french' / 'simple'

  created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),

  UNIQUE (speech_id, chunk_index)
);

CREATE INDEX idx_chunks_embedding ON speech_chunks
  USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64);
CREATE INDEX idx_chunks_tsv ON speech_chunks USING gin (tsv);
CREATE INDEX idx_chunks_filters ON speech_chunks (level, province_territory, spoken_at DESC);
CREATE INDEX idx_chunks_politician ON speech_chunks (politician_id, spoken_at DESC);
```

### Migration 0018 — `votes` + `vote_positions`

Drafted but **not applied** until we have two real jurisdictions' data (NT/NU consensus gov't will force revisions).

```sql
CREATE TABLE votes (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  session_id      UUID NOT NULL REFERENCES legislative_sessions(id) ON DELETE CASCADE,
  level           TEXT NOT NULL,
  province_territory TEXT,
  bill_id         UUID REFERENCES bills(id) ON DELETE SET NULL,
  vote_type       TEXT NOT NULL,  -- 'division','voice','acclamation','consensus'
  occurred_at     TIMESTAMPTZ,
  result          TEXT,           -- 'passed','defeated','tied','withdrawn'
  ayes            INTEGER,
  nays            INTEGER,
  abstentions     INTEGER,
  motion_text     TEXT,
  source_system   TEXT NOT NULL,
  source_url      TEXT,
  raw             JSONB NOT NULL DEFAULT '{}',
  created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE vote_positions (
  vote_id        UUID NOT NULL REFERENCES votes(id) ON DELETE CASCADE,
  politician_id  UUID REFERENCES politicians(id) ON DELETE SET NULL,
  politician_name_raw TEXT NOT NULL,
  party_at_time  TEXT,
  position       TEXT NOT NULL,  -- 'yea','nay','abstain','paired','absent'
  PRIMARY KEY (vote_id, politician_id)
);
```

### Migration 0019 — `jurisdiction_sources` (coverage dashboard)

User asked for "a data-table so we can track this in app too". This table doubles as (a) ingest-pipeline status, (b) a public coverage page on the frontend, (c) a machine-readable record of blocked/deferred jurisdictions.

```sql
CREATE TABLE jurisdiction_sources (
  jurisdiction       TEXT PRIMARY KEY,  -- 'federal','AB','BC',...
  legislature_name   TEXT,
  seats              INTEGER,
  bills_status       TEXT,  -- 'live','partial','blocked','none'
  hansard_status     TEXT,
  votes_status       TEXT,
  committees_status  TEXT,
  bills_difficulty   SMALLINT,  -- 1-5 per the plan doc
  hansard_difficulty SMALLINT,
  votes_difficulty   SMALLINT,
  committees_difficulty SMALLINT,
  blockers           TEXT,
  last_verified_at   TIMESTAMPTZ,
  notes              TEXT,
  source_urls        JSONB NOT NULL DEFAULT '{}',
  updated_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

### Migration 0020 — corrections inbox

```sql
CREATE TABLE correction_submissions (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  subject_type    TEXT NOT NULL,  -- 'speech','bill','politician','vote'
  subject_id      UUID,           -- nullable for general feedback
  submitter_email TEXT,
  submitter_name  TEXT,
  issue           TEXT NOT NULL,
  proposed_fix    TEXT,
  status          TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','triaged','applied','rejected','duplicate')),
  reviewer_notes  TEXT,
  received_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  resolved_at     TIMESTAMPTZ
);
```

## Ingest pipeline

```
source (API/HTML/PDF)
      │
      ▼
┌────────────────┐   raw_html cached
│ speeches_fetch │── on failure: retry next run
└───────┬────────┘
        ▼
┌────────────────┐
│ speeches_parse │── speaker-turn split, confidence scoring
└───────┬────────┘
        ▼
┌──────────────────────┐
│ speeches_resolve     │── slug / jurisdiction-id match → politician_id
└──────────┬───────────┘
           ▼
┌──────────────────────┐
│ speeches_chunk       │── speaker-turn = 1 chunk; split long turns
└──────────┬───────────┘
           ▼
┌──────────────────────┐
│ speeches_embed       │── Qwen3-0.6B batch inference via TEI (GPU fp16)
└──────────┬───────────┘
           ▼
┌──────────────────────┐
│ speeches_tsv         │── UPDATE tsv = to_tsvector(language, text)
└──────────────────────┘
```

Each stage is an idempotent Click subcommand. `raw_html` stays on `speeches.raw` (pattern from `bills.raw_html`) so re-parsing and re-chunking don't require refetching.

## Retrieval pipeline

```
query string + optional filters (level, province, party, date range, politician_id)
      │
      ▼
┌──────────────────────────────┐
│ Qwen3-0.6B embed query       │── instruction-wrapped, 1024-dim
└──────────┬───────────────────┘
           ▼
┌──────────────────────┐                   ┌─────────────────────┐
│ HNSW top-50 dense    │   ─── union ───   │ tsquery top-50 BM25 │
└──────────┬───────────┘                   └───────────┬─────────┘
           └────────────────┬──────────────────────────┘
                            ▼
                 ┌──────────────────────┐
                 │ dedup + reciprocal   │
                 │ rank fusion          │
                 └──────────┬───────────┘
                            ▼
                 hydrate speech+politician+bill joins → API response
```

Reranker stage was dropped on 2026-04-19 — Qwen3-0.6B retrieval quality cleared the bar without a cross-encoder. If a future eval argues for putting one back, treat it as a separate service.

## Chunking rules (concrete)

1. One speaker turn = one chunk by default.
2. If turn > 512 tokens: split at paragraph boundary, 50-token overlap, carry same `politician_id` / metadata.
3. Minimum chunk length: 20 tokens. Shorter turns (procedural "Mr. Speaker") are stored on `speeches` but skipped for `speech_chunks` embedding.
4. Chunk token count uses the embedding model's tokenizer (Qwen3 as of 2026-04-19; BGE-M3 tokenizer counts from pre-migration chunks were re-validated at re-embed time and stayed within the 512-token turn cap, so no re-chunking was required).

## Dedup strategy (at ingest)

- Normalize whitespace + unicode; compute `content_hash = sha256(normalized_text)`.
- Before insert: `SELECT id FROM speeches WHERE content_hash = $1 AND politician_id = $2`. If hit, attach additional `source_url` to existing row rather than creating a duplicate.
- Carried-over bills (same bill, new session, new number) are matched via `bills.raw.previous_bill_number` / title similarity at ingest, not schema-level.

## Multilingual handling

- Store source language in `speeches.language`.
- `speech_chunks.tsv_config` chooses between `english` / `french` / `simple` (for IU) for the tsvector.
- Qwen3-Embedding-0.6B handles dense embeddings across all three — one `embedding` column, no per-language branching.
- Retrieval queries embed the query once with Qwen3 and score against all languages. Cross-lingual retrieval works but regressed vs. BGE-M3 (R@10 0.063 vs 0.081); the 2026-04-18 decision was to accept that trade-off for the NDCG / throughput win, because users generally search in one language at a time.

## Person-over-time

- `politicians` stays the canonical person row.
- `politician_terms` gets populated properly (currently sparse) with `office`, `party`, `level`, `province_territory`, `constituency_id`, `started_at`, `ended_at`.
- When a politician crosses jurisdictions (ON MPP → federal MP), it's **one `politicians` row** with multiple `politician_terms` rows.
- At speech-ingest time, `speeches.party_at_time` / `constituency_at_time` are resolved from `politician_terms` matching on `spoken_at`.
- We do **not** adopt OpenCivicData `ocd-person/*` IDs. Our per-jurisdiction slug columns + `politician_terms` covers the Canadian context.

## Constituency-over-time

`constituency_boundaries` is current-only. Add:

```sql
ALTER TABLE constituency_boundaries
  ADD COLUMN effective_from DATE,
  ADD COLUMN effective_to   DATE,
  ADD COLUMN electoral_boundaries_version TEXT;
```

Future boundaries (new `electoral_boundaries_version`) get new rows; the map query picks the row where `spoken_at BETWEEN effective_from AND effective_to`. Backfill historical boundaries is its own project; for phase 1 we mark current rows `effective_from = 2023-01-01` and leave `effective_to` NULL.

## Corrections pipeline

- Public flag button on every speech / claim → POST to `/api/v1/corrections` → row in `correction_submissions`.
- SMTP inbox (e.g. `corrections@thebunkerops.ca`) → poll via IMAP → create `correction_submissions` rows with `submitter_email`.
- Simple admin review UI later; for v1, psql is fine.

## Scale + performance expectations (10M chunks worst case)

| Operation | Expected latency |
|---|---|
| Ingest 1k chunks (embed via TEI/Qwen3 on RTX 4050 Mobile) | ~20 s end-to-end (50.9 chunks/sec measured) |
| HNSW top-50 dense query | < 50 ms at ~2M rows, tuned (`hnsw.ef_search=200`, `iterative_scan=relaxed_order`) |
| tsvector top-50 BM25 | < 100 ms with GIN |
| End-to-end search response | < 2 s target, < 5 s ceiling (no reranker stage in the critical path) |

DB disk at 10M chunks × (1024×4 bytes embed + text + metadata) ≈ **30–50 GB**. Single-host is fine for the life of phase 1 on your bootstrapped infra.

## Phased rollout

### Phase 0 — foundation
- [x] Custom `db/Dockerfile` with pgvector; compose wired to `build: ./db`.
- [x] Migrations 0014 (pgvector + unaccent), 0015–0017 (speeches / refs / chunks + HNSW/GIN), 0019 (jurisdiction_sources + seed), 0020 (corrections), 0021 (constituency temporal).
- [x] Embed service: Qwen3-Embedding-0.6B via TEI on RTX 4050 fp16, Dockerised, model-cache volume shared with the retired BGE-M3 layout, `/embed` + `/v1/embeddings` endpoints live. (BGE-M3 + BGE-reranker wrapper was the prior incarnation; retired 2026-04-19 per `docs/plans/embedding-model-comparison.md`.)
- [x] GPU throughput benchmarked on a real speeches sample (50.9 chunks/sec end-to-end; ~75 chunks/sec pure GPU).
- [x] Frontend coverage page reading `jurisdiction_sources`.
- [x] Federal Hansard ingester + chunker + embedder scanner commands shipped; federal + QC + AB + BC + MB corpora ingested (2.04 M speeches, 2.70 M chunks, 100 % embedded).
- [ ] Extend `politician_terms` backfill to cover every politician currently in `politicians` (not just current term).
- [ ] `/api/v1/search` hybrid endpoint — schema + vectors are ready; route not written.

### Phase 1 — federal Hansard (2–4 weeks)
- Source: extend `politician_openparliament_cache` pattern into normalized `speeches` rows. Openparliament.ca provides structured JSON + speaker slugs.
- Backfill as much of 1994+ as the openparliament API exposes.
- Chunk + embed + index everything.
- Ship the single search box on `/search` scoped to federal Hansard only.

### Phase 2 — ON + QC Hansard (2–4 weeks each)
- ON: uses Drupal `?_format=json` pattern already proven.
- QC: uses FR primary, EN translation — exercises the multilingual pipeline end-to-end.
- Expand search filters to include level + province + language.

### Phase 3 — remaining provincial Hansards (rolling, research-handoff-gated)
- Bills layer already live for NB, NL, NT, NU, MB (no pipeline needed for bills).
- MB Hansard shipped 2026-04-20 (4th provincial Hansard after AB/BC/QC).
- SK — gated on a dedicated PDF-extraction investment (both roster and timeline live in PDFs with a different layout from MB's billstatus.pdf).
- PE + YT — gated on Playwright/browser-automation track.
- Each Hansard pipeline remains gated on the user's research-handoff rule (see `feedback_research_handoff.md`).

### Phase 4 — votes + committees (2–4 weeks each)
- Start with federal (LEGISinfo + openparliament). Apply `votes` migration 0018 only after seeing federal + one provincial dataset.
- Committee transcripts feed into the same `speeches` pipeline with `speech_type = 'committee'`.

### Phase 5 — paid API tier + bulk export
- Snippet-only redistribution policy enforced.
- CSV / Parquet export endpoints.
- Alerts / RSS on saved queries.

### Phase 6 — municipal, non-elected, third-party media
Deferred per goals doc.

## What we're explicitly not doing

- **No dedicated vector DB.** Single Postgres only.
- **No hosted embeddings.** Self-hosted Qwen3-Embedding-0.6B (via TEI) in the critical path.
- **No machine translation.** Source-language embeddings.
- **No browser-automation infra for Cloudflare/Radware-blocked legislatures in phase 1.** Flag them in `jurisdiction_sources` and defer.
- **No federation / per-user accounts in v1.** Corrections are email-only.
- **No streaming ingest.** Next-day cron is the SLA.

## Open follow-ups

- Pick the exact local LLM for PDF-fallback extraction (Ollama + llama3.1 8B? llama.cpp + Qwen2.5 7B?). Benchmark against AB Hansard sample.
- Decide on the English/French tsvector config for NU's Inuktitut (`simple` is a placeholder).
- Write the corrections policy page text.
- Decide the paid-API snippet length (sentence? paragraph? 500 chars?).
