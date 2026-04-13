-- ═══════════════════════════════════════════════════════════════════════════
-- Canadian Political Data — seed data for referendum organizations
-- ═══════════════════════════════════════════════════════════════════════════
-- Idempotent: uses ON CONFLICT so it's safe to run multiple times.
-- Runs automatically after init.sql (mounted into /docker-entrypoint-initdb.d).
-- ═══════════════════════════════════════════════════════════════════════════

BEGIN;

-- ── LEAVE SIDE ──────────────────────────────────────────────────────────
INSERT INTO organizations (slug, name, type, side, description, key_people, province_territory)
VALUES
('alberta-prosperity-project', 'Alberta Prosperity Project', 'referendum_leave', 'leave',
 'Primary Alberta separatist organization. Filed original CIP application for independence referendum. Co-founded by Dennis Modry, CEO Mitch Sylvestre, legal counsel Jeffrey Rath.',
 '[{"name":"Mitch Sylvestre","role":"CEO"},{"name":"Dennis Modry","role":"Co-founder"},{"name":"Jeffrey Rath","role":"Legal Counsel"}]'::jsonb,
 'AB'),
('stay-free-alberta', 'Stay Free Alberta', 'referendum_leave', 'leave',
 'Rebranded petition vehicle for APP after Bill 14. Runs active signature collection campaign for independence referendum question. Same leadership as APP.',
 '[{"name":"Mitch Sylvestre","role":"Petition figurehead"},{"name":"Jeffrey Rath","role":"Legal Counsel"}]'::jsonb,
 'AB'),
('republican-party-of-alberta', 'Republican Party of Alberta', 'political_party', 'leave',
 'Alberta separatist political party. Polled 0.67–17.66% in 2025 by-elections.',
 '[]'::jsonb, 'AB'),

-- ── STAY SIDE ───────────────────────────────────────────────────────────
('forever-canadian', 'Forever Canadian / Alberta Forever Canada', 'referendum_stay', 'stay',
 'Anti-separatist citizen initiative led by Thomas Lukaszuk (former PC deputy premier). 404,293 verified signatures. Certified by Elections Alberta Dec 2025. Non-partisan: backed by Ed Stelmach (PC), Ray Martin (NDP), Ian McClelland (Reform).',
 '[{"name":"Thomas Lukaszuk","role":"Organizer"},{"name":"Ed Stelmach","role":"Supporter (former PC Premier)"},{"name":"Ray Martin","role":"Supporter (former NDP leader)"},{"name":"Ian McClelland","role":"Supporter (former Reform MP)"}]'::jsonb,
 'AB'),

-- ── PROVINCIAL POLITICAL PARTIES ───────────────────────────────────────
('ucp', 'United Conservative Party (UCP)', 'political_party', 'neutral',
 'Alberta''s governing party. Architect of Bill 14 (lowered referendum thresholds) and the nine official referendum questions for Oct 19. Led by Premier Danielle Smith.',
 '[{"name":"Danielle Smith","role":"Leader / Premier"}]'::jsonb,
 'AB'),
('alberta-ndp', 'Alberta NDP', 'political_party', 'stay',
 'Official opposition in Alberta. 93% of NDP voters oppose separation per Angus Reid polling.',
 '[]'::jsonb, 'AB'),

-- ── FEDERAL POLITICAL PARTIES ──────────────────────────────────────────
('liberal-party', 'Liberal Party of Canada', 'political_party', 'neutral',
 'Federal Liberal Party of Canada.', '[]'::jsonb, NULL),
('conservative-party', 'Conservative Party of Canada', 'political_party', 'neutral',
 'Federal Conservative Party of Canada.', '[]'::jsonb, NULL),
('ndp-federal', 'New Democratic Party (Federal)', 'political_party', 'neutral',
 'Federal New Democratic Party.', '[]'::jsonb, NULL),
('bloc-quebecois', 'Bloc Québécois', 'political_party', 'neutral',
 'Federal Bloc Québécois.', '[]'::jsonb, 'QC'),
('green-party', 'Green Party of Canada', 'political_party', 'neutral',
 'Federal Green Party of Canada.', '[]'::jsonb, NULL),
('peoples-party', 'People''s Party of Canada', 'political_party', 'neutral',
 'Federal People''s Party of Canada.', '[]'::jsonb, NULL),

