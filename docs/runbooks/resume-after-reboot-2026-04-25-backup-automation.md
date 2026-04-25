# Resume after reboot — 2026-04-25 (daily backup automation built, not yet smoke-tested end-to-end)

**Status when paused:** Daily Postgres backup automation **built and installed**. The runbook in `docs/operations.md` (Path B — fast parallel snapshot) has been mechanically translated into `scripts/backup-database.sh`. User crontab installed for `bunker-admin` to fire it daily at **04:30 UTC**. Existing manual backup at `/media/bunker-admin/Internal/canadian-political-data-backups/sovereignwatch-20260424T230945Z.{d,globals.sql,manifest.txt}` was validated and is **a good backup** (TOC parses, segment count + size match manifest, `exit_code: 0`). **Nothing has been committed yet** — files are on the working tree. **No end-to-end smoke run was performed** before reboot; the script's first real exercise will be either a manual one-off post-reboot, or the next 04:30 UTC cron tick.

**TL;DR to resume:**

```bash
# After reboot:

# 1. Confirm the stack came back up
docker compose ps                      # sw-db should be up; sovpro_sw network should exist
docker network ls | grep sovpro_sw

# 2. Confirm crontab survived the reboot
crontab -l                             # expect MAILTO="" + the 30 4 * * * line
systemctl is-active cron               # expect: active

# 3. (Optional) smoke-test the script end-to-end now instead of waiting for 04:30 UTC.
#    Takes ~17 min, hammers sw-db with parallel pg_dump, writes ~216 GB then compacts
#    the existing backup down to ~30-45 GB. Net disk delta: ~+30 GB.
/home/bunker-admin/sovpro/scripts/backup-database.sh
tail -f /media/bunker-admin/Internal/canadian-political-data-backups/sovereignwatch-*.log

# 4. Verify the result (see "Post-run verification" below)

# 5. Commit (see "Commits" below)
```

---

## What was done in this session

### Existing backup validation (read-only)

Verified the manual backup taken on 2026-04-24:

| Check | Result |
|---|---|
| `pg_restore --list` reads `toc.dat` | ✅ 319 entries, format `DIRECTORY`, dbname `sovereignwatch` |
| Segment count vs manifest's `dump_segments: 40` | ✅ 40 files in `.d/` |
| Total size vs manifest's `dump_size: 216G` | ✅ 216 GB |
| `toc.dat` end-of-stream | ✅ no truncation; ends on a normal entry record |
| Manifest `exit_code: 0`, `elapsed_seconds: 1035` | ✅ |

**Verdict: existing backup is valid.** The new script will compact it to `.tar.zst` on its first successful run.

### Files written (working tree, not committed)

**New (2):**
- `scripts/backup-database.sh` — bash, executable, syntax-checked. Mechanical translation of `docs/operations.md` lines 278–323. flock-guarded, validates each new dump with `pg_restore --list` before touching prior backups, demotes older `.d/` dumps to `.tar.zst` (zstd -19), prunes beyond `BACKUP_RETENTION` (default 7) total units. Env knobs: `BACKUP_DEST`, `BACKUP_RETENTION`, `BACKUP_COMPRESS_LEVEL`, `BACKUP_PARALLEL_JOBS`, `SOVPRO_REPO`.
- `docs/runbooks/resume-after-reboot-2026-04-25-backup-automation.md` — this file.

**Modified (1):**
- `docs/operations.md` — adds an "Automation (cron)" subsection under "Path B" pointing at the script + cron line. Manual one-shot procedure intact below it.

### Host-level changes (NOT in the working tree)

- **`bunker-admin` crontab installed** with:
  ```
  MAILTO=""
  PATH=/usr/bin:/bin

  # daily Postgres backup -- 04:30 UTC, before the 06:00 scanner sweep
  30 4 * * * /home/bunker-admin/sovpro/scripts/backup-database.sh >/dev/null 2>&1
  ```
  No prior crontab existed; this was a fresh install. `systemctl is-active cron` → `active`.

### Decisions baked into the script (confirmed via AskUserQuestion before writing)

- **Retention = 7** total units (1 uncompressed latest + 6 compacted).
- **Compression = zstd -19 -T0** (parallel, ~5-7× ratio on Postgres dumps).
- **04:30 UTC daily** — between scanner-cron's 02:00 UTC weekly Open North re-ingest (Sunday) and the 06:00 UTC daily full sweep. Avoids the heaviest scanner contention.
- **User crontab, not systemd timer.** Host has no passwordless sudo and `Linger=no` for `bunker-admin`, so user-level systemd timers wouldn't fire unattended. System-level timers would need a sudo dance the user wasn't prompted for.

### Decisions baked into the architecture

