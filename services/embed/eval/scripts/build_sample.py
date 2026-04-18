"""Build the 5,000-chunk stratified sample for eval.

Produces services/embed/eval/sample/chunk_ids.txt: one UUID per line,
plus a header comment describing the stratification.

Requirements (from docs/plans/embedding-model-comparison.md):
- 3,800 EN + 1,200 FR (76/24 to match corpus distribution).
- Within each language, stratify across year (2021-2026) and party.
- **Every chunk referenced in queries.jsonl as ground-truth must be
  included** — otherwise that query can't be scored.

Stratification:
- Year × party cross-table for each language.
- Proportional allocation: bucket share of sample = bucket share of
  corpus for that language.
- Fill ground-truth chunks first, then fill the rest to hit exact
  per-bucket quotas.

Run:
    docker cp services/embed/eval/queries/queries.jsonl sw-scanner-jobs:/tmp/queries.jsonl
    cat services/embed/eval/scripts/build_sample.py | docker exec -i sw-scanner-jobs python -
    docker cp sw-scanner-jobs:/tmp/chunk_ids.txt services/embed/eval/sample/chunk_ids.txt
"""
from __future__ import annotations

import asyncio
import json
import os
import random
from collections import defaultdict
from pathlib import Path

import asyncpg


IN_QUERIES = Path("/tmp/queries.jsonl")
OUT_PATH = Path("/tmp/chunk_ids.txt")

TARGET_EN = 3800
TARGET_FR = 1200
TARGET_TOTAL = TARGET_EN + TARGET_FR

RANDOM_SEED = 20260418


async def main() -> None:
    random.seed(RANDOM_SEED)

    # Load ground-truth chunk UUIDs from queries.jsonl. We must include
    # every one so the queries can actually be scored against the sample.
    must_include: set[str] = set()
    with IN_QUERIES.open() as fh:
        for line in fh:
            rec = json.loads(line)
            for cid in rec.get("relevant_chunk_ids", []):
                must_include.add(cid)
    print(f"  queries.jsonl contributes {len(must_include)} must-include chunk IDs.")

    pool = await asyncpg.create_pool(os.environ["DATABASE_URL"])
    assert pool is not None

    # Fetch the lightweight (id, language, year, party) for every
    # embedded chunk. 242k rows × ~60 bytes ≈ 15 MB — trivial.
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id::text AS id,
                   language,
                   coalesce(EXTRACT(YEAR FROM spoken_at)::int, 0) AS year,
                   coalesce(party_at_time, '(unknown)') AS party
              FROM speech_chunks
             WHERE embedding IS NOT NULL
            """
        )
    await pool.close()

    total = len(rows)
    print(f"  fetched {total} embedded chunks from DB.")

    # Bucket every chunk by (language, year, party).
    buckets: dict[tuple[str, int, str], list[str]] = defaultdict(list)
    for r in rows:
        buckets[(r["language"], r["year"], r["party"])].append(r["id"])

    # Compute per-language totals, used for the proportional allocation.
    lang_totals: dict[str, int] = defaultdict(int)
    for (lang, _, _), ids in buckets.items():
        lang_totals[lang] += len(ids)
    print(f"  corpus by language: en={lang_totals.get('en', 0)}  fr={lang_totals.get('fr', 0)}")

    # Proportional bucket quotas per language.
    per_lang_target = {"en": TARGET_EN, "fr": TARGET_FR}
    bucket_quota: dict[tuple[str, int, str], int] = {}
    for key, ids in buckets.items():
        lang = key[0]
        if lang not in per_lang_target:
            bucket_quota[key] = 0
            continue
        if lang_totals[lang] == 0:
            bucket_quota[key] = 0
            continue
        share = len(ids) / lang_totals[lang]
        bucket_quota[key] = round(share * per_lang_target[lang])

    # Round-off: nudge quotas so per-language sums hit exactly the target.
    for lang, target in per_lang_target.items():
        keys_for_lang = [k for k in bucket_quota if k[0] == lang]
        current = sum(bucket_quota[k] for k in keys_for_lang)
        diff = target - current
        if diff == 0:
            continue
        # Sort biggest-bucket-first for fair rounding adjustments.
        keys_sorted = sorted(keys_for_lang, key=lambda k: len(buckets[k]), reverse=True)
        i = 0
        step = 1 if diff > 0 else -1
        while diff != 0 and keys_sorted:
            k = keys_sorted[i % len(keys_sorted)]
            # Can we nudge this bucket's quota without exceeding its real size?
            if step > 0 and bucket_quota[k] < len(buckets[k]):
                bucket_quota[k] += 1
                diff -= 1
            elif step < 0 and bucket_quota[k] > 0:
                bucket_quota[k] -= 1
                diff += 1
            i += 1
            if i > len(keys_sorted) * 10:  # runaway guard
                break

    # Build the sample. Ground-truth chunks go in first (counted against
    # their bucket quota), then fill the rest of each bucket at random.
    selected: set[str] = set()

    # Index must-include chunks by bucket.
    must_by_bucket: dict[tuple[str, int, str], list[str]] = defaultdict(list)
    # We need a reverse lookup id → bucket. Build once.
    id_to_bucket: dict[str, tuple[str, int, str]] = {}
    for key, ids in buckets.items():
        for cid in ids:
            id_to_bucket[cid] = key

    missing_from_embedded: list[str] = []
    for cid in must_include:
        bucket = id_to_bucket.get(cid)
        if bucket is None:
            missing_from_embedded.append(cid)
            continue
        must_by_bucket[bucket].append(cid)

    if missing_from_embedded:
        print(f"  ⚠  {len(missing_from_embedded)} ground-truth chunk IDs are NOT in the embedded corpus — the queries.jsonl is stale or points at chunks that were deleted.")

    # Fill must-include chunks into the selection.
    for bucket, cids in must_by_bucket.items():
        # If a bucket has more must-include than its quota, we still include
        # all of them and eat the overage against the language total.
        for cid in cids:
            selected.add(cid)

    # Random fill to hit per-bucket quota.
    random.seed(RANDOM_SEED)
    for bucket, quota in bucket_quota.items():
        already = sum(1 for cid in must_by_bucket.get(bucket, []) if cid in selected)
        need = quota - already
        if need <= 0:
            continue
        pool_ids = [cid for cid in buckets[bucket] if cid not in selected]
        random.shuffle(pool_ids)
        for cid in pool_ids[:need]:
            selected.add(cid)

    print(f"  selected {len(selected)} chunks (target {TARGET_TOTAL}).")

    # Language breakdown for the header comment.
    lang_counts: dict[str, int] = defaultdict(int)
    for cid in selected:
        bucket = id_to_bucket.get(cid)
        if bucket:
            lang_counts[bucket[0]] += 1
    print(f"  by language: en={lang_counts.get('en', 0)}  fr={lang_counts.get('fr', 0)}")

    # Write the sample. One UUID per line. Comments at the top for reproducibility.
    with OUT_PATH.open("w") as fh:
        fh.write(f"# SovereignWatch eval sample — {len(selected)} chunks\n")
        fh.write(f"# stratified by (language, year, party), seed={RANDOM_SEED}\n")
        fh.write(f"# en={lang_counts.get('en', 0)}  fr={lang_counts.get('fr', 0)}\n")
        fh.write(f"# must-include from queries.jsonl: {len(must_include)}\n")
        fh.write("#\n")
        for cid in sorted(selected):
            fh.write(cid + "\n")

    print(f"\nWrote {len(selected)} chunk IDs to {OUT_PATH}")


if __name__ == "__main__":
    asyncio.run(main())
