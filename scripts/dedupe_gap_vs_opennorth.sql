-- Dedupe: merge Team A gap-filler rows into Open North rows for overlapping politicians
-- Context: Team A created direct:* rows for BC/NB/NL/YT before Open North data arrived.
-- Later, `ingest-legislatures` added opennorth:* rows for the same politicians.
-- Strategy: keep Open North row (richer structured data), migrate child records, delete gap-filler.

BEGIN;

-- 1) Build pairings: gap-filler → Open North by normalized name + province
CREATE TEMP TABLE dup_pairs AS
SELECT
    gf.id   AS loser_id,
    on_.id  AS keeper_id,
    gf.name AS name,
    gf.province_territory AS province
FROM politicians gf
JOIN politicians on_
  ON on_.province_territory = gf.province_territory
 AND on_.level = gf.level
 AND on_.source_id LIKE 'opennorth:%'
 AND regexp_replace(lower(on_.name), '[^a-z0-9]+', ' ', 'g')
   = regexp_replace(lower(gf.name), '[^a-z0-9]+', ' ', 'g')
WHERE gf.source_id LIKE 'direct:%'
  AND gf.level = 'provincial';

SELECT province, COUNT(*) AS pairs FROM dup_pairs GROUP BY 1 ORDER BY 1;

-- 2) Migrate websites (keep unique URLs on keeper)
INSERT INTO websites (owner_type, owner_id, url, label, last_scanned_at, scan_failures)
SELECT w.owner_type, dp.keeper_id, w.url, w.label, w.last_scanned_at, w.scan_failures
FROM websites w
JOIN dup_pairs dp ON w.owner_type='politician' AND w.owner_id = dp.loser_id
ON CONFLICT (owner_type, owner_id, url) DO NOTHING;

-- 3) Migrate politician_socials
INSERT INTO politician_socials (politician_id, platform, handle, url, last_verified_at, is_live, follower_count)
SELECT dp.keeper_id, s.platform, s.handle, s.url, s.last_verified_at, s.is_live, s.follower_count
FROM politician_socials s
JOIN dup_pairs dp ON s.politician_id = dp.loser_id
ON CONFLICT (politician_id, platform, (LOWER(handle))) DO NOTHING;

-- 4) Migrate politician_offices (no unique constraint, use NOT EXISTS guard)
INSERT INTO politician_offices (politician_id, kind, address, city, province_territory,
                                postal_code, phone, fax, email, hours, lat, lon, source)
SELECT dp.keeper_id, o.kind, o.address, o.city, o.province_territory,
       o.postal_code, o.phone, o.fax, o.email, o.hours, o.lat, o.lon, o.source
FROM politician_offices o
JOIN dup_pairs dp ON o.politician_id = dp.loser_id
WHERE NOT EXISTS (
    SELECT 1 FROM politician_offices o2
    WHERE o2.politician_id = dp.keeper_id
      AND COALESCE(o2.kind,'') = COALESCE(o.kind,'')
      AND COALESCE(o2.phone,'') = COALESCE(o.phone,'')
      AND COALESCE(o2.postal_code,'') = COALESCE(o.postal_code,'')
);

-- 5) Migrate politician_committees
INSERT INTO politician_committees (politician_id, committee_name, role, level, started_at, ended_at, source)
SELECT dp.keeper_id, c.committee_name, c.role, c.level, c.started_at, c.ended_at, c.source
FROM politician_committees c
JOIN dup_pairs dp ON c.politician_id = dp.loser_id
WHERE NOT EXISTS (
    SELECT 1 FROM politician_committees c2
    WHERE c2.politician_id = dp.keeper_id
      AND c2.committee_name = c.committee_name
      AND COALESCE(c2.role,'') = COALESCE(c.role,'')
);

-- 6) Keep keeper's term, drop loser's initial term (will be recreated on next ingest anyway)
DELETE FROM politician_terms
 WHERE politician_id IN (SELECT loser_id FROM dup_pairs);

-- 7) Clean synthetic politician_changes on loser
DELETE FROM politician_changes
 WHERE politician_id IN (SELECT loser_id FROM dup_pairs);

-- 8) Fill keeper's scalar fields from loser where keeper is NULL
UPDATE politicians AS keeper
SET personal_url = COALESCE(NULLIF(keeper.personal_url,''), NULLIF(loser.personal_url,'')),
    photo_url    = COALESCE(NULLIF(keeper.photo_url,''),    NULLIF(loser.photo_url,'')),
    email        = COALESCE(NULLIF(keeper.email,''),        NULLIF(loser.email,'')),
    phone        = COALESCE(NULLIF(keeper.phone,''),        NULLIF(loser.phone,'')),
    first_name   = COALESCE(NULLIF(keeper.first_name,''),   NULLIF(loser.first_name,'')),
    last_name    = COALESCE(NULLIF(keeper.last_name,''),    NULLIF(loser.last_name,''))
FROM dup_pairs dp, politicians AS loser
WHERE keeper.id = dp.keeper_id AND loser.id = dp.loser_id;

-- 9) Delete loser politicians (CASCADE handles any remaining child rows)
DELETE FROM politicians
 WHERE id IN (SELECT loser_id FROM dup_pairs);

-- Post-dedup sanity
SELECT province_territory, COUNT(*) AS remaining
FROM politicians
WHERE level='provincial' AND is_active=true
  AND province_territory IN ('BC','NB','NL','YT','NU','ON')
GROUP BY 1 ORDER BY 1;

COMMIT;
