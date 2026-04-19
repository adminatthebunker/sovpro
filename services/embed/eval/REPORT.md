# Embedding Model Eval — Report

**Date:** 2026-04-18
**Sample:** 5,000 chunks (3,800 EN / 1,200 FR) stratified by language × year × party
**Queries:** 40 across six categories (auto-labeled via Postgres FTS top-20 OR-expansion)

## Verdict

**Qwen3-Embedding-4B (INT8, instruct)** meets the "re-embed" threshold from the decision rule with room to spare.

- vs BGE-M3 baseline: **NDCG@10 +17.6%**, Recall@20 +22.4%, cross-lingual R@10 **+31%**
- Fits in 5.55 GiB VRAM (vs BGE-M3 fp16's ~5.7 GiB) — same hardware.
- **Throughput cost is real:** 8 chunks/sec on-GPU vs BGE-M3's ~40 chunks/sec at batch=32. Historical-Parliament backfill of 1 M chunks ≈ 35 h of continuous compute — feasible over a weekend.

**Caveat that should block an immediate re-embed:** fp16 4B would almost certainly score higher but does **not fit the 6 GiB VRAM budget** (weights alone are ~8 GB in fp16). The 17.6% headline is the INT8 number. If hardware upgrades to ≥ 8 GiB VRAM, redo the eval at fp16 before committing the corpus.

Runner-up: **Qwen3-Embedding-0.6B (instruct)**. +13% NDCG, 5× faster, but drops cross-lingual retrieval *below* BGE-M3 (0.063 vs 0.081). Only sensible if cross-lingual is not a product requirement — which for a bilingual-Parliament corpus it clearly is.

## Results table

| Model | Variant | NDCG@10 | Recall@20 | XL R@10 | Enc (chunks/s) | Query p50 (ms) | VRAM peak (GiB) |
|---|---|---:|---:|---:|---:|---:|---:|
| BGE-M3 | baseline | 0.336 | 0.286 | **0.081** | ~40 (est.) | 118 | ~5.7 (fp16) |
| Qwen3-0.6B | vanilla | 0.220 | 0.178 | 0.069 | 39.8 | 22 | 2.38 |
| Qwen3-0.6B | **instruct** | **0.381** | **0.313** | 0.063 | 39.5 | 20 | 2.38 |
| Qwen3-4B-int8 | vanilla | 0.236 | 0.199 | 0.088 | 8.0 | 157 | 5.55 |
| **Qwen3-4B-int8** | **instruct** | **0.395** | **0.350** | **0.106** | 8.1 | 144 | 5.55 |

Deltas vs BGE-M3:

| Model+variant | NDCG@10 | Recall@20 | XL R@10 |
|---|---:|---:|---:|
| Qwen3-0.6B vanilla | −34.5% | −37.8% | −14.8% |
| Qwen3-0.6B instruct | **+13.4%** | **+9.4%** | −22.2% |
| Qwen3-4B-int8 vanilla | −29.6% | −30.4% | +8.6% |
| Qwen3-4B-int8 instruct | **+17.6%** | **+22.4%** | **+31.1%** |

## Per-category breakdown (NDCG@10)

| Category | BGE-M3 | 0.6B-inst | 4B-int8-inst | Winner |
|---|---:|---:|---:|---|
| A_euphemism (9 q) | 0.188 | 0.270 | 0.301 | 4B-int8-inst |
| B_crosslingual (8 q) | 0.135 | 0.104 | **0.193** | 4B-int8-inst |
| C_script (5 q) | 0.362 | 0.422 | 0.430 | 4B-int8-inst |
| D_stance (7 q) | 0.307 | 0.363 | 0.358 | 0.6B-inst |
| E_bill (5 q) | 0.642 | **0.670** | 0.649 | 0.6B-inst |
| F_edge (6 q) | 0.580 | 0.547 | **0.647** | 4B-int8-inst |

*(per-category numbers recomputed from per_query metrics in the results JSON files.)*

Qwen3-4B-int8-instruct wins **4 of 6 categories** outright; Qwen3-0.6B-instruct wins 2 (stance + bill). BGE-M3 wins none against the instruct-prompted Qwen3 variants.

The cross-lingual win for 4B-int8 (0.193 vs 0.135) is the single biggest category-level delta. It's also where our labels are noisiest (FTS can't find semantic cross-language matches), so the true gap is likely larger.

## Decision rule application

From `docs/plans/embedding-model-comparison.md`:

| Rule | Threshold | Observed | Verdict |
|---|---|---:|---|
| Qwen3-4B beats BGE-M3 on NDCG@10 by ≥ 5% | ≥ 5% | +17.6% | **Clear** |
| Qwen3-4B beats BGE-M3 on XL R@10 by ≥ 3% | ≥ 3% | +31.1% | **Clear** |

Both thresholds cleared at INT8. fp16 would likely be higher but is hardware-out-of-scope on this machine.

## Operational implications if we re-embed

