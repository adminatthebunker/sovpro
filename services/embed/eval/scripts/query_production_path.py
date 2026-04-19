"""Production-path query validation — TEI + embedding_next HNSW end-to-end.

Two modes:

    eval:         rerun the 40-query eval against the live TEI service
                  using pgvector HNSW over embedding_next. Confirms the
                  production path matches the offline eval numbers.

    interactive:  accept a question on stdin, return the top-10 chunks
                  from the corpus with attribution. Human sanity check.

Run via:
    # eval mode
    docker cp services/embed/eval/scripts/query_production_path.py sw-scanner-jobs:/tmp/
    docker cp services/embed/eval/queries/queries.jsonl sw-scanner-jobs:/tmp/
    docker exec sw-scanner-jobs python /tmp/query_production_path.py eval

    # interactive mode (type queries; blank line to exit)
    docker exec -it sw-scanner-jobs python /tmp/query_production_path.py interactive
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
import httpx
import orjson


TEI_URL = os.environ.get("EMBED_NEXT_URL", "http://tei:80").rstrip("/")
QUERIES_PATH = Path("/tmp/queries.jsonl")

INSTRUCT_TASK = "Given a parliamentary search query, retrieve relevant Canadian Hansard speech excerpts"


def wrap_query(q: str) -> str:
    """Qwen3-Embedding instruct-prompt format, applied at query time only."""
    return f"Instruct: {INSTRUCT_TASK}\nQuery: {q}"


async def embed_query(client: httpx.AsyncClient, q: str) -> list[float]:
    body = orjson.dumps({"inputs": [wrap_query(q)], "normalize": True})
    r = await client.post(f"{TEI_URL}/embed", content=body,
                           headers={"Content-Type": "application/json"})
    r.raise_for_status()
    data = r.json()
    # TEI returns bare [[...]] for /embed
    if isinstance(data, dict) and "data" in data:
        return data["data"][0]["embedding"]
    return data[0]


def vec_literal(vec: list[float]) -> str:
    return "[" + ",".join(f"{float(v):.8f}" for v in vec) + "]"


async def search_chunks(conn, q_vec: list[float], *,
                        limit: int = 10, lang: str | None = None) -> list[dict]:
    """HNSW cosine search over speech_chunks.embedding_next.

    Joins speeches + politicians so the caller gets everything needed
    to render a result card. Uses the cosine distance operator (<=>).
    """
    where = "sc.embedding_next IS NOT NULL"
    params: list = [vec_literal(q_vec)]
    if lang:
        where += " AND sc.language = $2"
        params.append(lang)
    sql = f"""
        SELECT sc.id::text AS chunk_id,
               sc.speech_id::text,
               sc.language,
               sc.text,
               sc.spoken_at,
               sc.party_at_time,
               sc.politician_id::text,
               s.speaker_name_raw,
               s.source_url,
               1 - (sc.embedding_next <=> $1::vector) AS similarity
          FROM speech_chunks sc
          JOIN speeches s ON s.id = sc.speech_id
         WHERE {where}
         ORDER BY sc.embedding_next <=> $1::vector ASC
         LIMIT {int(limit)}
    """
    rows = await conn.fetch(sql, *params)
    return [dict(r) for r in rows]


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


async def cmd_eval() -> None:
    queries = []
    with QUERIES_PATH.open() as fh:
        for line in fh:
            queries.append(json.loads(line))
    print(f"  {len(queries)} queries loaded", flush=True)

    pool = await asyncpg.create_pool(os.environ["DATABASE_URL"])
    assert pool is not None

    overall_ndcg, overall_recall = [], []
    xl_recall = []
    per_category: dict[str, list[float]] = {}
    latencies: list[float] = []

    async with httpx.AsyncClient(timeout=60) as client:
        async with pool.acquire() as conn:
            for q in queries:
                t0 = time.perf_counter()
                q_vec = await embed_query(client, q["query_text"])
                # For cross-lingual queries, filter to the target language
                # since that's what the ground-truth labels live in.
                lang_filter = q.get("target_language") if q["category"] == "B_crosslingual" else None
                results = await search_chunks(conn, q_vec, limit=20, lang=lang_filter)
                dt_ms = (time.perf_counter() - t0) * 1000
                latencies.append(dt_ms)

                ranking = [r["chunk_id"] for r in results]
                relevant = set(q["relevant_chunk_ids"])
                ndcg = ndcg_at_k(ranking, relevant, 10)
                recall = recall_at_k(ranking, relevant, 20)
                overall_ndcg.append(ndcg)
                overall_recall.append(recall)
                per_category.setdefault(q["category"], []).append(ndcg)
                if q["category"] == "B_crosslingual":
                    xl_recall.append(recall_at_k(ranking, relevant, 10))

    await pool.close()

    print("\n── Production-path retrieval quality (TEI + embedding_next HNSW) ──")
    print(f"  NDCG@10               mean = {statistics.mean(overall_ndcg):.4f}")
    print(f"  Recall@20             mean = {statistics.mean(overall_recall):.4f}")
    print(f"  Cross-ling R@10       mean = {(statistics.mean(xl_recall) if xl_recall else 0):.4f}")
    print(f"  Query latency         p50  = {statistics.median(latencies):.1f} ms")
    print(f"                        p95  = {sorted(latencies)[int(len(latencies) * 0.95)]:.1f} ms")
    print(f"\n── Per category ──")
    for cat, scores in sorted(per_category.items()):
        print(f"  {cat:18s} n={len(scores):2d}  NDCG@10 = {statistics.mean(scores):.4f}")


async def cmd_interactive() -> None:
    pool = await asyncpg.create_pool(os.environ["DATABASE_URL"])
    assert pool is not None
    async with httpx.AsyncClient(timeout=60) as client:
        async with pool.acquire() as conn:
            print("  Interactive mode — empty line to quit.", flush=True)
            while True:
                try:
                    q = input("\n  query> ").strip()
                except EOFError:
                    break
                if not q:
                    break
                t0 = time.perf_counter()
                q_vec = await embed_query(client, q)
                results = await search_chunks(conn, q_vec, limit=10, lang=None)
                dt_ms = (time.perf_counter() - t0) * 1000
                print(f"  ({dt_ms:.0f} ms, {len(results)} results)")
                for i, r in enumerate(results, 1):
                    date = r["spoken_at"].date().isoformat() if r["spoken_at"] else "?"
                    party = r["party_at_time"] or "—"
                    who = r["speaker_name_raw"] or "—"
                    snippet = " ".join(r["text"].split())[:160]
                    print(f"    [{i:2d}] {r['similarity']:.3f}  {date} {r['language']} {party:6s}  {who[:35]:35s}  {snippet}")
    await pool.close()


async def main() -> None:
    if len(sys.argv) < 2:
        print(f"usage: {sys.argv[0]} {{eval|interactive}}")
        sys.exit(2)
    mode = sys.argv[1]
    if mode == "eval":
        await cmd_eval()
    elif mode == "interactive":
        await cmd_interactive()
    else:
        print(f"unknown mode: {mode}")
        sys.exit(2)


if __name__ == "__main__":
    asyncio.run(main())
