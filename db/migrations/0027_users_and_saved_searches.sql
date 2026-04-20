-- User accounts phase 1: passwordless magic-link auth + saved searches.
--
-- Three new tables plus one column hook on correction_submissions.
--
-- `users` holds verified end-user identities. No password_hash — phase 1
-- auth is magic-link only. email is the stable identity key (see
-- docs/plans: an IdP swap later would add a nullable external_id column,
-- not rewrite the row set). CITEXT for case-insensitive uniqueness.
--
-- `login_tokens` is the one-shot magic-link nonce store. We store the
-- SHA-256 of the URL nonce in token_hash, never the plaintext — a DB
-- leak must not leak working links. High-entropy nonce + 15-minute TTL
-- makes bcrypt unnecessary.
--
-- `saved_searches` persists the same 12-field filter payload the
-- /search/speeches endpoint accepts. query_embedding caches the Qwen3
-- vector at save time so alert ticks don't re-call TEI. last_checked_at
-- is the watermark the alerts worker uses to avoid re-notifying on old
-- matches.
--
-- correction_submissions.user_id is a schema hook for phase 1.5
-- "corrections with credit" — public submissions keep working unchanged
-- when user_id is NULL.

CREATE EXTENSION IF NOT EXISTS citext;

CREATE TABLE IF NOT EXISTS users (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email         CITEXT NOT NULL UNIQUE,
    display_name  TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_login_at TIMESTAMPTZ
);

CREATE TRIGGER trg_users_touch BEFORE UPDATE ON users
    FOR EACH ROW EXECUTE FUNCTION touch_updated_at();

CREATE TABLE IF NOT EXISTS login_tokens (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email        CITEXT NOT NULL,
    token_hash   BYTEA NOT NULL UNIQUE,
    expires_at   TIMESTAMPTZ NOT NULL,
    consumed_at  TIMESTAMPTZ,
    requested_ip INET,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_login_tokens_email
    ON login_tokens (email, created_at DESC);

-- Partial index supports the cleanup + redeem paths (unconsumed rows
-- only). Consumed rows are retained briefly for audit then purged.
CREATE INDEX IF NOT EXISTS idx_login_tokens_expiry
    ON login_tokens (expires_at)
    WHERE consumed_at IS NULL;

CREATE TABLE IF NOT EXISTS saved_searches (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id           UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name              TEXT NOT NULL,
    filter_payload    JSONB NOT NULL,
    query_embedding   VECTOR(1024),
    alert_cadence     TEXT NOT NULL DEFAULT 'none'
                          CHECK (alert_cadence IN ('none','daily','weekly')),
    last_checked_at   TIMESTAMPTZ,
    last_notified_at  TIMESTAMPTZ,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TRIGGER trg_saved_searches_touch BEFORE UPDATE ON saved_searches
    FOR EACH ROW EXECUTE FUNCTION touch_updated_at();

CREATE INDEX IF NOT EXISTS idx_saved_searches_user
    ON saved_searches (user_id, created_at DESC);

-- Alerts worker polls this: partial index keeps the hot path tight.
CREATE INDEX IF NOT EXISTS idx_saved_searches_alerts_due
    ON saved_searches (alert_cadence, last_checked_at)
    WHERE alert_cadence <> 'none';

-- Phase 1.5 hook. Nullable; anonymous submissions continue to work.
ALTER TABLE correction_submissions
    ADD COLUMN IF NOT EXISTS user_id UUID
        REFERENCES users(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_correction_submissions_user
    ON correction_submissions (user_id)
    WHERE user_id IS NOT NULL;
