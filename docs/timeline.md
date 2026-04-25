# Timeline / direction

Where the project is going, in priority order. Written 2026-04-25.

This is the **what we're building next** doc. For the **why**, read [`goals.md`](./goals.md). For the **how**, read the per-feature plan under [`plans/`](./plans/) linked from each item below. When this file disagrees with the plan docs, the plan docs win — keep this one short and reorder it as priorities shift.

Three horizons:
- **Now** — in flight, expected to land in the next cycle.
- **Next** — the four stated priorities, in order. Each has a one-paragraph framing and a link to the plan doc (or a flag that no plan exists yet).
- **Later** — documented in plan docs but deliberately deferred. Not orphaned; just not the current focus.

A separate **Always-on** section covers governance, monitoring, and ingest hygiene that doesn't fit a horizon.

---

## Now (in flight)

These are partially built and the goal is to finish them, not to start something new on top.

- **Phase 1b — premium report generation.** Phase 1a (billing ledger, credit UI, admin comp) shipped 2026-04-23. Phase 1b is the LLM map-reduce that actually spends a credit: query → relevant speeches → HTML report → email + saved to account. Plan: [`plans/premium-reports.md`](./plans/premium-reports.md).
- **`/api/v1/search` finalization.** Hybrid HNSW + BM25 retrieval is wired with zod validation and instruction prompting at query time. Pending: performance tuning and the public contract freeze. Plan: [`plans/semantic-layer-rollout.md`](./plans/semantic-layer-rollout.md), [`plans/search-features-handoff.md`](./plans/search-features-handoff.md).
- **Stripe Tax (GST/HST) enablement.** Config-only, no code change. Required before any public-revenue launch. Tracked under [`plans/premium-reports.md`](./plans/premium-reports.md) § out-of-scope-for-1a.

---

## Next (priorities, in order)

### 1. Database — finish the corpus

The product is only as definitive as the data behind it. Database expansion stays priority one until the remaining Hansard pipelines are live and votes are modelled.

