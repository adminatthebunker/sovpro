# TODO

Actionable checkbox view of [`docs/timeline.md`](./docs/timeline.md). When this file disagrees with the timeline or with a plan doc under [`docs/plans/`](./docs/plans/), **the plan docs win** — update this file rather than the other way round.

- **Last synced with `docs/timeline.md`:** 2026-04-25
- **Why this exists:** the timeline is prose-shaped; this is the version you tick off. One source of priority ordering (timeline), one place to mark progress (here).
- **How to update:** check items as they ship, move them to *Recently shipped*, and re-sync the date above. If a horizon shifts, edit `docs/timeline.md` first, then mirror here.

---

## Now (in flight)

Partially built — finish, do not start new things on top.

- [ ] **Phase 1b — premium report generation.** LLM map-reduce: query → relevant speeches → HTML report → email + saved to account. The credit *spender* on top of phase-1a's billing rail. → [`docs/plans/premium-reports.md`](./docs/plans/premium-reports.md)
- [ ] **`/api/v1/search` finalization.** Hybrid HNSW + BM25 is wired; pending: performance tuning + public contract freeze. → [`docs/plans/semantic-layer.md`](./docs/plans/semantic-layer.md), [`docs/plans/search-features-handoff.md`](./docs/plans/search-features-handoff.md)
- [ ] **Stripe Tax (GST/HST) enablement.** Config-only, no code change. Blocks any public-revenue launch. → [`docs/plans/premium-reports.md`](./docs/plans/premium-reports.md) § out-of-scope-for-1a

---

## Next 1 — Database: finish the corpus

