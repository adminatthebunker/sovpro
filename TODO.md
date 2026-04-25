# TODO

Tactical near-term checklist. Small, concrete items. Tick them off as they ship.

For strategic direction (horizons, the four standing priorities), read [`docs/timeline.md`](./docs/timeline.md). When this list and the timeline disagree, the timeline wins — fix this file.

For deferred doc-cleanup work, see [`docs/refactor-followups.md`](./docs/refactor-followups.md).

## In flight

- [ ] **Phase 1b — premium report generation.** LLM map-reduce → HTML report → email + saved on account. Plan: [`docs/plans/premium-reports.md`](./docs/plans/premium-reports.md).
- [ ] **`/api/v1/search` finalization.** Performance tune the hybrid HNSW + BM25 path; freeze the public contract. Plan: [`docs/plans/search-features-handoff.md`](./docs/plans/search-features-handoff.md).
- [ ] **Stripe Tax (GST/HST) enablement.** Config-only, before any public-revenue launch.

## Up next — database (priority #1)

- [ ] **ON Hansard pipeline.** Largest caucus, highest-value next build. Gated on the research-handoff rule — user research pass first.
- [ ] **NT/NU Hansard pipelines.** Consensus-government schema (no party whip). Will exercise `vote_type='consensus'` shape needed before 0018.
- [ ] **Apply migration 0018 (votes table)** once NT/NU consensus-gov data is in hand.
- [ ] **Historical-roster backfill: ON.** Replicate the AB/MB date-windowed speaker-resolver pattern.
- [ ] **Historical-roster backfill: BC.**
- [ ] **Historical-roster backfill: QC.**
- [ ] **Corrections inbox: SMTP poller.** Email → `correction_submissions`. Table exists (migration 0020); poller does not.
- [ ] **Corrections inbox: admin review queue.** UI under `/admin` to triage submissions.
- [ ] **SK Hansard pipeline** (PDF-only; needs dedicated parser investment).
- [ ] **PE/YT Hansard pipelines** (sit behind WAFs/CAPTCHAs; consider direct legislature outreach before browser-automation).
- [ ] **SK/PE/YT bills** — bundle with each jurisdiction's Hansard work.

## Up next — chat interface (priority #2)

- [ ] **Write `docs/plans/chat-interface.md`.** Decide: scope (general / politician-scoped / bill-scoped), grounding discipline (every claim cites a chunk), model (OpenRouter free vs paid), metering (free / credit / rate-limited).
- [ ] **Decide session persistence.** Extend `saved_searches` or introduce `chat_sessions`?
- [ ] **Specify voice-input handoff.** Voice queries should land in chat, not `/search` — couples to priority #3.

## Up next — accessibility incl. voice (priority #3)

- [ ] **Accessibility audit.** Keyboard nav + screen reader + contrast on `/search`, `/coverage`, `/postal`, the politician page, and the admin shell. WCAG 2.2 AA target.
- [ ] **Map contrast pass.** Leaflet defaults are below AA on several markers.
- [ ] **Write `docs/plans/voice-interface.md`.** STT (input) + TTS (read-back). Sovereignty constraint: self-hostable or defensible Canadian-data path.

## Up next — UI (priority #4)

- [ ] **`/chunk/<uuid>` detail page.** Full speech around a chunk + neighbours; expand context without leaving the site.
- [ ] **Map polish.** Symbol legend, faster cluster-zoom transitions, constituency-boundary year picker (boundaries are temporal as of 0021).

## Always-on

- [ ] Run `refresh-coverage-stats` after every Hansard / bills ingest.
- [ ] Update `docs/api.md` when `/api/v1/search` ships.
- [ ] Watch the AI-contradictions feature for quoted-opponent and party-transition false positives.
- [ ] Embedding-model drift: re-run `services/embed/eval/queries/queries.jsonl` on any model change.

## Governance (small but launch-blocking)

- [ ] **Takedown / correction policy** drafted and linked from the public site.
- [ ] **DSAR workflow** documented (priority before Apify social enrichment goes public).
- [ ] **Disclaimer text** on AI-generated reports.

## Recently done

- 2026-04-25 — Docs refactor: archived stale plan docs + recovery-log runbooks; renamed posts → marketing; gitignored `docs/draw/`; added this file and `docs/refactor-followups.md`.
- 2026-04-25 — Relicensed MIT → PolyForm Noncommercial 1.0.0.
- 2026-04-25 — Added `docs/timeline.md` as the direction doc.

For the cycle-level summary of recent work, see [`docs/timeline.md`](./docs/timeline.md) § Recently shipped.

## Notes

- Ship something → check it off → move it under **Recently done** with the ISO date.
- Don't promote a checkbox into this file just because it exists in a plan doc. Keep this list to things you'd actually start this week.
- When this file's "In flight" or "Up next" buckets drift from `docs/timeline.md`, update both — the timeline is the source of truth on horizon, this file is the source of truth on next-action.
