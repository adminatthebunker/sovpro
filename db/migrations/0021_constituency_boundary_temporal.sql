-- Make constituency_boundaries history-aware.
--
-- Today the table holds a single current row per constituency_id.
-- Historical queries ("who represented Nepean in 2008?") require
-- knowing which boundary version was in force at a given date, because
-- ridings redraw every census and names / ids get reused.
--
-- Schema change: add effective_from / effective_to / boundaries_version.
-- Backfill existing rows as the *current* version (effective_from set
-- to 2023-01-01 — the last federal redistribution order came into force
-- for the 2025 federal election, close enough for phase 0), and leave
-- effective_to NULL meaning "still in force".
--
-- Future redistribution orders land as NEW rows with a new version
-- string; the old row's effective_to is set to the day before the new
-- version took effect.
--
-- UI / API queries that want "boundary at date X" do:
--    WHERE effective_from <= :date AND (effective_to IS NULL OR effective_to >= :date)
--
-- The existing UNIQUE(constituency_id) constraint (from init.sql) has
-- to go — a constituency_id will exist once per boundary version. We
-- replace it with UNIQUE(constituency_id, boundaries_version) so the
-- ingester can still upsert by (id, version) idempotently.

ALTER TABLE constituency_boundaries
    ADD COLUMN IF NOT EXISTS effective_from       DATE,
    ADD COLUMN IF NOT EXISTS effective_to         DATE,
    ADD COLUMN IF NOT EXISTS boundaries_version   TEXT;

-- Backfill existing rows as the current version.
UPDATE constituency_boundaries
   SET effective_from     = COALESCE(effective_from, DATE '2023-01-01'),
       boundaries_version = COALESCE(boundaries_version, 'current')
 WHERE effective_from IS NULL
    OR boundaries_version IS NULL;

-- Replace the single-column uniqueness constraint with the
-- version-aware one. The old constraint's implicit name is
-- constituency_boundaries_constituency_id_key (from init.sql).
ALTER TABLE constituency_boundaries
    DROP CONSTRAINT IF EXISTS constituency_boundaries_constituency_id_key;

ALTER TABLE constituency_boundaries
    ADD CONSTRAINT constituency_boundaries_id_version_key
    UNIQUE (constituency_id, boundaries_version);

-- Partial index for "current" lookups (the common case).
CREATE INDEX IF NOT EXISTS idx_constituency_boundaries_current
    ON constituency_boundaries (constituency_id, province_territory)
    WHERE effective_to IS NULL;

-- Full temporal range index for "at date X" queries.
CREATE INDEX IF NOT EXISTS idx_constituency_boundaries_temporal
    ON constituency_boundaries (constituency_id, effective_from, effective_to);
