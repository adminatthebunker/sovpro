-- Extend the openparliament cache with a per-politician activity feed
-- (speeches + sponsored bills). Stored alongside the detail blob in the
-- same row so a single politician lookup resolves everything.
--
-- Activity has a shorter TTL than the biographical detail (1 day vs 30 days)
-- because speeches accrue every session day, whereas a politician's riding/
-- party/bio changes on the order of months.
--
-- Votes are deliberately NOT cached here: openparliament's ballot endpoint
-- returns only the Yes/No outcome per ballot, requiring one extra fetch
-- per vote to get the description — a profile view would trigger 20+
-- outbound calls. If needed later, add it as a separate column with
-- server-side enrichment.

ALTER TABLE politician_openparliament_cache
  ADD COLUMN IF NOT EXISTS activity_data       JSONB,
  ADD COLUMN IF NOT EXISTS activity_fetched_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS activity_expires_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS activity_last_error    TEXT,
  ADD COLUMN IF NOT EXISTS activity_last_error_at TIMESTAMPTZ;
