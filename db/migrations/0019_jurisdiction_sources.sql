-- Coverage dashboard — jurisdiction_sources.
--
-- One row per jurisdiction we do (or don't) cover. Serves triple duty:
--
--   1. Public coverage page on the frontend — "what does SovereignWatch
--      have for each province, and what's missing?" Honest coverage
--      reporting is a trust signal.
--   2. Ingest-pipeline status — each ingester updates its row after a
--      successful run, so `last_verified_at` is the freshness stamp
--      the UI surfaces.
--   3. Blocked-jurisdiction tracker — Yukon (Cloudflare Bot Management)
--      and PEI (Radware ShieldSquare) go here with blockers = 'waf',
--      and the frontend renders an explanatory banner rather than
--      hiding them.
--
-- Difficulty ratings match docs/plans/provincial-legislature-research.md:
--   1 = documented API
--   2 = undocumented but structured (JSON/XML/Socrata)
--   3 = predictable HTML scrape
--   4 = messy HTML / PDF
--   5 = blocked
--
-- Source-of-truth for this table is the research doc above; this table
-- is the machine-readable mirror.

CREATE TABLE IF NOT EXISTS jurisdiction_sources (
    jurisdiction           TEXT PRIMARY KEY,       -- 'federal' | 'AB' | 'BC' | 'ON' | 'QC' | 'NS' | 'MB' | 'SK' | 'NB' | 'NL' | 'PE' | 'YT' | 'NT' | 'NU'
    legislature_name       TEXT NOT NULL,
    seats                  INTEGER,

    -- Per-layer coverage status. 'live' | 'partial' | 'blocked' | 'none'
    bills_status           TEXT NOT NULL DEFAULT 'none' CHECK (bills_status      IN ('live','partial','blocked','none')),
    hansard_status         TEXT NOT NULL DEFAULT 'none' CHECK (hansard_status    IN ('live','partial','blocked','none')),
    votes_status           TEXT NOT NULL DEFAULT 'none' CHECK (votes_status      IN ('live','partial','blocked','none')),
    committees_status      TEXT NOT NULL DEFAULT 'none' CHECK (committees_status IN ('live','partial','blocked','none')),

    -- Per-layer difficulty 1..5. NULL if not yet researched.
    bills_difficulty       SMALLINT CHECK (bills_difficulty      BETWEEN 1 AND 5),
    hansard_difficulty     SMALLINT CHECK (hansard_difficulty    BETWEEN 1 AND 5),
    votes_difficulty       SMALLINT CHECK (votes_difficulty      BETWEEN 1 AND 5),
    committees_difficulty  SMALLINT CHECK (committees_difficulty BETWEEN 1 AND 5),

    blockers               TEXT,                   -- free-text: 'cloudflare-bot-mgmt', 'radware-shieldsquare', 'waf-budget', ...
    notes                  TEXT,                   -- free-text for UI display
    source_urls            JSONB NOT NULL DEFAULT '{}'::jsonb,  -- { "bills": "...", "hansard": "...", ... }

    -- Row counts — populated by an hourly refresh job, not by ingesters
    -- directly. Keeps this table cheap to update without race-y double
    -- writes.
    bills_count            INTEGER NOT NULL DEFAULT 0,
    speeches_count         INTEGER NOT NULL DEFAULT 0,
    votes_count            INTEGER NOT NULL DEFAULT 0,
    politicians_count      INTEGER NOT NULL DEFAULT 0,

    last_verified_at       TIMESTAMPTZ,            -- last successful ingest run
    updated_at             TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TRIGGER trg_jurisdiction_sources_touch BEFORE UPDATE ON jurisdiction_sources
    FOR EACH ROW EXECUTE FUNCTION touch_updated_at();

-- Seed with current coverage snapshot (2026-04-16). Update as pipelines
-- progress. Row counts here are starting values; the refresh job
-- overwrites them on next run.
--
-- As of 2026-04-16: 9/14 jurisdictions have a live bills pipeline —
-- NS (historical), AB, BC, ON, QC, NB, NL, NT, NU. Federal bills are
-- partial (openparliament mirror). Remaining 4 split into PDF-blocked
-- (MB + SK) and WAF-blocked (PE + YT) pairs. No Hansard ingestion
-- anywhere yet; votes/committees shipped only for AB.
INSERT INTO jurisdiction_sources (
    jurisdiction, legislature_name, seats,
    bills_status, bills_difficulty,
    hansard_status, hansard_difficulty,
    votes_status, votes_difficulty,
    committees_status, committees_difficulty,
    blockers, notes
) VALUES
    ('federal', 'Parliament of Canada',                              440, 'partial', 1, 'partial', 1, 'partial', 1, 'none',    2, NULL,                      'Mirrored from openparliament.ca; Hansard cache sparse as of 2026-04-16'),
    ('AB',      'Legislative Assembly of Alberta',                    87, 'live',    2, 'none',    4, 'none',    4, 'live',    2, NULL,                      'Legislature 31 S1+S2 live (114 bills); Hansard is PDF-only'),
    ('BC',      'Legislative Assembly of British Columbia',           93, 'live',    2, 'none',    3, 'none',    3, 'none',    3, NULL,                      'LIMS PDMS JSON endpoint; member-data GraphQL available'),
    ('ON',      'Legislative Assembly of Ontario',                   124, 'live',    3, 'none',    3, 'none',    3, 'none',    3, NULL,                      'P44-S1 live (102 bills); Drupal ?_format=json fallback'),
    ('QC',      'National Assembly of Quebec',                       125, 'live',    2, 'none',    3, 'none',    4, 'none',    4, NULL,                      'donneesquebec CSV + RSS; bilingual; 102 bills live'),
    ('NS',      'Nova Scotia House of Assembly',                      55, 'partial', 2, 'none',    3, 'none',    3, 'none',    2, 'waf-budget',              'Socrata bills live (3,522 historical); per-bill HTML blocked by WAF'),
    ('NB',      'Legislative Assembly of New Brunswick',              49, 'live',    3, 'none',    3, 'none',    4, 'none',    2, NULL,                      'legnb.ca two-step HTML pipeline; 33 bills live'),
    ('NL',      'Newfoundland and Labrador House of Assembly',        40, 'live',    3, 'none',    3, 'none',    3, 'none',    3, NULL,                      'Single-page progression table; 12 bills live; no sponsor data upstream'),
    ('NT',      'Legislative Assembly of the Northwest Territories',  19, 'live',    3, 'none',    2, 'none',    3, 'none',    3, 'consensus-govt',          'Drupal 9 list + detail; 20 bills live; consensus government, no sponsor concept'),
    ('NU',      'Legislative Assembly of Nunavut',                    22, 'live',    3, 'none',    2, 'none',    4, 'none',    3, 'consensus-govt',          'Drupal 9 view table; 4 bills live; consensus government; multilingual (EN/IU/IK/FR)'),
    ('MB',      'Legislative Assembly of Manitoba',                   57, 'none',    4, 'none',    3, 'none',    4, 'none',    2, 'pdf-only',                'PDF-only bills; deferred pending pdfplumber investment'),
    ('SK',      'Legislative Assembly of Saskatchewan',               61, 'none',    4, 'none',    2, 'none',    3, 'none',    2, 'pdf-only',                'PDF-only bills; indexed Hansard is a strength once PDF pipeline lands'),
    ('PE',      'Legislative Assembly of Prince Edward Island',       27, 'blocked', 5, 'none',    3, 'none',    3, 'none',    3, 'radware-shieldsquare',    'Requires browser automation (Radware ShieldSquare)'),
    ('YT',      'Yukon Legislative Assembly',                         21, 'blocked', 5, 'blocked', 5, 'blocked', 5, 'blocked', 5, 'cloudflare-bot-mgmt',     'Site returns 403 to non-browser requests; requires browser automation or alternative source')
ON CONFLICT (jurisdiction) DO NOTHING;
