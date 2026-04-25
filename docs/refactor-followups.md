# Doc refactor followups

Items surfaced during the 2026-04-25 docs audit that the user opted to **defer**. The high-impact subset shipped that day (archive stale plan docs, move recovery-log runbooks, rename posts → marketing, gitignore `docs/draw/`, add `TODO.md`). The items below are real but deferred — log them here so they aren't lost.

Each entry: **Problem** → **Proposed fix**. One pass at a time; don't bundle.

## Splits

- **`docs/plans/semantic-layer.md`** is doing two jobs (schema / migration log + retrieval rollout plan).
  - **Fix:** split into `semantic-layer-schema.md` (tables, columns, migration history) and `semantic-layer-rollout.md` (HNSW build, retrieval-quality phases). Cross-link from both.

## Trims

- **`docs/operations.md`** has duplicated embedding + billing content that lives authoritatively in `CLAUDE.md`.
  - **Fix:** cut the duplicates; keep operations-only material (compose commands, log locations, restart procedures). Target ~150 lines.
- **`docs/plans/apify-social-deep-enrichment.md`** carries legal/policy minutiae mixed with the technical plan.
  - **Fix:** move the consent / DSAR / TOS-compliance discussion into a sibling `docs/governance/social-enrichment-policy.md` (or fold it into the unwritten governance doc). Target plan-doc length: ~200 lines.
- ~~**`docs/plans/premium-reports.md`** still includes the phase-1a billing-rail design that has now shipped and is summarized in `CLAUDE.md`.~~ **Done 2026-04-25** (302 → 120 lines). Phase-1a marked shipped, verbatim SQL replaced with prose pointing at the migration file, deployment sequence + verification SQL extracted to the new billing-rail-operations runbook, file list collapsed to a `CLAUDE.md` cross-ref.

## Dedup across files

- **Embedding-model facts** — the Qwen3-Embedding-0.6B switch is described in `README.md`, `CLAUDE.md`, `docs/operations.md`, and `docs/plans/search-features-handoff.md`. Drift between them is inevitable.
  - **Fix:** make `CLAUDE.md`'s "Stack" section the single authoritative statement. Reduce the others to a one-line link back. (Consider this when next touching any of those files.)

## Stub research dossiers

- **SK, PE, YT, MB** dossiers under `docs/research/` are 57–67 lines vs. the ON/NS/NB template depth (~150+).
  - **Fix:** bring each up to the template at the time its Hansard pipeline is researched. Don't backfill speculatively — wait for the research-handoff pass.

## Runbooks → archive

- ~~**`docs/runbooks/handoff-2026-04-23-billing-rail-phase-1a.md`** is currently in `runbooks/` but is structured as a dated handoff narrative, not an evergreen procedure.~~ **Done 2026-04-25.** Evergreen procedures extracted to `docs/runbooks/billing-rail-operations.md`; dated narrative moved to `docs/archive/recovery-logs/`.

## Cross-links

- **`docs/research/overview.md`** describes the research-handoff protocol but doesn't link back to `docs/timeline.md`'s priority ordering of which jurisdictions are next.
  - **Fix:** add a one-line cross-link at the top: "for *which* jurisdiction to research next, see `docs/timeline.md` § Database."
