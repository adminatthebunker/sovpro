-- Manitoba's Legislative Assembly identifies each MLA via a surname
-- slug in the member profile URL:
--
--     /legislature/members/info/{surname}.html
--
-- Unlike AB/BC/QC, MB does not expose a stable numeric ID. The surname
-- is the canonical key used across the roster index, MLA profile
-- pages, and our internal lookup. Once persisted here, sponsor
-- resolution on manitoba-bills + speaker resolution on hansard-mb are
-- both exact FK lookups — no name-fuzz unless the upstream data
-- drops slug hints.
--
-- Parallel to politicians.{nslegislature_slug (0008), ola_slug (0010),
-- lims_member_id (0011), qc_assnat_id (0012), ab_assembly_mid (0013)}.
-- One column per jurisdiction's identifier scheme. Partial index (not
-- unique) matches the existing ola_slug / ab_assembly_mid pattern —
-- two MLAs with the same surname are rare but possible and we handle
-- disambiguation at ingest time (MB appends a numeric suffix on the
-- upstream profile URL when it happens).

ALTER TABLE politicians
  ADD COLUMN IF NOT EXISTS mb_assembly_slug TEXT;

CREATE INDEX IF NOT EXISTS idx_politicians_mb_assembly_slug
    ON politicians (mb_assembly_slug)
    WHERE mb_assembly_slug IS NOT NULL;
