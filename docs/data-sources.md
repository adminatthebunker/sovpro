# Data Sources

## Politicians + infrastructure

| Source | Used for | License |
|--------|----------|---------|
| [Open North Represent](https://represent.opennorth.ca/) | MPs, MLAs, councils, constituency boundaries | Open Government License — Canada |
| [MaxMind GeoLite2](https://www.maxmind.com/en/geolite2/signup) | IP → country/city/lat/lng/ASN | GeoLite2 EULA (free with attribution) |
| [Thedurancode/change](https://github.com/Thedurancode/change) | Website content change detection | Per upstream |
| [Uptime Kuma](https://uptimekuma.org/) | Uptime monitoring | MIT |
| Hand-curated | Referendum organizations + their websites | n/a (public web) |

## Provincial bills + stage events (9 of 13 jurisdictions live)

| Jurisdiction | Primary source | Format | License |
|---|---|---|---|
| Nova Scotia | `data.novascotia.ca/resource/iz5x-dzyf.json` (Socrata) + `nslegislature.ca/legislative-business/bills-statutes/rss` | Socrata API + RSS + HTML | Open Government Licence — Nova Scotia |
| Ontario | `ola.org/en/legislative-business/bills/...?_format=json` (Drupal REST serializer) | JSON | Queen's Printer for Ontario |
| British Columbia | `lims.leg.bc.ca/graphql` + `lims.leg.bc.ca/pdms/bills/progress-of-bills/{session}` | GraphQL + JSON | BC Legislative Assembly |
| Quebec | `donneesquebec.ca/.../projets-de-loi.csv` + `assnat.qc.ca/fr/rss/SyndicationRSS-210.html` + detail HTML | CSV + RSS + HTML | CC-BY-NC-4.0 |
| Alberta | `assembly.ab.ca/assembly-business/assembly-dashboard?legl={L}&session={S}` | Server-rendered HTML (one-page dashboard) | Crown copyright (Alberta) |
| New Brunswick | `legnb.ca/en/legislation/bills/{legl}/{session}` + detail pages | HTML (list + detail) | Open Government Licence — New Brunswick |
| Newfoundland & Labrador | `assembly.nl.ca/HouseBusiness/Bills/ga{GA}session{S}/` | HTML table | Crown copyright (NL) |
| Northwest Territories | `ntassembly.ca/documents-proceedings/bills/{slug}` | Drupal 9 HTML | Crown copyright (NWT) |
| Nunavut | `assembly.nu.ca/bills-and-legislation` | Drupal 9 HTML view | Crown copyright (Nunavut) |

**Deferred:** Manitoba + Saskatchewan are PDF-only (bill status documents), awaiting a `pdfplumber`-based extraction tool. PEI is behind Radware ShieldSquare; Yukon behind Cloudflare Bot Management — both awaiting a Playwright-based browser automation track.

Per-jurisdiction probe history, endpoint findings, and module pointers live in [`research/`](research/) — one self-contained dossier per jurisdiction (see [`research/overview.md`](research/overview.md) for the shared schema log + probe hierarchy).

## Attribution required

The frontend footer credits Open North and MaxMind. Do not remove the attribution if you redistribute. For Quebec bills data, CC-BY-NC-4.0 requires attribution to Assemblée nationale du Québec and restricts commercial use.

## Refreshing GeoLite2

MaxMind ships database updates ~weekly. Use their `geoipupdate` tool or download manually:

```bash
# After downloading via your MaxMind account
mv ~/Downloads/GeoLite2-City.mmdb data/
mv ~/Downloads/GeoLite2-ASN.mmdb  data/
sovpro restart   # scanner reads the file at start
```

If the DBs are missing, the scanner still runs — `ip_country`/`ip_city`/`ip_asn` will simply be NULL, and most rows will end up in tier 6 (Unknown). That's safe but not useful.
