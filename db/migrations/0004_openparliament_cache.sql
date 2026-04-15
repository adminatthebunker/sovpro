-- Progressive localization of federal MP detail from openparliament.ca.
--
-- When a user clicks on a federal MP's profile, the API fetches their detail
-- from api.openparliament.ca, caches it here, and serves future requests from
-- this table. This respects openparliament's rate limits (no published cap;
-- they 429 on bursts) and slowly builds a local copy of what we actually
-- surface — no batch backfill, no speculative prefetch.
--
-- openparliament keys everything on a URL slug (e.g. `justin-trudeau`). We
-- resolve slugs for our federal MPs via a separate scanner-side job
-- (services/scanner/src/resolve_openparliament.py) that name-matches our
-- politicians against their public list. This migration only sets up storage
-- — no data is written here.

ALTER TABLE politicians
  ADD COLUMN IF NOT EXISTS openparliament_slug TEXT;

-- Partial index: most politicians won't have a slug (non-federal), so this
-- stays small and fast for the common "look up by slug" path.
CREATE INDEX IF NOT EXISTS idx_politicians_opslug
  ON politicians (openparliament_slug) WHERE openparliament_slug IS NOT NULL;

CREATE TABLE IF NOT EXISTS politician_openparliament_cache (
  politician_id UUID PRIMARY KEY REFERENCES politicians(id) ON DELETE CASCADE,
  slug          TEXT NOT NULL,
  data          JSONB NOT NULL,
  fetched_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  expires_at    TIMESTAMPTZ NOT NULL DEFAULT now() + interval '30 days',
  -- Last-error fields let us serve stale-but-available cache on outbound
  -- failures without losing the failure trail. Both NULL = last fetch was OK.
  last_error    TEXT,
  last_error_at TIMESTAMPTZ
);

-- For expiry sweeps / observability queries ("how many need a refresh?").
CREATE INDEX IF NOT EXISTS idx_opcache_expires_at
  ON politician_openparliament_cache (expires_at);
