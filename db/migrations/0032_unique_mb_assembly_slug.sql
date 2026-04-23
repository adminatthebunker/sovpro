-- Tighten mb_assembly_slug to a UNIQUE partial index so the
-- former-MLAs ingester can use ON CONFLICT (mb_assembly_slug) DO
-- UPDATE for idempotent upserts. Parallel to migration 0031 for
-- ab_assembly_mid.
--
-- Current state (2026-04-22): 56 current MLA rows, all with
-- distinct slugs in the existing non-unique partial index (verified
-- via SELECT mb_assembly_slug, count(*) FROM politicians
-- WHERE mb_assembly_slug IS NOT NULL GROUP BY 1 HAVING count(*)>1 —
-- returned zero rows). Replacing the non-unique index with a
-- unique partial index is therefore safe.
--
-- The former-MLAs backfill generates slugs in the shape
-- "lastname-firstname" for historical rows to avoid collision with
-- the current roster's surname-only slugs ("byram" → current-MLA;
-- "byram-jodie" would be the historical form). The ingester also
-- name-matches existing MLAs before inserting, so "Jodie Byram"
-- from the living page attaches her historical terms to the
-- existing "byram"-slug row rather than creating a new one.

DROP INDEX IF EXISTS idx_politicians_mb_assembly_slug;

CREATE UNIQUE INDEX idx_politicians_mb_assembly_slug
    ON politicians (mb_assembly_slug)
    WHERE mb_assembly_slug IS NOT NULL;
