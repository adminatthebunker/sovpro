# Canadian Political Data

> **The most accessible political data website in Canada — open source to the core.**
>
> Who represents you, what they've said, how they've voted, and where their infrastructure lives. Across every level of government, every province and territory, as far back as the digital record goes.

Canadian Political Data tracks ~2,800 elected officials across every level of Canadian government — every currently-sitting federal MP and senator (plus historical MPs back to 1994 pulled in via the Hansard pipeline), every provincial and territorial legislature, and municipal councils from coast to coast — plus the organizations driving Alberta's October 19, 2026 independence referendum.

The public site is **free, requires no account, and never will**. Enter a postal code and see your own MP, MLA, and councillors — with their parliamentary record (mirrored from [openparliament.ca](https://openparliament.ca) for federal MPs), their social handles, and — as one lens among many — where their websites are hosted.

---

## Why this exists

**Access to information is a right.** Canadians shouldn't have to know which government website to dig through, or what a "Hansard" is, or which province publishes bills as PDFs and which as JSON, just to find out what their elected representatives have said and done.

This project takes that seriously. Three principles fall out of it:

1. **Free and frictionless for the public.** No accounts, no paywalls, no dark patterns on the public site. Postal-code lookup is the front door. Search is the next one.
2. **Open source to the core.** Every ingester, every schema decision, every upstream quirk and blocker lives in this repo under MIT. Per-jurisdiction research dossiers under [`docs/research/`](docs/research/) document exactly how each legislature's data was sourced, what's reliable, and what's not. If we got something wrong, you can see where.
3. **Honest about gaps.** Coverage holes are surfaced on the public [`/coverage`](https://canadianpoliticaldata.ca/coverage) dashboard rather than hidden. Four provincial bills pipelines are still blocked (MB/SK PDFs; PEI/YT WAFs); the dashboard says so.

The project is **not apolitical**. It's rooted in democratic values, civic transparency, and progressive stances on access to information. See [`docs/goals.md`](docs/goals.md) for the full framing.

---

## Coverage

| Level | Count | Source |
|---|---:|---|
| **Federal** | 1,424 | Every currently-sitting MP + senator, plus historical MPs back to 1994 (pulled in via the Hansard pipeline) |
| **Provincial / territorial** | 807 | Every MLA / MPP / MNA / MHA across all 10 provinces and 3 territories |
| **Municipal** | 571 | Council members across major cities — ON, QC, BC, AB, and the Atlantic provinces |
| **Referendum organizations** | 20 | Both sides of Alberta's Oct 19, 2026 independence question plus the UCP's nine referendum questions |
| **Websites tracked** | ~2,350 | Personal sites, campaign sites, official handles |
| **Provincial bills** | ~18,800 | NS + ON + BC + QC + AB + NB + NL + NT + NU (9 of 13 sub-national legislatures). Federal bills aren't in the `bills` table — they surface via the openparliament.ca mirror on each MP's detail page |
| **Bill stage events** | ~21,300 | Full Westminster progression per bill (1R / 2R / Committee / 3R / Royal Assent ± `comes_into_force` for AB) |
| **Hansard speeches** | ~1.7M | Federal (1994+, via openparliament.ca) + AB (assembly.ab.ca) + BC (hansard-bc), speaker-resolved including presiding-officer attributions |
| **Speech chunks (semantic-search ready)** | ~2.1M | ~74% embedded with Qwen3-Embedding-0.6B (1024-dim, multilingual EN/FR), HNSW-indexed in pgvector — historical backfill still running, embedding rate trailing chunk generation |

Coverage is built jurisdiction-by-jurisdiction via dedicated ingesters — Open North where available, then per-legislature scrapers for everything else. Bills are ingested from a mix of Socrata APIs, Drupal JSON, GraphQL backends, official CSVs, and server-rendered HTML. Each province is its own beast, and each one has a self-contained dossier in [`docs/research/`](docs/research/) (plus [`docs/research/overview.md`](docs/research/overview.md) for the cross-cutting probe hierarchy and schema log).

---

## What you can do today

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

## What's coming next

**Semantic search over Hansard speeches and recorded votes.** The moment you can finally search what your MP actually said — in plain language, not parliamentary jargon, across English and French sources — is the next major milestone.

The data and inference layer are shipped:

- pgvector 0.8.2 in Postgres with HNSW indexes (`hnsw.iterative_scan = relaxed_order`, `ef_search = 200`) so filtered semantic queries — e.g. "BC speeches about housing" — actually return rows
- **Hugging Face TEI** (Text Embeddings Inference) container serving **Qwen3-Embedding-0.6B** (1024-dim, multilingual, fp16) on a local NVIDIA RTX 4050 — ~75 chunks/sec pure GPU, **50.9 chunks/sec end-to-end** through the scanner's batched-UNNEST writes. Cutover from BGE-M3 + BGE-reranker landed 2026-04-19; eval comparison lives in [`services/embed/eval/REPORT.md`](services/embed/eval/REPORT.md). No paid API in the critical path.
- ~1.7M speeches ingested (federal 1994+ via openparliament.ca mirror, AB via assembly.ab.ca, BC via hansard-bc), ~2.1M chunks generated, **~1.6M (~74%) embedded** — historical backfill + embedding are still running concurrently, so chunk generation is ahead of embedding and both numbers climb daily
- `speeches`, `speech_chunks`, `speech_references`, `jurisdiction_sources`, `correction_submissions` tables live; presiding-officer speech attributions resolved to the politician occupying the chair at the time

Remaining for v1: hybrid (dense + Postgres tsvector) retrieval API, public search UI, provincial Hansard expansion beyond AB/BC, and corrections-inbox SMTP ingest. See [`docs/plans/semantic-layer.md`](docs/plans/semantic-layer.md) and [`docs/plans/search-features-handoff.md`](docs/plans/search-features-handoff.md) for the phased rollout.

---

## Architecture

```
┌────────────┐    ┌─────────┐     ┌─────────────┐    ┌──────────────┐
│  Frontend  │◄──►│  nginx  │◄───►│  API        │◄──►│  PostgreSQL  │
│  React/TS  │    │  proxy  │     │  Fastify    │    │  + PostGIS   │
│  Leaflet   │    └─────────┘     │  Node 20    │    │  + pgvector  │
└────────────┘                    └─────┬───────┘    └──────┬───────┘
                                        │                   ▲
                ┌───────────────────────┴────┐              │
                │                            │              │
         ┌──────▼───────┐            ┌───────▼────────┐     │
         │   change     │            │    Scanner     │─────┤
         │  detection   │            │    Python      │     │
         │  (webhook)   │            │    asyncio     │     │
         └──────────────┘            └───────┬────────┘     │
                                             │ HTTP         │
                                     ┌───────▼────────────┐ │
                                     │  TEI               │ │
                                     │  Qwen3-Embedding-  │ │
                                     │  0.6B (GPU fp16)   │ │
                                     └────────────────────┘ │
                                                            │
                                     ┌─────────────────┐    │
                                     │ scanner-jobs    │────┘
                                     │ admin worker    │
                                     └─────────────────┘
```

| Layer | Technology |
|---|---|
| Backend | Node.js 20 + Fastify + zod |
| Scanner | Python 3.13 + asyncio + dnspython + httpx |
| Embed service | Hugging Face Text Embeddings Inference (TEI) serving `Qwen/Qwen3-Embedding-0.6B` on CUDA fp16 |
| Frontend | React 18 + TypeScript 5 + Vite + Leaflet + React Router 6 + MDX |
| Database | PostgreSQL 16 + PostGIS 3.4 + pgvector 0.8.2 + unaccent |
| Change detection | [Thedurancode/change](https://github.com/Thedurancode/change) |
| Uptime | Uptime Kuma |
| Reverse proxy | nginx alpine |
| Containers | Docker Compose, single host, Pangolin tunnel to public |

See [`docs/architecture.md`](docs/architecture.md) for service-by-service detail.

---

## Quick Start

```bash
# 1. Configure environment
cp .env.example .env
# edit .env — at minimum set DB_PASSWORD, WEBHOOK_SECRET, JWT_SECRET, and SMTP

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

# 7. Open the site
open http://localhost
```

See [`docs/`](./docs) for deeper guides — including [`docs/operations.md`](docs/operations.md) for day-to-day ops and [`docs/scanner.md`](docs/scanner.md) for the full ingestion playbook.

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

# Legislative + semantic
sovpro ingest-federal-hansard         # Federal Hansard speeches (1994+, EN+FR)
sovpro ingest-ab-hansard              # Alberta Hansard speeches
sovpro ingest-bc-hansard              # BC Hansard speeches
sovpro chunk-speeches                 # Split speeches into embedding-ready chunks
sovpro embed-speech-chunks            # Embed pending chunks via TEI (Qwen3-Embedding-0.6B)
sovpro refresh-coverage-stats         # Recompute the public /coverage dashboard

# Scan + maintenance
sovpro scan [--limit N]               # Scan websites (DNS, GeoIP, TLS, HTTP)
sovpro stats                          # Print sovereignty summary
sovpro refresh-views                  # Refresh PostGIS materialized views
```

Run `sovpro --help` for the complete list — there are ~95 subcommands including per-province ingesters, per-legislature gap-fillers, and the full Hansard → chunk → embed → resolve-speakers pipeline.

---

## Audience and roadmap

The project serves two audiences in sequence:

- **Engaged citizens (free, public, forever).** Postal-code lookup, per-politician pages, the map, the change feed, and — soon — semantic search over Hansard. No account ever required.
- **Lobbyists, journalists, academics, advocacy orgs (paid API tiers, future).** Bulk export (CSV/Parquet), programmatic semantic search, scheduled topic alerts, "compare A vs. B" tooling. Funds the public side.

Funding model: free public UI + paid API tiers for institutional users + grant funding for long-term sustainability. The public side stays free forever. See [`docs/goals.md`](docs/goals.md) for non-goals and what's explicitly deferred.

---

## Contributing

This is an in-the-open project, and contributions — especially research dossiers for the four remaining bills pipelines (MB, SK, PE, YT), Hansard pipelines for non-federal legislatures, and corrections to existing data — are welcome.

Before opening a PR for a new ingestion pipeline, please read the **research-handoff protocol** in [`docs/research/overview.md`](docs/research/overview.md). Short version: pause and document the upstream endpoints first; the time saved by skipping that step is almost always lost rebuilding the scraper.

Read [`CLAUDE.md`](CLAUDE.md) for project-level conventions (jurisdiction-specific ID columns, the probe hierarchy, persistent rate-limit caching, idempotent Click subcommands).

---

## License + attribution

MIT. See [LICENSE](./LICENSE).

This project uses data from:

- [Open North's Represent API](https://represent.opennorth.ca/) under the [Open Government License — Canada](https://open.canada.ca/en/open-government-licence-canada)
- [openparliament.ca](https://openparliament.ca) (federal MP profiles, speeches, and sponsored bills)
- [MaxMind GeoLite2 databases](https://www.maxmind.com/) (IP geolocation)
- Per-jurisdiction provincial sources documented in [`docs/data-sources.md`](docs/data-sources.md), each under its respective open-government licence (CC-BY-NC-4.0 for Quebec; OGL variants elsewhere; Crown copyright where no open licence is published)

Attribution to Open North, openparliament.ca, and MaxMind is preserved in the public-site footer. Don't remove it if you redistribute.
