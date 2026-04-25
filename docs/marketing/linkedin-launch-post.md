# LinkedIn launch post

Draft copy for announcing the nationwide dataset expansion.
Numbers auto-matched to the production `/api/v1/stats` endpoint as of 2026-04-14 — refresh before posting if significant time has passed.

**LinkedIn does not render markdown.** The block below is plain text — arrows (→) and bullets (•) are unicode characters and render fine, but there is no bold/italic. For emphasis on LinkedIn, use line breaks or ALL CAPS. (If you want unicode-bold like 𝗧𝗵𝗶𝘀, paste into a bold-text converter before posting.)

---

58% of Canadian politicians host their websites outside Canada.

We spent the last few weeks building a civic transparency tool that maps exactly that — where every elected Canadian politician's personal and campaign website actually lives.

The result:

→ 1,819 politicians tracked across all 13 provinces and territories
→ 343 federal MPs + 97 senators + 808 provincial legislators + 571 municipal councillors
→ 691 with their hosting fully classified (DNS, GeoIP, TLS, provider)

And the numbers are stark:

• Only 52 use a truly Canadian-owned hosting company
• 222 host on "Canadian soil" — but via AWS Canada, Azure, or Shopify (all US-owned)
• 58% are hosted outside Canada entirely
• 3 companies hold 53% of all Canadian political web data
• The #1 offshore destination? Council Bluffs, Iowa — home to Google, Meta, and Microsoft data centres

Why this matters: hosting location dictates which country's legal process applies to that data. A Canadian MP's constituent communications living on AWS Canada (US-owned) are still subject to US discovery orders.

We've also mapped 2,077 constituency offices, 408 committee memberships, and 2,130 social-media handles across 9 platforms — all updated continuously.

Explore the live map: canadianpoliticaldata.ca

Built by The Bunker Operations. Open data, open source, no tracking.

#CivicTech #DataSovereignty #CanadianPolitics #OpenData #Privacy

---

## Variations on file

### Alternate hook: Council Bluffs angle

> The #1 city hosting Canadian politicians' websites isn't Toronto. It isn't Ottawa. It's Council Bluffs, Iowa.
>
> [rest of post]

### Shorter version (for X/Bluesky, ~280 chars)

> 58% of Canadian politicians host their websites outside Canada. Only 52 of 691 use a truly Canadian-owned host. The rest? AWS Canada, Azure, Shopify (all US-owned), or Council Bluffs, Iowa. Full map: canadianpoliticaldata.ca

## Numbers to refresh before posting

Query `/api/v1/stats` and update any of the below that have drifted:

- `politicians.total` → "1,819 politicians tracked"
- `politicians_by_level` → federal / provincial / municipal split
- Sum of `politicians.sovereignty` values → "691 with their hosting fully classified"
- `politicians.pct_not_canadian` → "58% are hosted outside Canada entirely"
- `politicians.sovereignty.tier_1` → "52 use a truly Canadian-owned hosting company"
- `politicians.sovereignty.tier_2` → "222 host on Canadian soil"
- `top_providers[0..2]` + concentration pct → "3 companies hold 53%"
- `top_foreign_locations[0]` → "Council Bluffs, Iowa"
- `dataset_depth.offices_mapped` → "2,077 constituency offices"
- `dataset_depth.committees_tracked` → "408 committee memberships"
- `dataset_depth.social_handles_total` → "2,130 social-media handles"
