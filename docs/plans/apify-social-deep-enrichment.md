# Apify Social Deep Enrichment + Private-Domain Discovery

Status: research / proposal — NOT IMPLEMENTED
Author: research pass, April 2026
Audience: project owner (review + approve), then implementation team

---

## 1. Context

Today the dataset is wide but shallow:

- **1,819 politicians** tracked (440 federal, 808 provincial across 13 P/Ts, 571 municipal).
- **675 social rows** in `politician_socials` across nine platforms — but each row is just a handle, URL, and liveness flag. We have no post text, no posting cadence, no engagement history, no record of when an account was renamed or quietly abandoned.
- The scanner pipeline (`services/scanner/`) classifies *known* websites for hosting sovereignty, but it cannot find domains a politician owns and has never publicly disclosed.

Both gaps matter for civic transparency: a politician's actual digital footprint is bigger than what they advertise, and rhetoric drift over time is exactly the kind of signal the public interest argument rests on.

This doc proposes **on-demand deep enrichment** — an admin in the panel clicks a button on a politician (or a batch), Apify actors run, and structured post/profile/domain data lands in Postgres for analysis and change-tracking.

---

## 2. What "on-demand" means in practice

```
Admin panel button
   -> POST /api/enrich  { politician_id, scope: ["twitter","instagram", ...] }
   -> enqueue job rows in apify_jobs
   -> worker picks up, calls Apify REST (run-sync-get-dataset-items
      for short jobs, async + webhook for long ones)
   -> on completion: parse dataset items, upsert into social_posts /
      politician_domains, update apify_jobs.status + cost_usd
   -> politician_changes gets a 'social_enriched' row
```

Two trigger modes:

1. **Single politician, manual** — admin clicks "Deep enrich". Used for spot-checks, journalists' requests, candidate vetting.
2. **Batched, scheduled** — nightly cron picks N politicians (round-robin so everyone refreshes ~quarterly) and queues them at low concurrency.

No real-time subscriptions. No background polling of every account. Cost and ToS pressure both push toward sparse, intentional pulls.

---

## 3. Scope

### In scope (this proposal)

- Pulling **public** post timelines + profile metadata for known handles.
- Storing structured posts (text + engagement + URL + timestamp) for analysis.
- Reverse-WHOIS lookup by known politician email/name to discover undisclosed domains.
- Admin UI to trigger + monitor jobs.
- Cost accounting per job and per politician.

### Out of scope (explicitly)

- Content generation, summarization with LLMs (separate proposal).
- Automated engagement (likes, replies, follows) — never.
- Mass historical archival ("every tweet they ever made") — too expensive, mostly unnecessary; we'll cap depth.
- Private/DM/group data — never, regardless of platform.
- Real-time streaming — daily/weekly is enough for transparency work.
- Network-graph scraping (followers, mutuals) — separate question, much bigger ToS surface.

---

## 4. Per-platform actor picks

Pricing as of April 2026. All actors verified at the URLs cited. **Recommend column** = our pick for first integration.

