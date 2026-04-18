"""Qwen3-Embedding eval runner (both 0.6B and 4B; vanilla + instruct).

Runs inside a one-shot container from the sovpro-embed:latest image
(sovpro-embed must be `docker compose stop`-ed first so we own the GPU).

Reads:
    /tmp/queries.jsonl
    /tmp/chunk_ids.txt
    env: MODEL_NAME   (e.g. "Qwen/Qwen3-Embedding-0.6B" or "Qwen/Qwen3-Embedding-4B")
    env: VARIANT      ("vanilla" or "instruct")
    env: BATCH_SIZE   (default 64 for 0.6B, 16 for 4B)
    env: DATABASE_URL

Writes:
    /tmp/qwen3_results.json

Implementation notes:
- Loads model via sentence-transformers (per Qwen3 HF model card).
- Uses fp16 on GPU for both 0.6B and 4B to keep VRAM in budget.
- In "instruct" variant, queries are prefixed per Qwen3 docs:
    "Instruct: {task}\\nQuery: {q}"
  Documents stay un-prefixed (V1 default).
- L2-normalizes both sides before cosine (Qwen3 models don't always
  normalize by default; we do it ourselves to be safe).

Invoked via the bash runner at the end of the conversation — see
orchestration in services/embed/eval/scripts/run_qwen3_eval.sh.
"""
from __future__ import annotations

import asyncio
import json
import math
import os
import statistics
import time
from pathlib import Path

import asyncpg
import torch
import numpy as np
from sentence_transformers import SentenceTransformer


IN_QUERIES = Path("/tmp/queries.jsonl")
IN_SAMPLE = Path("/tmp/chunk_ids.txt")
OUT_PATH = Path("/tmp/qwen3_results.json")

MODEL_NAME = os.environ["MODEL_NAME"]
VARIANT = os.environ.get("VARIANT", "vanilla")
BATCH_SIZE = int(os.environ.get("BATCH_SIZE", "64"))

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
    # Load queries
    queries: list[dict] = []
    with IN_QUERIES.open() as fh:
        for line in fh:
            queries.append(json.loads(line))
    print(f"  loaded {len(queries)} queries")

    # Load sample chunk IDs
    sample_ids: list[str] = []
    with IN_SAMPLE.open() as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            sample_ids.append(line)
    print(f"  loaded {len(sample_ids)} sample chunk IDs")

    # Pull sample texts from DB
    t0 = time.perf_counter()
    sample = asyncio.run(fetch_sample_texts(sample_ids))
    print(f"  fetched {len(sample)} sample texts in {time.perf_counter()-t0:.1f}s")

    # Load model
    use_int8 = os.environ.get("QUANT", "") == "int8"
    dtype_label = "int8" if use_int8 else "fp16"
    print(f"  loading {MODEL_NAME} (variant={VARIANT}, dtype={dtype_label}, batch={BATCH_SIZE}) …")
    t0 = time.perf_counter()
    if use_int8:
        from transformers import BitsAndBytesConfig
        bnb_config = BitsAndBytesConfig(load_in_8bit=True)
        model = SentenceTransformer(
            MODEL_NAME,
            model_kwargs={"quantization_config": bnb_config, "device_map": "cuda"},
        )
    else:
        model = SentenceTransformer(
            MODEL_NAME,
            model_kwargs={"torch_dtype": torch.float16},
            device="cuda",
        )
    # Qwen3 defaults to 32K context; cap to keep VRAM sane and parity with
    # the throughput-optimization plan in the tracking doc.
    max_seq_len = int(os.environ.get("MAX_SEQ_LEN", "512"))
    model.max_seq_length = max_seq_len
    print(f"  model loaded in {time.perf_counter()-t0:.1f}s  (max_seq_length={max_seq_len})")
    print(f"  VRAM allocated: {torch.cuda.memory_allocated()/1e9:.2f} GiB")

    # Encode sample (documents)
    t0 = time.perf_counter()
    sample_texts = [s["text"] for s in sample]
    sample_vecs = model.encode(
        sample_texts,
        batch_size=BATCH_SIZE,
        convert_to_numpy=True,
        normalize_embeddings=True,
        show_progress_bar=False,
    )
    sample_encode_s = time.perf_counter() - t0
    print(f"  encoded {len(sample_texts)} docs in {sample_encode_s:.1f}s "
          f"({len(sample_texts)/sample_encode_s:.1f} chunks/sec)")
    print(f"  VRAM peak after docs: {torch.cuda.max_memory_allocated()/1e9:.2f} GiB")

    # Encode queries (timed individually for latency stats)
    latencies_ms: list[float] = []
    query_vecs = np.zeros((len(queries), sample_vecs.shape[1]), dtype=np.float32)
    for i, q in enumerate(queries):
        t0 = time.perf_counter()
        v = model.encode(
            [format_query(q["query_text"])],
            batch_size=1,
            convert_to_numpy=True,
            normalize_embeddings=True,
            show_progress_bar=False,
        )[0]
        latencies_ms.append((time.perf_counter() - t0) * 1000)
        query_vecs[i] = v
    print(f"  encoded {len(queries)} queries (median latency {statistics.median(latencies_ms):.1f} ms)")

    # Score: cosine similarity = dot product since both sides normalized.
    sims = query_vecs @ sample_vecs.T  # (Nq, Nd)
    sample_ids_arr = np.array([s["id"] for s in sample])

    per_query: list[dict] = []
    cross_ling_ndcg: list[float] = []
    cross_ling_recall: list[float] = []
    overall_ndcg: list[float] = []
    overall_recall: list[float] = []

    for i, q in enumerate(queries):
        order = np.argsort(-sims[i])[:20]
        ranking_ids = sample_ids_arr[order].tolist()
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
        "query_latency_p50_ms": round(statistics.median(latencies_ms), 1),
        "query_latency_p95_ms": round(sorted(latencies_ms)[int(len(latencies_ms) * 0.95)], 1),
        "sample_encode_seconds": round(sample_encode_s, 1),
        "sample_encode_rate_chunks_per_sec": round(len(sample_texts) / sample_encode_s, 2),
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
        "date": time.strftime("%Y-%m-%d"),
        "sample_size": len(sample),
        "queries": len(queries),
        "batch_size": BATCH_SIZE,
        "aggregate": aggregate,
        "by_category": by_cat_summary,
        "per_query": per_query,
    }

    OUT_PATH.write_text(json.dumps(out, ensure_ascii=False, indent=2))
    print(f"\nWrote {OUT_PATH}")
    print(f"  NDCG@10          mean = {aggregate['ndcg_at_10_mean']}")
    print(f"  Recall@20        mean = {aggregate['recall_at_20_mean']}")
    print(f"  CrossLing R@10   mean = {aggregate['crosslingual_recall_at_10_mean']}")
    print(f"  Sample encode    {aggregate['sample_encode_rate_chunks_per_sec']} chunks/sec")
    print(f"  GPU peak VRAM    {aggregate['gpu_peak_vram_gb']} GiB")


if __name__ == "__main__":
    main()
