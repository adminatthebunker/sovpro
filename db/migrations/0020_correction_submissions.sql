-- Corrections inbox.
--
-- Any user can flag a speech / bill / politician / vote row as wrong
-- or incomplete. Rows land here via:
--   • a "flag this" UI button on the frontend → POST /api/v1/corrections
--   • IMAP poll of a public corrections mailbox (e.g.
--     corrections@thebunkerops.ca) → auto-created rows with
--     submitter_email populated from the From: header
--
-- V1 review is manual via psql. A proper admin UI comes after the
-- inbox proves worth building one.
--
-- No auth, no user accounts. Anyone with an email can write. Spam
-- filtering is the reviewer's problem in phase 1.

CREATE TABLE IF NOT EXISTS correction_submissions (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- What is being corrected. subject_id may be NULL for general
    -- feedback ("your politicians page is missing half of PEI").
    subject_type     TEXT NOT NULL CHECK (subject_type IN ('speech','bill','politician','vote','organization','general')),
    subject_id       UUID,

    -- Who submitted. Both optional; anonymous submissions allowed.
    submitter_email  TEXT,
    submitter_name   TEXT,

    -- What's wrong + suggested fix.
    issue            TEXT NOT NULL,
    proposed_fix     TEXT,
    evidence_url     TEXT,            -- link to upstream source backing the claim

    -- Review workflow.
    status           TEXT NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending','triaged','applied','rejected','duplicate','spam')),
    reviewer_notes   TEXT,
    reviewed_by      TEXT,            -- free-text reviewer identifier (admin email)

    -- Ingest source.
    source           TEXT NOT NULL DEFAULT 'web'
                        CHECK (source IN ('web','email','api')),
    raw              JSONB NOT NULL DEFAULT '{}'::jsonb,  -- raw POST body / email headers for audit

    received_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    resolved_at      TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_corrections_status   ON correction_submissions (status, received_at DESC);
CREATE INDEX IF NOT EXISTS idx_corrections_subject  ON correction_submissions (subject_type, subject_id);
CREATE INDEX IF NOT EXISTS idx_corrections_email    ON correction_submissions (lower(submitter_email)) WHERE submitter_email IS NOT NULL;