-- ── GOVERNMENT / ELECTIONS ─────────────────────────────────────────────
('elections-alberta', 'Elections Alberta', 'government_body', 'neutral',
 'Official body administering the October 19, 2026 referendum.',
 '[]'::jsonb, 'AB'),
('elections-canada', 'Elections Canada', 'government_body', 'neutral',
 'Federal election authority.', '[]'::jsonb, NULL),
('alberta-government', 'Government of Alberta', 'government_body', 'neutral',
 'Official provincial government of Alberta.', '[]'::jsonb, 'AB'),

-- ── INDIGENOUS RIGHTS ──────────────────────────────────────────────────
('treaty-6-confederacy', 'Confederacy of Treaty No. 6 First Nations', 'indigenous_rights', 'stay',
 'Represents 16 First Nations in Alberta. Granted intervenor status in court challenge. Opposes separation as violation of Treaty rights and Section 35.',
 '[]'::jsonb, 'AB'),
('treaty-7-nations', 'Treaty 7 First Nations', 'indigenous_rights', 'stay',
 'Treaty 7 First Nations of southern Alberta. Allied with Treaty 6 against separation.',
 '[]'::jsonb, 'AB'),
('treaty-8-first-nations', 'Treaty 8 First Nations of Alberta', 'indigenous_rights', 'stay',
 'Treaty 8 First Nations of northern Alberta.', '[]'::jsonb, 'AB'),

-- ── MEDIA / WATCHDOGS ──────────────────────────────────────────────────
('pressprogress', 'PressProgress', 'media', 'neutral',
 'Investigative journalism. Reporting on APP/US government connections.',
 '[]'::jsonb, NULL),

-- ── ADJACENT / CONTEXT ─────────────────────────────────────────────────
('parti-quebecois', 'Parti Québécois', 'political_party', 'neutral',
 'Quebec separatist party. Paul St-Pierre Plamondon met with APP leaders, expressed support.',
 '[{"name":"Paul St-Pierre Plamondon","role":"Leader"}]'::jsonb, 'QC')
ON CONFLICT (slug) DO UPDATE
SET name = EXCLUDED.name,
    type = EXCLUDED.type,
    side = EXCLUDED.side,
    description = EXCLUDED.description,
    key_people = EXCLUDED.key_people,
    province_territory = EXCLUDED.province_territory,
    updated_at = now();

-- ── WEBSITES FOR ORGANIZATIONS ─────────────────────────────────────────
INSERT INTO websites (owner_type, owner_id, url, label)
SELECT 'organization', o.id, v.url, v.label FROM organizations o
JOIN (VALUES
    ('alberta-prosperity-project', 'https://albertaprosperityproject.com/',            'primary'),
    ('alberta-prosperity-project', 'https://nb.albertaprosperity.com/',                'pledge'),
    ('stay-free-alberta',          'https://stayfreealberta.com/',                     'primary'),
    ('stay-free-alberta',          'https://stayfreealberta.com/sign/',                'petition'),
    ('forever-canadian',           'https://www.forever-canadian.ca/en',               'primary'),
    ('ucp',                        'https://www.unitedconservative.ca/',               'party'),
    ('alberta-ndp',                'https://www.albertandp.ca/',                       'party'),
    ('liberal-party',              'https://liberal.ca/',                              'party'),
    ('conservative-party',         'https://www.conservative.ca/',                     'party'),
    ('ndp-federal',                'https://www.ndp.ca/',                              'party'),
    ('bloc-quebecois',             'https://www.blocquebecois.org/',                   'party'),
    ('green-party',                'https://www.greenparty.ca/',                       'party'),
    ('peoples-party',              'https://www.peoplespartyofcanada.ca/',             'party'),
    ('elections-alberta',          'https://www.elections.ab.ca/',                     'primary'),
    ('elections-alberta',          'https://www.elections.ab.ca/elections/referendum/','referendum'),
    ('elections-canada',           'https://www.elections.ca/',                        'primary'),
    ('alberta-government',         'https://www.alberta.ca/',                          'primary'),
    ('pressprogress',              'https://pressprogress.ca/',                        'primary'),
    ('parti-quebecois',            'https://pq.org/',                                  'party')
) AS v(slug, url, label) ON v.slug = o.slug
ON CONFLICT (owner_type, owner_id, url) DO NOTHING;

COMMIT;