- **Validate before demoting.** `pg_restore --list` on the new dump must succeed before any old `.d/` is touched. A bad new dump cannot cascade into deletion of healthy old history.
- **flock exits 0 on contention.** A long-running dump overlapping the next 04:30 trigger is normal, not a failure — exiting 0 keeps cron quiet. Real failures still exit non-zero.
- **chown sidecar reads `stat -c %u/%g` on `BACKUP_DEST`** rather than the runbook's hardcoded `1000:1000`. Same value today; self-adjusts if the destination ever moves.
- **Companion files bundle into the `.tar.zst`.** `.globals.sql` + `.manifest.txt` go inside the archive together with the `.d/` so the unit moves as one (easier retention pruning + future USB mirroring).

---

## Resume procedure

### 1. Confirm the stack came back up

```bash
cd /home/bunker-admin/sovpro
docker compose ps
# Expected: sw-db at minimum running. The script depends on:
#   - container name `sw-db`
#   - network `sovpro_sw`
docker network ls | grep sovpro_sw   # must exist; pg_dump sidecar joins it
```

If `sovpro_sw` is missing, `sovpro up` (or `docker compose up -d`) once before the script runs.

### 2. Confirm cron survived

```bash
crontab -l
# Expected output (verbatim):
#   MAILTO=""
#   PATH=/usr/bin:/bin
#
#   # daily Postgres backup -- 04:30 UTC, before the 06:00 scanner sweep
#   30 4 * * * /home/bunker-admin/sovpro/scripts/backup-database.sh >/dev/null 2>&1

systemctl is-active cron     # expect: active
```

If the crontab is gone (very unlikely — user crontabs persist in `/var/spool/cron/crontabs/bunker-admin` across reboots), reinstall:

```bash
printf 'MAILTO=""\nPATH=/usr/bin:/bin\n\n# daily Postgres backup -- 04:30 UTC, before the 06:00 scanner sweep\n30 4 * * * /home/bunker-admin/sovpro/scripts/backup-database.sh >/dev/null 2>&1\n' | crontab -
```

### 3. (Recommended) Run the script once as a smoke test

```bash
/home/bunker-admin/sovpro/scripts/backup-database.sh
```

