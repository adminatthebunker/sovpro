# SovereignWatch MVP — Specification v2

## What This Is

A civic transparency tool that answers one question: **Where do Canadian politicians and political organizations actually store their website data?**

It pulls elected officials from the Open North API, adds Alberta referendum campaign organizations on both sides of the independence question, scans every associated website to determine hosting location, and renders the results on an interactive map — constituency polygons connected by lines to server pins, overwhelmingly clustered in the United States.

Change detection monitors all tracked sites and alerts when infrastructure moves.

**Three target groups at launch:**

1. **All federal MPs** (~338 politicians, ~500+ websites)
2. **All Alberta politicians** — MLAs (~87), Edmonton city council (~13), Calgary city council (~15)
3. **Alberta referendum organizations** — both sides of the October 19, 2026 independence question, plus the UCP government's nine referendum questions on immigration, constitutional reform, and provincial powers

The referendum angle is the sharpest part of the story. Organizations arguing Alberta should "leave Canada" to be more sovereign — while hosting their digital infrastructure on American servers — is the thesis of this entire project distilled into a single irony.

---

## Tech Stack

| Layer | Technology |
|---|---|
| **Backend** | Node.js (Fastify) |
| **Scanner** | Python (async) |
| **Frontend** | React + TypeScript + Leaflet.js |
| **Database** | PostgreSQL 16 + PostGIS |
| **Change Detection** | [change](https://github.com/Thedurancode/change) |
| **Uptime Monitoring** | [Uptime Kuma](https://uptimekuma.org/) |
| **Reverse Proxy** | nginx |
| **Access** | Pangolin |
| **Containers** | Docker Compose |

---

## Target Database

### Group 1: Federal MPs (Open North API)

**Source:** `https://represent.opennorth.ca/representatives/house-of-commons/`

~338 MPs, each with:
- Official parliamentary page (ourcommons.ca)
- Personal/campaign website (if exists)
- Party profile page
- Social media URLs

**Websites to scan per MP:**
- Personal/campaign site (the interesting one — this is what they or their campaign chose)
- Official parliamentary page (shared infrastructure, likely Canadian — Shared Services Canada)
- Party profile page (liberal.ca, conservative.ca, ndp.ca, etc.)

### Group 2: Alberta Politicians

#### Alberta MLAs (~87)
**Source:** `https://represent.opennorth.ca/representatives/alberta-legislature/`

Each MLA with:
- Official legislature page (assembly.ab.ca)
- Personal/campaign site
- Party profile page
- Constituency office web presence

#### Edmonton City Council (~13 councillors + mayor)
**Source:** `https://represent.opennorth.ca/representatives/edmonton-city-council/`

- Official city page (edmonton.ca/city_government/city_council)
- Personal/campaign sites

#### Calgary City Council (~15 councillors + mayor)
**Source:** `https://represent.opennorth.ca/representatives/calgary-city-council/`

- Official city page (calgary.ca)
- Personal/campaign sites

### Group 3: Alberta Referendum Organizations

This group is manually curated. These are the organizations driving the October 19, 2026 referendum — both the independence question and the government's nine referendum questions.

#### Leave / Separation Side

| Organization | Website | Key People | Notes |
|---|---|---|---|
| **Alberta Prosperity Project (APP)** | `albertaprosperityproject.com` | Mitch Sylvestre (CEO), Dennis Modry (co-founder), Jeffrey Rath (legal counsel) | Primary separatist organization. Filed original CIP application. Met with US State Department officials. Partnered with PQ. |
| **Stay Free Alberta** | `stayfreealberta.com` | Same leadership as APP | Rebranded petition vehicle after Bill 14 passed. Runs the active signature collection campaign. Claims 177,732+ signatures as of March 30, 2026. |
| **APP merch/donation** | `nb.albertaprosperity.com` | | Pledge registration and donations portal |
| **Republican Party of Alberta** | TBD (search needed) | | Separatist political party. Got 0.67–17.66% in 2025 by-elections. |

**Also monitor:**
- Social media accounts for APP/SFA leadership
- Donation platforms (where does their payment processing run?)
- Any petition infrastructure (forms, signature collection tools)

#### Stay / Forever Canadian Side

| Organization | Website | Key People | Notes |
|---|---|---|---|
| **Forever Canadian / Alberta Forever Canada** | `forever-canadian.ca` | Thomas Lukaszuk (organizer, former PC deputy premier) | 404,293 verified signatures. Certified by Elections Alberta Dec 1, 2025. Backed by Ed Stelmach, Ray Martin, Ian McClelland. |

**Also monitor:**
- Coalition partners and supporting organizations
- Any pro-Canada advocacy groups active in the referendum space

#### Government / Official Referendum

| Entity | Website | Notes |
|---|---|---|
| **Elections Alberta — Referendum page** | `elections.ab.ca/elections/referendum/` | Official referendum info, nine questions |
| **Alberta.ca referendum info** | TBD | Government communications about the Oct 19 vote |
| **UCP Party** | `unitedconservative.ca` | Danielle Smith's party. Architect of Bill 14 and the referendum questions. |
| **Alberta NDP** | `albertandp.ca` | Official opposition. 93% of NDP voters oppose separation. |

#### Adjacent Organizations (Phase 1.5 expansion)

| Organization | Type | Notes |
|---|---|---|
| **Confederacy of Treaty No. 6 First Nations** | Indigenous rights | Strongly opposes separation. Won court challenge on constitutional grounds. |
| **Treaty 7 / Treaty 8 First Nations** | Indigenous rights | Allied with Treaty 6 against separation |
| **Parti Québécois** | Quebec separatist party | Paul St-Pierre Plamondon met with APP leaders, expressed support |
| **PressProgress** | Investigative journalism | Reporting on APP/US government connections |

---

## Data Model

```sql
-- Enable extensions
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ─────────────────────────────────────────────
-- Politicians
-- ─────────────────────────────────────────────
CREATE TABLE politicians (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    source_id           TEXT UNIQUE,          -- Open North ID (null for manually added)
    name                TEXT NOT NULL,
    party               TEXT,
    elected_office      TEXT,                 -- "MP", "MLA", "City Councillor", "Mayor"
    level               TEXT NOT NULL,        -- "federal", "provincial", "municipal"
    province_territory  TEXT,
    constituency_name   TEXT,
    constituency_id     TEXT,
    email               TEXT,
    photo_url           TEXT,
    personal_url        TEXT,
    official_url        TEXT,
    social_urls         JSONB DEFAULT '{}',
    is_active           BOOLEAN DEFAULT true,
    created_at          TIMESTAMPTZ DEFAULT now(),
    updated_at          TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX idx_politicians_level ON politicians(level);
CREATE INDEX idx_politicians_party ON politicians(party);
CREATE INDEX idx_politicians_province ON politicians(province_territory);

-- ─────────────────────────────────────────────
-- Organizations (referendum groups, parties, advocacy)
-- ─────────────────────────────────────────────
CREATE TABLE organizations (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name                TEXT NOT NULL,
    slug                TEXT UNIQUE,          -- URL-safe identifier
    type                TEXT NOT NULL,        -- "referendum_leave", "referendum_stay",
                                              -- "political_party", "indigenous_rights",
                                              -- "advocacy", "government_body"
    side                TEXT,                 -- "leave", "stay", "neutral", null
    description         TEXT,
    key_people          JSONB DEFAULT '[]',   -- [{name, role}]
    province_territory  TEXT,
    social_urls         JSONB DEFAULT '{}',
    is_active           BOOLEAN DEFAULT true,
    created_at          TIMESTAMPTZ DEFAULT now(),
    updated_at          TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX idx_organizations_type ON organizations(type);
CREATE INDEX idx_organizations_side ON organizations(side);

-- ─────────────────────────────────────────────
-- Websites
-- ─────────────────────────────────────────────
CREATE TABLE websites (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    owner_type          TEXT NOT NULL,        -- "politician" or "organization"
    owner_id            UUID NOT NULL,        -- FK to politicians or organizations
    url                 TEXT NOT NULL,
    label               TEXT,                 -- "personal", "official", "party", "campaign",
                                              -- "donate", "petition", "merch"
    is_active           BOOLEAN DEFAULT true,
    last_scanned_at     TIMESTAMPTZ,
    last_changed_at     TIMESTAMPTZ,
    created_at          TIMESTAMPTZ DEFAULT now(),
    updated_at          TIMESTAMPTZ DEFAULT now(),
    UNIQUE(owner_type, owner_id, url)
);

CREATE INDEX idx_websites_owner ON websites(owner_type, owner_id);
CREATE INDEX idx_websites_active ON websites(is_active) WHERE is_active = true;

-- ─────────────────────────────────────────────
-- Infrastructure Scans
-- ─────────────────────────────────────────────
CREATE TABLE infrastructure_scans (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    website_id          UUID NOT NULL REFERENCES websites(id) ON DELETE CASCADE,
    scanned_at          TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- DNS
    ip_addresses        TEXT[],
    cname_chain         TEXT[],
    nameservers         TEXT[],
    mx_records          TEXT[],

    -- Geolocation
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
    sovereignty_tier    SMALLINT NOT NULL,   -- 1-6
    cdn_detected        TEXT,
    cms_detected        TEXT,

    -- TLS
    tls_issuer          TEXT,
    tls_expiry          TIMESTAMPTZ,

    -- HTTP
    http_server_header  TEXT,
    http_powered_by     TEXT,

    -- Raw
    raw_data            JSONB
);

CREATE INDEX idx_scans_website ON infrastructure_scans(website_id, scanned_at DESC);

-- ─────────────────────────────────────────────
-- Scan Changes
-- ─────────────────────────────────────────────
CREATE TABLE scan_changes (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    website_id          UUID NOT NULL REFERENCES websites(id) ON DELETE CASCADE,
    detected_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    change_type         TEXT NOT NULL,
    old_value           TEXT,
    new_value           TEXT,
    details             JSONB,
    summary             TEXT
);

CREATE INDEX idx_changes_recent ON scan_changes(detected_at DESC);

-- ─────────────────────────────────────────────
-- Constituency Boundaries
-- ─────────────────────────────────────────────
CREATE TABLE constituency_boundaries (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    constituency_id     TEXT UNIQUE NOT NULL,
    name                TEXT NOT NULL,
    level               TEXT NOT NULL,
    province_territory  TEXT,
    boundary            GEOMETRY(MultiPolygon, 4326),
    boundary_simple     GEOMETRY(MultiPolygon, 4326),
    centroid            GEOMETRY(Point, 4326),
    created_at          TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX idx_boundaries_geo ON constituency_boundaries USING GIST(boundary_simple);

-- ─────────────────────────────────────────────
-- Materialized view: map data
-- ─────────────────────────────────────────────

-- For politicians (with constituency boundaries)
CREATE MATERIALIZED VIEW map_politicians AS
SELECT
    p.id AS politician_id,
    p.name, p.party, p.elected_office, p.level,
    p.province_territory, p.constituency_name, p.photo_url,
    cb.constituency_id,
    ST_AsGeoJSON(cb.boundary_simple)::jsonb AS boundary_geojson,
    ST_X(cb.centroid) AS constituency_lng,
    ST_Y(cb.centroid) AS constituency_lat,
    w.id AS website_id,
    w.url AS website_url,
    w.label AS website_label,
    s.ip_country, s.ip_city,
    s.ip_latitude AS server_lat,
    s.ip_longitude AS server_lng,
    s.hosting_provider, s.hosting_country,
    s.sovereignty_tier, s.cdn_detected, s.cms_detected,
    s.scanned_at
FROM politicians p
JOIN websites w ON w.owner_type = 'politician' AND w.owner_id = p.id AND w.is_active = true
LEFT JOIN constituency_boundaries cb ON cb.constituency_id = p.constituency_id
JOIN LATERAL (
    SELECT * FROM infrastructure_scans
    WHERE website_id = w.id ORDER BY scanned_at DESC LIMIT 1
) s ON true
WHERE p.is_active = true;

-- For organizations (no constituency, just org location → server location)
CREATE MATERIALIZED VIEW map_organizations AS
SELECT
    o.id AS organization_id,
    o.name, o.type, o.side, o.description,
    o.province_territory,
    w.id AS website_id,
    w.url AS website_url,
    w.label AS website_label,
    s.ip_country, s.ip_city,
    s.ip_latitude AS server_lat,
    s.ip_longitude AS server_lng,
    s.hosting_provider, s.hosting_country,
    s.sovereignty_tier, s.cdn_detected, s.cms_detected,
    s.scanned_at
FROM organizations o
JOIN websites w ON w.owner_type = 'organization' AND w.owner_id = o.id AND w.is_active = true
JOIN LATERAL (
    SELECT * FROM infrastructure_scans
    WHERE website_id = w.id ORDER BY scanned_at DESC LIMIT 1
) s ON true
WHERE o.is_active = true;
```

### Sovereignty Tiers

| Tier | Label | Criteria |
|------|-------|----------|
| 1 | 🍁 Canadian Sovereign | Canadian-owned hosting provider + Canadian datacenter |
| 2 | 🇨🇦 Canadian Soil | Foreign provider but server geolocates to Canada |
| 3 | 🌐 CDN-Fronted | Behind a global CDN, origin unknown or non-Canadian |
| 4 | 🇺🇸 US-Hosted | Server in the US, US provider |
| 5 | 🌍 Other Foreign | Hosted outside Canada and US |
| 6 | ❓ Unknown | Scan failed or inconclusive |

---

## Data Ingestion

### Open North Pipeline (automated)

```
# Federal MPs
GET https://represent.opennorth.ca/representatives/house-of-commons/?limit=500

# Alberta MLAs
GET https://represent.opennorth.ca/representatives/alberta-legislature/?limit=100

# Edmonton Council
GET https://represent.opennorth.ca/representatives/edmonton-city-council/?limit=20

# Calgary Council
GET https://represent.opennorth.ca/representatives/calgary-city-council/?limit=20

# Boundaries for all of the above
GET https://represent.opennorth.ca/boundaries/{set}/{district}/simple_shape

Schedule:
  - Weekly full sync (Sunday 2 AM MST)
  - Daily delta check (6 AM MST)
```

### Referendum Organizations (manual seed + automated scan)

Seed script populates the `organizations` table with known entities:

```python
REFERENDUM_ORGS = [
    # ── LEAVE SIDE ──────────────────────────────────────────
    {
        "name": "Alberta Prosperity Project",
        "slug": "alberta-prosperity-project",
        "type": "referendum_leave",
        "side": "leave",
        "description": "Primary Alberta separatist organization. Filed original CIP application for independence referendum. Co-founded by Dennis Modry, CEO Mitch Sylvestre, legal counsel Jeffrey Rath.",
        "key_people": [
            {"name": "Mitch Sylvestre", "role": "CEO"},
            {"name": "Dennis Modry", "role": "Co-founder"},
            {"name": "Jeffrey Rath", "role": "Legal Counsel"},
        ],
        "province_territory": "AB",
        "websites": [
            {"url": "https://albertaprosperityproject.com/", "label": "primary"},
            {"url": "https://nb.albertaprosperity.com/", "label": "pledge"},
        ],
    },
    {
        "name": "Stay Free Alberta",
        "slug": "stay-free-alberta",
        "type": "referendum_leave",
        "side": "leave",
        "description": "Rebranded petition vehicle for APP after Bill 14. Runs active signature collection campaign for independence referendum question. Same leadership as APP.",
        "key_people": [
            {"name": "Mitch Sylvestre", "role": "Petition figurehead"},
            {"name": "Jeffrey Rath", "role": "Legal Counsel"},
        ],
        "province_territory": "AB",
        "websites": [
            {"url": "https://stayfreealberta.com/", "label": "primary"},
            {"url": "https://stayfreealberta.com/sign/", "label": "petition"},
        ],
    },

    # ── STAY SIDE ───────────────────────────────────────────
    {
        "name": "Forever Canadian / Alberta Forever Canada",
        "slug": "forever-canadian",
        "type": "referendum_stay",
        "side": "stay",
        "description": "Anti-separatist citizen initiative led by Thomas Lukaszuk (former PC deputy premier). 404,293 verified signatures. Certified by Elections Alberta Dec 2025. Non-partisan: backed by Ed Stelmach (PC), Ray Martin (NDP), Ian McClelland (Reform).",
        "key_people": [
            {"name": "Thomas Lukaszuk", "role": "Organizer"},
        ],
        "province_territory": "AB",
        "websites": [
            {"url": "https://www.forever-canadian.ca/en", "label": "primary"},
        ],
    },

    # ── POLITICAL PARTIES ───────────────────────────────────
    {
        "name": "United Conservative Party (UCP)",
        "slug": "ucp",
        "type": "political_party",
        "side": None,
        "description": "Alberta's governing party. Architect of Bill 14 (lowered referendum thresholds) and the nine official referendum questions for Oct 19. Led by Premier Danielle Smith.",
        "key_people": [
            {"name": "Danielle Smith", "role": "Leader / Premier"},
        ],
        "province_territory": "AB",
        "websites": [
            {"url": "https://www.unitedconservative.ca/", "label": "party"},
        ],
    },
    {
        "name": "Alberta NDP",
        "slug": "alberta-ndp",
        "type": "political_party",
        "side": "stay",
        "description": "Official opposition. 93% of NDP voters oppose separation per Angus Reid polling.",
        "province_territory": "AB",
        "websites": [
            {"url": "https://www.albertandp.ca/", "label": "party"},
        ],
    },

    # ── FEDERAL PARTIES (for comparison) ────────────────────
    {
        "name": "Liberal Party of Canada",
        "slug": "liberal-party",
        "type": "political_party",
        "side": None,
        "province_territory": None,
        "websites": [
            {"url": "https://liberal.ca/", "label": "party"},
        ],
    },
    {
        "name": "Conservative Party of Canada",
        "slug": "conservative-party",
        "type": "political_party",
        "side": None,
        "province_territory": None,
        "websites": [
            {"url": "https://www.conservative.ca/", "label": "party"},
        ],
    },
    {
        "name": "New Democratic Party (Federal)",
        "slug": "ndp-federal",
        "type": "political_party",
        "side": None,
        "province_territory": None,
        "websites": [
            {"url": "https://www.ndp.ca/", "label": "party"},
        ],
    },
    {
        "name": "Bloc Québécois",
        "slug": "bloc-quebecois",
        "type": "political_party",
        "side": None,
        "province_territory": "QC",
        "websites": [
            {"url": "https://www.blocquebecois.org/", "label": "party"},
        ],
    },
    {
        "name": "Green Party of Canada",
        "slug": "green-party",
        "type": "political_party",
        "side": None,
        "province_territory": None,
        "websites": [
            {"url": "https://www.greenparty.ca/", "label": "party"},
        ],
    },

    # ── OFFICIAL / GOVERNMENT ───────────────────────────────
    {
        "name": "Elections Alberta",
        "slug": "elections-alberta",
        "type": "government_body",
        "side": "neutral",
        "description": "Official body administering the October 19, 2026 referendum.",
        "province_territory": "AB",
        "websites": [
            {"url": "https://www.elections.ab.ca/", "label": "primary"},
            {"url": "https://www.elections.ab.ca/elections/referendum/", "label": "referendum"},
        ],
    },

    # ── INDIGENOUS RIGHTS ───────────────────────────────────
    {
        "name": "Confederacy of Treaty No. 6 First Nations",
        "slug": "treaty-6-confederacy",
        "type": "indigenous_rights",
        "side": "stay",
        "description": "Represents 16 First Nations in Alberta. Granted intervenor status in court challenge. Opposes separation as violation of Treaty rights and Section 35.",
        "province_territory": "AB",
        "websites": [],  # Discover during research phase
    },
]
```

---

## API Endpoints

### Politicians
```
GET /api/v1/politicians
  ?level=federal|provincial|municipal
  &province=AB|ON|BC|...
  &party=...
  &sovereignty_tier=1|2|3|4|5|6
  &search=name
  &page=1&limit=50

GET /api/v1/politicians/:id
```

### Organizations
```
GET /api/v1/organizations
  ?type=referendum_leave|referendum_stay|political_party|indigenous_rights|government_body
  &side=leave|stay|neutral
  &search=name
  &page=1&limit=50

GET /api/v1/organizations/:id
```

### Map
```
GET /api/v1/map/geojson
  ?level=federal|provincial|municipal
  &province=AB
  &group=politicians|organizations|all

GET /api/v1/map/referendum
  → GeoJSON specifically for the referendum view:
    - Leave org websites → server pins (with "leave" flag)
    - Stay org websites → server pins (with "stay" flag)
    - AB provincial boundary as context
```

### Stats
```
GET /api/v1/stats
  → {
    politicians: {
      total: 453,
      by_level: { federal: 338, provincial: 87, municipal: 28 },
      sovereignty: { tier_1: 12, tier_2: 28, tier_3: 89, tier_4: 297, tier_5: 15, tier_6: 12 },
      pct_not_canadian: 87.4,
      by_party: { ... }
    },
    organizations: {
      total: 14,
      referendum: {
        leave: {
          orgs: ["Alberta Prosperity Project", "Stay Free Alberta"],
          websites_scanned: 4,
          sovereignty: { tier_4: 3, tier_3: 1 }
        },
        stay: {
          orgs: ["Forever Canadian"],
          websites_scanned: 1,
          sovereignty: { tier_4: 1 }
        }
      }
    },
    top_server_locations: [...],
    top_providers: [...]
  }

GET /api/v1/stats/referendum
  → Focused stats for the referendum orgs:
    {
      leave_side: {
        total_websites: 4,
        hosted_in_canada: 0,
        hosted_in_us: 3,
        cdn_fronted: 1,
        providers: ["Cloudflare", "AWS", "Squarespace"],
        irony_score: "Organizations advocating to leave Canada for sovereignty
                      store 100% of their data outside Canada"
      },
      stay_side: {
        total_websites: 1,
        hosted_in_canada: 0,
        hosted_in_us: 1,
        providers: ["WordPress.com"],
        note: "Even the pro-Canada side hosts in the US"
      }
    }
```

### Changes
```
GET /api/v1/changes
  ?since=2026-04-01
  &owner_type=politician|organization
  &change_type=hosting_moved|ip_changed|...
  &page=1&limit=25
```

---

## Frontend

### Main Map View

Three toggleable layers:

1. **Federal layer** — all 338 MP constituencies, colored by sovereignty tier, with connection lines to server locations
2. **Alberta layer** — provincial ridings + Edmonton/Calgary wards
3. **Referendum layer** — referendum organizations plotted with "leave" (red) and "stay" (blue) markers, lines to their server locations

### Referendum Spotlight Section

Below or beside the map, a dedicated panel:

```
┌─────────────────────────────────────────────────────────────────┐
│  🗳️ REFERENDUM WATCH — October 19, 2026                        │
│                                                                  │
│  ┌─────────────────────────┐  ┌──────────────────────────────┐  │
│  │ LEAVE SIDE              │  │ STAY SIDE                    │  │
│  │                         │  │                              │  │
│  │ Alberta Prosperity      │  │ Forever Canadian             │  │
│  │ Project                 │  │ forever-canadian.ca          │  │
│  │ albertaprosperity       │  │                              │  │
│  │ project.com             │  │ 🇺🇸 US-Hosted               │  │
│  │ 🇺🇸 US-Hosted           │  │ WordPress.com (Automattic)  │  │
│  │ Cloudflare → AWS        │  │ San Francisco, CA            │  │
│  │ us-east-1, Virginia     │  │                              │  │
│  │                         │  │                              │  │
│  │ Stay Free Alberta       │  │                              │  │
│  │ stayfreealberta.com     │  │                              │  │
│  │ 🇺🇸 US-Hosted           │  │                              │  │
│  │ Squarespace             │  │                              │  │
│  │ New York, NY            │  │                              │  │
│  │                         │  │                              │  │
│  │ 0/4 websites Canadian   │  │ 0/1 websites Canadian        │  │
│  └─────────────────────────┘  └──────────────────────────────┘  │
│                                                                  │
│  Neither side of Alberta's sovereignty debate hosts their        │
│  digital infrastructure in Canada.                               │
└─────────────────────────────────────────────────────────────────┘
```

### Stats Headlines

The frontend should prominently feature these datapoints:

**Politicians:**
- "X% of Canada's 338 MPs host their personal websites in the United States."
- "Only Y MPs out of 338 use Canadian-owned hosting infrastructure."
- "The most popular hosting location for Canadian political data is Ashburn, Virginia."
- "[Party] has the best/worst sovereignty score among federal parties."

**Alberta:**
- "X% of Alberta MLAs host their websites outside Canada."
- "X of Y Edmonton councillors store their website data in the US."

**Referendum (the kicker):**
- "Organizations campaigning for Alberta to leave Canada and become a sovereign nation store 100% of their website data on American servers."
- "Even the pro-Canada side hosts in the US."
- "Neither side of Alberta's independence debate hosts their digital infrastructure in Canada."
- "The Alberta Prosperity Project — which met three times with US State Department officials — hosts its website in [US city, US state]."

---

## Docker Compose

```yaml
services:
  api:
    build: ./services/api
    ports: ["3000:3000"]
    depends_on: [db]
    environment:
      DATABASE_URL: postgresql://sw:${DB_PASSWORD}@db:5432/sovereignwatch
      CHANGE_WEBHOOK_SECRET: ${WEBHOOK_SECRET}
    restart: unless-stopped

  frontend:
    build: ./services/frontend
    restart: unless-stopped

  db:
    image: postgis/postgis:16-3.4
    volumes:
      - pgdata:/var/lib/postgresql/data
      - ./db/init.sql:/docker-entrypoint-initdb.d/01-init.sql
      - ./db/seed.sql:/docker-entrypoint-initdb.d/02-seed.sql
    environment:
      POSTGRES_DB: sovereignwatch
      POSTGRES_USER: sw
      POSTGRES_PASSWORD: ${DB_PASSWORD}
    restart: unless-stopped

  scanner:
    build: ./services/scanner
    depends_on: [db]
    environment:
      DATABASE_URL: postgresql://sw:${DB_PASSWORD}@db:5432/sovereignwatch
    volumes:
      - ./data/GeoLite2-City.mmdb:/data/GeoLite2-City.mmdb:ro
      - ./data/GeoLite2-ASN.mmdb:/data/GeoLite2-ASN.mmdb:ro
    restart: unless-stopped

  change-detection:
    image: ghcr.io/thedurancode/change:latest
    volumes:
      - changedata:/data
    environment:
      WEBHOOK_URL: http://api:3000/api/v1/webhooks/change
      WEBHOOK_SECRET: ${WEBHOOK_SECRET}
    restart: unless-stopped

  uptime-kuma:
    image: louislam/uptime-kuma:1
    volumes:
      - kumadata:/app/data
    ports: ["3001:3001"]
    restart: unless-stopped

  nginx:
    image: nginx:alpine
    ports: ["80:80", "443:443"]
    volumes:
      - ./nginx/nginx.conf:/etc/nginx/nginx.conf:ro
    depends_on: [api, frontend]
    restart: unless-stopped

volumes:
  pgdata:
  changedata:
  kumadata:
```

---

## Project Structure

```
sovereignwatch/
├── docker-compose.yml
├── .env.example
├── db/
│   ├── init.sql                     # Schema (tables, indexes, views)
│   └── seed.sql                     # Referendum organizations seed data
├── services/
│   ├── api/                         # Node.js Fastify
│   │   ├── Dockerfile
│   │   ├── package.json
│   │   └── src/
│   │       ├── index.ts
│   │       ├── routes/
│   │       │   ├── politicians.ts
│   │       │   ├── organizations.ts
│   │       │   ├── map.ts
│   │       │   ├── stats.ts
│   │       │   ├── changes.ts
│   │       │   └── webhooks.ts
│   │       ├── db.ts
│   │       └── types.ts
│   ├── frontend/                    # React + TypeScript + Leaflet
│   │   ├── Dockerfile
│   │   ├── package.json
│   │   └── src/
│   │       ├── App.tsx
│   │       ├── components/
│   │       │   ├── Map.tsx
│   │       │   ├── MapLayers.tsx
│   │       │   ├── StatsBar.tsx
│   │       │   ├── ChangesFeed.tsx
│   │       │   ├── PoliticianPanel.tsx
│   │       │   ├── OrgPanel.tsx
│   │       │   ├── ReferendumSpotlight.tsx
│   │       │   └── Filters.tsx
│   │       ├── hooks/
│   │       │   ├── useMapData.ts
│   │       │   ├── useStats.ts
│   │       │   └── useChanges.ts
│   │       └── types.ts
│   └── scanner/                     # Python
│       ├── Dockerfile
│       ├── requirements.txt
│       └── src/
│           ├── __main__.py
│           ├── scanner.py
│           ├── classify.py
│           ├── opennorth.py
│           ├── compare.py
│           ├── seed_orgs.py         # Seeds referendum organizations
│           └── db.py
├── nginx/
│   └── nginx.conf
└── data/
    ├── GeoLite2-City.mmdb
    └── GeoLite2-ASN.mmdb
```

---

## Implementation Order

### Week 1: Data Foundation
- [ ] PostgreSQL + PostGIS schema
- [ ] Open North ingestion: all federal MPs + boundaries
- [ ] Open North ingestion: Alberta MLAs + boundaries
- [ ] Open North ingestion: Edmonton + Calgary councils + ward boundaries
- [ ] Seed referendum organizations + websites
- [ ] Website discovery (personal URLs, official URLs, social)
- [ ] Spot-check: verify 20 politicians and all referendum org URLs

### Week 2: Scanner
- [ ] Core scan engine: DNS → GeoIP → TLS → HTTP headers
- [ ] Provider classification (ASN map + CNAME patterns)
- [ ] Sovereignty tier logic
- [ ] CDN detection and conservative origin classification
- [ ] Scan all websites (~550+ politician, ~15+ org)
- [ ] Change comparison logic
- [ ] Verify: spot-check 20 scan results manually

### Week 3: API + Map
- [ ] Fastify API: politicians, organizations, map/geojson, stats, changes
- [ ] Materialized views for fast map queries
- [ ] React scaffold
- [ ] Leaflet map: federal constituencies colored by tier
- [ ] Leaflet map: Alberta provincial + municipal layers
- [ ] Server pin clusters
- [ ] Connection lines
- [ ] Layer toggle controls
- [ ] Stats bar

### Week 4: Referendum + Polish
- [ ] Referendum spotlight component
- [ ] Organization detail panels
- [ ] /stats/referendum endpoint
- [ ] Referendum-specific map layer (leave/stay markers with lines)
- [ ] `change` detection configured for all websites
- [ ] Uptime Kuma monitors for all websites
- [ ] Changes feed in frontend
- [ ] Filter controls (level, party, province, tier, org type, side)
- [ ] Politician and org detail panels on click

### Week 5: Ship
- [ ] Scanner cron: daily quick, weekly full
- [ ] Deploy behind Pangolin
- [ ] Social sharing OG images with headline stats
- [ ] Landing page with the key numbers
- [ ] README + license (open source)
- [ ] Write the blog post

---

## The Blog Post

Two stories to tell:

### Story 1: The Big Picture

> We scanned the websites of all 338 Members of Parliament, 87 Alberta MLAs, and 28 Edmonton and Calgary councillors. X% store their data in the United States. Only Y use Canadian-owned hosting. The most popular location for Canadian political data is Ashburn, Virginia.

### Story 2: The Referendum Irony

> Alberta is heading to a referendum on independence. The Alberta Prosperity Project — the organization driving the "leave" petition, which met three times with US State Department officials — hosts its website on [American provider] servers in [American city]. Stay Free Alberta, their petition vehicle, runs on [American provider]. Even the Forever Canadian campaign, fighting to keep Alberta in Canada, hosts on American infrastructure.
>
> Both sides of Alberta's sovereignty debate have already outsourced their digital sovereignty to the United States.
>
> If you can't keep your own website in Canada, what exactly are you liberating?

That second story is the one that gets shared.
