# Socials audit + backfill runbook

Cadence: quarterly, or after a roster ingest that adds >10 new politicians.

Goal: drive `politician_socials` coverage up, flag dead handles, review low-confidence rows, ship. Designed so that the default path (Tiers 1 + 2) costs zero LLM tokens; Tier 3 is opt-in.

See [`docs/scanner.md`](../scanner.md) for the architectural reference — this runbook assumes you know the tier model and jumps straight to "what to run".

## 0 · Snapshot the current state

```bash
docker compose run --rm scanner audit-socials
```

Prints platform depth, coverage-by-jurisdiction, source breakdown, and missing-rows-by-platform. Also writes `/tmp/politician_socials_audit.csv` and refreshes the `v_socials_missing` view that Tiers 2 + 3 read from. Re-run between tiers to see deltas.

## 1 · Tier 1 — re-run the free deterministic enrichers

```bash
docker compose run --rm scanner enrich-socials-all
docker compose run --rm scanner harvest-personal-socials
```

`enrich-socials-all` hits Wikidata SPARQL, openparliament.ca (federal MPs), and canada.masto.host (Mastodon). `harvest-personal-socials` walks every politician's `personal_url` and extracts social links from the HTML.

These are idempotent — re-running after a long gap is how fresh Wikidata edits / new MP websites enter the DB. Typical yield after the initial backfill is tens to low hundreds of rows per run.

## 2 · Tier 2 — pattern probe the thin platforms

```bash
# Start with Bluesky — clean public API, highest missing count, best signal.
docker compose run --rm scanner probe-missing-socials --platform bluesky --limit 2000 --dry-run
# Inspect the output. If hit-rate + false-positive rate look reasonable, re-run without --dry-run.
docker compose run --rm scanner probe-missing-socials --platform bluesky --limit 2000

# Then the og:title-based platforms, one at a time:
docker compose run --rm scanner probe-missing-socials --platform twitter   --limit 500
docker compose run --rm scanner probe-missing-socials --platform facebook  --limit 500
docker compose run --rm scanner probe-missing-socials --platform instagram --limit 500
docker compose run --rm scanner probe-missing-socials --platform youtube   --limit 500
docker compose run --rm scanner probe-missing-socials --platform threads   --limit 500
```

Each run prints `candidates → {high, flagged, rejected, no_hit, no_profile}` and a sample of hits. LinkedIn is intentionally excluded (anti-bot too aggressive for pattern probing — it goes to Tier 3 or not at all).

Expected hit rates from the initial rollout (Bluesky on 1,726 missing): ~5% auto-promoted, ~13% flagged for review, rest no hit. Other platforms will have lower yields because their meta tags are JS-rendered or behind login walls — that's expected.

## 3 · Review the flagged queue

Open [`/admin/socials`](http://localhost/admin/socials) (or your deployed equivalent). Filter by platform, spot-check each flagged row against the `evidence_url`, and click **Approve** (clears `flagged_low_confidence`) or **Reject** (hard-deletes the row).

Signals that usually mean Reject:

- Profile describes a different jurisdiction or profession (the "Chicago theatre director" pattern for a Canadian senator)
- Account bio says "parody", "fan", "not affiliated", or names a different person
- Handle obviously belongs to a party caucus or constituency office, not the personal account

Nothing auto-expires from the flagged queue — unreviewed rows stay queryable via the public API with the `flagged_low_confidence` flag set. If the API ever surfaces this to end users, that's where the "we're not 100% sure" badge would appear.

## 4 · Tier 3 — Sonnet agent on the residual (optional)

Prereq: `ANTHROPIC_API_KEY` set in `.env`, then `docker compose up -d scanner-jobs` to pick it up. Without the key, the command aborts cleanly.

```bash
# Dry-run first — writes candidate JSON to stdout, doesn't insert.
docker compose run --rm scanner agent-missing-socials \
    --batch-size 10 --max-batches 2 --dry-run

# Confirm the hits look real, then flip to a real run.
docker compose run --rm scanner agent-missing-socials \
    --batch-size 10 --max-batches 20

# Or target one platform at a time (cheaper, easier to eyeball):
docker compose run --rm scanner agent-missing-socials \
    --platform linkedin --batch-size 10 --max-batches 20
```

Cost guardrails:

- Default cap: **20 batches × 10 politicians = 200 politicians per invocation** before the hard stop
- Per-batch ceiling: `max_tokens=4096` output
- Every Tier-3 row lands with `source='agent_sonnet'` and the agent-supplied `evidence_url` — spot-check via the `/admin/socials` page same as Tier-2 flagged rows

Rough budget at the time of writing: ~$1-2 for a full residual sweep (~1,000 politicians). Adjust `--max-batches` down if you want to cap spend harder.

## 5 · Liveness sweep

```bash
docker compose run --rm scanner verify-socials --limit 5000 --stale-hours 720
```

HEADs/GETs every URL that hasn't been verified in 720h. Flips `is_live` true/false, writes `social_dead` rows in `politician_changes` on live→dead transitions. Safe to run any time — it does not touch discovery state.

On the initial 4,372-row baseline the split was **3,315 live / 379 dead / 678 transient** in ~3m30s. Re-run weekly if you want reliable dead-link signal on the public pages.

## 6 · Refresh the audit snapshot

```bash
docker compose run --rm scanner audit-socials
```

Diff against the snapshot from step 0. Expected deltas after a full pass:

- `pattern_probe` rows grow by a few hundred, mostly split high / flagged
- `agent_sonnet` rows appear if Tier 3 ran
- Flagged counts drop as you work through the admin review queue
- `is_live=null` count drops sharply after the liveness sweep
- Jurisdictional coverage ticks up in the 70-90% band (provincial Nunavut and municipal PE are the usual laggards)

## What *not* to do

- **Do not bypass the tier ordering.** Tier 3 is designed for the residual after Tiers 1 + 2 shrink the candidate set. Jumping straight to `agent-missing-socials` on the full missing matrix wastes a lot of tokens on politicians the deterministic tiers would have found for free.
- **Do not lower `_NAME_ONLY_CAP` in `socials_probe.py::_score()`** without reviewing the admin `/socials` flagged queue first. The cap is what keeps celebrity-name collisions out of the public table.
- **Do not add a new discovery path without a `source=` value.** `upsert_social()` requires it, and `_should_flag()` decides promotion based on the source name. Forgetting the argument is a loud `TypeError` at import time — by design.
- **Do not edit `politician_socials` rows by hand for batch corrections.** Prefer adding an upstream feed or tuning Tier-2 scoring. One-off fixes are fine via `/admin/socials`.