# Scanner

The scanner is a Python async tool that turns a list of URLs into rows in `infrastructure_scans`. It runs as an on-demand container (`scanner` profile) and as a long-running sidecar (`scanner-cron`).

## Pipeline (per website)

1. **DNS** — resolve A/AAAA, follow CNAME chain, fetch NS + MX.
2. **GeoIP** — look up the primary A record in MaxMind GeoLite2-City + GeoLite2-ASN.
3. **TLS** — open an SSL connection to port 443, parse the cert (issuer, subject, expiry).
4. **HTTP** — GET the URL, follow redirects, capture `Server` and `X-Powered-By` headers and the final URL.
5. **Classify** — `services/scanner/src/classify.py` decides:
   - `hosting_provider` (best guess from CNAME / ip_org / headers)
   - `cdn_detected` (Cloudflare/CloudFront/Akamai/Fastly/...)
   - `cms_detected` (WordPress/Drupal/Squarespace/...)
   - `sovereignty_tier` (1-6)
6. **Persist** — INSERT a row in `infrastructure_scans`. Compare with the previous scan and INSERT into `scan_changes` for any deltas.
7. **Bookkeeping** — UPDATE `websites.last_scanned_at` (and `last_changed_at` if anything moved).

After a batch finishes, `SELECT refresh_map_views()` re-materializes `map_politicians` + `map_organizations`.

## Tunables

| Env var | Default | Effect |
|---|---|---|
| `SCANNER_CONCURRENCY` | 16 | Parallel scans in flight |
| `SCANNER_HTTP_TIMEOUT` | 15 | Per-request HTTP timeout (s) |
| `SCANNER_DNS_TIMEOUT` | 5 | DNS resolver timeout (s) |
| `SCANNER_USER_AGENT` | `CanadianPoliticalDataBot/1.0 (+...)` | Sent on every scan request |
| `GEOIP_CITY_PATH` | `/data/GeoLite2-City.mmdb` | Geo DB |
| `GEOIP_ASN_PATH` | `/data/GeoLite2-ASN.mmdb` | ASN DB |

## Adding a new provider/CDN

Edit `services/scanner/src/classify.py`:
- `CANADIAN_PROVIDER_SUBSTRINGS` — adds tier-1 eligibility.
- `CDN_PATTERNS` — pattern → display label.
- `PROVIDER_PATTERNS` — pattern → display label.
- `CMS_HEADER_HINTS` — pattern → display label.

Re-run scans (`sovpro scan full`) to apply.

## Adding a new ingestion source

Each source is a row in `services/scanner/src/opennorth.py::SETS`. Add an `OpenNorthSet` and a wrapper in `__main__.py`. The pattern is:

```python
"my_source": OpenNorthSet(
    path="/representatives/<name>/",
    level="provincial",
    province="BC",
    office="MLA",
    boundary_set="<bc-set-name>",
    boundary_level="provincial",
),
```

## Provincial bills pipelines

In addition to scanning websites, the scanner ingests **bills and stage events** for every provincial / territorial legislature we cover. Each province is a bespoke pipeline because no two legislative websites share a backend; modules live under `services/scanner/src/legislative/`.

