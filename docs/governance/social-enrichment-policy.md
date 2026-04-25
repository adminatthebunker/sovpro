# Social-enrichment policy

Operating rules for the Apify social-deep-enrichment pipeline (Twitter, Facebook page metadata, Instagram, TikTok, Bluesky, Mastodon, Threads, YouTube) and the Whoxy reverse-WHOIS domain-discovery pipeline. Architecture / cost / phasing live in [`docs/plans/apify-social-deep-enrichment.md`](../plans/apify-social-deep-enrichment.md); this doc is the policy surface.

This is a **draft**. The Apify pipeline is not yet built; finalising this doc is on the launch-blocking list before any public-facing surfacing of the data it produces.

## Scope

In scope:
- Pulling **public** post timelines + profile metadata for known politician handles.
- Storing structured posts (text + engagement + URL + timestamp) for analysis.
- Reverse-WHOIS lookup by known politician email/name to discover undisclosed domains, with admin review before any discovered domain is surfaced publicly.

Out of scope, hard stops:
- Private/DM/group/follower-only data — never, regardless of platform or what an actor offers.
- Automated engagement (likes, replies, follows, reposts) — never. Read-only always.
- Network-graph scraping (followers, mutuals, who-replies-to-whom) — separate question, much bigger ToS surface; defer.
- Realtime streaming or polling of every account — daily/weekly is sufficient for transparency work; sparse intentional pulls only.
- Data on family members or non-politician namesakes surfaced via reverse-WHOIS — must be filtered at admin review, never published.

## Universal rules

These apply to every platform, every actor, every job:

- **Civic-transparency framing only.** This pipeline exists for journalists, researchers, and the voting public — not advertising, not opposition-research-as-a-service. Public-facing surfacing decisions should be defensible under that framing.
- **Public data only.** No DMs, no private accounts, no follower graphs.
- **Personal-data minimization.** PIPEDA (federal) and provincial private-sector privacy laws apply. Retain only what serves the transparency purpose. Raw API dumps live in `social_posts.raw` for forensic auditability but are **not** surfaced publicly.
- **DSAR / takedown workflow.** A documented process must exist for politicians (or staff) to request correction or deletion before this feature ships publicly. The `correction_submissions` table is the technical hook; the policy and SLA on top of it are tracked separately under timeline § governance.
- **No automated engagement.** Read-only, always. The codebase does not import any Apify actor that performs writes.
- **Audit trail.** Every job logged in `apify_jobs` with admin user ID, timestamp, cost, result count. The audit log is admin-only.
- **Admin-trigger by default.** Batched / scheduled enrichment is an opt-in operational decision; the default should be deliberate, per-politician, admin-triggered jobs.

## Per-platform posture

| Platform | Public ToS posture on scraping | Our posture |
|---|---|---|
| Twitter / X | Hostile in ToS, but the official API exists; widely accepted that public-profile scraping is tolerated. *hiQ v. LinkedIn* (9th Cir. 2022) supports that scraping public data is not a CFAA violation. | Proceed via Apify; document each scrape; admin-triggered only. |
| Facebook (Meta) | Hostile. Scraping prohibited; account/IP ban risk. | Page metadata only. Defer post-text scraping pending counsel. |
| Instagram (Meta) | Hostile. Same as Facebook. | Limited public-profile pulls only; cap depth at the most-recent N posts. |
| TikTok | Discouraged, low enforcement on public data. | Proceed; admin-triggered. |
| LinkedIn | Hostile + technically blocks. Cookie-based scrapers risk account loss. | **Defer entirely.** Get counsel sign-off first. |
| YouTube | Official Data API v3 offered (free within 10k units/day); scraping the site discouraged. | Use the official API. Skip Apify for YouTube. |
| Bluesky | Public AT Protocol explicitly invites public clients. | Free use; attribute the data source. |
| Mastodon | Federated, instance-specific; public timelines explicitly public. | Free use; respect per-instance rate limits. |
| Threads | API still maturing. Apify scrapers exist. | Proceed cautiously, low volume. |
| WHOIS data | Public by design. | OK to query; admin review before publishing any discovered domain. |

## Reverse-WHOIS specifics

- Discovered domains land in `politician_domains` with `verified_by_admin = false` by default.
- The public-facing frontend renders **only** verified entries.
- The internal scanner pipeline can run against unverified ones (so we know *what* a domain looks like before deciding whether to surface it), but findings stay admin-only until reviewed.
- Family members' / unrelated namesakes' domains will be in result sets — explicitly require human review for relevance.
- Surfacing an undisclosed personal domain is a meaningful editorial act, not a mechanical one. Even a verified domain should be checked for "is this newsworthy in a transparency sense, or just incidental?"

## Counsel review

Pending. LinkedIn integration and Facebook post-text scraping both require written sign-off before code is written. Update this doc when counsel is engaged and again when each platform is cleared.

## Public surfacing

Currently undecided. Default posture: `social_posts` data is admin-only at first. Public surfacing decisions are per-platform and reviewed against this policy before any frontend route is added.
