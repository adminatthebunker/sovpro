-- Embedding model migration — layer 4: parallel Qwen3-Embedding-0.6B column.
--
-- Why a second vector column instead of replacing `embedding`:
--   - 242 k chunks × 1024-dim of BGE-M3 embeddings represent ~8 h of
--     GPU compute we want to keep around for rollback during the
--     migration window.
--   - Two columns lets backfill run in parallel with retrieval (if any
--     retrieval existed yet), and lets us A/B two HNSW indexes live.
--   - Dim matches (both models are 1024-dim at their default output),
--     so the column type is identical to `embedding`.
--
-- Rollout:
--   1. Apply this migration — adds NULL `embedding_next` column.
--   2. Populate via the new `embed-speech-chunks-next` scanner command
--      (talks to the `tei` service, uses batched UPDATE via UNNEST).
--   3. When populated-ness is validated, cut retrieval reads to
--      `embedding_next` and drop the old column + index in a future
--      migration. Do NOT drop `embedding` here.
--
-- DEPENDS ON: 0017 (speech_chunks), 0014 (vector extension).

ALTER TABLE speech_chunks
    ADD COLUMN IF NOT EXISTS embedding_next        vector(1024),
    ADD COLUMN IF NOT EXISTS embedding_next_model  TEXT,
    ADD COLUMN IF NOT EXISTS embedded_next_at      TIMESTAMPTZ;

-- HNSW index on the new column. Same m/ef_construction as the old one
-- so quality comparisons are apples-to-apples.
CREATE INDEX IF NOT EXISTS idx_chunks_embedding_next
    ON speech_chunks USING hnsw (embedding_next vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

-- Partial index used by the backfill worker's "where NULL" scan. Mirrors
-- the existing idx_chunks_needs_embedding pattern.
CREATE INDEX IF NOT EXISTS idx_chunks_needs_embedding_next
    ON speech_chunks (id) WHERE embedding_next IS NULL;