| Platform | Actor (slug) | Pricing | Recommend? | Notes |
|---|---|---|---|---|
| Twitter / X | [`apidojo/tweet-scraper`](https://apify.com/apidojo/tweet-scraper) (Tweet Scraper V2) | $0.40 / 1,000 tweets, pay-per-result | **Yes** | 30–80 tweets/sec, accepts `twitterHandles`, supports date filters. Min 50 tweets/query. 1 concurrent run. |
| Twitter / X (alt) | [`apidojo/twitter-scraper-lite`](https://apify.com/apidojo/twitter-scraper-lite) | Event-based: $0.016/query + $0.0004–$0.002/item | Backup | Cheaper at low volumes per handle (~40 tweets free per query). Use if cost becomes a concern. |
| Twitter / X (cheapest) | [`kaitoeasyapi/twitter-x-data-tweet-scraper-pay-per-result-cheapest`](https://apify.com/kaitoeasyapi/twitter-x-data-tweet-scraper-pay-per-result-cheapest/api) | $0.25 / 1,000 tweets | Watch | Cheaper, but younger actor — re-evaluate after 6 months. |
| Facebook (pages) | [`apify/facebook-pages-scraper`](https://apify.com/apify/facebook-pages-scraper) | $6.60 / 1,000 pages ($0.0066 each) | **Yes (page metadata only)** | Returns page bio, follower count, categories, contact info — NOT post text. |
| Facebook (posts) | [`apify/facebook-posts-scraper`](https://apify.com/apify/facebook-posts-scraper) | ~$9.99/mo rental + run cost | Cautious | Meta ToS hostile to scraping; use page-metadata only for now, defer post text until legal review. |
| Instagram | [`apify/instagram-scraper`](https://apify.com/apify/instagram-scraper) | $1.50 / 1,000 results | **Yes** | Single actor handles profile + posts + hashtags; pay-per-result. Public data only. |
| TikTok | [`clockworks/tiktok-scraper`](https://apify.com/clockworks/tiktok-scraper) | from $1.70 / 1,000 results, PPE | **Yes** | Long-running, well-maintained (157k users). Returns video metadata + captions + engagement. |
| TikTok (alt) | [`apidojo/tiktok-scraper`](https://apify.com/apidojo/tiktok-scraper) | $0.30 / 1,000 posts | Backup | Cheaper but newer; trial both on a few politicians and pick. |
| YouTube | [`streamers/youtube-scraper`](https://apify.com/streamers/youtube-scraper) | $5 / 1,000 videos | Maybe | Apify avoids YouTube Data API v3 quotas, but v3 is **free** within 10k units/day — use the official API instead. See §5. |
| LinkedIn | [`curious_coder/linkedin-profile-scraper`](https://apify.com/curious_coder/linkedin-profile-scraper) | $4 / 1,000 profiles | **Defer** | Requires LinkedIn cookies. Direct ToS violation. 300–500 profiles/day account limit. Hold until legal sign-off. |
| Bluesky | none — use AT Protocol directly | free | **Yes (no Apify)** | `https://public.api.bsky.app/xrpc/app.bsky.feed.getAuthorFeed?actor={handle}` is unauthenticated, "generous" rate limits. See §5. |
| Mastodon | none — use instance API | free | **Yes (no Apify)** | Public timeline endpoints work unauthenticated; ~300 req/5min per instance. Iterate by `account_id`. |
| Threads | [`apify/threads-profile-api-scraper`](https://apify.com/apify/threads-profile-api-scraper) | $5 / 1,000 profiles | Maybe | Smaller footprint in Canadian politics so far; trial on the handful that exist before committing. |

### Selected first-pass set (pilot)

1. **Twitter** via `apidojo/tweet-scraper` — biggest signal-to-cost ratio.
2. **Bluesky** via direct AT Protocol — free, no vendor risk.
3. **Instagram** via `apify/instagram-scraper` — 2nd most common politician platform here.
4. **Mastodon** via direct instance API — free.

Add Facebook page metadata, TikTok, Threads in phase 2 once the pipeline is proven. Defer LinkedIn entirely until counsel weighs in.

---

## 5. Alternatives to Apify (where they win)

- **Bluesky** — Apify is overkill. The public AT Protocol AppView is free, unauthenticated, and the docs explicitly say "generous rate limits, contact us if you hit one." Use a thin Python client. ([rate-limit docs](https://docs.bsky.app/docs/advanced-guides/rate-limits))
- **Mastodon** — Same story. Public timelines require no auth; per-instance limits ~300 req / 5 min for authenticated, lower for unauthenticated, but plenty for our use. ([rate-limit docs](https://docs.joinmastodon.org/api/rate-limits/))
- **YouTube Data API v3** — Free up to 10,000 units/day; a channel `uploads` playlist + `videos.list` pulls a handful of units per politician. For 1,800 politicians refreshed quarterly, we are nowhere near the quota. **Prefer the official API; skip Apify for YouTube.**
- **Twitter (X) official API** — $0.005 per post read on the new Pay-Per-Use tier (Feb 2026 pricing reset), capped at 2M reads/month. ([X API pricing 2026](https://postproxy.dev/blog/x-api-pricing-2026/)) That's ~$0.50 per 100 tweets vs Apify's ~$0.04. Apify is **10x cheaper** here — stay with Apify.
- **Meta Graph API for Pages** — viable for politicians who *administer* their own pages and grant us a token, but we are an outside observer; not usable at scale. Fall back to Apify or skip.

---

## 6. Private-website / undisclosed-domain discovery

Goal: given a politician's known personal email and full name, return the list of domains registered to them. This surfaces personal blogs, side-businesses, dormant campaign sites, etc., which can then be fed into the existing scanner pipeline.

### Tooling

| Service | Pricing | Notes |
|---|---|---|
| [Whoxy Reverse WHOIS](https://www.whoxy.com/reverse-whois/) | $10 per 1,000 queries (volume discounts to $0.004/query at 1M); free credits on signup; **no charge for empty results** | Searches 682M domains by registrant name, email, or company. Best fit for our scale. |
| [WhoisXML API Reverse WHOIS](https://reverse-whois.whoisxmlapi.com/) | Annual subscription, opaque tiered pricing (contact sales) | Larger historical corpus (25.5B records). Likely overkill unless we go enterprise. |
| DomainTools Iris | Enterprise-only ($$$$) | Out of scope — too expensive for civic-transparency work. |

**Recommendation:** start with Whoxy. $10 covers 1,000 lookups; even if we run *every* politician twice (by name + by email) that is 3,600 queries = ~$36. Cheap. Switch only if we hit data-quality gaps.

### Inputs we need

- Politician personal email (collected manually or scraped from existing socials' bio links — not all will have one).
- Full legal name (already in DB).
- Known nicknames / former names (manual enrichment).

### Legal / ethical posture

WHOIS is public by design, but surfacing an undisclosed personal domain is an editorial act. The reverse-WHOIS specifics — `verified_by_admin` defaults, frontend-only-renders-verified, family-member filter requirement — are documented in the governance doc cross-linked in §10. The data-model defaults (`verified_by_admin = false`, `verified_at`, `notes`) match those policy requirements.

---

## 7. Data model changes

New tables (PostGIS roles unchanged: `sw` owns, `sovereignwatch` db).

```sql
-- Per-post records, polymorphic across platforms.
CREATE TABLE social_posts (
  id              BIGSERIAL PRIMARY KEY,
  politician_id   INT NOT NULL REFERENCES politicians(id) ON DELETE CASCADE,
  platform        TEXT NOT NULL,            -- 'twitter','instagram','tiktok',...
  post_id         TEXT NOT NULL,            -- platform-native ID
  posted_at       TIMESTAMPTZ,
  text            TEXT,                     -- caption / tweet text / video description
  url             TEXT,
  media_urls      TEXT[],
  engagement      JSONB,                    -- {likes,replies,reposts,views,...}
  raw             JSONB,                    -- full actor output for forensic use
  scraped_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  apify_job_id    BIGINT REFERENCES apify_jobs(id),
  UNIQUE (platform, post_id)
);
CREATE INDEX ON social_posts (politician_id, posted_at DESC);
CREATE INDEX ON social_posts USING GIN (engagement);

-- Domains discovered via reverse-WHOIS (or other means later).
CREATE TABLE politician_domains (
  id                 BIGSERIAL PRIMARY KEY,
  politician_id      INT NOT NULL REFERENCES politicians(id) ON DELETE CASCADE,
  domain             TEXT NOT NULL,
  source             TEXT NOT NULL,         -- 'whois','manual','scraped_bio'
  registrant_match   TEXT,                  -- which input matched: 'email','name'
  discovered_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  verified_by_admin  BOOLEAN NOT NULL DEFAULT false,
  verified_at        TIMESTAMPTZ,
  notes              TEXT,
  UNIQUE (politician_id, domain)
);

-- One row per Apify (or Whoxy) job for cost + audit.
CREATE TABLE apify_jobs (
  id              BIGSERIAL PRIMARY KEY,
  actor           TEXT NOT NULL,            -- 'apidojo/tweet-scraper', 'whoxy/reverse-whois', etc.
  input           JSONB NOT NULL,
  politician_id   INT REFERENCES politicians(id) ON DELETE SET NULL,
  status          TEXT NOT NULL,            -- 'queued','running','succeeded','failed','timed_out'
  started_at      TIMESTAMPTZ,
  finished_at     TIMESTAMPTZ,
  apify_run_id    TEXT,
  dataset_id      TEXT,
  cost_usd        NUMERIC(10,4),
  result_count    INT,
  error           TEXT,
  triggered_by    TEXT                      -- admin user id / 'cron'
);
CREATE INDEX ON apify_jobs (politician_id, started_at DESC);
CREATE INDEX ON apify_jobs (status) WHERE status IN ('queued','running');
```

Add to existing `politician_changes` enum: `social_enriched`, `domain_discovered`, `domain_verified`.

---

## 8. Apify integration primitives

Auth: a single `APIFY_API_TOKEN` env var. All requests go to `https://api.apify.com/v2/`.

Two execution paths:

1. **Sync (short jobs, < 5 min):** `POST /v2/acts/{actor}/run-sync-get-dataset-items?token=...` with the input as the body. Returns dataset items inline. Good for: single-politician profile lookups, small handle batches. Hard 300s timeout. ([docs](https://docs.apify.com/api/v2/act-run-sync-get-dataset-items-post))
2. **Async + webhook (anything bigger):** `POST /v2/acts/{actor}/runs` to start, register a webhook that hits our `/api/apify/webhook` on completion, then we fetch the dataset via `GET /v2/datasets/{id}/items`. Required for batched jobs.

Recommended: use the official [`apify-client` Python package](https://docs.apify.com/api/client/python/docs) — handles 429/5xx retries with exponential backoff, both sync and async interfaces. Lives in the existing `services/scanner/` pyproject.

Cost reporting: poll `GET /v2/actor-runs/{id}` after completion to read `usageTotalUsd` and persist into `apify_jobs.cost_usd`. Apify is transparent about unit costs; this lets us trend spend by platform and per politician.

---

## 9. Implementation sequence

**Phase 0 — Plumbing (1–2 days)**
- Create the three new tables.
- Add `APIFY_API_TOKEN` and `WHOXY_API_KEY` to env / docker-compose secrets.
- Add `apify-client` and a thin `whoxy` HTTP wrapper to `services/scanner/`.

**Phase 1 — Twitter pilot (2–3 days)**
- Build a single worker that, given a politician_id, looks up their Twitter handle from `politician_socials`, calls `apidojo/tweet-scraper` for the last 90 days, upserts into `social_posts`.
- Admin panel: "Deep enrich (Twitter)" button on the politician detail view.
- Verify on 5 hand-picked politicians (1 federal MP, 1 senator, 1 premier, 1 mayor, 1 backbench MLA) — see §11.

**Phase 2 — Free platforms (2 days)**
- Bluesky direct AT Protocol client. No vendor cost.
- Mastodon direct client. No vendor cost.
- These are quick wins — no Apify spend, no ToS risk.

**Phase 3 — Instagram, TikTok (3 days)**
- Same pattern as Twitter, two more actor integrations.
- Watch first-week cost closely.

**Phase 4 — Reverse WHOIS (2 days)**
- Whoxy integration. Single endpoint per politician. Results land in `politician_domains` unverified.
- Admin review queue UI.
- Auto-feed verified new domains into existing scanner pipeline.

**Phase 5 — Facebook page metadata, Threads (1–2 days)**
- Page metadata only for FB. No post scraping.
- Threads where handles exist (small set today).

**Phase 6 (deferred) — LinkedIn, FB posts**
- Pending legal review. Do not start until written sign-off.

---

## 10. Legal + ethical guardrails

Policy lives in [`docs/governance/social-enrichment-policy.md`](../governance/social-enrichment-policy.md): scope, universal rules (civic-transparency framing, public-data-only, PIPEDA, no automated engagement, audit trail), per-platform ToS posture, reverse-WHOIS handling, and counsel-review status. Read that before adding a new actor or surfacing data publicly.

This plan defers to the governance doc on what is and isn't allowed; new platform integrations land in the policy table there before code is written here.

---

## 11. Verification plan (single politician, end-to-end)

Before turning this loose on the dataset:

1. Pick a politician with a known active Twitter, Bluesky, Instagram, and Mastodon presence. Justin Trudeau, an active opposition critic, and one provincial premier with a Bluesky account are good candidates.
2. Manually trigger enrichment from the admin panel.
3. Confirm:
   - `apify_jobs` row created → status transitions queued → running → succeeded.
   - `social_posts` row count matches what's visible on the live profile (spot-check 5 posts).
   - Engagement counts are within 5% of live (allows for delta during scrape).
   - `cost_usd` is recorded and within ballpark estimate.
   - Re-running the same enrichment 24h later produces only *new* posts (no duplicates — UNIQUE constraint holds).
4. Trigger a Whoxy reverse-WHOIS lookup with the politician's known parliamentary email. Verify at least the public website appears in results.
5. Verify the admin "verify domain" flow gates publication on the public frontend.
6. Run a deliberate failure: bad handle, deleted account. Confirm `apify_jobs.status = 'failed'`, `error` populated, no partial garbage in `social_posts`.

Only after all six pass → enable batched / scheduled enrichment.

---

## 12. Rate-limit + cost-control strategy

- **Single global Apify queue.** One worker process pulls from `apify_jobs WHERE status='queued'`. Concurrency = 1 to start (Apify per-actor concurrency caps already constrain us anyway).
- **Per-actor backoff.** If we get a 429 from Apify, exponential backoff via the `apify-client` retry layer.
- **Daily cap.** Hard ceiling on `cost_usd` per UTC day, configurable. Default $5/day during pilot. Worker stops dequeuing when cap hit; resumes next day.
- **Per-politician rate-limit.** Don't re-enrich the same politician's same platform within 24h, regardless of trigger source.
- **Spread scheduled refreshes.** Quarterly cycle = ~20 politicians/day. Cron drips them in across the day, not in a burst.
- **Bluesky / Mastodon** — no Apify cost, but still respect their rate limits via their published guidance (Mastodon: 300 req/5min/instance).
- **Whoxy** — billed only on hits; cost is bounded by registered-domain count per politician (usually small).

---

## 13. Cost estimate

Assumptions:

- 1,800 politicians × distribution observed in `politician_socials` (extrapolated to full cohort):
  - ~60% have Twitter → ~1,080 handles
  - ~40% have Facebook page → ~720
  - ~25% have Instagram → ~450
  - ~10% have TikTok → ~180
  - ~5% have YouTube → ~90 (free via Data API)
  - ~3% Bluesky / Mastodon / Threads → ~50 each (free except Threads)
- Pull depth: most-recent 100 posts per platform per refresh.

| Platform | Handles | Posts/refresh | Unit cost | Cost / refresh |
|---|---:|---:|---:|---:|
| Twitter (`apidojo/tweet-scraper`) | 1,080 | 100 | $0.40 / 1k | **~$43** |
| Instagram (`apify/instagram-scraper`) | 450 | 100 | $1.50 / 1k | **~$68** |
| TikTok (`clockworks/tiktok-scraper`) | 180 | 50 | $1.70 / 1k | **~$15** |
| Facebook page metadata | 720 | n/a (1 page each) | $6.60 / 1k | **~$5** |
| Threads | 50 | 20 | $5 / 1k | **~$5** |
| YouTube (Data API v3) | 90 | up to 50 | free (within quota) | **$0** |
| Bluesky (AT Protocol) | 50 | 100 | free | **$0** |
| Mastodon | 50 | 100 | free | **$0** |
| **Total per full refresh** | | | | **~$136** |

Quarterly refresh cadence → **~$45/month steady state.** Add ~$5/month for Whoxy reverse-WHOIS amortized. Add Apify platform fee if we exceed free-credit allowance (Apify's $5/month free credit is consumed within minutes at this volume — expect a Starter or Scale plan, ~$49–$199/mo, see [Apify pricing](https://apify.com/pricing)).

**Realistic monthly budget: $100–$250** depending on plan tier and whether we add ad-hoc deep pulls.

If we add LinkedIn later (post legal sign-off): 1,800 × $4/1,000 = ~$7 per refresh, but the 500-profile/day account cap means a refresh takes a week; not a cost issue, an operational one.

---

## 14. Out-of-scope for now (deferred)

- LLM summarization of post content ("what has this politician been saying about X?").
- Sentiment / topic classification.
- Cross-politician network analysis (who replies to whom, who shares whose posts).
- Realtime alerting on new posts.
- Historical backfill beyond 90–180 days at first scrape.
- LinkedIn integration (pending legal).
- Facebook post-text scraping (pending legal).
- Public-facing API for the social_posts table — admin-only initially.
- Automated takedown workflow (manual for now).

---

## 15. Open questions for the project owner

1. **Budget ceiling** — confirm the $100–$250/mo range is acceptable, or set a lower hard cap.
2. **Apify plan** — Starter ($49/mo) or Scale ($199/mo)? Affects per-result rates.
3. **Public surfacing** — is `social_posts` data displayed on the public frontend, admin-only, or both with different views?
4. **Whoxy vs WhoisXML** — start with Whoxy as proposed, or invest in WhoisXML for the larger historical corpus?
5. **Counsel review** — is there an existing legal contact, or do we need to source one before LinkedIn / Facebook-posts work begins?
6. **DSAR process owner** — who handles takedown requests? Needs to exist before public-facing rollout.

---

## Sources

- [Apify pricing](https://apify.com/pricing)
- [Apify REST: run-sync-get-dataset-items](https://docs.apify.com/api/v2/act-run-sync-get-dataset-items-post)
- [Apify Python client](https://docs.apify.com/api/client/python/docs)
- [Tweet Scraper V2 — apidojo](https://apify.com/apidojo/tweet-scraper)
- [Twitter Scraper Lite — apidojo](https://apify.com/apidojo/twitter-scraper-lite)
- [Cheapest Twitter Scraper — kaitoeasyapi](https://apify.com/kaitoeasyapi/twitter-x-data-tweet-scraper-pay-per-result-cheapest/api)
- [Facebook Pages Scraper — apify](https://apify.com/apify/facebook-pages-scraper)
- [Facebook Posts Scraper — apify](https://apify.com/apify/facebook-posts-scraper)
- [Instagram Scraper — apify](https://apify.com/apify/instagram-scraper)
- [TikTok Scraper — clockworks](https://apify.com/clockworks/tiktok-scraper)
- [TikTok Scraper — apidojo (alt)](https://apify.com/apidojo/tiktok-scraper)
- [YouTube Scraper — streamers](https://apify.com/streamers/youtube-scraper)
- [LinkedIn Profile Scraper — curious_coder](https://apify.com/curious_coder/linkedin-profile-scraper)
- [Threads Profile Scraper — apify](https://apify.com/apify/threads-profile-api-scraper)
- [Bluesky API rate limits](https://docs.bsky.app/docs/advanced-guides/rate-limits)
- [Bluesky getAuthorFeed (no auth)](https://docs.bsky.app/docs/api/app-bsky-feed-get-author-feed)
- [Mastodon API rate limits](https://docs.joinmastodon.org/api/rate-limits/)
- [X API pricing 2026 — Postproxy summary](https://postproxy.dev/blog/x-api-pricing-2026/)
- [Whoxy Reverse WHOIS pricing](https://www.whoxy.com/reverse-whois/)
- [WhoisXML API Reverse WHOIS](https://reverse-whois.whoisxmlapi.com/)
- [Is web scraping legal? (Use-Apify 2026)](https://use-apify.com/docs/what-is-apify/is-apify-legal)