| Jurisdiction | Source | Module | Sponsor FK |
|---|---|---|---|
| NS | Socrata API + RSS + HTML | `ns_bills.py`, `ns_rss.py`, `ns_bill_pages.py`, `ns_bill_parse.py` | text slug |
| ON | Drupal `?_format=json` | `on_bills.py` | text slug |
| BC | LIMS GraphQL + PDMS JSON | `bc_bills.py` | integer FK |
| QC | donneesquebec CSV + RSS + HTML | `qc_bills.py`, `qc_mnas.py` | integer FK |
| AB | Single-page Assembly Dashboard HTML | `ab_bills.py`, `ab_mlas.py` | zero-padded text FK |
| NB | legnb.ca list + detail HTML | `nb_bills.py` | name-based |
| NL | `/HouseBusiness/Bills/ga{GA}session{S}/` HTML table | `nl_bills.py` | (sponsor not exposed) |
| NT | ntassembly.ca Drupal 9 list + detail | `nt_bills.py` | (consensus gov't) |
| NU | assembly.nu.ca Drupal 9 single-view table | `nu_bills.py` | (consensus gov't) |

Run any with `docker compose run --rm scanner ingest-<province>-bills`. See `sovpro --help` for the full list of flags (most support `--all-sessions` / `--all-sessions-in-legislature` for historical backfill). Full per-jurisdiction research + build notes live in [`docs/research/`](research/) (one file per jurisdiction; [`docs/research/overview.md`](research/overview.md) for cross-cutting context).

### Schema

Four tables under `db/migrations/0006_legislative_bills.sql` plus extensions 0007-0013:

- `legislative_sessions (level, province_territory, parliament_number, session_number, …)`
- `bills (session_id, bill_number, title, status, source_id, source_system, raw jsonb, …)`
- `bill_events (bill_id, stage, stage_label, event_date, event_type, outcome, committee_name, …)` — `UNIQUE NULLS NOT DISTINCT` constraint `bill_events_uniq` for idempotent stage writes
- `bill_sponsors (bill_id, politician_id, sponsor_slug, sponsor_name_raw, role, source_system)` — politician_id nullable; a generic `sponsor_resolver` fills slug/name-based rows after ingestion

`politicians` gets one column per jurisdiction's upstream ID scheme (`nslegislature_slug`, `ola_slug`, `lims_member_id`, `qc_assnat_id`, `ab_assembly_mid`).

## Socials audit + tiered backfill

`politician_socials` holds one row per (politician, platform, handle). Discovery is organised as a **three-tier funnel** so LLM tokens are only spent on the residual after two free tiers have run. Every row carries provenance (`source`, `confidence`, `evidence_url`, `flagged_low_confidence`) via migration `0026_politician_socials_provenance.sql`. Operators run this through `docs/runbooks/socials-audit.md`.

| Tier | Command | Cost | Source values | Auto-promote at |
|---|---|---|---|---|
| — | `audit-socials` | free SQL | refreshes `v_socials_missing` view + writes CSV | — |
| 1 | `enrich-socials-all` (+ `harvest-personal-socials`) | free HTTP | `wikidata`, `openparliament`, `masto_host`, `personal_site`, `html_regex`, `muni_scrape`, `gap_filler` | `confidence=1.0` (trusted feeds) |
| 2 | `probe-missing-socials --platform {bluesky,twitter,facebook,instagram,youtube,threads}` | free HTTP | `pattern_probe` | `confidence >= 0.70` |
| 3 | `agent-missing-socials` | Sonnet + `web_search_20250305` | `agent_sonnet` | `confidence >= 0.85` |
| — | `verify-socials --limit N --stale-hours H` | free HTTP HEAD+GET | — | flips `is_live` + writes `social_dead` change rows |

### Design notes

- **Provenance is required.** `upsert_social()` takes `source=` as a mandatory kwarg. Helpers (`enrich._attach_socials`, `gap_fillers.shared.attach_socials`) accept `source=` too and default to safe values (`html_regex`, `gap_filler`). Adding a new discovery path means picking a source string — add it to `_TRUSTED_SOURCES` in `socials.py` if it's upstream-authoritative; otherwise pick a probe/agent threshold.
- **Confidence cannot regress.** The `ON CONFLICT` clause in `upsert_social` uses `GREATEST(confidence, EXCLUDED.confidence)` so a low-confidence probe hit can never demote a Tier-1 row for the same (politician, platform, handle).
- **False-positive gate.** Tier-2 scoring caps pure-name matches at `_NAME_ONLY_CAP = 0.55` when the profile bio has no Canadian-politics keyword. This sends ambiguous matches (common names, celebrity collisions) to the flagged queue rather than auto-publishing. Caught `dawnarnold.bsky.social` (a Chicago theatre director, not the NB senator) during the initial rollout.
- **Bluesky pacing override.** `socials_probe.py::_HOST_GAP_OVERRIDES` sets `public.api.bsky.app = 0.10s` (10 QPS), well under the documented ~300 QPS/IP cap but enough to probe ~1,700 missing handles in ~10 minutes. All other hosts use the polite 1s/host default.
- **Tier-3 is gated.** `agent-missing-socials` aborts with a log line if `ANTHROPIC_API_KEY` is unset; the public build works without a key. Hard caps: `--max-batches 20` (default), `--batch-size 10` (max 25). Rough cost: ~$1-2 for a full residual sweep at current model pricing.

### Admin UI

`/admin/socials` (see `services/frontend/src/pages/admin/AdminSocialsReview.tsx`) shows coverage by source + platform and surfaces `flagged_low_confidence=true` rows with approve / reject buttons. API endpoints live under `/api/v1/admin/socials/*` in `services/api/src/routes/admin.ts`. All three Click commands are also in the admin command catalog — operators can queue `probe-missing-socials --platform twitter --limit 500` from the jobs form without shelling into the box.
