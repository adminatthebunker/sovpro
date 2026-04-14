#!/bin/sh
# ═══════════════════════════════════════════════════════════════════════════
# Scanner cron loop — run inside the scanner-cron container.
# Schedule:
#   - Quick pass every 6 hours (stale > 6h)
#   - Full sweep once a day at 06:00 UTC (stale > 0)
#   - Weekly Open North re-ingest Sunday 02:00 UTC
#     (federal MPs + all provincial/territorial legislatures + all Open
#      North municipal councils + seed-orgs)
#   - Weekly enrichment + socials normalization Sunday 04:00 UTC
#   - Weekly socials liveness verification Monday 03:00 UTC
#
# NOTE: `backfill-terms` is a one-time manual operation (seeds the
# politician_terms table from current holders). It is intentionally NOT
# scheduled here — run it by hand once after the schema migration lands:
#     docker compose run --rm scanner python -m src backfill-terms
# ═══════════════════════════════════════════════════════════════════════════

set -eu

log() { printf '[scanner-cron %s] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"; }

# First boot: seed orgs (idempotent) and ingest reps if DB looks empty.
bootstrap() {
    log "Bootstrapping — seed-orgs"
    python -m src seed-orgs || log "seed-orgs failed (will retry)"

    POLITICIAN_COUNT=$(
        python -c 'import asyncio,asyncpg,os; \
async def n():\n    c=await asyncpg.connect(os.environ["DATABASE_URL"]);\n    r=await c.fetchval("SELECT COUNT(*) FROM politicians");\n    await c.close(); print(r)\nasyncio.run(n())' 2>/dev/null || echo 0
    )
    if [ "${POLITICIAN_COUNT:-0}" -lt 10 ]; then
        log "No politicians yet — ingesting MPs, MLAs, councils"
        python -m src ingest-mps || log "ingest-mps failed"
        python -m src ingest-mlas || log "ingest-mlas failed"
        python -m src ingest-councils || log "ingest-councils failed"
    fi
}

bootstrap

LAST_FULL=0
LAST_WEEKLY=0
LAST_WEEKLY_ENRICH=0
LAST_WEEKLY_VERIFY=0

while true; do
    NOW=$(date -u +%s)
    DOW=$(date -u +%u)   # 1=Mon .. 7=Sun
    HOUR=$(date -u +%H)

    # Weekly ingest: Sunday 02:00 UTC
    # Full Open North re-ingest: federal MPs, Alberta MLAs (legacy), every
    # provincial/territorial legislature, every indexed municipal council,
    # plus org seeds. 86400s guard prevents re-running within the same day.
    if [ "$DOW" = "7" ] && [ "$HOUR" = "02" ] && [ $((NOW - LAST_WEEKLY)) -gt 86400 ]; then
        log "weekly (Sun 02:00): re-ingesting Open North nationwide"
        python -m src ingest-mps || log "ingest-mps failed"
        python -m src ingest-mlas || log "ingest-mlas failed"
        python -m src ingest-councils || log "ingest-councils failed"
        python -m src ingest-legislatures || log "ingest-legislatures failed"
        python -m src ingest-all-councils || log "ingest-all-councils failed"
        python -m src seed-orgs || log "seed-orgs failed"
        LAST_WEEKLY=$NOW
    fi

    # Weekly enrichment + socials normalization: Sunday 04:00 UTC
    # Runs 2h after the Sun 02:00 ingest so fresh social_urls JSONB is
    # available to normalize, and personal_url enrichment sees fresh rosters.
    if [ "$DOW" = "7" ] && [ "$HOUR" = "04" ] && [ $((NOW - LAST_WEEKLY_ENRICH)) -gt 86400 ]; then
        log "weekly (Sun 04:00): normalize-socials + enrich-legislatures + enrich-mps"
        python -m src normalize-socials || log "normalize-socials failed"
        python -m src enrich-legislatures || log "enrich-legislatures failed"
        python -m src enrich-mps || log "enrich-mps failed"
        LAST_WEEKLY_ENRICH=$NOW
    fi

    # Weekly socials liveness verification: Monday 03:00 UTC
    # Verifies up to 5000 socials that haven't been checked in the last week.
    if [ "$DOW" = "1" ] && [ "$HOUR" = "03" ] && [ $((NOW - LAST_WEEKLY_VERIFY)) -gt 86400 ]; then
        log "weekly (Mon 03:00): verify-socials (limit=5000, stale-hours=168)"
        python -m src verify-socials --limit 5000 --stale-hours 168 \
            || log "verify-socials failed"
        LAST_WEEKLY_VERIFY=$NOW
    fi

    # Daily: 06:00 UTC full sweep
    if [ "$HOUR" = "06" ] && [ $((NOW - LAST_FULL)) -gt 43200 ]; then
        log "daily: full scan (stale-hours=0)"
        python -m src scan --stale-hours 0 || log "full scan failed"
        python -m src refresh-views || true
        LAST_FULL=$NOW
    else
        # Quick pass every 6h for sites not scanned in the last 6h
        log "quick scan (stale-hours=6)"
        python -m src scan --stale-hours 6 || log "quick scan failed"
        python -m src refresh-views || true
    fi

    # Sleep 1h between loops
    sleep 3600
done
