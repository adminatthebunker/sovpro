-- Extend bill_sponsors with upstream slug + role text.
--
-- Per-bill pages on nslegislature.ca link the sponsor via a stable
-- profile URL ``/members/profiles/<slug>``. That slug is a better
-- resolution key than fuzzy-matching a name — same precedent as
-- ``politicians.openparliament_slug`` for federal.
--
-- ``sponsor_role`` captures the ministerial title verbatim
-- (e.g. "Minister of Service Nova Scotia"). Useful for display and
-- for distinguishing Cabinet vs. private-member bills downstream.

ALTER TABLE bill_sponsors
  ADD COLUMN IF NOT EXISTS sponsor_slug  TEXT,
  ADD COLUMN IF NOT EXISTS sponsor_role  TEXT,
  ADD COLUMN IF NOT EXISTS source_system TEXT;

-- Idempotency: one (bill_id, sponsor_slug) per parse. Without a slug
-- we fall back to name-based uniqueness to stop duplicate inserts.
CREATE UNIQUE INDEX IF NOT EXISTS uq_bill_sponsors_slug
    ON bill_sponsors (bill_id, sponsor_slug)
    WHERE sponsor_slug IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS uq_bill_sponsors_name
    ON bill_sponsors (bill_id, sponsor_name_raw)
    WHERE sponsor_slug IS NULL AND sponsor_name_raw IS NOT NULL;

-- Mirror field on politicians so sponsor→politician resolution is an
-- exact slug join rather than name fuzzing. Populated by a separate
-- resolver pass (future: resolve_ns_profiles.py).
ALTER TABLE politicians
  ADD COLUMN IF NOT EXISTS nslegislature_slug TEXT;

CREATE INDEX IF NOT EXISTS idx_politicians_nsslug
    ON politicians (nslegislature_slug)
    WHERE nslegislature_slug IS NOT NULL;
