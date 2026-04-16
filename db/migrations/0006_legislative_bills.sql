-- Legislative activity — layer 1: sessions + bills.
--
-- First piece of the provincial legislative-activity pipeline (see
-- docs/plans/provincial-legislature-research.md). Starts with Nova Scotia,
-- whose bills are available via the Socrata API at data.novascotia.ca
-- (dataset iz5x-dzyf) — the lowest-friction ingestion path in the country.
--
-- Schema is province-agnostic from day one: every row carries
-- (level, province_territory) so federal bills and other provinces slot
-- in without migration. Natural key for a bill is
-- (session_id, bill_number) — stable across ingestions.
--
-- Hansard, votes, and committee-activity tables are deliberately deferred
-- to their own migration — we want to see real NS bill data before
-- committing to the shape of the downstream tables.

-- ─────────────────────────────────────────────────────────────────────
-- legislative_sessions
--   One row per (level, province_territory, parliament_number,
--   session_number). A "Parliament" in federal parlance = a "General
--   Assembly" in Nova Scotia = a "Legislature" in other provinces.
--   We use the federal term internally to keep column names short; the
--   public API will render jurisdiction-appropriate labels.
-- ─────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS legislative_sessions (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    level              TEXT NOT NULL,              -- 'federal' | 'provincial'
    province_territory TEXT,                       -- NULL for federal, else 'NS', 'ON', ...
    parliament_number  INTEGER NOT NULL,
    session_number     INTEGER NOT NULL,
    name               TEXT,                       -- optional human label, e.g. "65th General Assembly, 1st Session"
    start_date         DATE,
    end_date           DATE,
    source_system      TEXT,                       -- e.g. 'socrata-ns'
    source_url         TEXT,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (level, province_territory, parliament_number, session_number)
);

CREATE INDEX IF NOT EXISTS idx_sessions_level_prov
    ON legislative_sessions (level, province_territory);

-- ─────────────────────────────────────────────────────────────────────
-- bills
--   Natural key: (session_id, bill_number). source_id is the stable
--   upstream identifier (e.g. 'socrata-ns:assembly-65-session-1:bill-127')
--   and is also globally unique — the ingester uses it for idempotent
--   upserts.
-- ─────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS bills (
    id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id            UUID NOT NULL REFERENCES legislative_sessions(id) ON DELETE CASCADE,
    level                 TEXT NOT NULL,
    province_territory    TEXT,
    bill_number           TEXT NOT NULL,
    title                 TEXT NOT NULL,
    short_title           TEXT,
    bill_type             TEXT,                       -- 'government' | 'private_member' | 'private' | NULL if unknown
    status                TEXT,                       -- free-text status label as reported upstream ("Royal Assent", "Second Reading", ...)
    status_changed_at     TIMESTAMPTZ,
    introduced_date       DATE,
    source_id             TEXT NOT NULL UNIQUE,
    source_system         TEXT NOT NULL,              -- 'socrata-ns', 'ola-on-scrape', ...
    source_url            TEXT,
    raw                   JSONB NOT NULL DEFAULT '{}'::jsonb,
    first_seen_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_fetched_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (session_id, bill_number)
);

CREATE INDEX IF NOT EXISTS idx_bills_level_prov            ON bills (level, province_territory);
CREATE INDEX IF NOT EXISTS idx_bills_session               ON bills (session_id);
CREATE INDEX IF NOT EXISTS idx_bills_status_changed        ON bills (status_changed_at DESC NULLS LAST);

-- ─────────────────────────────────────────────────────────────────────
-- bill_events — append-only stage transitions (first reading, second
-- reading, royal assent, ...). For NS we can only synthesize the current
-- stage from Socrata; full history comes later when we scrape per-bill
-- HTML pages. Rows here are idempotent via (bill_id, stage, event_date).
-- ─────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS bill_events (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    bill_id     UUID NOT NULL REFERENCES bills(id) ON DELETE CASCADE,
    stage       TEXT NOT NULL,                    -- 'first_reading', 'second_reading', 'committee', 'third_reading', 'royal_assent', 'introduced', 'withdrawn', ...
    stage_label TEXT,                             -- verbatim upstream label if different
    event_date  DATE,
    source_url  TEXT,
    raw         JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (bill_id, stage, event_date)
);

CREATE INDEX IF NOT EXISTS idx_bill_events_bill ON bill_events (bill_id, event_date DESC);

-- ─────────────────────────────────────────────────────────────────────
-- bill_sponsors — many-to-many politicians ↔ bills.
--
-- Two-stage pattern, matching the codebase's federal convention
-- (resolve_openparliament.py): the ingester stores a text name in
-- `sponsor_name_raw` and leaves `politician_id` NULL. A separate resolver
-- pass fuzzy-matches names to politicians.id. Keeps ingestion robust to
-- sponsors we haven't ingested yet (e.g. pre-2006 MLAs).
-- ─────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS bill_sponsors (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    bill_id           UUID NOT NULL REFERENCES bills(id) ON DELETE CASCADE,
    politician_id     UUID REFERENCES politicians(id) ON DELETE SET NULL,
    sponsor_name_raw  TEXT,
    role              TEXT NOT NULL DEFAULT 'sponsor',  -- 'sponsor' | 'co_sponsor'
    ordering          INTEGER NOT NULL DEFAULT 0,       -- preserve upstream order when multiple
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_bill_sponsors_bill       ON bill_sponsors (bill_id);
CREATE INDEX IF NOT EXISTS idx_bill_sponsors_pol        ON bill_sponsors (politician_id) WHERE politician_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_bill_sponsors_unresolved ON bill_sponsors (bill_id) WHERE politician_id IS NULL;
