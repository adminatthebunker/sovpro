"""Production-path eval runner for /api/v1/search/speeches.

Hits the live API with each eval query and scores NDCG@10 / Recall@20
against the same ground-truth annotations used by the offline model
runners (run_eval_qwen3.py, run_eval_bgem3.py).

Unlike those offline runners, this one searches the full
`speech_chunks.embedding_next` column (currently ~600k+ rows) rather
than a 5k sample, so the numbers are NOT directly comparable to the
REPORT.md baselines. Its job is to catch wiring regressions:

    * query-side instruction prompt missing -> NDCG craters ~0.43 → ~0.22
    * wrong distance operator -> NDCG ~0
    * filter bleed (e.g. language filter on when it shouldn't be) -> Recall drops

Invocation:

    python services/embed/eval/scripts/run_eval_search.py \\
        --base-url http://localhost:3000 \\
        --queries services/embed/eval/queries/queries.jsonl \\
        --out     services/embed/eval/out/search_results.json

The script reports per-category and aggregate scores to stdout and
persists a JSON artifact so REPORT.md rows can be appended.
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path


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


def fetch_search(base_url: str, query_text: str, limit: int, timeout: float) -> tuple[list[str], float]:
    """Return (ordered chunk_ids, elapsed_ms)."""
    qs = urllib.parse.urlencode({"q": query_text, "limit": limit})
    url = f"{base_url.rstrip('/')}/api/v1/search/speeches?{qs}"
    t0 = time.perf_counter()
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        payload = json.load(resp)
    elapsed_ms = (time.perf_counter() - t0) * 1000
    items = payload.get("items", [])
    return [it["chunk_id"] for it in items], elapsed_ms


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__ or "")
    ap.add_argument("--base-url", default="http://localhost:3000",
                    help="API base URL (default: http://localhost:3000)")
    ap.add_argument("--queries", type=Path,
                    default=Path(__file__).resolve().parents[1] / "queries" / "queries.jsonl")
    ap.add_argument("--out", type=Path,
                    default=Path(__file__).resolve().parents[1] / "out" / "search_results.json")
    ap.add_argument("--limit", type=int, default=20,
                    help="Results per query to request (must be ≥20 so Recall@20 is measurable)")
    ap.add_argument("--timeout", type=float, default=30.0)
    args = ap.parse_args()

    if args.limit < 20:
        print(f"error: --limit must be >= 20 (got {args.limit})", file=sys.stderr)
        return 2

    queries: list[dict] = []
    with args.queries.open() as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            queries.append(json.loads(line))
    print(f"loaded {len(queries)} queries from {args.queries}")
    print(f"searching against {args.base_url}")

    per_query: list[dict] = []
    latencies_ms: list[float] = []
    overall_ndcg: list[float] = []
    overall_recall: list[float] = []
    cross_ling_recall_10: list[float] = []

    errors: list[dict] = []
    for q in queries:
        try:
            ranking_ids, elapsed_ms = fetch_search(
                args.base_url, q["query_text"], args.limit, args.timeout,
            )
        except Exception as exc:  # noqa: BLE001 — surface and continue
            errors.append({"query_id": q["query_id"], "error": str(exc)})
            print(f"  [{q['query_id']}] ERROR: {exc}", file=sys.stderr)
            continue

        latencies_ms.append(elapsed_ms)
        relevant = set(q.get("relevant_chunk_ids", []))
        ndcg = ndcg_at_k(ranking_ids, relevant, k=10)
        recall20 = recall_at_k(ranking_ids, relevant, k=20)
        per_query.append({
            "query_id": q["query_id"],
            "category": q.get("category"),
            "language": q.get("language"),
            "target_language": q.get("target_language", q.get("language")),
            "relevant_count": len(relevant),
            "returned_count": len(ranking_ids),
            "ndcg_at_10": round(ndcg, 4),
            "recall_at_20": round(recall20, 4),
            "latency_ms": round(elapsed_ms, 1),
            "top_5_chunk_ids": ranking_ids[:5],
        })
        overall_ndcg.append(ndcg)
        overall_recall.append(recall20)
        if q.get("category") == "B_crosslingual":
            cross_ling_recall_10.append(recall_at_k(ranking_ids, relevant, k=10))

    if not per_query:
        print("no queries succeeded", file=sys.stderr)
        return 1

    aggregate = {
        "ndcg_at_10_mean": round(statistics.mean(overall_ndcg), 4),
        "recall_at_20_mean": round(statistics.mean(overall_recall), 4),
        "crosslingual_recall_at_10_mean": (
            round(statistics.mean(cross_ling_recall_10), 4) if cross_ling_recall_10 else None
        ),
        "query_latency_p50_ms": round(statistics.median(latencies_ms), 1),
        "query_latency_p95_ms": (
            round(sorted(latencies_ms)[int(len(latencies_ms) * 0.95)], 1)
            if len(latencies_ms) >= 20 else None
        ),
    }

    by_cat: dict[str, dict[str, list[float]]] = {}
    for entry in per_query:
        cat = entry["category"] or "_uncat"
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
        "path": "production:/api/v1/search/speeches",
        "base_url": args.base_url,
        "date": time.strftime("%Y-%m-%d"),
        "queries": len(queries),
        "successful_queries": len(per_query),
        "limit": args.limit,
        "aggregate": aggregate,
        "by_category": by_cat_summary,
        "per_query": per_query,
        "errors": errors,
        "note": (
            "Production path searches the full embedding_next column, NOT the "
            "5k sample used by run_eval_qwen3.py. Numbers are not directly "
            "comparable to the baseline rows in REPORT.md; use this as a "
            "regression gate for API wiring (instruct prompt, distance op, filters)."
        ),
    }

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(out, ensure_ascii=False, indent=2))

    print(f"\nwrote {args.out}")
    print(f"  NDCG@10          mean = {aggregate['ndcg_at_10_mean']}")
    print(f"  Recall@20        mean = {aggregate['recall_at_20_mean']}")
    if aggregate["crosslingual_recall_at_10_mean"] is not None:
        print(f"  CrossLing R@10   mean = {aggregate['crosslingual_recall_at_10_mean']}")
    print(f"  Latency p50      = {aggregate['query_latency_p50_ms']} ms")
    if aggregate["query_latency_p95_ms"] is not None:
        print(f"  Latency p95      = {aggregate['query_latency_p95_ms']} ms")
    print("\n  Per-category:")
    for cat, summary in sorted(by_cat_summary.items()):
        print(f"    {cat:20s} n={summary['n']:3d}  "
              f"NDCG@10={summary['ndcg_at_10_mean']:.4f}  "
              f"R@20={summary['recall_at_20_mean']:.4f}")
    if errors:
        print(f"\n  {len(errors)} errored queries (see {args.out} for details)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
