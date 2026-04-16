-- Enrich bill_events with Ontario-shaped detail, and add a second HTML
-- cache slot on bills for the /status sub-page.
--
-- Ontario's ola.org /status tab exposes a 5-column table
--   Date | Bill stage | Event | Outcome | Committee
-- which carries information the NS Socrata feed never had: distinct
-- procedural events within a stage ("Debated" vs. "Vote" vs. "Moved
-- closure"), outcome ("Carried on division", "Debate adjourned", ...),
-- and committee assignment. Preserving those is the difference between
-- "Second Reading happened on Apr 30" and the 4 separate events that
-- actually occurred on that date.
--
-- The old unique constraint (bill_id, stage, event_date) would collapse
-- legitimate same-day events into one row. We replace it with a 5-column
-- key using NULLS NOT DISTINCT (requires Postgres 15+) so that NS rows
-- with NULL event_type / committee_name still dedupe correctly.

-- 1. Second HTML cache slot on bills, for the /status sub-page (Ontario
--    and any future jurisdiction that splits bill detail across tabs).
ALTER TABLE bills
  ADD COLUMN IF NOT EXISTS raw_status_html          TEXT,
  ADD COLUMN IF NOT EXISTS status_html_fetched_at   TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS status_html_last_error   TEXT,
  ADD COLUMN IF NOT EXISTS status_html_last_error_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_bills_status_html_needed
    ON bills (id)
    WHERE raw_status_html IS NULL;

-- 2. Richer event columns.
ALTER TABLE bill_events
  ADD COLUMN IF NOT EXISTS event_type     TEXT,
  ADD COLUMN IF NOT EXISTS outcome        TEXT,
  ADD COLUMN IF NOT EXISTS committee_name TEXT;

-- 3. Replace the old unique key. The implicit name from migration 0006
--    is bill_events_bill_id_stage_event_date_key.
ALTER TABLE bill_events
  DROP CONSTRAINT IF EXISTS bill_events_bill_id_stage_event_date_key;

ALTER TABLE bill_events
  ADD CONSTRAINT bill_events_uniq
  UNIQUE NULLS NOT DISTINCT
  (bill_id, stage, event_date, event_type, committee_name);
