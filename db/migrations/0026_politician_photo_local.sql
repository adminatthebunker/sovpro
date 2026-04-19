-- Add local-mirror columns for politician portraits.
--
-- Context: today `politicians.photo_url` points at upstream URLs
-- (openparliament.ca, represent.opennorth.ca, sencanada.ca, provincial
-- legislature sites). The frontend dereferences those at render time,
-- so any upstream rate-limit, URL rewrite, or outage manifests as a
-- broken <img> on ~1,815 profiles. Infrastructure-sovereignty is one
-- of the project's core framings (see docs/goals.md + the tier system
-- in classify.py), so we mirror the bytes onto our own disk.
--
-- Storage pattern established by this migration:
--   - Bytes land in a named Docker volume `assets`, mounted RW into
--     the scanner and scanner-jobs containers and RO into nginx at
--     /var/www/assets. nginx serves them under /assets/*.
--   - `photo_path` is the volume-relative path (e.g.
--     'politicians/<uuid>.jpg'). API helper resolves it to /assets/...
--   - `photo_url` stays populated with the upstream URL for attribution
--     + re-fetch. Nothing in the UI dereferences it after cutover.
--   - `photo_bytes_hash` (sha256 hex) enables change detection so
--     re-runs are cheap: HEAD doesn't help with the CDNs we're against,
--     but a body-hash comparison still avoids rewriting unchanged files
--     and preserves filesystem atime for audit.
--
-- Rollback: drop the added columns. The files on the `assets` volume
-- are harmless without the DB pointer and can be left to bit-rot or
-- wiped with `docker volume rm sovpro_assets`.

BEGIN;

ALTER TABLE politicians
    ADD COLUMN photo_path        text,
    ADD COLUMN photo_bytes_hash  text,
    ADD COLUMN photo_fetched_at  timestamptz,
    ADD COLUMN photo_source_url  text;

COMMENT ON COLUMN politicians.photo_path IS
    'Path within the `assets` Docker volume, served at /assets/<path>. '
    'NULL means the local mirror has not run or fetch failed; API falls '
    'back to photo_url.';
COMMENT ON COLUMN politicians.photo_bytes_hash IS
    'sha256 hex of the fetched bytes. Used by the backfill command to '
    'skip rewrites when upstream content is unchanged.';
COMMENT ON COLUMN politicians.photo_fetched_at IS
    'Last successful download timestamp. Drives refresh cadence (re-'
    'fetch if older than 30 days in the backfill command).';
COMMENT ON COLUMN politicians.photo_source_url IS
    'The upstream URL the bytes came from. Typically equals photo_url '
    'at fetch time, but we persist separately in case photo_url gets '
    'rewritten to a /assets/ path by a future cleanup pass.';

COMMIT;
