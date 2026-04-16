# Canadian Political Data

> A civic transparency tool that answers one question: **where do Canadian politicians and political organizations actually store their website data?**

Canadian Political Data scans the websites of ~1,800 elected officials across every level of Canadian government — federal MPs, senators, every provincial and territorial legislature, and municipal councils from coast to coast — plus the organizations driving Alberta's October 19, 2026 independence referendum. Constituency polygons are drawn, then connected by lines to server pins, overwhelmingly clustered in the United States.

Enter a postal code and see your own MP, MLA, and councillors — with their hosting, their social handles, and their parliamentary record (for federal MPs, mirrored from [openparliament.ca](https://openparliament.ca)).

Change detection monitors all tracked sites and emits alerts when hosting moves.

---

## Coverage

| Level | Count | Source |
|---|---:|---|
| **Federal** | 440 | Every MP in the House of Commons + every senator |
| **Provincial / territorial** | 808 | Every MLA / MPP / MNA / MHA across all 10 provinces and 3 territories |
| **Municipal** | 571 | Council members across major cities — ON, QC, BC, AB, and the Atlantic provinces |
| **Referendum organizations** | 20 | Both sides of Alberta's Oct 19, 2026 independence question plus the UCP's nine referendum questions |
| **Websites tracked** | ~2,350 | Personal sites, campaign sites, official handles |
| **Provincial bills** | ~3,950 | NS + ON + BC + QC + AB + NB + NL + NT + NU (9 of 13 legislatures) |
| **Bill stage events** | ~5,300 | Full Westminster progression captured per bill (1R / 2R / Committee / 3R / Royal Assent ± `comes_into_force` for AB) |

Coverage is built province-by-province via dedicated ingesters (Open North where available, then per-legislature scrapers for gaps like Yukon / Nunavut / NB / NL / BC / ON). Bills are ingested from a mix of Socrata APIs, Drupal JSON, GraphQL backends, official CSVs, and server-rendered HTML — each province is its own beast. See `sovpro --help` and [`docs/plans/provincial-legislature-research.md`](docs/plans/provincial-legislature-research.md) for per-jurisdiction details.

---

## Architecture

```
┌────────────┐    ┌─────────┐     ┌─────────────┐    ┌──────────────┐
│  Frontend  │◄──►│  nginx  │◄───►│  API        │◄──►│  PostgreSQL  │
│  React/TS  │    │  proxy  │     │  Fastify    │    │  + PostGIS   │
│  Leaflet   │    └─────────┘     │  Node 20    │    └──────────────┘
└────────────┘                    └─────────────┘           ▲
                                         ▲                  │
                                         │                  │
                                  ┌──────┴──────┐   ┌───────┴──────┐
                                  │   change    │   │   Scanner    │
                                  │ detection   │   │   Python     │
                                  │  (webhook)  │   │   async      │
                                  └─────────────┘   └──────────────┘
```

| Layer | Technology |
|---|---|
| Backend | Node.js 20 + Fastify |
| Scanner | Python 3.13 + asyncio + dnspython + httpx |
| Frontend | React 18 + TypeScript 5 + Vite + Leaflet + React Router 6 |
| Database | PostgreSQL 16 + PostGIS 3.4 |
| Change detection | [Thedurancode/change](https://github.com/Thedurancode/change) |
| Uptime | Uptime Kuma |
| Reverse proxy | nginx alpine |
| Containers | Docker Compose |

### Pages

| Route | Purpose |
|---|---|
| `/` | Lander with postal-code "Find your data" lookup |
| `/map` | Full map, polygons, flow lines, party report card, referendum view |
| `/politicians` | Cards grid of every tracked politician, filterable by level/province/party/socials |
| `/politicians/:id` | Per-politician detail — socials, offices, terms, changes, and (federal MPs) a Parliament timeline sourced from openparliament.ca |
| `/coverage` | Honest coverage dashboard — every Canadian legislature with status of bills / Hansard / votes / committees layers, blockers flagged |
| `/blog` | Work-as-we-go updates, authored as MDX in `services/frontend/src/content/blog/*.mdx` |
| `/blog/:slug` | Individual post |

Federal MPs additionally get a lazily-mirrored **Parliament** tab backed by a local JSONB cache of openparliament.ca — 30-day TTL for the profile blob, 1-day TTL for their speeches+bills feed, coalesced per-politician so concurrent requests share one outbound call.

---

## Quick Start

```bash
# 1. Configure environment
cp .env.example .env
# edit .env — at minimum set DB_PASSWORD and WEBHOOK_SECRET

# 2. Download GeoLite2 databases
# Create a free MaxMind account and place these files in ./data/
#   - GeoLite2-City.mmdb
#   - GeoLite2-ASN.mmdb

# 3. Build and start
make up

# 4. Seed referendum organizations (first time only)
make seed

# 5. Ingest politicians — pick your coverage
docker compose run --rm scanner ingest-mps            # Federal MPs
docker compose run --rm scanner ingest-senators       # Senators
docker compose run --rm scanner ingest-legislatures   # All provincial + territorial
docker compose run --rm scanner ingest-all-councils   # Municipal councils
docker compose run --rm scanner fill-gaps             # Legislatures Open North doesn't cover

# 6. Run a scan
make scan

# 7. Open the map
open http://localhost
```

See [`docs/`](./docs) for deeper guides.

---

## CLI

```bash
# Ingest (roster)
sovpro ingest-mps                     # Federal MPs from Open North
sovpro ingest-senators                # 105 senators (sencanada.ca)
sovpro ingest-legislatures            # Every provincial / territorial legislature
sovpro ingest-all-councils            # Every municipal council Open North exposes
sovpro fill-gaps                      # Gap-fillers for legislatures Open North doesn't cover
sovpro seed-orgs                      # Seed referendum organizations

# Enrichment (personal sites + socials)
sovpro harvest-personal-socials       # Mine campaign sites for social handles
sovpro enrich-socials-all             # Wikidata → openparliament → Mastodon pipeline
sovpro resolve-openparliament-slugs   # Match federal MPs to openparliament.ca slugs

# Scan + maintenance
sovpro scan [--limit N]               # Scan websites (DNS, GeoIP, TLS, HTTP)
sovpro stats                          # Print sovereignty summary
sovpro refresh-views                  # Refresh PostGIS materialized views
```

Run `sovpro --help` for the complete list — there are ~40 subcommands including per-province ingesters and per-legislature gap-fillers.

---

## License

MIT. See [LICENSE](./LICENSE).

This project uses data from [Open North's Represent API](https://represent.opennorth.ca/) under the [Open Government License — Canada](https://open.canada.ca/en/open-government-licence-canada), [openparliament.ca](https://openparliament.ca) (federal MP profiles, speeches, and sponsored bills), and [MaxMind GeoLite2 databases](https://www.maxmind.com/).
