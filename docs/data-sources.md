# Data Sources

| Source | Used for | License |
|--------|----------|---------|
| [Open North Represent](https://represent.opennorth.ca/) | MPs, MLAs, councils, constituency boundaries | Open Government License — Canada |
| [MaxMind GeoLite2](https://www.maxmind.com/en/geolite2/signup) | IP → country/city/lat/lng/ASN | GeoLite2 EULA (free with attribution) |
| [Thedurancode/change](https://github.com/Thedurancode/change) | Website content change detection | Per upstream |
| [Uptime Kuma](https://uptimekuma.org/) | Uptime monitoring | MIT |
| Hand-curated | Referendum organizations + their websites | n/a (public web) |

## Attribution required

The frontend footer credits Open North and MaxMind. Do not remove the attribution if you redistribute.

## Refreshing GeoLite2

MaxMind ships database updates ~weekly. Use their `geoipupdate` tool or download manually:

```bash
# After downloading via your MaxMind account
mv ~/Downloads/GeoLite2-City.mmdb data/
mv ~/Downloads/GeoLite2-ASN.mmdb  data/
sovpro restart   # scanner reads the file at start
```

If the DBs are missing, the scanner still runs — `ip_country`/`ip_city`/`ip_asn` will simply be NULL, and most rows will end up in tier 6 (Unknown). That's safe but not useful.
