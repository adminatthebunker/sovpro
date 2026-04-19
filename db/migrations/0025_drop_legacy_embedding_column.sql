-- Retire the legacy BGE-M3 `embedding` column and promote the Qwen3
-- `embedding_next` column to the canonical name.
--
-- Context: 0023 added `embedding_next` as a blue-green column so we
-- could re-embed the whole corpus with Qwen3-Embedding-0.6B while the
-- old BGE-M3 embeddings stayed live for rollback. That migration
-- succeeded: 2026-04-18 re-embed landed 242 k chunks in 1h19m at 50.9
-- chunks/sec end-to-end, and the subsequent historical retag (0024)
-- confirmed all 1,483,610 chunks in the corpus carry Qwen3 vectors.
--
-- The legacy `embedding` column now holds BGE-M3 vectors for only the
-- 44th-Parliament subset (~242 k of 1.48 M rows) and is never read by
-- any retrieval path. Dropping it:
--   - simplifies the schema to one vector column / one HNSW index.
--   - reclaims disk (~1 GB) and removes index-maintenance cost on
--     future UPDATEs of the 242 k dual-tagged rows.
--   - aligns the column name with the scanner helpers
--     (`embed_pending`, `/embed`, CLI `embed-speech-chunks`) that we
--     rename in this same commit.
--
-- Rollback: restore from pg_dump taken before migration. Re-embedding
-- 242 k chunks back onto a new BGE-M3 column is cheap (~1.5 h with
-- TEI serving BGE-M3, or bring the `sw-embed` service back).
--
-- Paired with code changes: `embed_pending_next` → `embed_pending`,
-- `embed-speech-chunks-next` → `embed-speech-chunks`, `EMBED_NEXT_URL`
-- → `EMBED_URL`, removal of the `embed` compose service (TEI is the
-- lone embedding server now).

BEGIN;

-- Drop BGE-M3 column + its indexes.
DROP INDEX IF EXISTS idx_chunks_embedding;
DROP INDEX IF EXISTS idx_chunks_needs_embedding;

ALTER TABLE speech_chunks
    DROP COLUMN IF EXISTS embedding,
    DROP COLUMN IF EXISTS embedding_model,
    DROP COLUMN IF EXISTS embedded_at;

-- Promote the Qwen3 columns to the canonical names.
ALTER TABLE speech_chunks RENAME COLUMN embedding_next       TO embedding;
ALTER TABLE speech_chunks RENAME COLUMN embedding_next_model TO embedding_model;
ALTER TABLE speech_chunks RENAME COLUMN embedded_next_at     TO embedded_at;

ALTER INDEX idx_chunks_embedding_next       RENAME TO idx_chunks_embedding;
ALTER INDEX idx_chunks_needs_embedding_next RENAME TO idx_chunks_needs_embedding;

COMMIT;
