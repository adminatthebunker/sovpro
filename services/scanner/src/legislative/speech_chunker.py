"""Speech → speech_chunks splitter.

Turns rows in `speeches` into retrievable units in `speech_chunks`
(one per speaker turn by default, paragraph-split on long turns).

This module is jurisdiction-agnostic: it chunks any speeches row,
whether the upstream source was openparliament (federal), Hansard
scrape (provincial), or committee transcript. The `speech_chunks`
table's discriminator columns (level, province_territory) are copied
from the parent speech.

## Rules

- **One speaker turn = one chunk** by default (the simplest, most
  informative unit — politician_id attaches cleanly).
- **Long turns split at paragraph boundary** with a 50-token overlap.
  The splitter targets `CHUNK_TARGET_TOKENS` (default 480) so we stay
  safely under BGE-M3's 512-tok practical window; the embed service
  will still accept up to 8192 but throughput drops.
- **Tiny turns skipped** (< `MIN_TOKENS`, default 8). Procedural
  "Mr. Speaker" / "Thank you" entries stay in `speeches` for timeline
  continuity but don't clutter the retrieval index.
- **Token estimation is approximate** — 1 token ≈ 3.5 chars for
  XLMR-family tokenizers on EN/FR mixed corpora. Under-counting leads
  to slightly larger-than-ideal chunks; over-counting leads to more
  splits than needed. Good enough for v0; we can call the embed
  service's tokenizer later for exact counts if it matters.

## tsvector setup

`speech_chunks.tsv` is the BM25 index. We set `tsv_config` per-language
(`english` / `french` / `simple`) so the tsvector normalises sensibly.
`unaccent` is installed (migration 0014) but we don't apply it in the
config here — exact accent-preserving match matters for FR names /
ridings. Re-evaluate after first retrieval tuning pass.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, Optional

from ..db import Database

log = logging.getLogger(__name__)

CHUNK_TARGET_TOKENS = 480
CHUNK_OVERLAP_TOKENS = 50
MIN_CHUNK_TOKENS = 8
# XLMR tokenizers average ~3.5 chars/token on mixed EN/FR corpora.
CHARS_PER_TOKEN = 3.5

LANG_TO_TSCONFIG = {
    "en": "english",
    "fr": "french",
    # Inuktitut / other: simple normaliser (no stemmer).
}


def _estimate_tokens(text: str) -> int:
    return max(1, int(round(len(text) / CHARS_PER_TOKEN)))


def _tsconfig_for(language: str) -> str:
    return LANG_TO_TSCONFIG.get(language.lower(), "simple")


@dataclass
class Chunk:
    text: str
    char_start: int
    char_end: int
    token_count: int


def split_into_chunks(text: str) -> list[Chunk]:
    """Split a speaker turn into embeddable chunks.

    Returns an empty list if the input is empty or below the minimum
    token threshold.
    """
    text = text.strip()
    if not text:
        return []
    total_tokens = _estimate_tokens(text)
    if total_tokens < MIN_CHUNK_TOKENS:
        return []
    if total_tokens <= CHUNK_TARGET_TOKENS:
        return [
            Chunk(
                text=text,
                char_start=0,
                char_end=len(text),
                token_count=total_tokens,
            )
        ]

    # Long turn: split at paragraph boundaries, greedy-pack up to target.
    # openparliament's html_to_text joined paragraphs with "\n" so we
    # split on blank lines or single \n — both work because we
    # collapsed whitespace earlier.
    paragraphs = [p for p in re.split(r"\n+", text) if p.strip()]
    if len(paragraphs) == 1:
        # No paragraph boundaries — hard-split on sentence boundaries.
        # Cheap heuristic: split on ".  " or ". " followed by uppercase.
        paragraphs = re.split(r"(?<=[.!?])\s+(?=[A-ZÀ-Ý])", text)

    chunks: list[Chunk] = []
    buf: list[str] = []
    buf_tokens = 0
    # Track char offset into original text for provenance.
    offset = 0
    char_cursor = 0
    for para in paragraphs:
        para_tokens = _estimate_tokens(para)
        # If adding this paragraph would overflow, flush current buf.
        if buf and buf_tokens + para_tokens > CHUNK_TARGET_TOKENS:
            chunk_text = "\n".join(buf).strip()
            if chunk_text:
                # Find chunk_text inside original text from cursor onwards.
                pos = text.find(chunk_text, offset)
                if pos < 0:
                    pos = offset
                chunks.append(
                    Chunk(
                        text=chunk_text,
                        char_start=pos,
                        char_end=pos + len(chunk_text),
                        token_count=_estimate_tokens(chunk_text),
                    )
                )
                offset = chunks[-1].char_end
            # Start next buffer with overlap from the tail of this one.
            if CHUNK_OVERLAP_TOKENS and chunks:
                tail_chars = int(CHUNK_OVERLAP_TOKENS * CHARS_PER_TOKEN)
                tail = chunks[-1].text[-tail_chars:]
                buf = [tail, para]
                buf_tokens = _estimate_tokens(tail) + para_tokens
            else:
                buf = [para]
                buf_tokens = para_tokens
        else:
            buf.append(para)
            buf_tokens += para_tokens
        char_cursor += len(para) + 1  # +1 for the joining newline

    if buf:
        chunk_text = "\n".join(buf).strip()
        if chunk_text and _estimate_tokens(chunk_text) >= MIN_CHUNK_TOKENS:
            pos = text.find(chunk_text, offset) if offset < len(text) else offset
            if pos < 0:
                pos = offset
            chunks.append(
                Chunk(
                    text=chunk_text,
                    char_start=pos,
                    char_end=pos + len(chunk_text),
                    token_count=_estimate_tokens(chunk_text),
                )
            )
    return chunks


@dataclass
class ChunkStats:
    speeches_seen: int = 0
    speeches_chunked: int = 0
    speeches_skipped: int = 0
    chunks_inserted: int = 0


async def chunk_pending(
    db: Database,
    *,
    limit_speeches: Optional[int] = None,
) -> ChunkStats:
    """Find speeches without chunks and produce them.

    Idempotent via (speech_id, chunk_index) unique; callers can re-run
    safely. Existing speech_chunks rows are never deleted here —
    re-chunking after a code change is a separate admin task.
    """
    stats = ChunkStats()
    query = """
        SELECT s.id, s.text, s.language, s.politician_id, s.level,
               s.province_territory, s.spoken_at, s.session_id,
               s.party_at_time
        FROM speeches s
        LEFT JOIN speech_chunks c ON c.speech_id = s.id
        WHERE c.id IS NULL
        ORDER BY s.spoken_at DESC NULLS LAST, s.id
    """
    if limit_speeches:
        query += f" LIMIT {int(limit_speeches)}"

    rows = await db.fetch(query)
    for row in rows:
        stats.speeches_seen += 1
        chunks = split_into_chunks(row["text"] or "")
        if not chunks:
            stats.speeches_skipped += 1
            continue
        tsconfig = _tsconfig_for(row["language"] or "en")
        async with db.pool.acquire() as conn:
            async with conn.transaction():
                for idx, ch in enumerate(chunks):
                    await conn.execute(
                        """
                        INSERT INTO speech_chunks (
                            speech_id, chunk_index, text, token_count,
                            char_start, char_end, language,
                            politician_id, party_at_time, level,
                            province_territory, spoken_at, session_id,
                            embedding, tsv, tsv_config
                        ) VALUES (
                            $1, $2, $3, $4,
                            $5, $6, $7,
                            $8, $9, $10,
                            $11, $12, $13,
                            NULL, to_tsvector($14::regconfig, $3), $14
                        )
                        ON CONFLICT (speech_id, chunk_index) DO NOTHING
                        """,
                        row["id"],
                        idx,
                        ch.text,
                        ch.token_count,
                        ch.char_start,
                        ch.char_end,
                        row["language"],
                        row["politician_id"],
                        row["party_at_time"],
                        row["level"],
                        row["province_territory"],
                        row["spoken_at"],
                        row["session_id"],
                        tsconfig,
                    )
                    stats.chunks_inserted += 1
        stats.speeches_chunked += 1

    log.info(
        "chunk-speeches: seen=%d chunked=%d skipped=%d chunks=%d",
        stats.speeches_seen,
        stats.speeches_chunked,
        stats.speeches_skipped,
        stats.chunks_inserted,
    )
    return stats
