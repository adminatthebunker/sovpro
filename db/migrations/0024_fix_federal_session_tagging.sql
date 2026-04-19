-- Fix federal speech session-tagging + duplicate legislative_sessions rows.
--
-- Two bugs discovered 2026-04-19 during historical-backfill work:
--
--   1. The `ingest-federal-hansard` command walks every /debates/ page
--      on openparliament regardless of its --parliament / --session
--      flags (those flags only drive the session-attribution of
--      newly-inserted rows). A single un-bounded ingest tagged ~896 k
--      speeches with whatever session-id the command was invoked for,
--      even though the rows themselves span 1994 → 2026.
--      Code-side fix: scanner now auto-derives /debates/?date__gte=… &
--      date__lte=… bounds from a static Canadian-parliament session
--      date table (federal_hansard.FEDERAL_SESSION_DATES). That stops
--      future runs from bleeding. This migration repairs history.
--
--   2. `legislative_sessions` has a UNIQUE constraint on
--      (level, province_territory, parliament_number, session_number),
--      but province_territory is NULL for federal and Postgres treats
--      NULL as DISTINCT from NULL by default. So every
--      ensure_session(...) INSERT for federal created a NEW duplicate
--      row. We ended up with 4 P44-S1 rows, 3 P43-S2 rows, etc.
--      Fix: consolidate dupes to one canonical row per (parliament,
--      session), repoint speeches/bills at the canonical row, drop the
--      old UNIQUE constraint, recreate it WITH NULLS NOT DISTINCT so
--      federal upserts actually dedupe going forward.
--
-- After this migration:
--   - There is exactly one `legislative_sessions` row per
--     (level, province_territory, parliament_number, session_number).
--   - Every federal speech's `session_id` matches the session whose
--     date range contains its `spoken_at`.
--   - Future ensure_session() calls dedupe correctly.

BEGIN;

-- ── Step 1: create any missing federal sessions (35-44) ─────────────

WITH wanted(parliament_number, session_number, name, start_date, end_date) AS (
    VALUES
        (35, 1, '35th Parliament, Session 1', DATE '1994-01-17', DATE '1996-02-02'),
        (35, 2, '35th Parliament, Session 2', DATE '1996-02-27', DATE '1997-04-27'),
        (36, 1, '36th Parliament, Session 1', DATE '1997-09-22', DATE '1999-09-18'),
        (36, 2, '36th Parliament, Session 2', DATE '1999-10-12', DATE '2000-10-22'),
        (37, 1, '37th Parliament, Session 1', DATE '2001-01-29', DATE '2002-09-16'),
        (37, 2, '37th Parliament, Session 2', DATE '2002-09-30', DATE '2003-11-12'),
        (37, 3, '37th Parliament, Session 3', DATE '2004-02-02', DATE '2004-05-23'),
        (38, 1, '38th Parliament, Session 1', DATE '2004-10-04', DATE '2005-11-29'),
        (39, 1, '39th Parliament, Session 1', DATE '2006-04-03', DATE '2007-09-14'),
        (39, 2, '39th Parliament, Session 2', DATE '2007-10-16', DATE '2008-09-07'),
        (40, 1, '40th Parliament, Session 1', DATE '2008-11-18', DATE '2008-12-04'),
        (40, 2, '40th Parliament, Session 2', DATE '2009-01-26', DATE '2009-12-30'),
        (40, 3, '40th Parliament, Session 3', DATE '2010-03-03', DATE '2011-03-26'),
        (41, 1, '41st Parliament, Session 1', DATE '2011-06-02', DATE '2013-09-13'),
        (41, 2, '41st Parliament, Session 2', DATE '2013-10-16', DATE '2015-08-02'),
        (42, 1, '42nd Parliament, Session 1', DATE '2015-12-03', DATE '2019-09-11'),
        (43, 1, '43rd Parliament, Session 1', DATE '2019-12-05', DATE '2020-08-18'),
        (43, 2, '43rd Parliament, Session 2', DATE '2020-09-23', DATE '2021-08-15'),
        (44, 1, '44th Parliament, Session 1', DATE '2021-11-22', DATE '2099-12-31')
)
INSERT INTO legislative_sessions
    (level, province_territory, parliament_number, session_number,
     name, start_date, end_date, source_system, source_url)
SELECT 'federal', NULL, w.parliament_number, w.session_number,
       w.name, w.start_date, w.end_date, 'openparliament',
       'https://openparliament.ca/debates/'
  FROM wanted w
 WHERE NOT EXISTS (
     SELECT 1 FROM legislative_sessions ls
      WHERE ls.level = 'federal'
        AND ls.province_territory IS NULL
        AND ls.parliament_number = w.parliament_number
        AND ls.session_number = w.session_number
 );

