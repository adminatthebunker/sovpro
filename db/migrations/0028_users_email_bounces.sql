-- Track hard email bounces at the user level so the alerts worker
-- stops hammering dead addresses.
--
-- Why a column and not a separate table: a bounced address is a
-- per-user state, not an event log. We only need "most recent hard
-- bounce" to decide whether to keep sending; a history table can come
-- later if we ever want to show the user "we disabled your alerts
-- because your mail server said X". Keep it simple until then.
--
-- We also null this out on /auth/verify (next magic-link redemption)
-- because successful delivery of the magic link is itself evidence
-- the inbox is alive again. That logic lives in the API route, not
-- the schema.

ALTER TABLE users
    ADD COLUMN IF NOT EXISTS email_bounced_at TIMESTAMPTZ;

-- Partial index keeps the alerts-due hot path fast: the WHERE filter
-- in process_due_searches() will short-circuit users whose address
-- bounces, and this index lets that filter stay index-only.
CREATE INDEX IF NOT EXISTS idx_users_email_bounced
    ON users (email_bounced_at)
    WHERE email_bounced_at IS NOT NULL;
