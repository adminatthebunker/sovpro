-- Phase 1 of the dataset expansion plan (dapper-brewing-petal):
-- adds term history, politician-level change tracking, office locations,
-- normalized socials, and committee memberships. Idempotent; does NOT
-- modify the existing politicians.social_urls JSONB column (kept for
-- back-compat during migration to politician_socials).

-- 1. politician_terms -- append-only term history
CREATE TABLE IF NOT EXISTS politician_terms (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    politician_id UUID NOT NULL REFERENCES politicians(id) ON DELETE CASCADE,
    office TEXT NOT NULL,
    party TEXT,
    level TEXT NOT NULL,
    province_territory TEXT,
    constituency_id TEXT,
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    ended_at TIMESTAMPTZ,
    source TEXT,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_politician_terms_pol_started
    ON politician_terms (politician_id, started_at DESC);

CREATE INDEX IF NOT EXISTS idx_politician_terms_current
    ON politician_terms (politician_id)
    WHERE ended_at IS NULL;

-- 2. politician_changes -- mirror of scan_changes for politician-level deltas
CREATE TABLE IF NOT EXISTS politician_changes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    politician_id UUID NOT NULL REFERENCES politicians(id) ON DELETE CASCADE,
    change_type TEXT NOT NULL CHECK (change_type IN (
        'party_switch',
        'office_change',
        'retired',
        'newly_elected',
        'social_added',
        'social_removed',
        'social_dead',
        'constituency_change',
        'name_change'
    )),
    old_value JSONB,
    new_value JSONB,
    severity TEXT DEFAULT 'info',
    detected_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_politician_changes_pol_detected
    ON politician_changes (politician_id, detected_at DESC);

CREATE INDEX IF NOT EXISTS idx_politician_changes_detected
    ON politician_changes (detected_at DESC);

-- 3. politician_offices -- constituency/legislature/campaign office locations
CREATE TABLE IF NOT EXISTS politician_offices (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    politician_id UUID NOT NULL REFERENCES politicians(id) ON DELETE CASCADE,
    kind TEXT,
    address TEXT,
    city TEXT,
    province_territory TEXT,
    postal_code TEXT,
    phone TEXT,
    fax TEXT,
    email TEXT,
    hours TEXT,
    lat DOUBLE PRECISION,
    lon DOUBLE PRECISION,
    source TEXT,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_politician_offices_pol
    ON politician_offices (politician_id);

-- 4. politician_socials -- normalized social handles + liveness
CREATE TABLE IF NOT EXISTS politician_socials (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    politician_id UUID NOT NULL REFERENCES politicians(id) ON DELETE CASCADE,
    platform TEXT NOT NULL CHECK (platform IN (
        'twitter',
        'facebook',
        'instagram',
        'youtube',
        'tiktok',
        'linkedin',
        'mastodon',
        'bluesky',
        'threads',
        'other'
    )),
    handle TEXT,
    url TEXT NOT NULL,
    last_verified_at TIMESTAMPTZ,
    is_live BOOLEAN,
    follower_count INT,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

-- Unique per (politician, platform, lowercased handle). Expression index
-- required since Postgres unique constraints can't reference expressions.
CREATE UNIQUE INDEX IF NOT EXISTS uq_politician_socials_pol_platform_handle
    ON politician_socials (politician_id, platform, LOWER(handle));

-- Liveness worker queue: NULLs first so never-verified rows are processed
-- ahead of already-verified ones.
CREATE INDEX IF NOT EXISTS idx_politician_socials_verify_queue
    ON politician_socials (last_verified_at NULLS FIRST);

-- 5. politician_committees -- committee memberships
CREATE TABLE IF NOT EXISTS politician_committees (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    politician_id UUID NOT NULL REFERENCES politicians(id) ON DELETE CASCADE,
    committee_name TEXT NOT NULL,
    role TEXT,
    level TEXT,
    started_at TIMESTAMPTZ,
    ended_at TIMESTAMPTZ,
    source TEXT,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_politician_committees_pol
    ON politician_committees (politician_id);

CREATE INDEX IF NOT EXISTS idx_politician_committees_name_level
    ON politician_committees (committee_name, level);
