-- Seed daily-ingest schedules for live jurisdictions.
--
-- Idempotent: re-running this script updates existing rows by name.
-- NS schedules pre-date this seed and are intentionally NOT touched —
-- they live on their own legacy cron offsets (12:00, 13:00, 13:30 UTC).
--
-- Cadence: staggered, one jurisdiction per UTC hour, with intra-hour
-- offsets so each chain runs bills → hansard → speaker resolvers in
-- order. Args are mostly empty {}: each ingest command auto-resolves
-- the current parliament/session from legislative_sessions (see
-- services/scanner/src/legislative/current_session.py).
--
-- Apply via:
--   docker exec -i sw-db psql -U sw -d sovereignwatch -v ON_ERROR_STOP=1 \
--     < scripts/seed-daily-ingest-schedules.sql
--
-- Slot map (UTC):
--   11:00 federal  | 12:00 NS (existing) | 14:00 BC | 15:00 AB | 16:00 QC
--   17:00 MB       | 18:00 ON           | 19:00 NB | 20:00 NL | 21:00 NT/NU

BEGIN;

-- Helper: idempotent upsert pattern.
-- We key on `name` (no unique constraint exists today), so rely on the
-- INSERT…WHERE NOT EXISTS pattern + a follow-up UPDATE for re-runs.
-- This is wordier than ON CONFLICT but works without schema changes.

-- Strategy: DELETE-then-INSERT for the rows this seed owns. All rows
-- carry created_by='daily-ingest-rollout' to scope the delete.
DELETE FROM scanner_schedules WHERE created_by = 'daily-ingest-rollout';

-- ─── Federal (11:00 UTC) ────────────────────────────────────────────
INSERT INTO scanner_schedules (name, command, args, cron, enabled, created_by) VALUES
('Federal bills daily ingest',
 'ingest-federal-bills', '{}'::jsonb,
 '0 11 * * *', true, 'daily-ingest-rollout'),
('Federal Hansard daily ingest',
 'ingest-federal-hansard', '{}'::jsonb,
 '15 11 * * *', true, 'daily-ingest-rollout');

-- ─── BC (14:00 UTC) ─────────────────────────────────────────────────
INSERT INTO scanner_schedules (name, command, args, cron, enabled, created_by) VALUES
('BC bills daily ingest',
 'ingest-bc-bills', '{}'::jsonb,
 '0 14 * * *', true, 'daily-ingest-rollout'),
('BC Hansard daily ingest',
 'ingest-bc-hansard', '{}'::jsonb,
 '15 14 * * *', true, 'daily-ingest-rollout'),
('BC speaker resolver',
 'resolve-bc-speakers', '{}'::jsonb,
 '30 14 * * *', true, 'daily-ingest-rollout'),
('BC presiding speaker resolver',
 'resolve-presiding-speakers', '{"province": "BC"}'::jsonb,
 '45 14 * * *', true, 'daily-ingest-rollout');

-- ─── AB (15:00 UTC) ─────────────────────────────────────────────────
INSERT INTO scanner_schedules (name, command, args, cron, enabled, created_by) VALUES
('AB bills daily ingest',
 'ingest-ab-bills', '{}'::jsonb,
 '0 15 * * *', true, 'daily-ingest-rollout'),
('AB Hansard daily ingest',
 'ingest-ab-hansard', '{}'::jsonb,
 '15 15 * * *', true, 'daily-ingest-rollout'),
('AB speaker resolver',
 'resolve-ab-speakers', '{}'::jsonb,
 '30 15 * * *', true, 'daily-ingest-rollout'),
('AB presiding speaker resolver',
 'resolve-presiding-speakers', '{"province": "AB"}'::jsonb,
 '45 15 * * *', true, 'daily-ingest-rollout');

-- ─── QC (16:00 UTC) ─────────────────────────────────────────────────
INSERT INTO scanner_schedules (name, command, args, cron, enabled, created_by) VALUES
('QC bills CSV daily ingest',
 'ingest-qc-bills', '{}'::jsonb,
 '0 16 * * *', true, 'daily-ingest-rollout'),
('QC bills RSS refresh',
 'ingest-qc-bills-rss', '{}'::jsonb,
 '5 16 * * *', true, 'daily-ingest-rollout'),
('QC Hansard daily ingest',
 'ingest-qc-hansard', '{}'::jsonb,
 '15 16 * * *', true, 'daily-ingest-rollout'),
('QC speaker resolver',
 'resolve-qc-speakers', '{}'::jsonb,
 '30 16 * * *', true, 'daily-ingest-rollout'),
('QC presiding speaker resolver',
 'resolve-presiding-speakers', '{"province": "QC"}'::jsonb,
 '45 16 * * *', true, 'daily-ingest-rollout');

-- ─── MB (17:00 UTC) ─────────────────────────────────────────────────
-- MB has the longest chain — bills (HTML index), then PDF download,
-- then PDF parse, then Hansard, then 3 resolvers (sponsor + 2 speaker).
INSERT INTO scanner_schedules (name, command, args, cron, enabled, created_by) VALUES
('MB bills daily ingest',
 'ingest-mb-bills', '{}'::jsonb,
 '0 17 * * *', true, 'daily-ingest-rollout'),
