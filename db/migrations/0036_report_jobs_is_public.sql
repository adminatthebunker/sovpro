-- 0036_report_jobs_is_public.sql
--
-- Premium reports phase 1c: public-share toggle.
--
-- Owner can flip a succeeded report to is_public=true, after which the
-- /public/reports/:id route serves it to anonymous visitors. Default is
-- false so existing rows stay private. Toggle is reversible — flipping
-- back to false 404s the public URL on the next request (cached crawls
-- obviously persist).

BEGIN;

ALTER TABLE report_jobs
  ADD COLUMN IF NOT EXISTS is_public boolean NOT NULL DEFAULT false;

-- Partial index — only public rows are looked up by id without a
-- user_id constraint, so this keeps the public-viewer path cheap as
-- the table grows. Almost all rows will be private, so the index
-- stays tiny.
CREATE INDEX IF NOT EXISTS idx_report_jobs_public_id
  ON report_jobs (id)
  WHERE is_public = true;

COMMIT;
