-- ═══════════════════════════════════════════════════════════════════════════
-- SovereignWatch — database schema
-- ═══════════════════════════════════════════════════════════════════════════
--
-- Runs once on first container start (mounted into /docker-entrypoint-initdb.d).
-- Creates all tables, indexes, materialized views, and helper functions.
--
-- Re-run safe via IF NOT EXISTS where possible; for a full reset drop the
-- `pgdata` volume.
-- ═══════════════════════════════════════════════════════════════════════════

CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS pg_trgm;

SET TIME ZONE 'UTC';

-- ─────────────────────────────────────────────────────────────
-- Politicians (federal MPs, provincial MLAs, city councillors)
-- ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS politicians (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    source_id           TEXT UNIQUE,
    name                TEXT NOT NULL,
    first_name          TEXT,
    last_name           TEXT,
    gender              TEXT,
    party               TEXT,
    elected_office      TEXT,
    level               TEXT NOT NULL CHECK (level IN ('federal','provincial','municipal')),
    province_territory  TEXT,
    constituency_name   TEXT,
    constituency_id     TEXT,
    email               TEXT,
    phone               TEXT,
    photo_url           TEXT,
    personal_url        TEXT,
    official_url        TEXT,
    social_urls         JSONB NOT NULL DEFAULT '{}'::jsonb,
    extras              JSONB NOT NULL DEFAULT '{}'::jsonb,
    is_active           BOOLEAN NOT NULL DEFAULT true,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_politicians_level        ON politicians(level);
CREATE INDEX IF NOT EXISTS idx_politicians_party        ON politicians(party);
CREATE INDEX IF NOT EXISTS idx_politicians_province     ON politicians(province_territory);
CREATE INDEX IF NOT EXISTS idx_politicians_constituency ON politicians(constituency_id);
CREATE INDEX IF NOT EXISTS idx_politicians_name_trgm    ON politicians USING GIN (name gin_trgm_ops);

-- ─────────────────────────────────────────────────────────────
-- Organizations (referendum groups, parties, advocacy)
-- ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS organizations (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name                TEXT NOT NULL,
    slug                TEXT UNIQUE NOT NULL,
    type                TEXT NOT NULL CHECK (type IN (
                          'referendum_leave','referendum_stay','political_party',
                          'indigenous_rights','advocacy','government_body','media')),
    side                TEXT CHECK (side IN ('leave','stay','neutral')),
    description         TEXT,
    key_people          JSONB NOT NULL DEFAULT '[]'::jsonb,
    province_territory  TEXT,
    social_urls         JSONB NOT NULL DEFAULT '{}'::jsonb,
    extras              JSONB NOT NULL DEFAULT '{}'::jsonb,
    is_active           BOOLEAN NOT NULL DEFAULT true,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_organizations_type ON organizations(type);
CREATE INDEX IF NOT EXISTS idx_organizations_side ON organizations(side);
CREATE INDEX IF NOT EXISTS idx_organizations_name_trgm ON organizations USING GIN (name gin_trgm_ops);

-- ─────────────────────────────────────────────────────────────
-- Websites (polymorphic: owned by politician OR organization)
-- ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS websites (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    owner_type          TEXT NOT NULL CHECK (owner_type IN ('politician','organization')),
    owner_id            UUID NOT NULL,
    url                 TEXT NOT NULL,
    hostname            TEXT GENERATED ALWAYS AS (
                          lower(regexp_replace(regexp_replace(url, '^https?://', ''),
                                 '/.*$', ''))) STORED,
    label               TEXT,
    notes               TEXT,
    is_active           BOOLEAN NOT NULL DEFAULT true,
    last_scanned_at     TIMESTAMPTZ,
    last_changed_at     TIMESTAMPTZ,
    scan_failures       INT NOT NULL DEFAULT 0,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(owner_type, owner_id, url)
);

CREATE INDEX IF NOT EXISTS idx_websites_owner      ON websites(owner_type, owner_id);
CREATE INDEX IF NOT EXISTS idx_websites_active     ON websites(is_active) WHERE is_active = true;
CREATE INDEX IF NOT EXISTS idx_websites_hostname   ON websites(hostname);
CREATE INDEX IF NOT EXISTS idx_websites_last_scan  ON websites(last_scanned_at NULLS FIRST);

-- ─────────────────────────────────────────────────────────────
-- Infrastructure scans (append-only time series)
-- ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS infrastructure_scans (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    website_id          UUID NOT NULL REFERENCES websites(id) ON DELETE CASCADE,
    scanned_at          TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- DNS
    ip_addresses        TEXT[],
    cname_chain         TEXT[],
    nameservers         TEXT[],
    mx_records          TEXT[],

    -- Geolocation (primary IP)
    ip_country          TEXT,
    ip_region           TEXT,
    ip_city             TEXT,
    ip_latitude         DOUBLE PRECISION,
    ip_longitude        DOUBLE PRECISION,
    ip_asn              TEXT,
    ip_org              TEXT,

    -- Classification
    hosting_provider    TEXT,
    hosting_country     TEXT,
    datacenter_region   TEXT,
    sovereignty_tier    SMALLINT NOT NULL CHECK (sovereignty_tier BETWEEN 1 AND 6),
    cdn_detected        TEXT,
    cms_detected        TEXT,

    -- TLS
    tls_issuer          TEXT,
    tls_subject         TEXT,
    tls_expiry          TIMESTAMPTZ,
    tls_valid           BOOLEAN,

    -- HTTP
    http_status         INT,
    http_server_header  TEXT,
    http_powered_by     TEXT,
    http_final_url      TEXT,

    -- Scan meta
    duration_ms         INT,
    error               TEXT,

    -- Raw
    raw_data            JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_scans_website    ON infrastructure_scans(website_id, scanned_at DESC);
CREATE INDEX IF NOT EXISTS idx_scans_country    ON infrastructure_scans(ip_country);
CREATE INDEX IF NOT EXISTS idx_scans_tier       ON infrastructure_scans(sovereignty_tier);
CREATE INDEX IF NOT EXISTS idx_scans_provider   ON infrastructure_scans(hosting_provider);
CREATE INDEX IF NOT EXISTS idx_scans_recent     ON infrastructure_scans(scanned_at DESC);

-- ─────────────────────────────────────────────────────────────
-- Scan changes (detected deltas between consecutive scans)
-- ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS scan_changes (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    website_id          UUID NOT NULL REFERENCES websites(id) ON DELETE CASCADE,
    from_scan_id        UUID REFERENCES infrastructure_scans(id) ON DELETE SET NULL,
    to_scan_id          UUID REFERENCES infrastructure_scans(id) ON DELETE SET NULL,
    detected_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    change_type         TEXT NOT NULL,
    old_value           TEXT,
    new_value           TEXT,
    severity            TEXT NOT NULL DEFAULT 'info' CHECK (severity IN ('info','notable','major')),
    details             JSONB NOT NULL DEFAULT '{}'::jsonb,
    summary             TEXT
);

CREATE INDEX IF NOT EXISTS idx_changes_recent   ON scan_changes(detected_at DESC);
CREATE INDEX IF NOT EXISTS idx_changes_website  ON scan_changes(website_id, detected_at DESC);
CREATE INDEX IF NOT EXISTS idx_changes_type     ON scan_changes(change_type);

-- ─────────────────────────────────────────────────────────────
-- Constituency boundaries
-- ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS constituency_boundaries (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    constituency_id     TEXT UNIQUE NOT NULL,
    name                TEXT NOT NULL,
    level               TEXT NOT NULL CHECK (level IN ('federal','provincial','municipal')),
    province_territory  TEXT,
    source_set          TEXT,
    boundary            GEOMETRY(MultiPolygon, 4326),
    boundary_simple     GEOMETRY(MultiPolygon, 4326),
    centroid            GEOMETRY(Point, 4326),
    area_sqkm           DOUBLE PRECISION,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_boundaries_geo       ON constituency_boundaries USING GIST(boundary_simple);
CREATE INDEX IF NOT EXISTS idx_boundaries_centroid  ON constituency_boundaries USING GIST(centroid);
CREATE INDEX IF NOT EXISTS idx_boundaries_level     ON constituency_boundaries(level);
CREATE INDEX IF NOT EXISTS idx_boundaries_province  ON constituency_boundaries(province_territory);

-- ─────────────────────────────────────────────────────────────
-- Helpers: updated_at trigger
-- ─────────────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION touch_updated_at() RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DO $$
DECLARE t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY['politicians','organizations','websites','constituency_boundaries']
    LOOP
        EXECUTE format('DROP TRIGGER IF EXISTS trg_%I_touch ON %I;', t, t);
        EXECUTE format(
            'CREATE TRIGGER trg_%I_touch BEFORE UPDATE ON %I FOR EACH ROW EXECUTE FUNCTION touch_updated_at();',
            t, t);
    END LOOP;
END $$;

-- ─────────────────────────────────────────────────────────────
-- Materialized view: map data (politicians + latest scan + boundary)
-- ─────────────────────────────────────────────────────────────
DROP MATERIALIZED VIEW IF EXISTS map_politicians CASCADE;
CREATE MATERIALIZED VIEW map_politicians AS
SELECT
    p.id                AS politician_id,
    p.name, p.party, p.elected_office, p.level,
    p.province_territory, p.constituency_name, p.photo_url,
    cb.constituency_id,
    ST_AsGeoJSON(cb.boundary_simple)::jsonb AS boundary_geojson,
    ST_X(cb.centroid)   AS constituency_lng,
    ST_Y(cb.centroid)   AS constituency_lat,
    w.id                AS website_id,
    w.url               AS website_url,
    w.label             AS website_label,
    w.hostname,
    s.id                AS scan_id,
    s.ip_country, s.ip_region, s.ip_city,
    s.ip_latitude       AS server_lat,
    s.ip_longitude      AS server_lng,
    s.ip_asn, s.ip_org,
    s.hosting_provider, s.hosting_country, s.datacenter_region,
    s.sovereignty_tier, s.cdn_detected, s.cms_detected,
    s.scanned_at
FROM politicians p
JOIN websites w ON w.owner_type = 'politician' AND w.owner_id = p.id AND w.is_active = true
LEFT JOIN constituency_boundaries cb ON cb.constituency_id = p.constituency_id
LEFT JOIN LATERAL (
    SELECT * FROM infrastructure_scans
    WHERE website_id = w.id ORDER BY scanned_at DESC LIMIT 1
) s ON true
WHERE p.is_active = true;

CREATE INDEX IF NOT EXISTS idx_mp_level        ON map_politicians(level);
CREATE INDEX IF NOT EXISTS idx_mp_province     ON map_politicians(province_territory);
CREATE INDEX IF NOT EXISTS idx_mp_party        ON map_politicians(party);
CREATE INDEX IF NOT EXISTS idx_mp_tier         ON map_politicians(sovereignty_tier);
CREATE UNIQUE INDEX IF NOT EXISTS idx_mp_unique ON map_politicians(politician_id, website_id);

-- ─────────────────────────────────────────────────────────────
-- Materialized view: organizations
-- ─────────────────────────────────────────────────────────────
DROP MATERIALIZED VIEW IF EXISTS map_organizations CASCADE;
CREATE MATERIALIZED VIEW map_organizations AS
SELECT
    o.id                AS organization_id,
    o.name, o.slug, o.type, o.side, o.description,
    o.province_territory,
    w.id                AS website_id,
    w.url               AS website_url,
    w.label             AS website_label,
    w.hostname,
    s.id                AS scan_id,
    s.ip_country, s.ip_region, s.ip_city,
    s.ip_latitude       AS server_lat,
    s.ip_longitude      AS server_lng,
    s.ip_asn, s.ip_org,
    s.hosting_provider, s.hosting_country, s.datacenter_region,
    s.sovereignty_tier, s.cdn_detected, s.cms_detected,
    s.scanned_at
FROM organizations o
JOIN websites w ON w.owner_type = 'organization' AND w.owner_id = o.id AND w.is_active = true
LEFT JOIN LATERAL (
    SELECT * FROM infrastructure_scans
    WHERE website_id = w.id ORDER BY scanned_at DESC LIMIT 1
) s ON true
WHERE o.is_active = true;

CREATE INDEX IF NOT EXISTS idx_mo_type         ON map_organizations(type);
CREATE INDEX IF NOT EXISTS idx_mo_side         ON map_organizations(side);
CREATE INDEX IF NOT EXISTS idx_mo_tier         ON map_organizations(sovereignty_tier);
CREATE UNIQUE INDEX IF NOT EXISTS idx_mo_unique ON map_organizations(organization_id, website_id);

-- ─────────────────────────────────────────────────────────────
-- Function: refresh materialized views
-- ─────────────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION refresh_map_views() RETURNS void AS $$
BEGIN
    REFRESH MATERIALIZED VIEW CONCURRENTLY map_politicians;
    REFRESH MATERIALIZED VIEW CONCURRENTLY map_organizations;
EXCEPTION WHEN OTHERS THEN
    -- Concurrent refresh needs unique indexes; fall back on plain refresh.
    REFRESH MATERIALIZED VIEW map_politicians;
    REFRESH MATERIALIZED VIEW map_organizations;
END;
$$ LANGUAGE plpgsql;

-- ─────────────────────────────────────────────────────────────
-- View: sovereignty summary per politician (latest scan only)
-- ─────────────────────────────────────────────────────────────
CREATE OR REPLACE VIEW politician_sovereignty AS
SELECT
    p.id, p.name, p.party, p.level, p.province_territory, p.constituency_name,
    MIN(s.sovereignty_tier) AS best_tier,
    MAX(s.sovereignty_tier) AS worst_tier,
    COUNT(w.id) AS website_count,
    COUNT(s.id) FILTER (WHERE s.ip_country = 'CA') AS ca_hosted,
    COUNT(s.id) FILTER (WHERE s.ip_country = 'US') AS us_hosted
FROM politicians p
LEFT JOIN websites w ON w.owner_type = 'politician' AND w.owner_id = p.id AND w.is_active = true
LEFT JOIN LATERAL (
    SELECT * FROM infrastructure_scans
    WHERE website_id = w.id ORDER BY scanned_at DESC LIMIT 1
) s ON true
WHERE p.is_active = true
GROUP BY p.id;
