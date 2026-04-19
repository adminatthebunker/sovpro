"""speech_chunks → Qwen3 dense embeddings via TEI.

Finds chunks where `embedding IS NULL`, batches them into calls to the
TEI (Text Embeddings Inference) service serving Qwen3-Embedding-0.6B,
and writes the resulting 1024-dim vectors back via a batched
`UPDATE ... FROM UNNEST(...)` (one DB round-trip per batch, not per row).

Measured 2026-04-18 at 50.9 chunks/sec end-to-end on the RTX 4050 Mobile
— roughly 10.8× the earlier BGE-M3-via-FastAPI path (4.7 chunks/sec,
dominated by per-row UPDATE overhead).

Kept as its own command (separate from chunking) so operators can:
- run the chunker at ingest time (cheap, instant),
- let the embedder catch up overnight (GPU-bound),
- re-embed a specific set later if the model changes.

## Contract with TEI

- HTTP POST `{EMBED_URL}/embed` with body
  `{"inputs": [...], "normalize": true}` → bare JSON array of 1024-dim
  embedding arrays.
- Batch size is bounded by TEI's `--max-client-batch-size` (default 64
  in our compose config).
- Documents are sent *raw*. The Qwen3 instruction-prompt wrapper
  (`Instruct: ...\\nQuery: ...`) is retrieval-time only; applying it at
  indexing time would embed the instruction into every document vector
  and regress quality to the vanilla-Qwen3 numbers (0.220 NDCG@10 vs
  0.381 for instruct, per services/embed/eval/REPORT.md).
- Failures are surfaced with retry — one embed failure does not block
  subsequent batches; unembedded chunks stay NULL and a re-run picks
  them up.

## pgvector write format

asyncpg has no native vector type, so we send the vector as a literal
string `"[0.1,0.2,…]"` and cast server-side via `$1::vector`. asyncpg's
prepared-statement cache handles this fine.
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from typing import Optional

import httpx
import orjson

from ..db import Database

log = logging.getLogger(__name__)

EMBED_URL = os.environ.get("EMBED_URL", "http://tei:80").rstrip("/")
EMBED_BATCH = int(os.environ.get("EMBED_BATCH", "32"))
EMBED_TIMEOUT = int(os.environ.get("EMBED_TIMEOUT", "600"))
EMBED_MODEL_TAG = os.environ.get("EMBED_MODEL_TAG", "qwen3-embedding-0.6b")
REQUEST_HEADERS = {
    "User-Agent": "SovereignWatchScanner/1.0",
    "Accept": "application/json",
    "Content-Type": "application/json",
}


@dataclass
class EmbedStats:
    chunks_seen: int = 0
    chunks_embedded: int = 0
    batches: int = 0
    total_elapsed_ms: int = 0
    errors: int = 0


def _vec_literal(vec: list[float]) -> str:
    """pgvector accepts '[0.1,0.2,...]' strings; avoid scientific notation
    to keep the input parseable across locales."""
    return "[" + ",".join(f"{float(v):.8f}" for v in vec) + "]"


async def _embed_batch(
    client: httpx.AsyncClient, texts: list[str]
) -> tuple[list[list[float]], int]:
    """Send a batch of raw document texts to TEI. Returns (vectors, elapsed_ms)."""
    body = orjson.dumps({"inputs": texts, "normalize": True})
    t0 = time.perf_counter()
    r = await client.post(f"{EMBED_URL}/embed", content=body)
    r.raise_for_status()
    elapsed_ms = int((time.perf_counter() - t0) * 1000)
    # TEI returns a bare JSON array of embedding arrays on /embed (the
    # OpenAI-compatible /v1/embeddings endpoint returns {"data": [...]}).
    data = r.json()
    if isinstance(data, dict) and "data" in data:
        vecs = [d["embedding"] for d in data["data"]]
    else:
        vecs = list(data)
    return vecs, elapsed_ms


async def embed_pending(
    db: Database,
    *,
    limit_chunks: Optional[int] = None,
    batch_size: int = EMBED_BATCH,
) -> EmbedStats:
    """Embed every speech_chunk with embedding IS NULL via TEI.

    Args:
        limit_chunks: cap on total chunks to embed this run.
        batch_size: texts per /embed call.
    """
    stats = EmbedStats()
    q = """
        SELECT id, text
        FROM speech_chunks
        WHERE embedding IS NULL
        ORDER BY spoken_at DESC NULLS LAST, id
    """
    if limit_chunks:
        q += f" LIMIT {int(limit_chunks)}"
    rows = await db.fetch(q)
    stats.chunks_seen = len(rows)
    if not rows:
        log.info("embed-speech-chunks: nothing to do")
        return stats

    log.info(
        "embed-speech-chunks: %d chunks to embed (batch=%d → %s, model=%s)",
        stats.chunks_seen, batch_size, EMBED_URL, EMBED_MODEL_TAG,
    )

    async with httpx.AsyncClient(
        timeout=EMBED_TIMEOUT, headers=REQUEST_HEADERS
    ) as client:
        for start in range(0, len(rows), batch_size):
            batch = rows[start : start + batch_size]
            texts = [r["text"] or " " for r in batch]
            try:
                vecs, elapsed_ms = await _embed_batch(client, texts)
            except Exception as exc:
                stats.errors += 1
                log.warning("embed batch failed at offset %d: %s", start, exc)
                continue
            if len(vecs) != len(batch):
                log.warning(
                    "embed response mismatch: asked %d got %d; skipping batch",
                    len(batch), len(vecs),
                )
                stats.errors += 1
                continue

            stats.batches += 1
            stats.total_elapsed_ms += elapsed_ms

            # Batched write via UNNEST — one UPDATE per batch instead of
            # len(batch) separate UPDATEs. This is the throughput unlock
            # that took end-to-end from 4.7 c/s (legacy per-row path) to
            # 50.9 c/s, with the GPU becoming the actual bottleneck.
            ids = [row["id"] for row in batch]
            vec_literals = [_vec_literal(v) for v in vecs]

            await db.execute(
                """
                UPDATE speech_chunks AS sc
                   SET embedding       = v.emb::vector,
                       embedding_model = $3,
                       embedded_at     = now()
                  FROM UNNEST($1::uuid[], $2::text[]) AS v(id, emb)
                 WHERE sc.id = v.id
                """,
                ids,
                vec_literals,
                EMBED_MODEL_TAG,
            )

            stats.chunks_embedded += len(batch)
            log.info(
                "batch %d: %d chunks in %d ms (server) — total %d/%d",
                stats.batches, len(batch), elapsed_ms,
                stats.chunks_embedded, stats.chunks_seen,
            )

    log.info(
        "embed-speech-chunks done: seen=%d embedded=%d batches=%d errors=%d "
        "server_ms=%d",
        stats.chunks_seen, stats.chunks_embedded, stats.batches, stats.errors,
        stats.total_elapsed_ms,
    )
    return stats
