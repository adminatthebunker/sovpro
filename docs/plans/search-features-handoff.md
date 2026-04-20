# Handoff: Canadian Political Data search features

You're taking over to design and build the search API + UI for Canadian Political Data. The retrieval layer is brand-new — no search endpoint exists yet. Below is everything you need to start.

---

## What Canadian Political Data is

Read `docs/goals.md` and `docs/plans/semantic-layer.md` first — those are the product + schema authorities. One-line summary: it's becoming the definitive source of Canadian political data, and the feature you're building is the "single search box over what politicians have said" surfaced in the goals doc.

---

## What the backend has ready for you

### Corpus

- **242,014 speech_chunks** from federal Hansard, 44th Parliament (Nov 2021 → April 2026).
- **Bilingual**: 76% English (184,221), 24% French (57,793).
- **Speaker-resolved** to the `politicians` FK on 69.9% of chunks (the rest are Speakers/Chairs/procedural staff — intentional).
- Chunks have denormalised filter columns already: `level`, `province_territory`, `spoken_at`, `party_at_time`, `session_id`, `politician_id`. Indexed.
- Historical Parliaments 38-43 are now ingested — the full federal corpus sits at **1,716,550 speeches / 2,144,232 chunks** (2026-04-19) with 2,067,709 (96.4%) embedded.

### One vector column

The blue-green migration completed on 2026-04-18 and the legacy column was dropped in migration 0025.

| Column | Model | Dim | Status |
|---|---|---:|---|
| `embedding` | Qwen3-Embedding-0.6B fp16 | 1024 | 2,067,709 / 2,144,232 chunks populated (96.4%) |

HNSW index `idx_chunks_embedding` (`vector_cosine_ops`, `m=16, ef_construction=64`). The model tag is stored on each row in `embedding_model` (`qwen3-embedding-0.6b`); check it before mixing vectors across any future model swap. **Do not reintroduce `_next` suffixed columns** — one canonical column, one HNSW index going forward.

**Dim is 1024.** Qwen3-Embedding-0.6B is natively 1024-dim; no Matryoshka truncation is being applied.

### Eval results that drove the model choice

Read `services/embed/eval/REPORT.md` + `docs/plans/embedding-model-comparison.md`. Bottom line:

- Qwen3-0.6B instruct beats BGE-M3 by **+13% NDCG@10 and +9% Recall@20** on the 5k-chunk eval sample.
- Qwen3-0.6B **loses to BGE-M3 on cross-lingual R@10 (0.063 vs 0.081 — a 22% regression).** We accepted this because users generally search in one language at a time and the speed gain (months → weeks for the 1M-chunk backfill) mattered more.
- The incumbent reranker (BGE-reranker-v2-m3 in `sw-embed`) lifted NDCG to 0.435 but did NOT close the cross-lingual gap. Reranker is still available if you want it.

**You MUST apply instruction prompting at query encoding time**, or NDCG drops to 0.220 (the vanilla Qwen3 number). The wrapper is:

```
Instruct: Given a parliamentary search query, retrieve relevant Canadian Hansard speech excerpts
Query: {user query}
```

Documents are NOT prefixed — indexing already wrote them unwrapped. This is the single most load-bearing detail.

### Embedding serving

- **`tei` service** in docker-compose serves Qwen3-Embedding-0.6B via HuggingFace Text Embeddings Inference. Reachable at `http://tei:80/embed` (TEI-native) or `http://tei:80/v1/embeddings` (OpenAI-compatible). TEI-native body: `{"inputs": [...], "normalize": true}` → returns a bare JSON array of float arrays.
- Measured at 50.9 chunks/sec end-to-end (75 chunks/sec pure GPU) on an RTX 4050 Mobile. For query-time usage you'll be encoding one query at a time — ~20 ms latency.
- The legacy custom embed service (BGE-M3 + BGE-reranker-v2-m3) stays in the tree at `services/embed/` for rollback but has **no compose service** — it won't start accidentally. If you need the reranker back, stand it up as its own compose service rather than co-tenanting with `tei` on the 6 GiB card.

### Postgres full-text search is also populated

