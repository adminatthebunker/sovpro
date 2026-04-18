"""Baseline eval: BGE-M3 (incumbent), fp16, via the running /embed service.

Reads:
    /tmp/queries.jsonl        ← services/embed/eval/queries/queries.jsonl
    /tmp/chunk_ids.txt        ← services/embed/eval/sample/chunk_ids.txt

Writes:
    /tmp/bgem3_results.json   ← services/embed/eval/results/YYYY-MM-DD-bge-m3.json

Pipeline:
    1. Embed each query via POST /embed (BGE-M3 is already loaded).
    2. Fetch sample embeddings from pgvector.
    3. Score cosine similarity, rank top-20 per query against the sample.
    4. Compute NDCG@10, Recall@20, cross-lingual Recall@10, latency p50/p95.

Run:
    # Stage inputs
    docker cp services/embed/eval/queries/queries.jsonl   sw-scanner-jobs:/tmp/queries.jsonl
    docker cp services/embed/eval/sample/chunk_ids.txt    sw-scanner-jobs:/tmp/chunk_ids.txt
    cat services/embed/eval/scripts/run_eval_bgem3.py | docker exec -i sw-scanner-jobs python -
    # Pull output
    docker cp sw-scanner-jobs:/tmp/bgem3_results.json services/embed/eval/results/2026-04-18-bge-m3.json
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
import httpx
import orjson


IN_QUERIES = Path("/tmp/queries.jsonl")
IN_SAMPLE = Path("/tmp/chunk_ids.txt")
OUT_PATH = Path("/tmp/bgem3_results.json")

EMBED_URL = os.environ.get("EMBED_URL", "http://embed:8000").rstrip("/")


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


async def fetch_sample_embeddings(pool, chunk_ids: list[str]) -> tuple[list[str], list[list[float]], dict[str, str]]:
    """Return (ids, vectors, id_to_language)."""
    rows = await pool.fetch(
        """
        SELECT id::text AS id, embedding::text AS emb, language
          FROM speech_chunks
         WHERE id = ANY($1::uuid[])
           AND embedding IS NOT NULL
        """,
        chunk_ids,
    )
    ids: list[str] = []
    vecs: list[list[float]] = []
    langs: dict[str, str] = {}
    for r in rows:
        ids.append(r["id"])
        langs[r["id"]] = r["language"]
        # pgvector string form: '[0.1,0.2,...]'
        s = r["emb"]
        vec = [float(x) for x in s[1:-1].split(",")]
        vecs.append(vec)
    return ids, vecs, langs


def cosine_row(q: list[float], m: list[list[float]]) -> list[float]:
    """Return cosine similarity of query `q` against each row of `m`.

    All BGE-M3 vectors are L2-normalized at write time (FlagEmbedding
    does this by default), so cosine == dot product. We still compute
    norms to be safe in case one side isn't normalized for Qwen3 later.
    """
    qn = math.sqrt(sum(x * x for x in q)) or 1.0
    out: list[float] = []
    for row in m:
        rn = math.sqrt(sum(x * x for x in row)) or 1.0
        dot = 0.0
        for a, b in zip(q, row):
            dot += a * b
        out.append(dot / (qn * rn))
    return out


async def main() -> None:
    # Load queries
    queries: list[dict] = []
    with IN_QUERIES.open() as fh:
        for line in fh:
            queries.append(json.loads(line))
    print(f"  loaded {len(queries)} queries")

    # Load sample chunk IDs (skip header comments)
    sample_ids: list[str] = []
    with IN_SAMPLE.open() as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            sample_ids.append(line)
    print(f"  loaded {len(sample_ids)} sample chunk IDs")

    # Fetch sample embeddings
    pool = await asyncpg.create_pool(os.environ["DATABASE_URL"])
    assert pool is not None
    async with pool.acquire() as conn:
        t0 = time.perf_counter()
        ids, vecs, langs = await fetch_sample_embeddings(conn, sample_ids)
        print(f"  fetched {len(ids)} sample embeddings in {(time.perf_counter()-t0):.1f}s")
    await pool.close()

    # Embed queries via /embed
    query_texts = [q["query_text"] for q in queries]
    query_vecs: list[list[float]] = []
    latencies_ms: list[float] = []
    async with httpx.AsyncClient(timeout=120) as client:
        for qt in query_texts:
            t0 = time.perf_counter()
            r = await client.post(
                f"{EMBED_URL}/embed",
                content=orjson.dumps({"texts": [qt]}),
                headers={"Content-Type": "application/json"},
            )
            dt = (time.perf_counter() - t0) * 1000
            r.raise_for_status()
            data = r.json()
            query_vecs.append(data["items"][0]["embedding"])
            latencies_ms.append(dt)
    print(f"  embedded {len(query_vecs)} queries via /embed  (median latency {statistics.median(latencies_ms):.1f} ms)")

    # Score per query
    per_query: list[dict] = []
    cross_ling_ndcg: list[float] = []
    cross_ling_recall: list[float] = []
    overall_ndcg: list[float] = []
    overall_recall: list[float] = []

    for q, qv in zip(queries, query_vecs):
        sims = cosine_row(qv, vecs)
        # Rank indices by similarity desc
        order = sorted(range(len(sims)), key=lambda i: sims[i], reverse=True)
        ranking_ids = [ids[i] for i in order[:20]]
        relevant = set(q["relevant_chunk_ids"])
        # Limit relevant to those actually in the sample (they should all be, but belt+braces)
        relevant &= set(ids)

        ndcg = ndcg_at_k(ranking_ids, relevant, k=10)
        recall20 = recall_at_k(ranking_ids, relevant, k=20)

        entry = {
            "query_id": q["query_id"],
            "category": q["category"],
            "language": q["language"],
            "target_language": q.get("target_language", q["language"]),
            "relevant_count": len(relevant),
            "ndcg_at_10": round(ndcg, 4),
            "recall_at_20": round(recall20, 4),
            "top_5_chunk_ids": ranking_ids[:5],
        }
        per_query.append(entry)
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
    }

    # Per-category aggregates
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
        "model": "BAAI/bge-m3",
        "variant": "baseline",
        "date": time.strftime("%Y-%m-%d"),
        "sample_size": len(ids),
        "queries": len(queries),
        "aggregate": aggregate,
        "by_category": by_cat_summary,
        "per_query": per_query,
    }

    OUT_PATH.write_text(json.dumps(out, ensure_ascii=False, indent=2))
    print(f"\nWrote {OUT_PATH}")
    print(f"  NDCG@10          mean = {aggregate['ndcg_at_10_mean']}")
    print(f"  Recall@20        mean = {aggregate['recall_at_20_mean']}")
    print(f"  CrossLing R@10   mean = {aggregate['crosslingual_recall_at_10_mean']}")
    print(f"  Query latency p50 = {aggregate['query_latency_p50_ms']} ms   p95 = {aggregate['query_latency_p95_ms']} ms")


if __name__ == "__main__":
    asyncio.run(main())
