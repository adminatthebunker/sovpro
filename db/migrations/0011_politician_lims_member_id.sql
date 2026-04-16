-- BC's LIMS system identifies each MLA with a stable integer ID
-- (e.g. 236 = "Honourable John Horgan"). The PDMS bills endpoint
-- returns `memberId` on every bill, which means sponsor→politician
-- resolution can be an exact integer FK lookup — no name-fuzz,
-- no slug matching.
--
-- Parallel to politicians.ola_slug (0010) and nslegislature_slug
-- (0008). One column per jurisdiction's identifier scheme is
-- intentional — each upstream has a different namespace, and
-- per-column indexes keep lookups O(log n) as the set grows.

ALTER TABLE politicians
  ADD COLUMN IF NOT EXISTS lims_member_id INTEGER;

CREATE INDEX IF NOT EXISTS idx_politicians_lims_member_id
    ON politicians (lims_member_id)
    WHERE lims_member_id IS NOT NULL;
