#!/usr/bin/env bash
# scripts/backup-database.sh
#
# Daily Postgres backup automation for SovereignWatch / Canadian Political Data.
# Mechanical translation of the runbook in docs/operations.md (Path B — fast
# parallel snapshot). Latest dump stays uncompressed and restore-ready;
# older dumps are demoted to .tar.zst. Designed to run from cron.
#
# Env-var contract (defaults below; override by exporting before invocation):
#   BACKUP_DEST            target directory
#   BACKUP_RETENTION       total dumps kept (1 uncompressed + N-1 compacted)
#   BACKUP_COMPRESS_LEVEL  zstd level for compaction (1..19)
#   BACKUP_PARALLEL_JOBS   pg_dump -j value
#   SOVPRO_REPO            path to the sovpro checkout (for git SHA + .env)

set -euo pipefail

BACKUP_DEST="${BACKUP_DEST:-/media/bunker-admin/Internal/canadian-political-data-backups}"
BACKUP_RETENTION="${BACKUP_RETENTION:-7}"
BACKUP_COMPRESS_LEVEL="${BACKUP_COMPRESS_LEVEL:-19}"
BACKUP_PARALLEL_JOBS="${BACKUP_PARALLEL_JOBS:-8}"
SOVPRO_REPO="${SOVPRO_REPO:-/home/bunker-admin/sovpro}"

TS="$(date -u +%Y%m%dT%H%M%SZ)"
START_EPOCH="$(date -u +%s)"
LOG_FILE="$BACKUP_DEST/sovereignwatch-$TS.log"
MANIFEST="$BACKUP_DEST/sovereignwatch-$TS.manifest.txt"
GLOBALS="$BACKUP_DEST/sovereignwatch-$TS.globals.sql"
DUMP_DIR="$BACKUP_DEST/sovereignwatch-$TS.d"
LOCK_FILE="$BACKUP_DEST/.backup.lock"

stderr_log() {
    printf '[backup %s] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" >&2
}

log() {
    printf '[backup %s] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" | tee -a "$LOG_FILE"
}

fail() {
    log "FAIL: $*"
    exit 1
}

# Preflight — runs before lock so we can fail loudly on misconfig before
# pretending we're "skipping due to concurrency."
[ -d "$BACKUP_DEST" ]  || { stderr_log "BACKUP_DEST does not exist: $BACKUP_DEST"; exit 1; }
[ -w "$BACKUP_DEST" ]  || { stderr_log "BACKUP_DEST not writable: $BACKUP_DEST"; exit 1; }
FSTYPE="$(findmnt -no FSTYPE -T "$BACKUP_DEST" 2>/dev/null || echo unknown)"
case "$FSTYPE" in
    vfat|msdos|exfat)
        stderr_log "BACKUP_DEST is on $FSTYPE — single-file 4 GB ceiling will kill the dump"
        exit 1
        ;;
esac

# Lock — bail cleanly (exit 0) if another run still holds it. Cron will
# retry tomorrow; an overlap during a long dump is normal, not a failure.
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
    stderr_log "another backup is in progress (lock held on $LOCK_FILE), exiting"
    exit 0
fi

# Now safe to write the per-run log file
: > "$LOG_FILE"

# Append a failure verification block to the manifest if we exit non-zero
# after the manifest was opened. Success path appends its own block earlier.
on_exit() {
    local rc=$?
    if [ "$rc" -ne 0 ] && [ -f "$MANIFEST" ]; then
        {
            echo
            echo "verification:"
            echo "exit_code: $rc"
            echo "completed_utc: $(date -u +%Y%m%dT%H%M%SZ)"
            echo "status: FAILED"
        } >> "$MANIFEST" 2>/dev/null || true
    fi
}
trap on_exit EXIT

log "starting backup TS=$TS dest=$BACKUP_DEST retention=$BACKUP_RETENTION fstype=$FSTYPE"

# Load DB_PASSWORD from .env (same source as runbook, same parsing)
[ -f "$SOVPRO_REPO/.env" ] || fail ".env not found at $SOVPRO_REPO/.env"
DB_PASSWORD="$(grep '^DB_PASSWORD=' "$SOVPRO_REPO/.env" | cut -d= -f2-)"
[ -n "$DB_PASSWORD" ] || fail "DB_PASSWORD missing or empty in $SOVPRO_REPO/.env"

