-- Mirror politicians.nslegislature_slug (added in 0008) with an
-- ola.org slug so Ontario sponsor→politician resolution is an exact
-- join rather than repeated name fuzzing.
--
-- Design choice: one column per province's member-index URL scheme,
-- rather than a single generic slug column. Each jurisdiction has
-- its own URL namespace (/members/profiles/X on nslegislature.ca,
-- /members/all/X on ola.org), and a per-column index keeps slug
-- lookups cheap even as we add more provinces.

ALTER TABLE politicians
  ADD COLUMN IF NOT EXISTS ola_slug TEXT;

CREATE INDEX IF NOT EXISTS idx_politicians_ola_slug
    ON politicians (ola_slug)
    WHERE ola_slug IS NOT NULL;
