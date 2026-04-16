-- Semantic layer — layer 4: votes + vote_positions.
--
-- ⚠️  TENTATIVE SCHEMA. Apply only after we have real data for
--     federal + at least one partisan province. NT/NU consensus
--     governments will likely force revisions — there is no party
--     line, and decisions often happen by voice/consensus rather
--     than roll-call. Leaving this file in place so the schema is
--     visible and reviewable, but do not run it until phase 4 of
--     docs/plans/semantic-layer.md.
--
-- Model: one `votes` row per division / recorded event, many
-- `vote_positions` rows (one per politician who voted).
-- vote_type accommodates:
--   'division'     — recorded roll-call, every member's position known
--   'voice'        — "on division", no individual records
--   'acclamation'  — passed without objection
--   'consensus'    — NT/NU style, no party-line polling
--
-- result = 'passed' | 'defeated' | 'tied' | 'withdrawn' | 'deferred'
-- position = 'yea' | 'nay' | 'abstain' | 'paired' | 'absent'

CREATE TABLE IF NOT EXISTS votes (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id          UUID NOT NULL REFERENCES legislative_sessions(id) ON DELETE CASCADE,
    level               TEXT NOT NULL CHECK (level IN ('federal','provincial','municipal')),
    province_territory  TEXT,

    bill_id             UUID REFERENCES bills(id) ON DELETE SET NULL,
    speech_id           UUID REFERENCES speeches(id) ON DELETE SET NULL,  -- link to the Hansard moment if ingested

    vote_type           TEXT NOT NULL CHECK (vote_type IN ('division','voice','acclamation','consensus')),
    occurred_at         TIMESTAMPTZ,
    result              TEXT,                  -- 'passed' | 'defeated' | 'tied' | 'withdrawn' | 'deferred'
    ayes                INTEGER,
    nays                INTEGER,
    abstentions         INTEGER,
    motion_text         TEXT,                  -- the question put to the chamber

    source_system       TEXT NOT NULL,
    source_url          TEXT,
    raw                 JSONB NOT NULL DEFAULT '{}'::jsonb,

    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),

    UNIQUE (source_system, source_url)
);

CREATE INDEX IF NOT EXISTS idx_votes_session     ON votes (session_id);
CREATE INDEX IF NOT EXISTS idx_votes_bill        ON votes (bill_id) WHERE bill_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_votes_level_prov  ON votes (level, province_territory);
CREATE INDEX IF NOT EXISTS idx_votes_occurred_at ON votes (occurred_at DESC NULLS LAST);

-- ─────────────────────────────────────────────────────────────────────
-- vote_positions — one row per politician who participated.
--
-- For 'voice' / 'acclamation' / some 'consensus' votes this table may
-- be empty. The frontend should render "voted on division" rather than
-- assume an empty set means nobody voted.
--
-- politician_name_raw is kept even after FK resolution for audit
-- trail (upstream name spellings drift).
-- ─────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS vote_positions (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    vote_id                 UUID NOT NULL REFERENCES votes(id) ON DELETE CASCADE,
    politician_id           UUID REFERENCES politicians(id) ON DELETE SET NULL,
    politician_name_raw     TEXT NOT NULL,
    party_at_time           TEXT,
    constituency_at_time    TEXT,
    position                TEXT NOT NULL CHECK (position IN ('yea','nay','abstain','paired','absent')),
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (vote_id, politician_name_raw)
);

CREATE INDEX IF NOT EXISTS idx_vote_pos_vote       ON vote_positions (vote_id);
CREATE INDEX IF NOT EXISTS idx_vote_pos_pol        ON vote_positions (politician_id, position) WHERE politician_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_vote_pos_unresolved ON vote_positions (vote_id) WHERE politician_id IS NULL;