Wall-time: ~17 min on the live DB (matches the existing manifest's `elapsed_seconds: 1035`). Watch progress live in another shell:

```bash
tail -f /media/bunker-admin/Internal/canadian-political-data-backups/sovereignwatch-*.log
```

Expected log progression:
```
[backup ...Z] starting backup TS=... dest=... retention=7 fstype=ext4
[backup ...Z] writing manifest header
[backup ...Z] dumping globals (sw role + cluster config)
[backup ...Z] starting pg_dump (-Fd -j 8 -Z 0)
[backup ...Z] pg_dump completed in 1000ish s
[backup ...Z] fixing ownership to 1000:1000
[backup ...Z] validating dump (pg_restore --list)
[backup ...Z] validated: size=216G segments=40 toc_entries=300+
[backup ...Z] compacting older uncompressed dumps
[backup ...Z]   compacting sovereignwatch-20260424T230945Z -> sovereignwatch-20260424T230945Z.tar.zst
[backup ...Z]   archive ok (35-50G); removing originals
[backup ...Z] applying retention policy: keep newest 7
[backup ...Z]   found 2 backup unit(s)
[backup ...Z] OK — completed in ~XXXX s
```

### 4. Post-run verification

```bash
ls -la /media/bunker-admin/Internal/canadian-political-data-backups/
# Expect:
#   sovereignwatch-<new-TS>.d/                 (uncompressed, ~216 GB, 40 segments)
#   sovereignwatch-<new-TS>.globals.sql        (~507 bytes)
#   sovereignwatch-<new-TS>.manifest.txt       (full manifest with verification block)
#   sovereignwatch-<new-TS>.log                (run log)
#   sovereignwatch-20260424T230945Z.tar.zst    (compacted; ~30-45 GB)
#   .backup.lock                               (0-byte flock target)

# Confirm the new dump is structurally valid
docker run --rm -v /media/bunker-admin/Internal/canadian-political-data-backups:/backup postgres:16 \
  pg_restore --list /backup/sovereignwatch-<new-TS>.d | head -15

# Confirm the demoted archive is intact
zstd -t /media/bunker-admin/Internal/canadian-political-data-backups/sovereignwatch-20260424T230945Z.tar.zst
tar -tf /media/bunker-admin/Internal/canadian-political-data-backups/sovereignwatch-20260424T230945Z.tar.zst | head

# Confirm the manifest verification block
tail -15 /media/bunker-admin/Internal/canadian-political-data-backups/sovereignwatch-<new-TS>.manifest.txt
# Expect: dump_size, dump_segments, toc_entries, dump_format, parallel_jobs, compression,
#         elapsed_seconds, completed_utc, exit_code: 0
```

### 5. Confirm the lock guard works (optional, fast)

```bash
/home/bunker-admin/sovpro/scripts/backup-database.sh &
sleep 1
/home/bunker-admin/sovpro/scripts/backup-database.sh
# Second invocation should print to stderr "another backup is in progress" and exit 0,
# leaving the first one running. Wait for the first one to complete or kill it.
```

### 6. Commit

```bash
cd /home/bunker-admin/sovpro
git add scripts/backup-database.sh \
        docs/operations.md \
        docs/runbooks/resume-after-reboot-2026-04-25-backup-automation.md
git status
# Verify only those 3 paths are staged.

git commit -m "$(cat <<'EOF'
infra: daily postgres backup automation

Translates the docs/operations.md Path B runbook into scripts/backup-database.sh
+ a 04:30 UTC user-cron entry. Latest dump stays uncompressed (restore-ready);
older dumps demote to .tar.zst (zstd -19). Retention default 7 units. Validates
each new dump with pg_restore --list before touching prior backups.

Bunker Admin
EOF
)"
```

The crontab itself is host-state, not in the repo — no commit for it.

---

## Known limitations carried into this state

- **First end-to-end run has not been observed.** The script is bash-syntax-checked and binary-resolved (all of `docker`, `git`, `flock`, `findmnt`, `tar`, `zstd`, etc. exist at `/usr/bin/`), but no full dump-→-validate-→-compact cycle has run yet. Step 3 above (or the next 04:30 UTC cron tick) is the first real exercise.
- **No off-host mirror.** The USB / S3 / B2 targets described in `docs/operations.md` lines 330–373 are out of scope. A second cron line can wrap an `rsync`/`b2 sync` to the LUKS USB (when mounted) or a remote target. Add later.
- **No alerting.** The script writes a per-run log but doesn't email, ping Kuma, or post anywhere on failure. Cron's MAILTO is `""` because there's no MTA on the host. Re-evaluate when an escalation channel exists.
- **No automated restore drill.** The strongest backup-validity signal short of a real restore is `pg_restore --list` (which the script runs). A weekly "restore to a throwaway DB and SELECT count(*)" job would be the next maturity step but needs a spare host or temp ramdisk.
- **First run will demote the only existing backup.** After step 3 (or the first cron tick), the 2026-04-24 backup will exist only as `.tar.zst`. That's the intended design (1 uncompressed + N-1 compacted), but worth knowing: if you needed a fast, ready-to-restore version of the 2026-04-24 backup specifically, copy it elsewhere *before* the first run. Not required for normal operation.
- **`sovpro db backup` (Path A) is unchanged.** The legacy gzipped-SQL path stays as-is for ad-hoc snapshots / sharing. Two paths, two purposes.
- **Disk math assumes the 1.8 TB internal drive at ~929 GB free today.** Steady-state with retention 7: 1×216 + 6×~35 ≈ ~430 GB. If the live DB grows substantially, revisit retention before the math breaks.

---

## If something goes wrong

- **Script exits non-zero with `pg_dump failed`** → check the per-run log (`sovereignwatch-<TS>.log`); pg_dump's full stderr is in there. Common causes: `sw-db` container not running (run `docker compose up -d db` first), DB_PASSWORD missing/wrong in `.env`, or the `sovpro_sw` network gone (run `docker compose up -d`).
- **`pg_restore --list failed on new dump`** → the dump completed but is structurally invalid. Don't retry blindly; investigate the log. Old backups are intact (script bails before compaction).
- **Lock-file stuck (`.backup.lock` held but no script running)** → flock releases the lock when the file descriptor closes; if it doesn't, a previous run was killed without cleanup. `rm /media/bunker-admin/Internal/canadian-political-data-backups/.backup.lock` is safe — it's a 0-byte sentinel, not a state file.
- **Disk full during compaction** → the script `fail`s during `tar -I zstd ...`; the partial `.tar.zst` is removed by the `fail` path. The original `.d/` directory stays in place. Free disk and re-run; the next run will retry compaction.
- **Want to skip a single day** → `touch /media/bunker-admin/Internal/canadian-political-data-backups/.backup.lock && exec 9>"$_" && flock 9 &` in a long-lived shell will hold the lock; the cron tick will skip cleanly. Or just edit the crontab.
- **Want to change retention** → edit the cron line to add `BACKUP_RETENTION=N` before the script path, e.g. `30 4 * * * BACKUP_RETENTION=14 /home/.../backup-database.sh ...`. Or export it system-wide in the crontab header.

---

## Next session after this one wraps

1. **Off-host mirror.** Add a second cron entry that `rsync`s the latest unit (or all units) to the LUKS USB when it's mounted, or to a remote target (Backblaze B2 is the cheapest fit for 200+ GB; encrypted via `rclone crypt` on top). Reference: `docs/operations.md` lines 330–373.
2. **Restore drill.** Weekly job that picks the newest `.tar.zst`, extracts it to a scratch path, restores into a temp Postgres container on a different port, and asserts row counts match the manifest. Catches "the dump exists but pg_restore actually fails" — the strongest non-production-impacting validity signal.
3. **Alerting.** Even without an MTA, a `curl` to a Kuma push monitor on script success/failure gives you a dashboard and PagerDuty-style escalation if the backup ever silently stops running. The Kuma instance is already on-host (per `docs/architecture.md`).
