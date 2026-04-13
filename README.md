# SovereignWatch

> A civic transparency tool that answers one question: **where do Canadian politicians and political organizations actually store their website data?**

SovereignWatch scans the websites of every federal MP, every Alberta MLA, Edmonton and Calgary city councillors, and the organizations driving Alberta's October 19, 2026 independence referendum — and plots the result on an interactive map. Constituency polygons are drawn, then connected by lines to server pins, overwhelmingly clustered in the United States.

Change detection monitors all tracked sites and emits alerts when hosting moves.

---

## The Three Target Groups

1. **All federal MPs** (~338 politicians, ~500+ websites)
2. **All Alberta politicians** — MLAs (~87), Edmonton council (~13), Calgary council (~15)
3. **Alberta referendum organizations** — both sides of the independence question plus the UCP's nine referendum questions

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
| Frontend | React 18 + TypeScript 5 + Vite + Leaflet |
| Database | PostgreSQL 16 + PostGIS 3.4 |
| Change detection | [Thedurancode/change](https://github.com/Thedurancode/change) |
| Uptime | Uptime Kuma |
| Reverse proxy | nginx alpine |
| Containers | Docker Compose |

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

# 4. Seed politicians + organizations (first time only)
make seed

# 5. Run a scan
make scan

# 6. Open the map
open http://localhost
```

See [`docs/`](./docs) for deeper guides.

---

## CLI

```bash
sovpro ingest mps          # Fetch federal MPs from Open North
sovpro ingest mlas         # Fetch Alberta MLAs
sovpro ingest councils     # Fetch Edmonton + Calgary councils
sovpro seed orgs           # Seed referendum organizations
sovpro scan [--limit N]    # Scan websites
sovpro stats               # Print sovereignty summary
sovpro refresh-views       # Refresh PostGIS materialized views
```

---

## License

MIT. See [LICENSE](./LICENSE).

This project uses data from [Open North's Represent API](https://represent.opennorth.ca/) under the [Open Government License — Canada](https://open.canada.ca/en/open-government-licence-canada) and [MaxMind GeoLite2 databases](https://www.maxmind.com/).
