"""One-shot throughput + quality bench for a single config. Runs fresh
in its own Python process so CUDA state is clean — call me once per
config from a shell loop.

Env:
    MODEL_NAME       (default Qwen/Qwen3-Embedding-0.6B)
    VARIANT          (default instruct)
    BATCH_SIZE       (required)
    MAX_SEQ_LEN      (required)
    PRE_SORT         "1" to sort texts by length before encoding
    QUALITY_CHECK    "1" to also run 40-query NDCG/Recall scoring
    OUT_FILE         path to append one JSON line summarizing this run
"""
from __future__ import annotations

import asyncio
import json
import math
import os
import statistics
import sys
import time
from pathlib import Path

import asyncpg
import numpy as np
import torch
from sentence_transformers import SentenceTransformer


IN_QUERIES = Path("/tmp/queries.jsonl")
IN_SAMPLE = Path("/tmp/chunk_ids.txt")

MODEL_NAME = os.environ.get("MODEL_NAME", "Qwen/Qwen3-Embedding-0.6B")
VARIANT = os.environ.get("VARIANT", "instruct")
BATCH_SIZE = int(os.environ["BATCH_SIZE"])
MAX_SEQ_LEN = int(os.environ["MAX_SEQ_LEN"])
PRE_SORT = os.environ.get("PRE_SORT", "1") == "1"
QUALITY_CHECK = os.environ.get("QUALITY_CHECK", "0") == "1"
OUT_FILE = Path(os.environ.get("OUT_FILE", "/tmp/bench_single.jsonl"))
LABEL = os.environ.get("LABEL", f"b{BATCH_SIZE}_s{MAX_SEQ_LEN}_sort{int(PRE_SORT)}")

INSTRUCT_TASK = "Given a parliamentary search query, retrieve relevant Canadian Hansard speech excerpts"


def format_query(q_text: str) -> str:
    return f"Instruct: {INSTRUCT_TASK}\nQuery: {q_text}" if VARIANT == "instruct" else q_text


def ndcg_at_k(ranking, relevant, k=10):
    gains = [1.0 if ranking[i] in relevant else 0.0 for i in range(min(k, len(ranking)))]
    dcg = sum(g / math.log2(i + 2) for i, g in enumerate(gains))
    ideal_hits = min(len(relevant), k)
    idcg = sum(1.0 / math.log2(i + 2) for i in range(ideal_hits))
    return dcg / idcg if idcg > 0 else 0.0


def recall_at_k(ranking, relevant, k):
    if not relevant:
        return 0.0
    return sum(1 for p in ranking[:k] if p in relevant) / len(relevant)


async def fetch_sample_texts(chunk_ids):
    pool = await asyncpg.create_pool(os.environ["DATABASE_URL"])
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id::text AS id, text, language FROM speech_chunks WHERE id = ANY($1::uuid[])",
            chunk_ids,
        )
    await pool.close()
    return [{"id": r["id"], "text": r["text"], "language": r["language"]} for r in rows]


def main():
    sample_ids = []
    with IN_SAMPLE.open() as fh:
        for line in fh:
            line = line.strip()
            if line and not line.startswith("#"):
                sample_ids.append(line)
    sample = asyncio.run(fetch_sample_texts(sample_ids))
    texts = [s["text"] for s in sample]
    ids = [s["id"] for s in sample]

    if PRE_SORT:
        idx = sorted(range(len(texts)), key=lambda i: len(texts[i]))
        texts_ordered = [texts[i] for i in idx]
        ids_ordered = [ids[i] for i in idx]
    else:
        texts_ordered = list(texts)
        ids_ordered = list(ids)

    print(f"  [{LABEL}] {len(texts)} docs, batch={BATCH_SIZE}, max_seq={MAX_SEQ_LEN}, sort={PRE_SORT}", flush=True)

    t0 = time.perf_counter()
    model = SentenceTransformer(
        MODEL_NAME,
        model_kwargs={"torch_dtype": torch.float16},
        device="cuda",
    )
    model.max_seq_length = MAX_SEQ_LEN
    print(f"  model loaded in {time.perf_counter()-t0:.1f}s  weights VRAM={torch.cuda.memory_allocated()/1e9:.2f} GiB", flush=True)

    torch.cuda.reset_peak_memory_stats()
    t0 = time.perf_counter()
    try:
        doc_vecs = model.encode(
            texts_ordered,
            batch_size=BATCH_SIZE,
            convert_to_numpy=True,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        dt = time.perf_counter() - t0
        rate = len(texts) / dt
        vram_peak = torch.cuda.max_memory_allocated() / 1e9
        print(f"  encode OK: {dt:.1f}s ({rate:.1f} c/s)  VRAM peak {vram_peak:.2f} GiB", flush=True)
    except torch.OutOfMemoryError as e:
        print(f"  OOM: {str(e)[:200]}", flush=True)
        with OUT_FILE.open("a") as fh:
            fh.write(json.dumps({"label": LABEL, "oom": True, "batch_size": BATCH_SIZE, "max_seq_len": MAX_SEQ_LEN, "sort": PRE_SORT}) + "\n")
        return

    record = {
        "label": LABEL,
        "batch_size": BATCH_SIZE,
        "max_seq_len": MAX_SEQ_LEN,
        "pre_sort": PRE_SORT,
        "seconds": round(dt, 2),
        "chunks_per_sec": round(rate, 1),
        "vram_peak_gib": round(vram_peak, 2),
    }

    if QUALITY_CHECK:
        queries = []
        with IN_QUERIES.open() as fh:
            for line in fh:
                queries.append(json.loads(line))
        t0 = time.perf_counter()
        q_vecs = model.encode(
            [format_query(q["query_text"]) for q in queries],
            batch_size=1,
            convert_to_numpy=True,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        sims = q_vecs @ doc_vecs.T
        ids_arr = np.array(ids_ordered)
        ndcgs, recalls, xl_recalls = [], [], []
        for i, q in enumerate(queries):
            order = np.argsort(-sims[i])[:20]
            ranking = ids_arr[order].tolist()
            relevant = set(q["relevant_chunk_ids"]) & set(ids_arr.tolist())
            ndcgs.append(ndcg_at_k(ranking, relevant, 10))
            recalls.append(recall_at_k(ranking, relevant, 20))
            if q["category"] == "B_crosslingual":
                xl_recalls.append(recall_at_k(ranking, relevant, 10))
        record["ndcg_at_10"] = round(statistics.mean(ndcgs), 4)
        record["recall_at_20"] = round(statistics.mean(recalls), 4)
        record["crosslingual_recall_at_10"] = round(statistics.mean(xl_recalls), 4) if xl_recalls else None
        print(f"  quality  NDCG@10={record['ndcg_at_10']}  R@20={record['recall_at_20']}  XL_R@10={record['crosslingual_recall_at_10']}", flush=True)

    with OUT_FILE.open("a") as fh:
        fh.write(json.dumps(record) + "\n")
    print(f"  wrote record to {OUT_FILE}", flush=True)


if __name__ == "__main__":
    main()
