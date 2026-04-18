"""Qwen3-0.6B instruct retrieval + BGE-reranker-v2-m3 rerank eval.

Tests the hypothesis that the small encoder's weakness on cross-lingual
(Category B R@10 = 0.063 vs BGE-M3's 0.081) can be closed by a cross-
encoder rerank pass. Reranker is multilingual; it's already in
production (loaded by sw-embed on first /rerank call).

Pipeline:
    1. Load Qwen3-Embedding-0.6B (fp16). Encode sample + queries.
    2. Per query, cosine-rank the full 5000-doc sample; keep top-100.
    3. Unload Qwen3 to free VRAM.
    4. Load BGE-reranker-v2-m3 (fp16). For each query, score the 100
       (query, doc) pairs with the cross-encoder.
    5. Re-order the 100 by reranker score; take top-20 as the final ranking.
    6. Compute metrics.

Run inside a one-shot container (sw-embed must be stopped first so we
own the GPU). Env vars:
    MODEL_NAME        default "Qwen/Qwen3-Embedding-0.6B"
    VARIANT           default "instruct"
    BATCH_SIZE        default 32  (bigger — we have headroom vs. 4B run)
    MAX_SEQ_LEN       default 512
    RERANK_MODEL      default "BAAI/bge-reranker-v2-m3"
    RERANK_TOPK       default 100  (how deep the first-stage cosine ranks)
    RERANK_BATCH      default 32
"""
from __future__ import annotations

import asyncio
import gc
import json
import math
import os
import statistics
import time
from pathlib import Path

import asyncpg
import numpy as np
import torch
from sentence_transformers import SentenceTransformer, CrossEncoder


IN_QUERIES = Path("/tmp/queries.jsonl")
IN_SAMPLE = Path("/tmp/chunk_ids.txt")
OUT_PATH = Path("/tmp/rerank_results.json")

MODEL_NAME = os.environ.get("MODEL_NAME", "Qwen/Qwen3-Embedding-0.6B")
VARIANT = os.environ.get("VARIANT", "instruct")
BATCH_SIZE = int(os.environ.get("BATCH_SIZE", "32"))
MAX_SEQ_LEN = int(os.environ.get("MAX_SEQ_LEN", "512"))
RERANK_MODEL = os.environ.get("RERANK_MODEL", "BAAI/bge-reranker-v2-m3")
RERANK_TOPK = int(os.environ.get("RERANK_TOPK", "100"))
RERANK_BATCH = int(os.environ.get("RERANK_BATCH", "32"))

INSTRUCT_TASK = "Given a parliamentary search query, retrieve relevant Canadian Hansard speech excerpts"


def ndcg_at_k(ranking: list[str], relevant: set[str], k: int = 10) -> float:
    gains = [1.0 if ranking[i] in relevant else 0.0 for i in range(min(k, len(ranking)))]
    dcg = sum(g / math.log2(i + 2) for i, g in enumerate(gains))
    ideal_hits = min(len(relevant), k)
    idcg = sum(1.0 / math.log2(i + 2) for i in range(ideal_hits))
    return dcg / idcg if idcg > 0 else 0.0


def recall_at_k(ranking: list[str], relevant: set[str], k: int) -> float:
    if not relevant:
        return 0.0
    hits = sum(1 for p in ranking[:k] if p in relevant)
    return hits / len(relevant)


def format_query(q_text: str) -> str:
    if VARIANT == "instruct":
        return f"Instruct: {INSTRUCT_TASK}\nQuery: {q_text}"
    return q_text