- **Remaining Hansard pipelines: ON → NT/NU → SK → PE/YT.** Six jurisdictions left. ON is the largest caucus and the highest-value next build; NT/NU need consensus-government schema (no party whip); SK is PDF-only and needs dedicated parser investment; PE/YT sit behind WAFs/CAPTCHAs. Each is gated on the **research-handoff rule** (see CLAUDE.md convention #5) — user research pass first, code second. Status table: [`research/overview.md`](./research/overview.md).
- **Votes table — apply migration 0018.** Drafted, intentionally unapplied. Holding for real NT/NU consensus-government data so the `vote_type` discriminator (`division | voice | acclamation | consensus`) gets exercised on every shape it needs to handle. Plan: [`plans/semantic-layer-schema.md`](./plans/semantic-layer-schema.md) § Migration 0018.
- **Committee transcripts.** Same speech pipeline as Hansard, `speech_type='committee'`. Deferred until votes land so the table stays coherent. Plan: [`plans/semantic-layer-rollout.md`](./plans/semantic-layer-rollout.md) § Phase 4.
- **Historical-roster backfill — propagate AB/MB pattern to ON/BC/QC.** AB (+901 former MLAs) and MB (+764) shipped 2026-04-22/23 and unlocked date-windowed speaker resolution on pre-current-session Hansard. Same pattern needed for ON/BC/QC before their Hansard speaker attribution is meaningful pre-2010s.
- **Corrections inbox — SMTP poller + admin review queue.** Web flag-button shipped; `correction_submissions` table exists (migration 0020); SMTP ingest and admin UI not built yet. Small but blocks the public correction policy.
- **Apify social-post deep enrichment.** Phase 0/1 (schema + Twitter pilot) → 2–5 (Instagram, TikTok, Bluesky direct, Mastodon direct, reverse-WHOIS). $100–$250/mo steady-state on quarterly refresh. Plan: [`plans/apify-social-deep-enrichment.md`](./plans/apify-social-deep-enrichment.md).
- **Bill text for SK/PE/YT.** 10/13 sub-national + federal already have bills. Lower priority than Hansard for the same three jurisdictions; bundle the work when their Hansard pipeline lands.

### 2. Chat interface

A conversational front door over the semantic-search + contradictions stack. The retrieval, ranking, and grounded-citation primitives already exist — chat is the UX wrapper that strings them into a turn-based interaction with memory of what the user has already asked.

- **No plan doc yet.** Before writing one, decide: scope (general-purpose Q&A vs. politician-scoped vs. bill-scoped), grounding discipline (every claim cites a chunk, refusal otherwise), model (OpenRouter free-tier like contradictions, or paid like phase-1b reports), and metering (free, credit-metered, or rate-limited).
- **Reuse, don't fork.** The semantic-search hybrid retrieval and the contradictions consent/model picker are the load-bearing pieces. Building a separate retrieval path for chat is the wrong default.
- **Open questions to settle in the plan doc:** session persistence (saved-searches table extension vs. new `chat_sessions`?); transcript export; how chat interacts with the credit ledger; voice-input handoff (see priority #3).

### 3. Accessibility, including voice

Civic-transparency tooling that's only usable by sighted desktop users with steady hands isn't doing its job. Two distinct workstreams:

- **Accessibility audit + remediation.** Keyboard navigation across `/search`, `/coverage`, `/postal`, the politician page, the admin shell. Screen-reader testing on the same surfaces. Color contrast pass on the map (Leaflet defaults are not great). ARIA landmarks and form labels. WCAG 2.2 AA target. No plan doc; needs one before the audit starts.
- **Voice interface.** Two layers: (a) speech-to-text for query input — STT on-device where possible, server-side fallback; (b) text-to-speech for results read-back, prioritized for low-vision and low-literacy users. Tight coupling to the chat interface in priority #2 — voice queries should land in the chat surface, not in `/search`. Unscoped; the plan doc has to come before any code.
- **Sovereignty constraint.** Whatever STT/TTS we pick has to be self-hostable or have a defensible Canadian-data path. Hosted Whisper-via-third-party is not the default. See [`plans/sovereignty-runtime-deps.md`](./plans/sovereignty-runtime-deps.md) for the precedent.

### 4. UI improvements

Smaller, mostly contained UI work. Each can ship independently.

- **`/chunk/<uuid>` detail page.** Internal view showing the full speech around a chunk + neighbouring chunks. Currently chunks deep-link out to source Hansard via `source_url + source_anchor`; the internal page would let a user expand context without leaving the site.
- **Politician biography brief.** Full-coverage report (all speeches, bills, votes per politician) as a phase-2+ premium SKU on top of phase-1b query-scoped reports. Plan: [`plans/premium-reports.md`](./plans/premium-reports.md) § v2+.
- **Topic dashboard / time series.** "Climate mentions across all legislatures over time" style. Faceted aggregations by party, jurisdiction, speaker. Goals doc lists this as the phase-2+ artifact past the v1 search box.
- **Compare politician A vs. B on topic X.** Same phase-2+ bucket. Probably reuses the chat surface from priority #2 once that exists.
- **Map polish.** Symbol legend, faster cluster-zoom transitions, and constituency-boundary year picker now that boundaries are temporal (migration 0021).

---

## Later (deferred, but documented)

Plan docs exist for these. They're not abandoned — they're parked behind the priorities above.

- **Public developer API (`/api/public/v1/*`) with three paid tiers.** Greenfield: free / dev / pro tiers, Stripe subscriptions (distinct from credit-pack one-time), per-tier rate limits, OpenAPI + Swagger UI, key provisioning at `/account/api-keys`. Plan: [`plans/public-developer-api.md`](./plans/public-developer-api.md).
- **Bulk export endpoints (Parquet / CSV) — `read:bulk` scope.** Per-jurisdiction-month presigned exports. Sits behind the dev-API v1.0 launch as v1.1. Plan: same doc as above.
- **Map tiles self-hosting.** CARTO + OSM tiles currently CDN-loaded. Three options scoped: nginx raster cache, PMTiles + MapLibre GL (~25 GB Z0–Z14 Canada), OpenMapTiles container. Plan: [`plans/sovereignty-runtime-deps.md`](./plans/sovereignty-runtime-deps.md) § item 3.
- **Browser-automation (Playwright/Camoufox) for PE/YT WAF jurisdictions.** Investment only worth making if the alternative — direct outreach to the legislatures for a civic-transparency allowlist — fails. Background: [`archive/scoping-q-a-2026-04.md`](./archive/scoping-q-a-2026-04.md) q5.3.
- **Openparliament.ca live-call → scheduled refresh.** `/api/v1/openparliament` currently hits `api.openparliament.ca` per request; move to a scanner refresh job and cache in DB. Outage-mitigation. Plan: [`plans/sovereignty-runtime-deps.md`](./plans/sovereignty-runtime-deps.md) § item 4.

---

## Always-on

Not horizon-bound. These need attention every cycle regardless of what else is in flight.

- **Governance docs before public launch.** Takedown / correction policy. DSAR workflow (especially before Apify social enrichment goes public). Disclaimer text on AI-generated reports. None written yet; small, but blocking.
- **Embedding-model drift monitoring.** Re-run the eval set under [`services/embed/eval/queries/queries.jsonl`](../services/embed/eval/queries/queries.jsonl) on any model change. Qwen3-Embedding-0.6B is current; BGE-M3 wrapper kept on disk for rollback only. Background: [`archive/embedding-eval-2026-04.md`](./archive/embedding-eval-2026-04.md).
- **AI contradictions false-positive watch.** Feature is live and free-tier; watch for quoted-opponent and party-transition-boundary failure modes. Plan: [`plans/ai-contradictions-handoff.md`](./plans/ai-contradictions-handoff.md).
- **Coverage dashboard accuracy.** `/coverage` is the honesty surface. After every Hansard or bills ingest, run `refresh-coverage-stats` so the dashboard doesn't lie.
- **Documentation freshness.** When `/api/v1/search` ships, update [`docs/api.md`](./api.md). When the public dev-API ships, add a `/developers` section to [`README.md`](../README.md). When this timeline gets stale, edit it.

---

## Recently shipped (last cycle, 2026-04-16 → 2026-04-23)

For context on what just landed, so this doc reads against a known baseline:

- **Phase 1a billing rail** — credit ledger, credit-pack purchases, admin comp, suspended-tier enforcement (migration 0033).
- **Qwen3-Embedding-0.6B migration** — re-embedded 1.48 M chunks, dropped legacy BGE-M3 column, retired the FastAPI/FlagEmbedding wrapper from the critical path (migrations 0023–0025).
- **MB Hansard full-span ingest** — legs 37–43, 1999-11-26 → 2026-04-16, 407,695 speeches across 2,325 sittings; modern + Word-97-era parsers.
- **AB + MB historical roster backfill** — +901 former AB MLAs, +764 former MB MLAs; date-windowed speaker resolver pattern.
- **Magic-link user accounts + saved searches + alerts worker** (migrations 0027–0029).
- **`is_admin` flag collapse** — old `ADMIN_TOKEN` flow removed; admin is now a DB role on the user-session flow (migration 0029).
