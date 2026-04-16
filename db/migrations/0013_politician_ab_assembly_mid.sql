-- Alberta's Legislative Assembly identifies each MLA with a zero-padded
-- 4-char text id in every profile URL:
--
--     /members/members-of-the-legislative-assembly/member-information?mid=0814
--
-- Bill sponsor links in the assembly-dashboard page point at the same
-- mid, so once we store it on politicians we can resolve sponsor →
-- politician as an exact FK lookup — same leverage as BC's
-- lims_member_id (0011) and QC's qc_assnat_id (0012), but the upstream
-- format is a zero-padded string rather than an integer. Keep it as
-- TEXT so lookups stay lexical with the site's URLs and we don't have
-- to re-pad on every join.
--
-- Parallel to politicians.{lims_member_id, qc_assnat_id} and the text
-- slug columns in 0008 (NS) and 0010 (ON). One column per jurisdiction's
-- identifier scheme keeps each index O(log n).

ALTER TABLE politicians
  ADD COLUMN IF NOT EXISTS ab_assembly_mid TEXT;

CREATE INDEX IF NOT EXISTS idx_politicians_ab_assembly_mid
    ON politicians (ab_assembly_mid)
    WHERE ab_assembly_mid IS NOT NULL;
