# Semantic Layer — Pipeline + Rollout

**Last updated:** 2026-04-25 (split out of the original `semantic-layer.md`).
**Status:** Phase 0 complete. Federal + 7 provincial Hansards ingested, 3.4 M chunks 100 % embedded. Hybrid `/api/v1/search` route + remaining provincial Hansard pipelines (ON / NT / NU / SK / PE / YT) are the next-up work.

This doc covers **how data flows**: ingest pipeline, retrieval pipeline, chunking rules, dedup, multilingual handling, the operational side of corrections, phased rollout, and open follow-ups. For **what the data looks like** — tables, indexes, decisions of record, capacity expectations — see [`semantic-layer-schema.md`](./semantic-layer-schema.md). The retrieval-side public contract for `/api/v1/search` lives in [`search-features-handoff.md`](./search-features-handoff.md).

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

The query-time instruction-wrapping rule (without it, NDCG@10 drops from ~0.43 to ~0.22) and the public `/api/v1/search` shape are documented in [`search-features-handoff.md`](./search-features-handoff.md).

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

## Corrections pipeline

The `correction_submissions` table itself lives in [`semantic-layer-schema.md`](./semantic-layer-schema.md) § Migration 0020. Operational flow:

- Public flag button on every speech / claim → `POST /api/v1/corrections` → row in `correction_submissions`.
- SMTP inbox (e.g. `corrections@thebunkerops.ca`) → poll via IMAP → create `correction_submissions` rows with `submitter_email`. **Not yet built** — flagged in `TODO.md` under priority #1.
- Simple admin review UI later; for v1, psql is fine.

## Phased rollout

### Phase 0 — foundation
- [x] Custom `db/Dockerfile` with pgvector; compose wired to `build: ./db`.
- [x] Migrations 0014 (pgvector + unaccent), 0015–0017 (speeches / refs / chunks + HNSW/GIN), 0019 (jurisdiction_sources + seed), 0020 (corrections), 0021 (constituency temporal).
- [x] Embed service: Qwen3-Embedding-0.6B via TEI on RTX 4050 fp16, Dockerised, model-cache volume shared with the retired BGE-M3 layout, `/embed` + `/v1/embeddings` endpoints live. (BGE-M3 + BGE-reranker wrapper was the prior incarnation; retired 2026-04-19 per [`docs/archive/embedding-eval-2026-04.md`](../archive/embedding-eval-2026-04.md).)
- [x] GPU throughput benchmarked on a real speeches sample (50.9 chunks/sec end-to-end; ~75 chunks/sec pure GPU).
- [x] Frontend coverage page reading `jurisdiction_sources`.
- [x] Federal Hansard ingester + chunker + embedder scanner commands shipped; federal + QC + AB + BC + MB + NS + NB + NL corpora ingested (2.57 M speeches, 3.40 M chunks, 100 % embedded).
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

## What we're explicitly not doing (rollout / phasing)

The decisions of record on what *not* to do at the pipeline / phasing level:

- **No browser-automation infra for Cloudflare/Radware-blocked legislatures in phase 1.** Flag them in `jurisdiction_sources` and defer.
- **No federation / per-user accounts in v1.** Corrections are email-only.
- **No streaming ingest.** Next-day cron is the SLA.

Architecture-side "what we're not doing" (no dedicated vector DB, no hosted embeddings, no machine translation) is in [`semantic-layer-schema.md`](./semantic-layer-schema.md).

## Open follow-ups

- Pick the exact local LLM for PDF-fallback extraction (Ollama + llama3.1 8B? llama.cpp + Qwen2.5 7B?). Benchmark against AB Hansard sample.
- Decide on the English/French tsvector config for NU's Inuktitut (`simple` is a placeholder).
- Write the corrections policy page text.
- Decide the paid-API snippet length (sentence? paragraph? 500 chars?).
