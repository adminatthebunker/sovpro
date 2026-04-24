# Embedding Model Comparison — Eval Harness and Selection

**Last updated:** 2026-04-19
**Status:** **Migration shipped.** Qwen3-Embedding-0.6B (instruct) replaced BGE-M3 in production on 2026-04-19. Qwen3-4B-INT8 was the eval winner on raw NDCG but 0.6B was selected for throughput + VRAM headroom; the backfill for all 1.48 M chunks landed via TEI at 50.9 chunks/sec end-to-end. See migration 0023 → 0025 for the schema blue-green, `services/embed/eval/REPORT.md` for the eval write-up, and `docs/posts/linkedin-embedding-rebuild-post.md` for the narrative. The rest of this doc is preserved as the decision-log that led to the switch — do not retrofit it into a present-tense "current state" doc.

This doc is the authoritative tracking document for the Canadian Political Data embedding model bake-off: BGE-M3 (the then-incumbent) vs Qwen3-Embedding-0.6B vs Qwen3-Embedding-4B. It also tracks the throughput-optimization workstream that followed. If this disagrees with `docs/plans/semantic-layer.md`, that doc wins for schema; this one wins for the historical record of the model choice.

## Why now

Two load-bearing facts set the decision window:

1. **The 44th-Parliament federal Hansard is fully embedded.** A clean 7h52m run on 2026-04-17/18 closed out the last 132,127 chunks with zero GPU faults, bringing `speech_chunks` to 242,014 / 242,014 embedded in BGE-M3 fp16 (1024-dim). See `docs/runbooks/resume-after-reboot-2026-04-17-cudnn-fix.md` for the GPU-fix backstory and commits `d3472da` (cuDNN 9.5.1.17 pin) + `7a3135d` (allocator tuning).
2. **Next ingest is ~1M chunks** (historical Parliaments 38–43, roughly 1994 → 2021). Re-embedding 242 k now is cheap; re-embedding 1.24 M later after the backfill is punishing. If a newer model is genuinely better, the time to switch is before the backfill, not after.

Timeline pressure: the October 2026 Alberta independence referendum is the downstream product launch. Every week on embedding optimization is a week not spent on the search UI or referendum spotlight panel. Target: eval + decision within 2–3 working days; optional re-embed + throughput work within one further week.

## Current state — corpus composition

Snapshot taken 2026-04-18 after the 7h52m run completed.

| Dimension | Value |
|---|---:|
| Total chunks | 242,014 |
| Embedded | 242,014 (100%) |
| Pending | 0 |
| Speaker-resolved (FK to `politicians`) | 169,283 (69.9%) |
| Unresolved (Speaker, Chair, procedural staff) | 72,731 (30.1%) |
| English | 184,221 (76.1%) |
| French | 57,793 (23.9%) |
| Median chunk | 209 tokens |
| p95 chunk | 461 tokens |
| Max chunk | 3,052 tokens |

**By year (all federal, 44th Parliament):**

| Year | Chunks | Share |
|---:|---:|---:|
| 2021 | 8,978 | 3.7% |
| 2022 | 63,280 | 26.2% |
| 2023 | 61,284 | 25.3% |
| 2024 | 59,855 | 24.7% |
| 2025 | 35,300 | 14.6% |
| 2026 | 13,317 | 5.5% |

**Throughput baseline (RTX 4050 Laptop GPU, sm_89, 6 GiB, cuDNN 9.5.1.17, BGE-M3 fp16):**

