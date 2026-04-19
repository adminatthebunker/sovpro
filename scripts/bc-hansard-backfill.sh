#!/usr/bin/env bash
# BC Hansard historical backfill — one session at a time.
#
# Runs: ingest → chunk → embed per session, so if markup drift breaks one
# session we notice before loading the next one. Stops on first ingest
# failure (exit code non-zero from the Click command) so operator can
# inspect. Safe to re-run — every stage is idempotent.
#
# Sessions ordered newest-first (P42 down through P38-S4, the last era
# with structured SpeakerBegins markup). P43-S1 and P43-S2 are handled
# separately (already live or one-off).
#
# Usage:
#   ./scripts/bc-hansard-backfill.sh             # all sessions in the list
#   ./scripts/bc-hansard-backfill.sh P42-S5      # single session
set -euo pipefail

SESSIONS=(
    # Recent — should all parse cleanly
    "P42-S5" "P42-S4" "P42-S3" "P42-S2" "P42-S1"
    "P41-S5" "P41-S4" "P41-S3" "P41-S2" "P41-S1"
    "P40-S4" "P40-S3" "P40-S2" "P40-S1"
    "P39-S5" "P39-S4" "P39-S3" "P39-S2" "P39-S1"
    # Pre-2010 — P38-S4 is the oldest with structured markup
    "P38-S5" "P38-S4"
)

if [ $# -ge 1 ]; then
    SESSIONS=("$1")
fi

for slug in "${SESSIONS[@]}"; do
    # Parse "P43-S2" into parliament=43, session=2
    parl=$(echo "$slug" | sed -nE 's/^P([0-9]+)-S([0-9]+)$/\1/p')
    sess=$(echo "$slug" | sed -nE 's/^P([0-9]+)-S([0-9]+)$/\2/p')
    if [ -z "$parl" ] || [ -z "$sess" ]; then
        echo "malformed slug: $slug — expected PNN-SN" >&2
        exit 2
    fi

    echo
    echo "==================== $slug (parliament=$parl session=$sess) ===================="
    t0=$(date +%s)

    # Ingest
    if ! docker exec sw-scanner-jobs python -m src ingest-bc-hansard --parliament "$parl" --session "$sess"; then
        echo "!! ingest-bc-hansard failed for $slug; stopping" >&2
        exit 1
    fi

    # Chunk any new speeches (safe to run while a background embed is
    # draining the queue — chunk writes to a different column and is
    # serialized at the row level by Postgres).
    docker exec sw-scanner-jobs python -m src chunk-speeches

    # Embed is run out-of-band (background job on the scanner) during
    # historical backfill so GPU throughput isn't paced by per-session
    # sequencing. Caller should verify embedded counts after the loop.

    t1=$(date +%s)
    echo "=== $slug done in $((t1 - t0))s ==="

    # Per-session DB tally
    docker exec sw-db psql -U sw -d sovereignwatch -At -c "
        SELECT 'speeches=' || count(*) || ' linked=' || count(politician_id)
               || ' (' || round(count(politician_id)::numeric/GREATEST(count(*),1)*100, 1) || '%)'
          FROM speeches
         WHERE province_territory='BC' AND source_system='hansard-bc'
           AND session_id=(
             SELECT id FROM legislative_sessions
              WHERE level='provincial' AND province_territory='BC'
                AND parliament_number=$parl AND session_number=$sess
           );
    "
done

echo
echo "=== backfill complete ==="