# Determine target uid/gid from the backup directory itself, so the chown
# sidecar restores ownership to whoever owns the destination (typically 1000).
DEST_UID="$(stat -c %u "$BACKUP_DEST")"
DEST_GID="$(stat -c %g "$BACKUP_DEST")"

# 1. Manifest header — git SHA, status, db size, row counts, migrations
log "writing manifest header"
{
    echo "# sovereignwatch backup manifest"
    echo "timestamp_utc: $TS"
    echo "git_sha: $(git -C "$SOVPRO_REPO" rev-parse HEAD)"
    echo
    echo "git_status:"
    git -C "$SOVPRO_REPO" status --porcelain
    echo
    echo "db_size:"
    docker exec sw-db psql -U sw -d sovereignwatch -tAc \
        "SELECT pg_size_pretty(pg_database_size('sovereignwatch'))"
    echo
    echo "row_counts:"
    docker exec sw-db psql -U sw -d sovereignwatch -tAc \
        "SELECT 'users', count(*) FROM users UNION ALL
         SELECT 'credit_ledger', count(*) FROM credit_ledger UNION ALL
         SELECT 'politicians', count(*) FROM politicians UNION ALL
         SELECT 'bills', count(*) FROM bills UNION ALL
         SELECT 'speeches', count(*) FROM speeches UNION ALL
         SELECT 'speech_chunks', count(*) FROM speech_chunks"
    echo
    echo "applied_migrations:"
    ls "$SOVPRO_REPO/db/migrations/" | sort
} > "$MANIFEST"

# 2. Globals — sw role + cluster-level config; needed to restore to a fresh server
log "dumping globals (sw role + cluster config)"
docker exec sw-db pg_dumpall -U sw --globals-only > "$GLOBALS" \
    || fail "pg_dumpall --globals-only failed"

# 3. Main dump — parallel directory format, no compression, via throwaway sidecar
log "starting pg_dump (-Fd -j $BACKUP_PARALLEL_JOBS -Z 0)"
DUMP_START="$(date -u +%s)"
docker run --rm \
    --name "sw-backup-$TS" \
    --network sovpro_sw \
    -v "$BACKUP_DEST:/backup" \
    -e PGPASSWORD="$DB_PASSWORD" \
    postgres:16 \
    pg_dump -h db -U sw -d sovereignwatch \
            -Fd -j "$BACKUP_PARALLEL_JOBS" -Z 0 \
            -f "/backup/sovereignwatch-$TS.d" \
            --verbose >>"$LOG_FILE" 2>&1 \
    || fail "pg_dump failed (see log)"
DUMP_END="$(date -u +%s)"
ELAPSED_SECONDS=$((DUMP_END - DUMP_START))
log "pg_dump completed in ${ELAPSED_SECONDS}s"

# 4. Ownership fix-up — sidecar runs as root inside container
log "fixing ownership to ${DEST_UID}:${DEST_GID}"
docker run --rm -v "$BACKUP_DEST:/backup" busybox \
    chown -R "${DEST_UID}:${DEST_GID}" "/backup/sovereignwatch-$TS.d" \
    || fail "chown failed"

# 5. Validate — pg_restore --list must succeed before we touch any old backup
log "validating dump (pg_restore --list)"
TOC_OUTPUT="$(docker run --rm -v "$BACKUP_DEST:/backup" postgres:16 \
    pg_restore --list "/backup/sovereignwatch-$TS.d" 2>&1)" \
    || fail "pg_restore --list failed on new dump"

DUMP_SIZE="$(du -sh "$DUMP_DIR" | cut -f1)"
DUMP_SEGMENTS="$(ls "$DUMP_DIR" | wc -l)"
TOC_ENTRIES="$(printf '%s\n' "$TOC_OUTPUT" | grep -v '^;' | grep -v '^$' | wc -l)"
GLOBALS_SIZE_BYTES="$(stat -c %s "$GLOBALS")"
COMPLETED_UTC="$(date -u +%Y%m%dT%H%M%SZ)"