('MB billstatus PDF download',
 'fetch-mb-billstatus-pdf', '{}'::jsonb,
 '5 17 * * *', true, 'daily-ingest-rollout'),
('MB bill events from PDF',
 'parse-mb-bill-events', '{}'::jsonb,
 '10 17 * * *', true, 'daily-ingest-rollout'),
('MB Hansard daily ingest',
 'ingest-mb-hansard', '{}'::jsonb,
 '15 17 * * *', true, 'daily-ingest-rollout'),
('MB bill sponsor resolver',
 'resolve-mb-bill-sponsors', '{}'::jsonb,
 '25 17 * * *', true, 'daily-ingest-rollout'),
('MB speaker resolver',
 'resolve-mb-speakers', '{}'::jsonb,
 '30 17 * * *', true, 'daily-ingest-rollout'),
('MB speaker resolver (date-windowed)',
 'resolve-mb-speakers-dated', '{}'::jsonb,
 '35 17 * * *', true, 'daily-ingest-rollout'),
('MB presiding speaker resolver',
 'resolve-presiding-speakers', '{"province": "MB"}'::jsonb,
 '45 17 * * *', true, 'daily-ingest-rollout');

-- ─── ON (18:00 UTC) ─────────────────────────────────────────────────
-- ON bills: 3-step chain (discover → fetch HTML pages → parse them),
-- packed into the first 10 minutes of the hour to leave room for the
-- Hansard chain. Hansard via ola.org JSON node landed 2026-04-24.
INSERT INTO scanner_schedules (name, command, args, cron, enabled, created_by) VALUES
('ON bills discovery',
 'ingest-on-bills', '{}'::jsonb,
 '0 18 * * *', true, 'daily-ingest-rollout'),
('ON bill pages fetch',
 'fetch-on-bill-pages', '{}'::jsonb,
 '5 18 * * *', true, 'daily-ingest-rollout'),
('ON bill pages parse',
 'parse-on-bill-pages', '{}'::jsonb,
 '10 18 * * *', true, 'daily-ingest-rollout'),
('ON Hansard daily ingest',
 'ingest-on-hansard', '{}'::jsonb,
 '20 18 * * *', true, 'daily-ingest-rollout'),
('ON speaker resolver',
 'resolve-on-speakers', '{}'::jsonb,
 '35 18 * * *', true, 'daily-ingest-rollout'),
('ON presiding speaker resolver',
 'resolve-presiding-speakers', '{"province": "ON"}'::jsonb,
 '50 18 * * *', true, 'daily-ingest-rollout');

-- ─── NB (19:00 UTC) ─────────────────────────────────────────────────
INSERT INTO scanner_schedules (name, command, args, cron, enabled, created_by) VALUES
('NB bills daily ingest',
 'ingest-nb-bills', '{}'::jsonb,
 '0 19 * * *', true, 'daily-ingest-rollout'),
('NB Hansard daily ingest',
 'ingest-nb-hansard', '{}'::jsonb,
 '15 19 * * *', true, 'daily-ingest-rollout'),
('NB speaker resolver',
 'resolve-nb-speakers', '{}'::jsonb,
 '30 19 * * *', true, 'daily-ingest-rollout'),
('NB presiding speaker resolver',
 'resolve-presiding-speakers', '{"province": "NB"}'::jsonb,
 '45 19 * * *', true, 'daily-ingest-rollout');

-- ─── NL (20:00 UTC) ─────────────────────────────────────────────────
INSERT INTO scanner_schedules (name, command, args, cron, enabled, created_by) VALUES
('NL bills daily ingest',
 'ingest-nl-bills', '{}'::jsonb,
 '0 20 * * *', true, 'daily-ingest-rollout'),
('NL Hansard daily ingest',
 'ingest-nl-hansard', '{}'::jsonb,
 '15 20 * * *', true, 'daily-ingest-rollout'),
('NL speaker resolver',
 'resolve-nl-speakers', '{}'::jsonb,
 '30 20 * * *', true, 'daily-ingest-rollout'),
('NL presiding speaker resolver',
 'resolve-presiding-speakers', '{"province": "NL"}'::jsonb,
 '45 20 * * *', true, 'daily-ingest-rollout');

-- ─── NT + NU (21:00 UTC) ────────────────────────────────────────────
-- Consensus-government legislatures: bills only (no sponsors, no Hansard
-- ingester until research-handoff per CLAUDE.md rule #5).
INSERT INTO scanner_schedules (name, command, args, cron, enabled, created_by) VALUES
('NT bills daily ingest',
 'ingest-nt-bills', '{}'::jsonb,
 '0 21 * * *', true, 'daily-ingest-rollout'),
('NU bills daily ingest',
 'ingest-nu-bills', '{}'::jsonb,
 '15 21 * * *', true, 'daily-ingest-rollout');

-- next_run_at is computed by the worker the first time it polls; leave
-- it NULL here so croniter advances it correctly on the worker tick.

COMMIT;

-- Show what we just wrote.
SELECT name, cron, enabled, command FROM scanner_schedules
 WHERE created_by = 'daily-ingest-rollout'
 ORDER BY cron, name;
