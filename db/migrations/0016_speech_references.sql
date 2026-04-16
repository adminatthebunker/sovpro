-- Semantic layer — layer 2: speech_references.
--
-- Cross-links from a speech to the bills / committees / acts / motions
-- it mentions. Populated at parse time when the source provides
-- explicit references (e.g. openparliament's speech JSON tags bill
-- numbers), and enrichable later via regex + entity-linking passes
-- over speeches.text.
--
-- Purpose: powers "every speech about Bill C-11" queries and the
-- bill-detail "who debated this" section without re-scanning full text.
--
-- Kept separate from speeches (rather than a jsonb array column) so we
-- can index on bill_id, index on committee_name, and delete/update
-- references without rewriting the parent speech row.

CREATE TABLE IF NOT EXISTS speech_references (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    speech_id      UUID NOT NULL REFERENCES speeches(id) ON DELETE CASCADE,
    ref_type       TEXT NOT NULL CHECK (ref_type IN ('bill','committee','act','motion','politician','organization')),

    -- At most one of the following is set per row, matching ref_type.
    bill_id        UUID REFERENCES bills(id) ON DELETE SET NULL,
    politician_id  UUID REFERENCES politicians(id) ON DELETE SET NULL,
    committee_name TEXT,
    act_citation   TEXT,            -- e.g. "Criminal Code, R.S.C. 1985, c. C-46, s. 163.1"
    motion_text    TEXT,

    -- Where the mention lives in the source speech.
    mention_text   TEXT,
    char_start     INTEGER,
    char_end       INTEGER,

    confidence     REAL NOT NULL DEFAULT 1.0,  -- link confidence 0..1; NER hits are < 1.0

    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_speech_refs_speech ON speech_references (speech_id);
CREATE INDEX IF NOT EXISTS idx_speech_refs_bill   ON speech_references (bill_id)       WHERE bill_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_speech_refs_pol    ON speech_references (politician_id) WHERE politician_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_speech_refs_cte    ON speech_references (committee_name) WHERE committee_name IS NOT NULL;
