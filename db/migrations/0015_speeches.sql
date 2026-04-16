-- Semantic layer — layer 1: speeches.
--
-- Every utterance on the floor or in committee by a named speaker. This is
-- the payload the single-search-box retrieves against; it is also the
-- structured feed the politician-detail page will render timeline-style.
--
-- Jurisdiction-agnostic from day one: (level, province_territory)
-- discriminators, same pattern as bills/legislative_sessions. Federal
-- openparliament.ca is phase-1 ingestion; provincial Hansards land
-- after.
--
-- Attribution columns (speaker_name_raw, party_at_time,
-- constituency_at_time, speaker_role) are all **as of spoken_at** — we
-- never back-fill them from politicians' current state. This lets
-- "what did Liberals say in 2015" queries work without replaying term
-- history every query.
--
-- A separate resolver pass fills politician_id, matching on the
-- jurisdiction slug/id columns (openparliament_slug, ola_slug,
-- lims_member_id, qc_assnat_id, ab_assembly_mid, nslegislature_slug).
-- Same two-stage pattern as bill_sponsors in 0006.
--
-- content_hash lets us dedup at ingest — carried-over speeches
-- (committee → Hansard, quoted-back excerpts) match via normalised-text
-- sha256 and we attach a new source_url instead of inserting a duplicate.

CREATE TABLE IF NOT EXISTS speeches (
    id                     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id             UUID NOT NULL REFERENCES legislative_sessions(id) ON DELETE CASCADE,
    politician_id          UUID REFERENCES politicians(id) ON DELETE SET NULL,

    level                  TEXT NOT NULL CHECK (level IN ('federal','provincial','municipal')),
    province_territory     TEXT,

    -- Attribution, captured at-time-of-speech. Do not backfill from
    -- politicians' current state — that loses the historical record.
    speaker_name_raw       TEXT NOT NULL,
    speaker_role           TEXT,                 -- e.g. "Minister of Finance", "Speaker", "Leader of the Opposition"
    party_at_time          TEXT,
    constituency_at_time   TEXT,
    confidence             REAL NOT NULL DEFAULT 1.0,  -- speaker-id confidence 0..1; surface in UI when < 1.0

    -- Content.
    speech_type            TEXT,                 -- 'floor' | 'committee' | 'question_period' | 'statement' | 'point_of_order' | ...
    spoken_at              TIMESTAMPTZ,          -- date+time speech was given
    sequence               INTEGER,              -- position within session-day; preserved verbatim from upstream
    language               TEXT NOT NULL,        -- ISO 639-1: 'en' | 'fr' | 'iu' | ...
    text                   TEXT NOT NULL,
    word_count             INTEGER,

    -- Provenance.
    source_system          TEXT NOT NULL,        -- 'openparliament' | 'hansard-ab-pdf' | 'hansard-on-html' | ...
    source_url             TEXT NOT NULL,
    source_anchor          TEXT,                 -- paragraph id / href fragment, so UI can deep-link
    raw                    JSONB NOT NULL DEFAULT '{}'::jsonb,
    raw_html               TEXT,                 -- upstream HTML / extracted PDF text, for re-parsing without re-fetching
    content_hash           TEXT NOT NULL,        -- sha256 of normalised text; dedup key

    created_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at             TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- Natural key: the same speech, fetched twice, must collapse. Three
    -- columns (source_system, source_url, sequence) uniquely identify
    -- an upstream record. NULLS NOT DISTINCT so sources without a
    -- sequence (old PDFs) still dedup on source_system + source_url.
    UNIQUE NULLS NOT DISTINCT (source_system, source_url, sequence)
);

CREATE INDEX IF NOT EXISTS idx_speeches_politician      ON speeches (politician_id, spoken_at DESC) WHERE politician_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_speeches_level_prov      ON speeches (level, province_territory);
CREATE INDEX IF NOT EXISTS idx_speeches_session         ON speeches (session_id);
CREATE INDEX IF NOT EXISTS idx_speeches_spoken_at       ON speeches (spoken_at DESC);
CREATE INDEX IF NOT EXISTS idx_speeches_content_hash    ON speeches (content_hash);
CREATE INDEX IF NOT EXISTS idx_speeches_unresolved      ON speeches (id) WHERE politician_id IS NULL;
CREATE INDEX IF NOT EXISTS idx_speeches_needs_embedding ON speeches (id) WHERE raw_html IS NULL;  -- negative space; re-parse candidates

-- touch-updated-at trigger for parity with politicians / bills / organizations.
CREATE TRIGGER trg_speeches_touch BEFORE UPDATE ON speeches
    FOR EACH ROW EXECUTE FUNCTION touch_updated_at();
