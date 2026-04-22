-- Collapse the admin trust boundary into the user-session flow.
--
-- Before this migration we ran two parallel auth systems: a shared
-- ADMIN_TOKEN bearer (stored in browser localStorage) gating
-- /api/v1/admin/*, and the user-session cookie flow gating /me/*.
-- The security review on 2026-04-20 identified localStorage-held admin
-- tokens as the amplifier on a stored-XSS in /admin/corrections, and
-- more broadly two trust boundaries double the attack surface for no
-- real gain at our scale.
--
-- After this migration: admin access is "a user with is_admin=true".
-- Bearer-token middleware is deleted (see the Node-side changes).
-- Self-promotion via the HTTP surface is impossible — is_admin is
-- flipped only in psql. This file also flips the seed operator so
-- there's one admin row from the moment the migration lands.

ALTER TABLE users
    ADD COLUMN IF NOT EXISTS is_admin BOOLEAN NOT NULL DEFAULT false;

-- Partial index for the (rare) "who are the admins" lookup. Keeps
-- the main users table unchanged for non-admin queries.
CREATE INDEX IF NOT EXISTS idx_users_is_admin
    ON users (id)
    WHERE is_admin = true;

-- Seed the operator. Idempotent: no-op if the user row doesn't exist
-- yet (first boot), no-op on repeat runs. If the email ever changes,
-- flip by hand.
UPDATE users
   SET is_admin = true
 WHERE email = 'admin@thebunkerops.ca'
   AND is_admin = false;