-- Populate start_date / end_date on any legacy rows missing them (the
-- early ingests never set these; we need them for the retag step).
UPDATE legislative_sessions ls
   SET start_date = w.start_date,
       end_date   = w.end_date
  FROM (VALUES
        (35, 1, DATE '1994-01-17', DATE '1996-02-02'),
        (35, 2, DATE '1996-02-27', DATE '1997-04-27'),
        (36, 1, DATE '1997-09-22', DATE '1999-09-18'),
        (36, 2, DATE '1999-10-12', DATE '2000-10-22'),
        (37, 1, DATE '2001-01-29', DATE '2002-09-16'),
        (37, 2, DATE '2002-09-30', DATE '2003-11-12'),
        (37, 3, DATE '2004-02-02', DATE '2004-05-23'),
        (38, 1, DATE '2004-10-04', DATE '2005-11-29'),
        (39, 1, DATE '2006-04-03', DATE '2007-09-14'),
        (39, 2, DATE '2007-10-16', DATE '2008-09-07'),
        (40, 1, DATE '2008-11-18', DATE '2008-12-04'),
        (40, 2, DATE '2009-01-26', DATE '2009-12-30'),
        (40, 3, DATE '2010-03-03', DATE '2011-03-26'),
        (41, 1, DATE '2011-06-02', DATE '2013-09-13'),
        (41, 2, DATE '2013-10-16', DATE '2015-08-02'),
        (42, 1, DATE '2015-12-03', DATE '2019-09-11'),
        (43, 1, DATE '2019-12-05', DATE '2020-08-18'),
        (43, 2, DATE '2020-09-23', DATE '2021-08-15'),
        (44, 1, DATE '2021-11-22', DATE '2099-12-31')
       ) AS w(parliament_number, session_number, start_date, end_date)
 WHERE ls.level='federal'
   AND ls.province_territory IS NULL
   AND ls.parliament_number = w.parliament_number
   AND ls.session_number = w.session_number;

-- ── Step 2: pick canonical row per (parliament, session), repoint FKs ──

-- For each (parl, sess), keep the oldest `created_at` as canonical.
CREATE TEMP TABLE _canonical_sessions AS
SELECT DISTINCT ON (parliament_number, session_number)
       id AS canonical_id, parliament_number, session_number
  FROM legislative_sessions
 WHERE level='federal' AND province_territory IS NULL
 ORDER BY parliament_number, session_number, created_at;

-- Speeches: point every federal speech at the canonical row for its
-- currently-tagged session. (This is a no-op for already-canonical rows.)
UPDATE speeches sp
   SET session_id = c.canonical_id
  FROM legislative_sessions ls
  JOIN _canonical_sessions c
    ON c.parliament_number = ls.parliament_number
   AND c.session_number = ls.session_number
 WHERE sp.session_id = ls.id
   AND ls.level='federal'
   AND ls.province_territory IS NULL
   AND sp.session_id <> c.canonical_id;

-- Bills: same.
UPDATE bills b
   SET session_id = c.canonical_id
  FROM legislative_sessions ls
  JOIN _canonical_sessions c
    ON c.parliament_number = ls.parliament_number
   AND c.session_number = ls.session_number
 WHERE b.session_id = ls.id
   AND ls.level='federal'
   AND ls.province_territory IS NULL
   AND b.session_id <> c.canonical_id;

-- Delete the now-unreferenced duplicate rows.
DELETE FROM legislative_sessions ls
 USING _canonical_sessions c
 WHERE ls.level='federal'
   AND ls.province_territory IS NULL
   AND ls.parliament_number = c.parliament_number
   AND ls.session_number = c.session_number
   AND ls.id <> c.canonical_id;

-- ── Step 3: retag speeches whose spoken_at lies in a different session ──

-- For every federal speech, find the session whose date range includes
-- its spoken_at and rewrite session_id to that session's canonical id.
-- Speeches with NULL spoken_at (tiny minority) are left alone.
WITH retag AS (
    SELECT sp.id AS speech_id, ls.id AS correct_session_id
      FROM speeches sp
      JOIN legislative_sessions ls
        ON ls.level='federal'
       AND ls.province_territory IS NULL
       AND sp.spoken_at::date BETWEEN ls.start_date AND ls.end_date
     WHERE sp.level='federal'
       AND sp.spoken_at IS NOT NULL
       AND sp.session_id IS DISTINCT FROM ls.id
)
UPDATE speeches sp
   SET session_id = r.correct_session_id,
       updated_at = now()
  FROM retag r
 WHERE sp.id = r.speech_id;

-- NOTE: `speech_chunks.session_id` is a denormalised copy of
-- `speeches.session_id`. We deliberately do NOT rewrite it here: a blind
-- UPDATE on ~1.5M chunks forces HNSW index maintenance on every row
-- (Postgres UPDATE = new tuple → every index re-points), which took
-- >1h and still hadn't finished in testing. The denorm is not
-- load-bearing — the retrieval layer can always JOIN speeches for the
-- authoritative session_id. Refresh of speech_chunks.session_id is
-- handled separately by the `refresh-chunk-session-ids` scanner command
-- which drops and rebuilds the HNSW indexes around the UPDATE.

-- ── Step 4: UNIQUE constraint with NULLS NOT DISTINCT ───────────────

-- Drop the broken constraint.
ALTER TABLE legislative_sessions
    DROP CONSTRAINT IF EXISTS legislative_sessions_level_province_territory_parliament_nu_key;

-- Recreate it with NULLS NOT DISTINCT so (federal, NULL, 44, 1) matches
-- (federal, NULL, 44, 1) on future upserts.
ALTER TABLE legislative_sessions
    ADD CONSTRAINT legislative_sessions_level_province_territory_parliament_nu_key
    UNIQUE NULLS NOT DISTINCT (level, province_territory, parliament_number, session_number);

COMMIT;
