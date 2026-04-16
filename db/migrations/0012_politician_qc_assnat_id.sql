-- Quebec's Assemblée nationale embeds a stable integer MNA id in every
-- profile URL (e.g. /en/deputes/jolin-barrette-simon-15359/index.html
-- → 15359). Bill detail pages link sponsors via the same slug, so
-- once we store that id on politicians we can resolve sponsor →
-- politician as an exact integer FK lookup — no name-fuzz.
--
-- Parallel to politicians.lims_member_id (0011, BC) and the text slug
-- columns in 0008 (NS) and 0010 (ON). One column per jurisdiction's
-- identifier scheme keeps each index O(log n) and keeps the ambiguity
-- of "which 'Roberge' do you mean?" impossible for same-name MNAs.

ALTER TABLE politicians
  ADD COLUMN IF NOT EXISTS qc_assnat_id INTEGER;

CREATE INDEX IF NOT EXISTS idx_politicians_qc_assnat_id
    ON politicians (qc_assnat_id)
    WHERE qc_assnat_id IS NOT NULL;