async def fetch_sample_texts(chunk_ids: list[str]) -> list[dict]:
    pool = await asyncpg.create_pool(os.environ["DATABASE_URL"])
    assert pool is not None
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id::text AS id, text, language
              FROM speech_chunks
             WHERE id = ANY($1::uuid[])
               AND embedding IS NOT NULL
            """,
            chunk_ids,
        )
    await pool.close()
    return [{"id": r["id"], "text": r["text"], "language": r["language"]} for r in rows]


def main() -> None:
    queries: list[dict] = []
    with IN_QUERIES.open() as fh:
        for line in fh:
            queries.append(json.loads(line))
    print(f"  loaded {len(queries)} queries")

    sample_ids: list[str] = []
    with IN_SAMPLE.open() as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            sample_ids.append(line)
    print(f"  loaded {len(sample_ids)} sample chunk IDs")

    t0 = time.perf_counter()
    sample = asyncio.run(fetch_sample_texts(sample_ids))
    print(f"  fetched {len(sample)} sample texts in {time.perf_counter()-t0:.1f}s")

    # ── Stage 1: first-stage retrieval with Qwen3 ──────────────────
    print(f"\n── Stage 1: first-stage encoder ({MODEL_NAME}, variant={VARIANT}) ──")
    t0 = time.perf_counter()
    encoder = SentenceTransformer(
        MODEL_NAME,
        model_kwargs={"torch_dtype": torch.float16},
        device="cuda",
    )
    encoder.max_seq_length = MAX_SEQ_LEN
    print(f"  loaded in {time.perf_counter()-t0:.1f}s  (VRAM {torch.cuda.memory_allocated()/1e9:.2f} GiB)")

    t0 = time.perf_counter()
    doc_vecs = encoder.encode(
        [s["text"] for s in sample],
        batch_size=BATCH_SIZE,
        convert_to_numpy=True,
        normalize_embeddings=True,
        show_progress_bar=False,
    )
    enc_time = time.perf_counter() - t0
    print(f"  encoded {len(sample)} docs in {enc_time:.1f}s ({len(sample)/enc_time:.1f} chunks/sec)")

    query_vecs = np.zeros((len(queries), doc_vecs.shape[1]), dtype=np.float32)
    q_latencies: list[float] = []
    for i, q in enumerate(queries):
        t0 = time.perf_counter()
        v = encoder.encode(
            [format_query(q["query_text"])],
            batch_size=1,
            convert_to_numpy=True,
            normalize_embeddings=True,
            show_progress_bar=False,
        )[0]
        q_latencies.append((time.perf_counter() - t0) * 1000)
        query_vecs[i] = v
    print(f"  encoded {len(queries)} queries (median {statistics.median(q_latencies):.1f} ms)")

    # Cosine scores: both normalized → dot product.
    sims = query_vecs @ doc_vecs.T
    sample_ids_arr = np.array([s["id"] for s in sample])
    sample_texts_arr = np.array([s["text"] for s in sample], dtype=object)

    # Per query: indices of top-K candidates (flat int indices into sample).
    topk_idx = np.argsort(-sims, axis=1)[:, :RERANK_TOPK]
    print(f"  first-stage top-{RERANK_TOPK} per query built")

    # Free the encoder before loading the reranker.
    del encoder
    gc.collect()
    torch.cuda.empty_cache()
    print(f"  encoder unloaded  (VRAM {torch.cuda.memory_allocated()/1e9:.2f} GiB after cleanup)")

    # ── Stage 2: cross-encoder rerank ──────────────────────────────
    print(f"\n── Stage 2: cross-encoder rerank ({RERANK_MODEL}) ──")
    t0 = time.perf_counter()
    reranker = CrossEncoder(
        RERANK_MODEL,
        model_kwargs={"torch_dtype": torch.float16},
        device="cuda",
    )
    print(f"  loaded in {time.perf_counter()-t0:.1f}s  (VRAM {torch.cuda.memory_allocated()/1e9:.2f} GiB)")

    rerank_latencies: list[float] = []
    final_rankings: list[list[str]] = []

    for i, q in enumerate(queries):
        cand_idx = topk_idx[i]
        cand_texts = sample_texts_arr[cand_idx].tolist()
        cand_ids = sample_ids_arr[cand_idx].tolist()
        # Cross-encoder input: (query, doc) pairs. Use the RAW query (no
        # instruct wrapper) — cross-encoders have their own format.
        pairs = [(q["query_text"], d) for d in cand_texts]
        t0 = time.perf_counter()
        scores = reranker.predict(
            pairs,
            batch_size=RERANK_BATCH,
            show_progress_bar=False,
        )
        rerank_latencies.append((time.perf_counter() - t0) * 1000)
        order = np.argsort(-np.array(scores))[:20]
        final_rankings.append([cand_ids[j] for j in order])

    print(f"  reranked {len(queries)} queries (median {statistics.median(rerank_latencies):.1f} ms per query, "
          f"{RERANK_TOPK} pairs each)")
    print(f"  peak VRAM: {torch.cuda.max_memory_allocated()/1e9:.2f} GiB")

    # ── Scoring ─────────────────────────────────────────────────────
    per_query: list[dict] = []
    overall_ndcg: list[float] = []
    overall_recall: list[float] = []
    cross_ling_ndcg: list[float] = []
    cross_ling_recall: list[float] = []

    for i, q in enumerate(queries):
        ranking_ids = final_rankings[i]
        relevant = set(q["relevant_chunk_ids"]) & set(sample_ids_arr.tolist())
        ndcg = ndcg_at_k(ranking_ids, relevant, k=10)
        recall20 = recall_at_k(ranking_ids, relevant, k=20)

        per_query.append({
            "query_id": q["query_id"],
            "category": q["category"],
            "language": q["language"],
            "target_language": q.get("target_language", q["language"]),
            "relevant_count": len(relevant),
            "ndcg_at_10": round(ndcg, 4),
            "recall_at_20": round(recall20, 4),
            "top_5_chunk_ids": ranking_ids[:5],
        })
        overall_ndcg.append(ndcg)
        overall_recall.append(recall20)
        if q["category"] == "B_crosslingual":
            cross_ling_ndcg.append(ndcg)
            cross_ling_recall.append(recall_at_k(ranking_ids, relevant, k=10))

    aggregate = {
        "ndcg_at_10_mean": round(statistics.mean(overall_ndcg), 4),
        "recall_at_20_mean": round(statistics.mean(overall_recall), 4),
        "crosslingual_ndcg_at_10_mean": (round(statistics.mean(cross_ling_ndcg), 4) if cross_ling_ndcg else None),
        "crosslingual_recall_at_10_mean": (round(statistics.mean(cross_ling_recall), 4) if cross_ling_recall else None),
        "encoder_query_latency_p50_ms": round(statistics.median(q_latencies), 1),
        "encoder_query_latency_p95_ms": round(sorted(q_latencies)[int(len(q_latencies) * 0.95)], 1),
        "rerank_latency_p50_ms": round(statistics.median(rerank_latencies), 1),
        "rerank_latency_p95_ms": round(sorted(rerank_latencies)[int(len(rerank_latencies) * 0.95)], 1),
        "total_query_latency_p50_ms": round(
            statistics.median(q_latencies) + statistics.median(rerank_latencies), 1
        ),
        "sample_encode_seconds": round(enc_time, 1),
        "sample_encode_rate_chunks_per_sec": round(len(sample) / enc_time, 2),
        "gpu_peak_vram_gb": round(torch.cuda.max_memory_allocated() / 1e9, 2),
    }

    by_cat: dict[str, dict] = {}
    for entry in per_query:
        cat = entry["category"]
        by_cat.setdefault(cat, {"ndcg": [], "recall": []})
        by_cat[cat]["ndcg"].append(entry["ndcg_at_10"])
        by_cat[cat]["recall"].append(entry["recall_at_20"])
    by_cat_summary = {
        cat: {
            "n": len(v["ndcg"]),
            "ndcg_at_10_mean": round(statistics.mean(v["ndcg"]), 4),
            "recall_at_20_mean": round(statistics.mean(v["recall"]), 4),
        }
        for cat, v in by_cat.items()
    }

    out = {
        "model": MODEL_NAME,
        "variant": VARIANT,
        "reranker": RERANK_MODEL,
        "rerank_topk": RERANK_TOPK,
        "date": time.strftime("%Y-%m-%d"),
        "sample_size": len(sample),
        "queries": len(queries),
        "batch_size": BATCH_SIZE,
        "max_seq_len": MAX_SEQ_LEN,
        "aggregate": aggregate,
        "by_category": by_cat_summary,
        "per_query": per_query,
    }
    OUT_PATH.write_text(json.dumps(out, ensure_ascii=False, indent=2))

    print(f"\nWrote {OUT_PATH}")
    print(f"  NDCG@10                mean = {aggregate['ndcg_at_10_mean']}")
    print(f"  Recall@20              mean = {aggregate['recall_at_20_mean']}")
    print(f"  CrossLing R@10         mean = {aggregate['crosslingual_recall_at_10_mean']}")
    print(f"  encoder query p50  {aggregate['encoder_query_latency_p50_ms']} ms  + rerank p50  {aggregate['rerank_latency_p50_ms']} ms")
    print(f"  total query latency p50 = {aggregate['total_query_latency_p50_ms']} ms")
    print(f"  GPU peak VRAM     {aggregate['gpu_peak_vram_gb']} GiB")


if __name__ == "__main__":
    main()