Priority one until remaining Hansard pipelines are live and votes are modelled. Every provincial Hansard build is gated on the **research-handoff rule** (CLAUDE.md convention #5) — pause and ask the user for their endpoint research before probing.

### Remaining Hansard pipelines (5 left; ON shipped 2026-04-24)

- [ ] **NT Hansard** — consensus-government schema (no party whip). Research-handoff gated. → [`docs/research/northwest-territories.md`](./docs/research/northwest-territories.md)
- [ ] **NU Hansard** — consensus-government, multilingual (EN + Inuktitut + Inuinnaqtun + FR). Research-handoff gated. → [`docs/research/nunavut.md`](./docs/research/nunavut.md)
- [ ] **SK Hansard** — PDF-only; needs dedicated `pdfplumber` parser investment (same tooling unlocks AB Hansard historical). Research-handoff gated. → [`docs/research/saskatchewan.md`](./docs/research/saskatchewan.md)
- [ ] **PE Hansard** — sits behind WAF/CAPTCHA. Research-handoff gated; may require Playwright/Camoufox track (see *Later*). → [`docs/research/prince-edward-island.md`](./docs/research/prince-edward-island.md)
- [ ] **YT Hansard** — same WAF/CAPTCHA bucket as PE. Research-handoff gated. → [`docs/research/yukon.md`](./docs/research/yukon.md)

### Votes & committees

- [ ] **Apply migration `0018_votes.sql`.** Drafted, intentionally unapplied. Hold until real NT/NU consensus-gov't data exists so the `vote_type` discriminator (`division | voice | acclamation | consensus`) gets exercised on every shape. → [`docs/plans/semantic-layer.md`](./docs/plans/semantic-layer.md) § 0018
- [ ] **Committee transcripts.** Same speech pipeline, `speech_type='committee'`. Deferred until votes land. → [`docs/plans/semantic-layer.md`](./docs/plans/semantic-layer.md) § phase 4

### Historical-roster backfills (AB/MB pattern → ON/BC/QC)

- [ ] **ON historical roster.** Propagate the date-windowed-resolver pattern. Required before pre-current-session ON Hansard speaker attribution is meaningful pre-2010s.
- [ ] **BC historical roster.** Same pattern.
- [ ] **QC historical roster.** Same pattern.

### Corrections inbox

- [ ] **SMTP poller + admin review queue.** Web flag-button shipped; `correction_submissions` table exists (migration 0020); SMTP ingest and admin UI not built. Small but blocks the public correction policy.

### Social enrichment

- [ ] **Apify social-post deep enrichment, phase 0/1 → 2–5.** Schema + Twitter pilot done (phase 0/1); Instagram, TikTok, Bluesky direct, Mastodon direct, reverse-WHOIS pending. Steady-state cost $100–$250/mo on quarterly refresh. → [`docs/plans/apify-social-deep-enrichment.md`](./docs/plans/apify-social-deep-enrichment.md)

### Bill text for the laggards

- [ ] **SK / PE / YT bill ingest.** 10/13 sub-national + federal already live. Bundle this with each jurisdiction's Hansard build when it lands.

---

## Next 2 — Chat interface

A conversational front door over the existing semantic-search + contradictions stack. Retrieval, ranking, and grounded-citation primitives already exist — chat is the UX wrapper.

- [ ] **Write the plan doc.** No `docs/plans/chat-*.md` exists yet. Settle the open questions before any code:
  - Scope: general-purpose Q&A vs. politician-scoped vs. bill-scoped?
  - Grounding discipline: every claim cites a chunk, refusal otherwise?
  - Model: OpenRouter free-tier (like contradictions) or paid (like phase-1b reports)?
  - Metering: free / credit-metered / rate-limited?
  - Session persistence: extend `saved_searches` or new `chat_sessions`?
  - Transcript export, credit-ledger interaction, voice-input handoff (see Next 3).
- [ ] **Build, reusing existing primitives.** Semantic-search hybrid retrieval + contradictions consent/model picker are load-bearing. **Don't fork retrieval.**

---

## Next 3 — Accessibility (incl. voice)

Two distinct workstreams under one priority. WCAG 2.2 AA is the audit target.

### Accessibility audit + remediation

- [ ] **Plan doc for the audit.** None exists.
- [ ] **Keyboard navigation pass** across `/search`, `/coverage`, `/postal`, the politician page, the admin shell.
- [ ] **Screen-reader testing** on the same surfaces.
- [ ] **Color-contrast pass on the map.** Leaflet defaults are not great.
- [ ] **ARIA landmarks + form labels** site-wide.

### Voice interface

- [ ] **Plan doc for voice.** None exists. Must come before any code.
- [ ] **Speech-to-text for query input** (STT on-device where possible, server-side fallback). Lands in the chat surface from Next 2, not in `/search`.
- [ ] **Text-to-speech for results read-back.** Prioritised for low-vision and low-literacy users.
- [ ] **Sovereignty constraint:** STT/TTS must be self-hostable or have a defensible Canadian-data path. Hosted-Whisper-via-third-party is not the default. → [`docs/plans/sovereignty-runtime-deps.md`](./docs/plans/sovereignty-runtime-deps.md)

---

## Next 4 — UI improvements

Independent, ship-each-on-its-own work.

- [ ] **`/chunk/<uuid>` detail page.** Internal view: full speech around a chunk + neighbours. Today chunks deep-link out to source Hansard via `source_url + source_anchor`.
- [ ] **Politician biography brief.** Full-coverage report (all speeches/bills/votes per politician) as a phase-2+ premium SKU on top of phase-1b query-scoped reports. → [`docs/plans/premium-reports.md`](./docs/plans/premium-reports.md) § v2+
- [ ] **Topic dashboard / time series.** "Climate mentions across all legislatures over time"; faceted aggregations by party / jurisdiction / speaker.
- [ ] **Compare politician A vs. B on topic X.** Phase-2+. Probably reuses the chat surface from Next 2.
- [ ] **Map polish.** Symbol legend, faster cluster-zoom transitions, constituency-boundary year picker (boundaries are temporal as of migration 0021).

---

## Always-on (every cycle, regardless of horizon)

- [ ] **Governance docs before public launch.** Takedown / correction policy, DSAR workflow (especially before Apify social enrichment goes public), AI-report disclaimer text. None written yet; small but blocking.
- **Backup system completion.** Path B (parallel `pg_dump` directory format) is documented in [`docs/operations.md`](./docs/operations.md) § Backups but operator-run. Path A (`sovpro db backup` → single gzipped file) is fine for small / portable snapshots and stays as-is.
  - [ ] **Wrap Path B in a single CLI subcommand** — e.g. `sovpro db backup-fast`. One call performs the manifest write (git SHA + row counts + applied migrations), `pg_dumpall --globals-only`, sidecar `pg_dump -Fd -j 8`, ownership fix-up, and `pg_restore --list` verify. Today it's a five-step copy-paste block.
  - [ ] **Schedule it via `scanner_schedules`.** Daily cadence at a quiet UTC hour, well clear of the daily-ingest band. Failures should surface in the admin Jobs page like every other scanner job.
  - [ ] **Retention / rotation** on the primary backup directory. Keep N daily, M weekly, K monthly; prune the rest. Today snapshots accumulate forever at ~216 GB each.
  - [ ] **LUKS USB mirror automation** — at minimum a ready-to-paste script that drives `cryptsetup luksOpen` → `rsync` → `umount` → `cryptsetup luksClose` from one entry point. Stays operator-triggered (USB needs a passphrase), not scheduled.
  - [ ] **Off-host mirror.** S3 / B2 / equivalent on cron — same `rsync` shape as the USB mirror, different destination. `operations.md` already names this as the production posture; not yet implemented.
  - [ ] **Logged restore drill.** Run `pg_restore -j 4` end-to-end against a fresh staging DB and time it. The HNSW rebuild floor (30–60 min on the 3.4 M-chunk corpus) is currently an estimate; replace with a measured number and re-drill quarterly.
  - [ ] **Encryption-at-rest check.** Confirm the internal NVMe target (`/media/bunker-admin/Internal/canadian-political-data-backups/`) is on an encrypted partition or migrate it to one. Backups carry user emails, magic-link redemption history, Stripe customer IDs, and full speech text — same threat model as the LUKS USB.
- [ ] **Embedding-model drift monitoring.** Re-run the eval set under [`services/embed/eval/queries/queries.jsonl`](./services/embed/eval/queries/queries.jsonl) on any model change. Qwen3-Embedding-0.6B is current; BGE-M3 wrapper kept on disk for rollback only. → [`docs/plans/embedding-model-comparison.md`](./docs/plans/embedding-model-comparison.md)
- [ ] **AI-contradictions false-positive watch.** Live and free-tier; watch for quoted-opponent and party-transition-boundary failure modes. → [`docs/plans/ai-contradictions-handoff.md`](./docs/plans/ai-contradictions-handoff.md)
- [ ] **Coverage-dashboard accuracy.** Run `refresh-coverage-stats` after every Hansard or bills ingest so `/coverage` doesn't lie.
- [ ] **Documentation freshness.** Update [`docs/api.md`](./docs/api.md) when `/api/v1/search` ships; add a `/developers` section to [`README.md`](./README.md) when the public dev-API ships; edit `docs/timeline.md` (and re-sync this file) when priorities shift.

---

## Later (deferred, plan docs exist)

Parked behind the priorities above — not abandoned.

- [ ] **Public developer API** (`/api/public/v1/*`) with three paid tiers. Free / dev / pro, Stripe subscriptions distinct from credit-pack one-time, per-tier rate limits, OpenAPI + Swagger UI, key provisioning at `/account/api-keys`. → [`docs/plans/public-developer-api.md`](./docs/plans/public-developer-api.md)
- [ ] **Bulk export endpoints** (Parquet / CSV) — `read:bulk` scope, per-jurisdiction-month presigned exports. Sits behind dev-API v1.0 as v1.1. → same plan doc.
- [ ] **Map tiles self-hosting.** CARTO + OSM currently CDN-loaded. Three options scoped: nginx raster cache, PMTiles + MapLibre GL (~25 GB Z0–Z14 Canada), OpenMapTiles container. → [`docs/plans/sovereignty-runtime-deps.md`](./docs/plans/sovereignty-runtime-deps.md) § item 3
- [ ] **Browser automation (Playwright / Camoufox)** for PE/YT WAF jurisdictions. Only worth it if direct outreach to legislatures for a civic-transparency allowlist fails. → [`docs/plans/national-expansion-scoping.md`](./docs/plans/national-expansion-scoping.md) q5.3
- [ ] **Openparliament.ca live-call → scheduled refresh.** `/api/v1/openparliament` hits `api.openparliament.ca` per request; move to a scanner refresh job + DB cache (outage-mitigation). → [`docs/plans/sovereignty-runtime-deps.md`](./docs/plans/sovereignty-runtime-deps.md) § item 4

---

## Recently shipped (last cycle, 2026-04-16 → 2026-04-24)

For context. Move items here from above as they land; trim aggressively after a couple of cycles.

- [x] **ON Hansard pipeline** — name-based resolution + parens-name extraction; 6 ON commands packed into the 18:00 UTC daily-ingest slot (2026-04-24).
- [x] **Phase 1a billing rail** — credit ledger, credit-pack purchases, admin comp, suspended-tier enforcement (migration 0033).
- [x] **Qwen3-Embedding-0.6B migration** — re-embedded 1.48 M chunks, dropped legacy BGE-M3 column, retired the FastAPI/FlagEmbedding wrapper (migrations 0023–0025).
- [x] **MB Hansard full-span ingest** — legs 37–43, 1999-11-26 → 2026-04-16, 407,695 speeches across 2,325 sittings.
- [x] **AB + MB historical roster backfill** — +901 former AB MLAs, +764 former MB MLAs; date-windowed speaker resolver pattern.
- [x] **Magic-link user accounts + saved searches + alerts worker** (migrations 0027–0029).
- [x] **`is_admin` flag collapse** — old `ADMIN_TOKEN` flow removed; admin is a DB role on the user-session flow (migration 0029).
