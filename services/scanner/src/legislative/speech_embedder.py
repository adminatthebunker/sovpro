"""speech_chunks → BGE-M3 dense embeddings.

Finds chunks where `embedding IS NULL`, batches them into calls to the
embed service, and writes the resulting 1024-dim vectors back.

Kept as its own command (separate from chunking) so operators can:
- run the chunker at ingest time (cheap, instant),
- let the embedder catch up overnight (expensive, CPU-bound),
- re-embed a specific set later if the model changes.

## Contract with the embed service

- HTTP POST `{EMBED_URL}/embed` with body
  `{"texts": [...], "return_tokens": false}` → `{items: [{embedding:[...]}, ...], elapsed_ms}`.
- Batch size is capped by the embed service's `MAX_BATCH` (64 by default);
  we default to 32 which matches our benchmark sweet spot.
- Failures are surfaced with retry — one embed failure should not
  block subsequent batches; unembedded chunks stay NULL and a re-run
  picks them up.

## pgvector write format

asyncpg has no native vector type, so we send the vector as a literal
string `"[0.1,0.2,…]"` and cast server-side via `$1::vector`. asyncpg's
prepared-statement cache handles this fine.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, Optional

import httpx
import orjson

from ..db import Database

log = logging.getLogger(__name__)

EMBED_URL = os.environ.get("EMBED_URL", "http://embed:8000").rstrip("/")
EMBED_BATCH = int(os.environ.get("EMBED_BATCH", "32"))
EMBED_TIMEOUT = int(os.environ.get("EMBED_TIMEOUT", "600"))
EMBED_MODEL_TAG = os.environ.get("EMBED_MODEL_TAG", "bge-m3")
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
    body = orjson.dumps({"texts": texts})
    r = await client.post(f"{EMBED_URL}/embed", content=body)
    r.raise_for_status()
    data = r.json()
    vecs = [item["embedding"] for item in data.get("items", [])]
    return vecs, int(data.get("elapsed_ms") or 0)


async def embed_pending(
    db: Database,
    *,
    limit_chunks: Optional[int] = None,
    batch_size: int = EMBED_BATCH,
) -> EmbedStats:
    """Embed every speech_chunk with embedding IS NULL.

    Args:
        limit_chunks: cap on total chunks to embed this run.
        batch_size: texts per /embed call.
    """
    stats = EmbedStats()
    # Fetch id + text in a predictable order so re-runs walk the backlog
    # newest-first (lines up with retrieval freshness).
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
        "embed-speech-chunks: %d chunks to embed (batch=%d → %s)",
        stats.chunks_seen, batch_size, EMBED_URL,
    )

    async with httpx.AsyncClient(
        timeout=EMBED_TIMEOUT, headers=REQUEST_HEADERS
    ) as client:
        for start in range(0, len(rows), batch_size):
            batch = rows[start : start + batch_size]
            texts = [r["text"] or "" for r in batch]
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
            async with db.pool.acquire() as conn:
                async with conn.transaction():
                    for row, vec in zip(batch, vecs):
                        await conn.execute(
                            """
                            UPDATE speech_chunks
                               SET embedding = $1::vector,
                                   embedded_at = now(),
                                   embedding_model = $2
                             WHERE id = $3
                            """,
                            _vec_literal(vec),
                            EMBED_MODEL_TAG,
                            row["id"],
                        )
            stats.chunks_embedded += len(batch)
            log.info(
                "batch %d: %d chunks in %d ms (server) — total %d/%d",
                stats.batches,
                len(batch),
                elapsed_ms,
                stats.chunks_embedded,
                stats.chunks_seen,
            )

    log.info(
        "embed-speech-chunks done: seen=%d embedded=%d batches=%d errors=%d "
        "server_ms=%d",
        stats.chunks_seen,
        stats.chunks_embedded,
        stats.batches,
        stats.errors,
        stats.total_elapsed_ms,
    )
    return stats
