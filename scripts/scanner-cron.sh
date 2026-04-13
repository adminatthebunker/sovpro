#!/bin/sh
# ═══════════════════════════════════════════════════════════════════════════
# Scanner cron loop — run inside the scanner-cron container.
# Schedule:
#   - Quick pass every 6 hours (stale > 6h)
#   - Full sweep once a day at 06:00 UTC (stale > 0)
#   - Weekly Open North re-ingest Sunday 02:00 UTC
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

while true; do
    NOW=$(date -u +%s)

    # Weekly: Sunday 02:00
    if [ "$(date -u +%u)" = "7" ] && [ "$(date -u +%H)" = "02" ] && [ $((NOW - LAST_WEEKLY)) -gt 86400 ]; then
        log "weekly: re-ingesting Open North"
        python -m src ingest-mps || true
        python -m src ingest-mlas || true
        python -m src ingest-councils || true
        LAST_WEEKLY=$NOW
    fi

    # Daily: 06:00 UTC full sweep
    if [ "$(date -u +%H)" = "06" ] && [ $((NOW - LAST_FULL)) -gt 43200 ]; then
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
