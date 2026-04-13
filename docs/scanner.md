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
| `SCANNER_USER_AGENT` | `SovereignWatchBot/1.0 (+...)` | Sent on every scan request |
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