`speech_chunks.tsv` (tsvector, GIN-indexed) is config-aware for English + French. Use it for:

1. **BM25 hybrid retrieval** — union with HNSW top-K, dedupe, rerank. The standard pgvector trick.
2. **Cross-lingual fallback** — if the embedding-side cross-lingual is weak for a specific query, the tsv can catch same-language keyword matches on both sides.

### Related tables you'll join in result rendering

- `speeches` (parent) — has `speaker_name_raw`, `source_url`, `source_anchor` (deep-link back to Hansard), `raw_html`.
- `politicians` — FK on `speech_chunks.politician_id`. Has `openparliament_slug` and other jurisdiction-specific IDs; use those for permalinks.
- `legislative_sessions` — FK on `speech_chunks.session_id`. For "what Parliament/session" display.

---

## What does NOT exist yet

- **No `/search` API endpoint.** The Fastify API in `services/api/` has admin routes + some public read endpoints for map/politicians — nothing that hits `speech_chunks`. You're greenfield.
- **No search UI.** The React frontend in `services/frontend/` has a map view, blog, politicians/orgs pages. No search page.
- **No query-side embedding client.** The API doesn't talk to `tei` yet — you'll need to add that.
- **No result-ranking layer** above what HNSW gives you. Design space.
- **No query logging / analytics.** You may want to persist queries + top-K chunk IDs for offline eval quality tracking.

---

## Constraints from `CLAUDE.md` that apply here

Read CLAUDE.md in full. Load-bearing for search:

1. **Fastify + zod** is the API pattern. Strict TypeScript. Any new route schema must be zod-validated.
2. **Don't add hosted API dependencies** (OpenAI, Cohere, managed search services) in the critical path. Everything runs locally.
3. **Don't build per-jurisdiction UI variants.** One speech-search view, filterable by `level` + `province_territory`. The schema's discriminated-union design is load-bearing.
4. **Don't surface non-politician names (Speaker/Chair/staff) as first-class entities.** They show up in result text — fine — but don't offer them as filters or facets.
5. **Admin panel exists**; search is on the public side, no auth required. Public routes live in `services/api/src/routes/*` without the admin bearer guard.
6. **Corrections inbox table exists** (`correction_submissions`) but is not wired up yet — consider a "report this result" link on each chunk for users to flag misattribution.

---

## Design space (the actual product decisions)

These are yours to make; here's the lay of the land.

### 1. Endpoint shape

Minimum viable: `GET /api/v1/search/speeches?q=<query>&limit=10&cursor=<offset>&lang=<en|fr|any>&level=<federal|provincial>&party=<CPC|Lib|…>&from=<date>&to=<date>&politician_id=<uuid>`.

zod schema for response should probably include: chunk_id, speech_id, politician (id + name + slug + party_at_time), spoken_at, snippet (with highlight), similarity_score, source_url.

### 2. Hybrid retrieval strategy

Pure HNSW gets you reasonable quality; BM25+HNSW union is the usual upgrade. Options:

- **Simple union:** HNSW top-50 ∪ BM25 top-50 → dedupe → rerank-by-score.
- **RRF (Reciprocal Rank Fusion):** score = Σ 1/(k + rank). Known-good for this pattern.
- **Staged:** HNSW top-50 → cross-encoder rerank → return top-10. The legacy BGE-reranker-v2-m3 wrapper was retired on 2026-04-19; there is no reranker service running. If a future eval justifies putting one back, stand it up as a distinct compose service — don't co-tenant with `tei` on the 6 GiB card.

Start with pure HNSW on `embedding`, ship it, measure, then layer hybrid in once you have real query patterns.

### 3. Query highlighting / snippet extraction

Postgres `ts_headline(config, text, tsquery)` will give you a highlighted snippet. You need a tsquery — synthesize one from the user query (websearch_to_tsquery handles OR/phrase/negation nicely). If the embedding-stage match isn't keyword-obvious (euphemism/cross-lingual cases), `ts_headline` will return a bland extract — that's acceptable. Don't over-engineer snippet logic; a bad highlight is better than no highlight.

### 4. Pagination

