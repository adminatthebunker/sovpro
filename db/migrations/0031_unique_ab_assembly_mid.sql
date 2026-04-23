-- Replace the non-unique partial index on politicians.ab_assembly_mid
-- with a UNIQUE partial index, so the historical-MLA backfill
-- (ingest-ab-former-mlas) can use ON CONFLICT upserts keyed on mid.
--
-- Verified pre-migration: zero duplicates across 87 existing mids.
-- Matches the MB pattern from 0030 (UNIQUE partial on mb_assembly_slug).

DROP INDEX IF EXISTS idx_politicians_ab_assembly_mid;

CREATE UNIQUE INDEX idx_politicians_ab_assembly_mid
    ON politicians (ab_assembly_mid)
    WHERE ab_assembly_mid IS NOT NULL;