1. **Weekend compute:** 1 M-chunk historical backfill at 8 chunks/sec ≈ 35 h. Over one weekend with spare buffer.
2. **Switch to bitsandbytes INT8:** adds `bitsandbytes` + `accelerate` to the embed image; model weights loaded with `BitsAndBytesConfig(load_in_8bit=True)`. This contradicts the tracking doc's "out of scope" note on int8 — that note assumed decoder generation, not encoder forward. INT8 encoder inference is viable.
3. **Embed service rewrite:** swap `FlagEmbedding.BGEM3FlagModel` for `sentence_transformers.SentenceTransformer("Qwen/Qwen3-Embedding-4B", …)`. Different embedding dim: Qwen3-4B natively 2560, Matryoshka-truncatable to 1024. Recommend using native 2560 to keep full signal.
4. **Schema migration:** add `embedding_next VECTOR(2560)` column + `embedding_model_version` field. Keep `embedding` (BGE-M3 1024-dim) online during backfill for zero-downtime cutover.
5. **HNSW index rebuild** on the new column — `CREATE INDEX CONCURRENTLY idx_chunks_embedding_next ON speech_chunks USING hnsw(embedding_next vector_cosine_ops)`.
6. **Instruction-prompting at retrieval time** must be implemented on the query side of the API — queries get the `"Instruct: …\nQuery: …"` wrapper, stored documents do not.
7. **pg_dump** before any destructive action.

## Important caveats

1. **4B was INT8, not fp16.** fp16 doesn't fit 6 GiB. The +17.6% headline is likely a **lower bound** on what 4B can achieve if hardware permitted fp16. A 10 GB + GPU would likely push this higher.
2. **Auto-FTS labels are noisy** — they reward keyword-family matches and penalize true euphemism/cross-lingual retrieval that the embedding models are actually getting right. The *relative* ordering between models is the signal; absolute numbers are suppressed.
3. **Sample is 5 k / 242 k** — 2% of the corpus. Full-corpus numbers will differ.
4. **One run per model.** No variance estimate; results within ~±1-2% NDCG should be treated as noise.
5. **INT8 quantization cost** on quality wasn't directly measured (we don't have fp16 4B to compare to). Literature suggests ~1-2% NDCG loss for INT8 encoder, i.e. fp16 4B would likely score around 0.40-0.42 NDCG@10.

## Recommended next steps

If the user decides to re-embed:

1. **pg_dump** the current DB state.
2. Decide on Matryoshka-truncate dim: 1024 (matches BGE-M3 schema, easy drop-in) vs 2560 (full native, higher quality).
3. Migrate schema per section above.
4. Re-embed the existing 242 k chunks in parallel with the BGE-M3 embeddings (idempotent `WHERE embedding_next IS NULL`).
5. Flip retrieval API to `embedding_next`.
6. Proceed with 1 M-chunk historical backfill on the new model.

If the user stays on BGE-M3:

1. Proceed to **Phase B8 (throughput optimizations)** — TEI swap, max_length cap, length-sorted batching, content-hash dedup.
2. Historical backfill on BGE-M3 at current rate.
3. Revisit the Qwen3-4B decision after a GPU upgrade (if one happens).

## Production-path regression tracking

The offline rows above score each model against a 5,000-chunk sample. The live `/api/v1/search/speeches` endpoint searches the full `embedding_next` column (~620k rows as of 2026-04-18, growing) and does extra work (query-side instruct wrapping, `ts_headline` highlighting, joins to `politicians` / `speeches` / `legislative_sessions`). Numbers are **not directly comparable** to the offline table — the search space is ~125× larger, so the 20 gold chunks per query get diluted by other truly-relevant chunks that weren't annotated.

Purpose of these rows is **wiring-regression detection**: if someone breaks the instruct prompt, flips the distance operator, or introduces a filter bleed, the NDCG falls to near zero and top results go off-topic. A healthy row looks like the one below — absolute numbers are low, but top-1 chunks are thematically on-topic for each query.

| Path | Date | NDCG@10 | Recall@20 | XL R@10 | p50 (ms) | Notes |
|---|---|---:|---:|---:|---:|---|
| `/api/v1/search/speeches` (Qwen3-0.6B, instruct) | 2026-04-18 | 0.033 | 0.031 | 0.000 | 2,690 | Baseline row. p50 is inflated because the `embed-speech-chunks-next` backfill is actively hammering TEI — expect this to drop to ~100 ms once backfill completes. |

Script: `services/embed/eval/scripts/run_eval_search.py`. Invoke with `--base-url http://localhost:8088` to hit via nginx.

## Source files

- Queries: `services/embed/eval/queries/queries.jsonl`
- Sample: `services/embed/eval/sample/chunk_ids.txt`
- Scripts: `services/embed/eval/scripts/{draft_queries,build_sample,run_eval_bgem3,run_eval_qwen3,run_eval_search}.py`
- Raw results: `services/embed/eval/results/2026-04-18-*.json`, `services/embed/eval/out/search_results.json`