- End-to-end (embed service + scanner-job DB write): **4.7 chunks/sec** steady-state at `batch_size=32`
- GPU-bound fraction: ~97% (server-side elapsed_ms dominates wallclock)
- VRAM cycle: 4.1 GiB ↔ 5.7 GiB (allocator's `garbage_collection_threshold:0.8` is working)
- Thermals: 75–81 °C across 7h52m, no throttle events

The 9.5.1.17 cuDNN pin is empirically stable across the full run (242 k chunks, zero new kernel Xid events vs the pre-fix `Xid 62 + 154` pattern that forced reboots at ~1k chunks). The public-evidence attribution of that crash pattern to a cuDNN 9.1 fp16 MHA bug on sm_89 is our best hypothesis, not a cited public report — the pin stands regardless on empirical grounds.

## Candidate models

All three candidates must fit the 6 GiB VRAM budget on the existing RTX 4050 Laptop GPU. **Local-only**: no cloud GPU rental, no managed embedding APIs (DashScope, Voyage, Cohere, OpenAI). Qwen3-Embedding-8B is out of scope — it does not fit the hardware, and hardware upgrades are a separate conversation.

| Model | Params | Dim | Context | License | MTEB ML (published) | fp16 VRAM (weights) | Expected local throughput |
|---|---:|---:|---:|---|---:|---:|---|
| **BGE-M3** (incumbent) | 568 M | 1024 | 8192 | MIT | ~63 | ~1.1 GiB | 4.7 ch/sec (measured, batch=32); benchmark says 205 ch/sec @ batch=128 on fresh chunks |
| **Qwen3-Embedding-0.6B** | 639 M (≈)  | up to 1024 (Matryoshka) | 32 768 | Apache 2.0 | ~64 | ~1.3 GiB | faster than BGE-M3 at matched batch; large batches feasible |
| **Qwen3-Embedding-4B** | 4 B | up to 2560 (Matryoshka) | 32 768 | Apache 2.0 | ~68–69 (reported) | **~8 GiB fp16 (does NOT fit 6 GiB) / ~5.55 GiB INT8 (fits)** | **measured 8.0 chunks/sec @ batch=8, INT8, max_seq=512** |

Qwen3's **Matryoshka-embeddings** property lets us truncate vectors to a chosen dimensionality without re-training; a 4B-at-1024 variant is a legitimate option if we want its quality without changing downstream schema.

Qwen3 also supports **instruction-aware prompting** — prefixing the query with an `"Instruct: … Query: {q}"` wrapper typically yields a 1–5% gain. We will test both vanilla and instruction-prompted variants and report both.

## Decision rule

Applied after Phase 2 (comparison run) completes:

- **Qwen3-4B beats BGE-M3 by ≥ 5% NDCG@10 AND ≥ 3% cross-lingual Recall@10** → re-embed the corpus with Qwen3-4B locally. Winner: **Qwen3-4B**.
- **Qwen3-0.6B matches or beats BGE-M3 on NDCG@10 AND runs meaningfully faster** → use Qwen3-0.6B as the throughput-favoring alternative if the historical backfill timeline is tight. Winner: **Qwen3-0.6B**.
- **Neither Qwen3 variant clears those thresholds** → stay on BGE-M3, proceed with historical backfill. Winner: **BGE-M3**.

The thresholds are deliberately conservative — a 5% NDCG gain is meaningful; anything smaller isn't worth 8 h of re-embed GPU time + rebuild of the HNSW index.

## Phase 1 — Eval query set

**Target:** 40–50 queries across six categories, stratified by language, with hand-labeled ground truth.

**Labeling process (per query):**

1. Surface top-50 candidates via Postgres full-text (`speech_chunks.tsv` already has a GIN index, config-aware for EN/FR).
2. Hand-judge top-50 down to a top-20 relevance set. Binary labels for V1 (1 = relevant, 0 = not); graded labels are an upgrade we can do later if NDCG ties become common.
3. Persist to `services/embed/eval/queries/queries.jsonl`.

**Schema (one line per query):**

```json
{
  "query_id": "A001",
  "category": "A_euphemism",
  "language": "en",
  "query_text": "speeches arguing for carbon pricing",
  "query_text_alt": null,
  "relevant_chunk_ids": ["uuid1", "uuid2", "…"],
  "notes": "expect cross-party coverage incl. Conservative critique"
}
```

**Category targets:**

| Code | Category | Target count | EN | FR | Notes |
|---|---|---:|---:|---:|---|
| A | Euphemism-robust topic search | 8–10 | 6 | 3 | Same topic, multiple rhetorical framings. |
| B | Cross-lingual retrieval | 8–10 | 4 | 4 | Half EN→FR, half FR→EN. BGE-M3's strongest differentiator. |
| C | Talking-points / script detection | 5–7 | 4 | 2 | Near-duplicate retrieval; use known party-script instances. |
| D | Rhetorical-style / stance matching | 6–8 | 4 | 2 | Same topic, different sides. Tests stance + rationale. |
| E | Bill-specific discussion | 5–7 | 4 | 2 | Given a bill number, find speeches by theme — bill number not required to appear. |
| F | Edge cases | 5–7 | 3 | 2 | Short queries, single-speaker, procedural vs substantive, low-resource topics. |

**Status (2026-04-18):**

| Category | Queries drafted | Queries labeled | Notes |
|---|---:|---:|---|
| A | 9 | 9 | 7 EN + 2 FR. Auto-labeled via `to_tsquery` OR-expansion. |
| B | 8 | 8 | 4 EN→FR + 4 FR→EN. Labels use target-language FTS. |
| C | 5 | 5 | 4 EN + 1 FR. |
| D | 7 | 7 | 5 EN + 2 FR. |
| E | 5 | 5 | 4 EN + 1 FR. |
| F | 6 | 6 | 4 EN + 2 FR. |
| **Total** | **40** | **40** | All 20 hits/query. Labels are Path-3 noisy by design. |

## Phase 2 — Comparison run

**Metrics (all computed per model, both variants for Qwen3):**

| Metric | Scope | Why |
|---|---|---|
| NDCG@10 | all queries | Primary quality metric; handles graded relevance cleanly. |
| Recall@20 | all queries | Catches models that rank relevant chunks just outside top-10. |
| Cross-lingual Recall@10 | Category B only | Isolates multilingual retrieval, where BGE-M3 is historically strong. |
| Query latency p50/p95 | all queries | Bookkeeping for inference-time cost. |

**Sample construction:**

- 5,000 chunks stratified 3,800 EN / 1,200 FR (matches corpus distribution).
- Within each language, stratify across year (2021–2026) and party.
- Every `relevant_chunk_ids` UUID from Phase 1 must appear in the sample (hard requirement — otherwise the sample can't be used to score those queries).
- Persist the sample as a plain UUID list at `services/embed/eval/sample/chunk_ids.txt` so every model eval runs against identical text.

**Per-candidate procedure:**

| Candidate | Model source | Batch | Variants | Expected runtime |
|---|---|---:|---|---:|
| BGE-M3 | already embedded in `speech_chunks.embedding` | — | 1 (existing) | ~2 min (just fetch + score) |
| Qwen3-0.6B | HuggingFace `Qwen/Qwen3-Embedding-0.6B` | 64 | 2 (vanilla, instruct) | < 1 h combined |
| Qwen3-4B | HuggingFace `Qwen/Qwen3-Embedding-4B` | 16 | 2 (vanilla, instruct) | 1–2 h combined |

**Instruction-prompt wrapper (Qwen3 only):**

```
Instruct: Given a parliamentary search query, retrieve relevant Canadian Hansard speech excerpts
Query: {query}
```

Leave documents un-prefixed for V1. A document-side instruction is a possible secondary variant if headline numbers are close.

**Output-file convention:**

- `services/embed/eval/results/YYYY-MM-DD-<model>-<variant>.json`
- Schema contains: per-query metrics, aggregate metrics, run metadata (model hash, batch size, sample seed).

## Phase 3 — Throughput optimizations

Priority-ordered. Stop early if a change regresses retrieval quality or doesn't move throughput.

| # | Change | Location | Expected multiplier | Dependency | Status |
|---:|---|---|---:|---|---|
| 1 | Replace FastAPI+FlagEmbedding wrapper with **HuggingFace TEI** (`ghcr.io/huggingface/text-embeddings-inference:89-1.9`) | `services/embed/` Dockerfile + compose | **2–3×** | none | [ ] |
| 2 | Cap `max_length` at 512 for main stream; route outliers (>1024 tok) through a separate `batch=2, seq=3072` lane | `embed_pending()` in `services/scanner/src/legislative/speech_embedder.py` | 1.2–1.5× | none | [ ] |
| 3 | **Length-sorted batch=64** (BGE-M3; 16 on Qwen3-4B) | `embed_pending()` pre-sort by `token_count` | 1.25–1.4× | #1 (TEI handles this natively) | [ ] |
| 4 | **Content-hash dedup** — sha256 of normalized chunk text, skip re-embedding on hash hit | new migration + `chunk_speeches` / `embed_speech_chunks` commands | Infinite speedup on ~10–20% of historical backfill | schema migration | [ ] |

Each optimization lands as its own commit with a before/after chunks-per-sec measurement recorded in the Results table below.

**Explicitly out of scope (deep-research verdict):**

- **bitsandbytes INT8 weight quantization** — documented to *slow* sub-6.7B models, not speed them up.
- **FP8 on Ada** — no public BGE-M3 FP8 engine; weeks of work.
- **vLLM for embedding** — ~90% CPU-bound on short sequences, wrong tool for encoder pooling.
- **FlashAttention 3** — not available on sm_89.

## Results

### Retrieval quality (Phase 2, 2026-04-18)

| Model | Variant | NDCG@10 | Recall@20 | Cross-ling R@10 | Notes |
|---|---|---:|---:|---:|---|
| BGE-M3 | baseline | 0.336 | 0.286 | 0.081 | incumbent |
| Qwen3-0.6B | vanilla | 0.220 | 0.178 | 0.069 | under BGE-M3 |
| Qwen3-0.6B | instruct | **0.381** | **0.313** | 0.063 | beats BGE-M3 on NDCG/Recall, drops cross-ling |
| Qwen3-4B INT8 | vanilla | 0.236 | 0.199 | 0.088 | under BGE-M3 on NDCG |
| **Qwen3-4B INT8** | **instruct** | **0.395** | **0.350** | **0.106** | **WINNER** — beats BGE-M3 on all three |

### Per-category NDCG@10 (Phase 2)

| Category | n | BGE-M3 | 0.6B-inst | 4B-int8-inst | Winner |
|---|---:|---:|---:|---:|---|
| A_euphemism | 9 | 0.188 | 0.270 | 0.301 | 4B-int8-inst |
| B_crosslingual | 8 | 0.135 | 0.104 | 0.193 | 4B-int8-inst |
| C_script | 5 | 0.362 | 0.422 | 0.430 | 4B-int8-inst |
| D_stance | 7 | 0.307 | 0.363 | 0.358 | 0.6B-inst |
| E_bill | 5 | 0.642 | 0.670 | 0.649 | 0.6B-inst |
| F_edge | 6 | 0.580 | 0.547 | 0.647 | 4B-int8-inst |

4B-int8-instruct wins 4 of 6 categories, 0.6B-instruct wins 2, BGE-M3 wins none against the instruct-prompted Qwen3 variants.

### Query latency (Phase 2)

| Model | Batch | p50 (ms) | p95 (ms) | Notes |
|---|---:|---:|---:|---|
| BGE-M3 | 32 (server) | 118 | 156 | measured over 40 single-query POSTs |
| Qwen3-0.6B | 1 | 20 | ~30 | instruct variant shown; vanilla similar |
| Qwen3-4B INT8 | 1 | 144 | ~180 | instruct variant; bnb int8 overhead included |

### Sample-encode throughput (Phase 2)

| Model | Batch | max_seq | chunks/sec | VRAM peak |
|---|---:|---:|---:|---:|
| Qwen3-0.6B fp16 | 16 | 512 | 39.8 | 2.38 GiB |
| Qwen3-4B INT8 | 8 | 512 | 8.0 | 5.55 GiB |

### Throughput progression (Phase 3)

Each row is a production measurement on the real `embed-speech-chunks` path, not a synthetic benchmark.

| Date | Config | chunks/sec | Notes |
|---|---|---:|---|
| 2026-04-18 | BGE-M3 + FlagEmbedding + batch=32 | 4.7 | baseline (44th Parl run) |
| — | after TEI swap | — | pending |
| — | + max_length cap + outlier lane | — | pending |
| — | + length-sorted batch=64 | — | pending |
| — | + content-hash dedup | — | pending |

## Decisions log

| Date | Decision | Rationale |
|---|---|---|
| 2026-04-18 | Pin cuDNN to 9.5.1.17 in embed Dockerfile (commit `d3472da`) | Base image's cuDNN 9.1 was correlated with Xid 62 + 154 crashes at ~1k chunks on sm_89. 9.5.1.17 is empirically stable across a full 7h52m run. Public-bug attribution is hypothesis; the pin stands on empirical grounds. |
| 2026-04-18 | Re-enable `PYTORCH_CUDA_ALLOC_CONF` allocator tuning (commit `7a3135d`) | On a healthy (post-cuDNN-pin) driver, `expandable_segments:True,garbage_collection_threshold:0.8,max_split_size_mb:512` stabilizes VRAM at 4.1–5.7 GiB without regressing throughput. |
| 2026-04-18 | Bump `JOBS_DEFAULT_TIMEOUT` in `.env` from 7200 s → 36000 s | Single-run drain of 132 k chunks exceeds 2 h at the conservative batch=32 rate. Not committed (`.env` is gitignored). |
| 2026-04-18 | Treat BGE-M3 as baseline for the eval | Already embedded; 0-cost to include. Re-embed cost is asymmetric — cheap to do Qwen3 side, 8 h of GPU to replace BGE-M3. |
| 2026-04-18 | Run Qwen3-4B via bitsandbytes INT8 (contra tracking-doc "skip" note) | fp16 weights (~8 GB) do not fit 6 GiB VRAM; INT8 is the only way to benchmark 4B on this hardware. The "skip int8" reasoning in deep-research applied to decoder generation; encoder-forward INT8 is viable. |
| 2026-04-18 | Path-3 auto-labels (FTS top-20 OR-expansion) for queries.jsonl | Fastest-to-first-results. Labels are keyword-family-biased — real euphemism + cross-lingual performance is *understated* by these labels. Acceptable for *relative* model comparison; would re-label for production claims. |
| 2026-04-18 | Phase 2 verdict: Qwen3-4B INT8 instruct wins the decision rule | NDCG@10 +17.6%, XL R@10 +31% over BGE-M3 baseline. Caveated: INT8 only (fp16 OOMs), labels noisy. See `services/embed/eval/REPORT.md`. No re-embed action taken — awaiting user call on migration + throughput tradeoff. |

## Migration plan (conditional — only if re-embed wins)

Stub; will be elaborated in Phase B9 if Phase 2 decides a model change. Sketch:

1. `pg_dump sovereignwatch` to a backup file; 242 k × 1024-dim vectors = a few GB and 8 h of GPU time we want to protect before any destructive action.
2. New migration `0022_embedding_model_version.sql`:
   - Adds `embedding_model_version TEXT NOT NULL DEFAULT 'bge-m3'` to `speech_chunks`.
   - Adds a second vector column `embedding_next VECTOR(<dim>)` so old and new coexist during transition.
   - HNSW index built on whichever column is queried live.
3. Modify `embed_pending()` to write the new column under `WHERE embedding_next IS NULL`, preserving idempotency.
4. Cut over the retrieval API to `embedding_next` once backfill is complete.
5. Rollback = drop `embedding_next` column, revert `embed_pending` to single-column write.

## Constraints and non-goals

- **Local inference only.** No cloud GPU rentals, no managed embedding APIs. Aligns with the project's FOSS / self-hosted sovereignty ethos.
- **pg_dump before any destructive action.** The current 242 k-chunk embedding represents 8 h of GPU time.
- **Preserve `WHERE embedding IS NULL` idempotency.** Any new architecture must keep resume-from-crash behavior.
- **Do not alter the discriminated `speeches` / `speech_chunks` schema.** Level + province_territory discriminators are load-bearing for future provincial / territorial ingestion.
- **Do not change chunking strategy during this eval.** Hold chunk boundaries constant so all models see identical text inputs. Chunking is a separate future eval.
- **Pangolin / reverse proxy setup is out of scope.** Embed service stays on the internal Docker network.
- **Timeline:** eval complete in 2–3 working days; optional re-embed + optimization complete within a further week.

## Deliverables checklist

- [x] `services/embed/eval/queries/queries.jsonl` — 40 queries, auto-labeled via FTS top-20 OR-expansion.
- [x] `services/embed/eval/scripts/*.py` — harness: `draft_queries.py`, `build_sample.py`, `run_eval_bgem3.py`, `run_eval_qwen3.py`.
- [x] `services/embed/eval/results/*.json` — 5 result files: BGE-M3 baseline, Qwen3-0.6B (vanilla + instruct), Qwen3-4B-INT8 (vanilla + instruct).
- [x] `services/embed/eval/REPORT.md` — verdict written with metric tables and per-category breakdown.
- [ ] (Conditional) Migration `0022_embedding_model_version.sql` + scanner changes + `pg_dump` backup artifact — pending user decision on re-embed.
- [ ] TEI deployment validation: benchmark on a 10 k-chunk subset — pending Phase 3 kickoff.
