-- Add provenance + confidence columns to politician_socials so the
-- audit / backfill pipeline can distinguish upstream-feed rows from
-- heuristic probe hits and LLM-agent suggestions.
--
-- Tier-1 / upstream rows (Wikidata, OpenParliament, personal-site harvest,
-- muni scrape, HTML regex) get confidence = 1.0 and source = the feed name.
-- Tier-2 (pattern probe) writes confidence in [0.4, 1.0], flagging rows
-- below 0.7. Tier-3 (Sonnet agent) writes confidence in [0.6, 1.0],
-- flagging rows below 0.85.
--
-- flagged_low_confidence drives the /admin/socials/review queue — rows
-- stay queryable via the public API (they're still real handles; we
-- just want a human to spot-check them).

alter table politician_socials
    add column if not exists source                 text,
    add column if not exists confidence             numeric(4,3),
    add column if not exists evidence_url           text,
    add column if not exists flagged_low_confidence boolean not null default false,
    add column if not exists discovered_at          timestamptz;

-- Backfill: every row that exists today came from an upstream feed
-- (Phase-5 normalize_socials, enrich-*, harvest-personal-socials).
update politician_socials
   set source     = 'legacy',
       confidence = 1.000
 where source is null;

-- Partial index for the admin review queue — only flagged rows matter.
create index if not exists idx_politician_socials_flagged
    on politician_socials (flagged_low_confidence, platform)
 where flagged_low_confidence = true;

-- And a source breakdown index for audit reports.
create index if not exists idx_politician_socials_source
    on politician_socials (source)
 where source is not null;

comment on column politician_socials.source is
    'Origin of this row: legacy | wikidata | openparliament | masto_host | personal_site | muni_scrape | html_regex | pattern_probe | agent_sonnet | admin_manual';
comment on column politician_socials.confidence is
    'Probability handle belongs to this politician. >=0.85 auto-live, 0.60-0.85 flagged for agent-sourced rows; >=0.70 auto-live, 0.40-0.70 flagged for probe-sourced rows.';
comment on column politician_socials.evidence_url is
    'URL the discovery process verified against (e.g., the Wikipedia article, the bsky profile, the og:title page). Null for upstream feeds where the feed itself is the evidence.';
comment on column politician_socials.flagged_low_confidence is
    'True when the discovery confidence was below the platform-specific promotion threshold. Rows still live in the public response; UI shows a small caveat.';
comment on column politician_socials.discovered_at is
    'When this row was first inserted. Null for legacy rows backfilled by migration 0026.';