`HNSW` doesn't give deterministic ordering for ties. Use `(similarity DESC, chunk_id ASC)` as the ORDER BY so cursor-based pagination works. `LIMIT / OFFSET` works but expensive past ~1000. Keyset pagination on (score, id) is the right move if we expect deep scrolling.

### 5. Language handling

Query `lang` param options: `en`, `fr`, `any` (default `any`). For `any`, encode the query with the instruct wrapper and run HNSW unfiltered. For `en` or `fr`, also filter `WHERE language = $lang` — this is the recommended default UX, since the eval showed cross-lingual is Qwen3's weak spot.

### 6. Performance target

- Query encode via TEI: ~20 ms.
- HNSW top-20 scan: sub-10 ms on 242k rows; sub-50 ms at 1.2M after historical backfill.
- Result hydration (join speeches + politicians): 5-15 ms.
- **Target p95 end-to-end: 200 ms.** Should be easy.

### 7. Offline eval integration

You've got a 40-query eval set at `services/embed/eval/queries/queries.jsonl` with ground-truth chunk IDs. Wire up a one-shot script `services/embed/eval/scripts/run_eval_search.py` that hits your new `/search` endpoint and recomputes NDCG@10 / Recall@20. Lets you regress-test against the baselines in `services/embed/eval/REPORT.md` every time the search layer changes.

---

## Key files to read in order

1. `CLAUDE.md` — project-wide conventions, schema rules, don't-break list.
2. `docs/goals.md` — product intent.
3. `docs/plans/semantic-layer.md` — schema of record for everything you're querying.
4. `docs/plans/embedding-model-comparison.md` — why Qwen3-0.6B, known quality deltas, decisions log.
5. `services/embed/eval/REPORT.md` — measured retrieval numbers to regress against.
6. `db/migrations/0017_speech_chunks.sql` + `0023_embedding_next.sql` + `0025_drop_legacy_embedding_column.sql` — the final shape of the vector column you'll query (blue-green migration finished in 0025).
7. `services/api/src/routes/admin.ts` — Fastify + zod pattern match for a new route.
8. `services/scanner/src/legislative/speech_embedder.py` — the TEI call pattern (`_embed_batch_tei`) and the `Instruct: … Query: …` prompt handling, mirror it client-side for queries.
9. `services/frontend/src/pages/` — existing page patterns if you're building the search UI too.

---

## Known open questions that need a product call

1. **Politician-scoped search vs corpus-wide search** — same UI with a filter, or a dedicated "speeches by MP X" sub-view?
2. **Saved searches / alerts** — ✅ **shipped 2026-04-20.** Users can save a search (same filter payload `/search/speeches` accepts) and opt into daily or weekly email digests of new matches. Magic-link passwordless accounts gate the feature; alerts run out of a new `alerts-worker` compose service. Schema: `0027_users_and_saved_searches.sql`. Full details: CLAUDE.md § User accounts.
3. **Permalinks for individual chunks** — we have `source_url` + `source_anchor` back to the Hansard source. Should the result card deep-link to the Hansard page, or to an internal `/chunk/<uuid>` page?
4. **Cross-lingual UX** — since Qwen3 is weaker here, should we default `lang=any` (one result list, mixed quality on bilingual queries) or default to user's language + offer a "search in both" toggle?
5. **Result context expansion** — click a chunk to see its parent speech? The surrounding chunks in the same speech?

---

## Getting started checklist

- [ ] Read the eight files above.
- [ ] Confirm `embedding` is populated (`SELECT count(*) FILTER (WHERE embedding IS NOT NULL) FROM speech_chunks;` — ~2.07 M out of 2.14 M as of 2026-04-19).
- [ ] Confirm `tei` service is up (`docker compose up -d tei`, then `docker exec sw-tei curl -s http://localhost:80/health`).
- [ ] Prototype the query-encoding path: POST one query to `tei:80/embed` with the instruct wrapper, make sure you get back a 1024-dim vector.
- [ ] Write the first `/search/speeches` endpoint with zod schema + a basic HNSW query. Ship it, then iterate.
- [ ] Wire up `run_eval_search.py` against your new endpoint. Drop the numbers into `services/embed/eval/REPORT.md` as the third row.

---

Good luck.