# Append verification block — same shape as the existing manifest on disk
{
    echo
    echo "verification:"
    echo "dump_size: $DUMP_SIZE"
    echo "dump_segments: $DUMP_SEGMENTS"
    echo "toc_entries: $TOC_ENTRIES"
    echo "globals_size_bytes: $GLOBALS_SIZE_BYTES"
    echo "dump_format: directory (-Fd), parallel_jobs: $BACKUP_PARALLEL_JOBS, compression: 0 (none)"
    echo "elapsed_seconds: $ELAPSED_SECONDS"
    echo "completed_utc: $COMPLETED_UTC"
    echo "exit_code: 0"
} >> "$MANIFEST"

log "validated: size=$DUMP_SIZE segments=$DUMP_SEGMENTS toc_entries=$TOC_ENTRIES"

# 6. Compact older uncompressed dumps. The just-written one stays as .d/.
log "compacting older uncompressed dumps"
shopt -s nullglob
for old_dir in "$BACKUP_DEST"/sovereignwatch-*.d; do
    [ -d "$old_dir" ] || continue
    [ "$old_dir" = "$DUMP_DIR" ] && continue
    base="$(basename "$old_dir" .d)"
    archive="$BACKUP_DEST/$base.tar.zst"
    if [ -f "$archive" ]; then
        log "  $base: archive already exists, removing stale .d/"
        rm -rf "$old_dir"
        rm -f "$BACKUP_DEST/$base.globals.sql" "$BACKUP_DEST/$base.manifest.txt"
        continue
    fi
    log "  compacting $base -> $base.tar.zst"
    members=("$base.d")
    [ -f "$BACKUP_DEST/$base.globals.sql"  ] && members+=("$base.globals.sql")
    [ -f "$BACKUP_DEST/$base.manifest.txt" ] && members+=("$base.manifest.txt")
    if ! tar -C "$BACKUP_DEST" \
            -I "zstd -$BACKUP_COMPRESS_LEVEL -T0 --quiet" \
            -cf "$archive" "${members[@]}" 2>>"$LOG_FILE"; then
        rm -f "$archive"
        fail "tar+zstd of $base failed"
    fi
    if ! zstd -t -q "$archive" 2>>"$LOG_FILE"; then
        rm -f "$archive"
        fail "zstd integrity check failed for $archive"
    fi
    if ! tar -tf "$archive" >/dev/null 2>>"$LOG_FILE"; then
        rm -f "$archive"
        fail "tar listing failed for $archive"
    fi
    log "  archive ok ($(du -sh "$archive" | cut -f1)); removing originals"
    rm -rf "$old_dir"
    rm -f "$BACKUP_DEST/$base.globals.sql" "$BACKUP_DEST/$base.manifest.txt"
done

# 7. Retention — keep newest BACKUP_RETENTION units, delete the rest
log "applying retention policy: keep newest $BACKUP_RETENTION"
mapfile -t units < <(
    {
        for d in "$BACKUP_DEST"/sovereignwatch-*.d;       do [ -d "$d" ] && basename "$d" .d; done
        for a in "$BACKUP_DEST"/sovereignwatch-*.tar.zst; do [ -f "$a" ] && basename "$a" .tar.zst; done
    } | sort -r -u
)
total="${#units[@]}"
log "  found $total backup unit(s)"
if [ "$total" -gt "$BACKUP_RETENTION" ]; then
    for unit in "${units[@]:$BACKUP_RETENTION}"; do
        log "  pruning $unit"
        rm -rf "$BACKUP_DEST/$unit.d"
        rm -f  "$BACKUP_DEST/$unit.tar.zst"
        rm -f  "$BACKUP_DEST/$unit.globals.sql"
        rm -f  "$BACKUP_DEST/$unit.manifest.txt"
        rm -f  "$BACKUP_DEST/$unit.log"
    done
fi

TOTAL_ELAPSED=$(( $(date -u +%s) - START_EPOCH ))
log "OK — completed in ${TOTAL_ELAPSED}s"
