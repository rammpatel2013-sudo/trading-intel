#!/bin/bash
# backup_db.sh - created 2026-11-15 (database design session).
#
# Why: the trading-intel database had no backup, and /volume1 (Postgres + the Convex raw files) is RAID 0
# on drives 1+2 - if either drive fails, all of it is lost. /volume2 is drive 3 on its own.
#
# What it does each night, all copies under /volume2/ti-backup:
#   1. db/daily/trading_intel_YYYYMMDD.dump - pg_dump custom format (compressed; indexes are rebuilt on
#      restore, so it is ~4 GB on 11/15 vs 19 GB live). Checked with pg_restore --list before it counts.
#      Keeps 7 nights. Sunday's dump is also kept in db/weekly (5 kept), the 1st of the month's in
#      db/monthly (12 kept). Hard links, so those extra copies take no extra space.
#      Old dumps are pruned only after tonight's dump passed its check.
#   2. raw/ - scripts/cvxmap/data (Convex per-minute raw files: the only copy, Convex keeps no history),
#      reports/optionsdata, reports/replay. Copied WITHOUT deleting: a file removed on volume1 stays here.
#   3. config/ - .env and secrets/ (API keys, Gmail token) so the NAS can be rebuilt. Folder mode 700.
#   4. Sundays (or --restore-test): restore test - table vix_data from the newest dump into a scratch
#      database, row count compared with the live table, scratch database dropped.
# Telegram only when something fails. Log: ~/ti_backup_db.log ("=== ... EXIT n ===" like run_job.sh).
#
# One-time setup: DSM > Control Panel > Shared Folder > Create "ti-backup" on Volume 2 with the
# Recycle Bin box UNTICKED (otherwise every pruned dump is kept in #recycle and the disk fills).
# DSM Task Scheduler: user root, Daily, 03:15:
#   bash /var/services/homes/drmithil/trading-intel/scripts/nas/backup_db.sh
# Manual run in the background (~20-60 min):
#   sudo sh -c 'nohup bash /var/services/homes/drmithil/trading-intel/scripts/nas/backup_db.sh >/dev/null 2>&1 &'
# Options: --no-raw (database only)   --restore-test (run the restore test today)
set -uo pipefail

HOME_DIR="${TI_HOME_DIR:-/var/services/homes/drmithil}"
REPO_DIR="${TI_REPO_DIR:-$HOME_DIR/trading-intel}"
ENV_FILE="$REPO_DIR/.env"
DOCKER="/usr/local/bin/docker"
PG_CONTAINER="trading-intel-pg"
IMAGE="trading-intel"
NETWORK="trading-intel-net"
DB_NAME="trading_intel"
DB_USER="intel"
DEST="${TI_BACKUP_DIR:-/volume2/ti-backup}"
DATA_VOLUME="${TI_DATA_VOLUME:-/volume1}"
LOG="${TI_BACKUP_LOG:-$HOME_DIR/ti_backup_db.log}"
MIN_DUMP_BYTES="${TI_BACKUP_MIN_BYTES:-500000000}"        # a real dump is GBs; smaller = something broke
MIN_FREE_KB="${TI_BACKUP_MIN_FREE_KB:-20971520}"          # 20 GB floor (or 2x the last dump, if larger)
KEEP_DAILY=7
KEEP_WEEKLY=5
KEEP_MONTHLY=12
RAW_DIRS="scripts/cvxmap/data reports/optionsdata reports/replay"
LOCK="${TI_BACKUP_LOCK:-/tmp/ti_backup_db.lock}"

NO_RAW=0
RESTORE_TEST=0
for a in "$@"; do
    case "$a" in
        --no-raw) NO_RAW=1 ;;
        --restore-test) RESTORE_TEST=1 ;;
        *) echo "unknown option: $a" >&2; exit 2 ;;
    esac
done

FAILS=()
et() { TZ="America/New_York" date "$@"; }
log() { echo "$(et '+%Y-%m-%d %H:%M:%S') $*"; }
fail() { FAILS+=("$1"); log "FAIL: $1"; }
human() { awk -v b="$1" 'BEGIN{split("B KB MB GB TB",u);i=1;while(b>=1024&&i<5){b/=1024;i++};printf "%.1f %s",b,u[i]}'; }

# Run a Postgres client tool inside the database container (TI_BACKUP_PGX overrides it for tests).
pgx() {
    if [ -n "${TI_BACKUP_PGX:-}" ]; then "$TI_BACKUP_PGX" "$@"; else "$DOCKER" exec -i "$PG_CONTAINER" "$@"; fi
}

# Plain-text Telegram message through the image's own client (no "<" or ">" in the text).
tg() {
    [ -n "${TI_BACKUP_NO_TG:-}" ] && return 0
    "$DOCKER" run --rm --network "$NETWORK" -v "${ENV_FILE}:/app/.env" "$IMAGE" python -c \
        'import sys
from trading_intel.config import get_settings
from trading_intel.clients.telegram import TelegramClient
TelegramClient(get_settings()).send_message(sys.argv[1])' "$1" >/dev/null 2>&1 || true
}

# Copy a folder into the backup without ever deleting there (rsync; cp -a fallback if rsync is missing).
copy_tree() {
    if command -v rsync >/dev/null 2>&1; then
        rsync -a "$1/" "$2/"
    else
        mkdir -p "$2" && cp -a -u "$1/." "$2/"
    fi
}

# Keep the newest N files (names carry the date, so name order = date order).
prune() {
    local dir="$1" keep="$2"
    ls -1 "$dir"/*.dump 2>/dev/null | sort -r | tail -n +$((keep + 1)) | while read -r f; do
        rm -f "$f" && log "pruned $(basename "$dir")/$(basename "$f")"
    done
}

check_destination() {
    if [ ! -d "$DEST" ]; then
        fail "$DEST is missing - create the shared folder ti-backup on Volume 2 in DSM first"
        return 1
    fi
    if [ -z "${TI_BACKUP_SKIP_DISKCHECK:-}" ] && [ "$(stat -c %d "$DEST")" = "$(stat -c %d "$DATA_VOLUME")" ]; then
        fail "$DEST is on the same disk as $DATA_VOLUME - that is not a backup"
        return 1
    fi
    local last need avail
    last="$(ls -1 "$DEST"/db/daily/*.dump 2>/dev/null | sort -r | head -n 1)"
    need="$MIN_FREE_KB"
    if [ -n "$last" ]; then
        local twice=$(( $(stat -c %s "$last") * 2 / 1024 ))
        [ "$twice" -gt "$need" ] && need="$twice"
    fi
    avail="$(df -Pk "$DEST" | awk 'NR==2{print $4}')"
    if [ "$avail" -lt "$need" ]; then
        fail "only $(human $((avail * 1024))) free on $DEST, need $(human $((need * 1024)))"
        return 1
    fi
    return 0
}

dump_database() {
    mkdir -p "$DEST/db/daily" "$DEST/db/weekly" "$DEST/db/monthly"
    local day out tmp t0 size ntab
    day="$(et +%Y%m%d)"
    out="$DEST/db/daily/${DB_NAME}_${day}.dump"
    tmp="$out.part"
    t0=$(date +%s)
    if ! pgx pg_dump -U "$DB_USER" -d "$DB_NAME" -Fc > "$tmp"; then
        rm -f "$tmp"; fail "pg_dump failed"; return 1
    fi
    size=$(stat -c %s "$tmp")
    if [ "$size" -lt "$MIN_DUMP_BYTES" ]; then
        rm -f "$tmp"; fail "dump is only $(human "$size") - expected GBs"; return 1
    fi
    if ! pgx pg_restore --list < "$tmp" > "$tmp.toc"; then
        rm -f "$tmp" "$tmp.toc"; fail "pg_restore cannot read tonight's dump"; return 1
    fi
    ntab=$(grep -c " TABLE DATA " "$tmp.toc")
    rm -f "$tmp.toc"
    mv -f "$tmp" "$out"
    log "db dump OK: $(basename "$out") $(human "$size"), $ntab tables with data, $(( $(date +%s) - t0 ))s"

    [ "$(et +%u)" = "7" ] && ln -f "$out" "$DEST/db/weekly/$(basename "$out")" && log "kept as weekly"
    [ "$(et +%d)" = "01" ] && ln -f "$out" "$DEST/db/monthly/$(basename "$out")" && log "kept as monthly"
    prune "$DEST/db/daily" "$KEEP_DAILY"
    prune "$DEST/db/weekly" "$KEEP_WEEKLY"
    prune "$DEST/db/monthly" "$KEEP_MONTHLY"
    return 0
}

copy_raw() {
    local src
    for src in $RAW_DIRS; do
        if [ ! -d "$REPO_DIR/$src" ]; then
            log "raw $src: not on this NAS, skipped"
            continue
        fi
        mkdir -p "$DEST/raw/$src"
        if copy_tree "$REPO_DIR/$src" "$DEST/raw/$src"; then
            log "raw $src OK ($(du -sh "$DEST/raw/$src" | cut -f1) in the backup)"
        else
            fail "copy of $src failed"
        fi
    done
}

copy_config() {
    local ok=1
    mkdir -p "$DEST/config"
    chmod 700 "$DEST/config"
    if [ -f "$ENV_FILE" ]; then
        { cp -p "$ENV_FILE" "$DEST/config/env.backup" && chmod 600 "$DEST/config/env.backup"; } || { ok=0; fail "copy of .env failed"; }
    fi
    if [ -d "$REPO_DIR/secrets" ]; then
        copy_tree "$REPO_DIR/secrets" "$DEST/config/secrets" || { ok=0; fail "copy of secrets/ failed"; }
    fi
    [ "$ok" = "1" ] && log "config copied (.env, secrets/)"
}

restore_test() {
    local latest live got
    latest="$(ls -1 "$DEST"/db/daily/*.dump 2>/dev/null | sort -r | head -n 1)"
    if [ -z "$latest" ]; then
        fail "restore test: no dump to test"; return 1
    fi
    pgx psql -U "$DB_USER" -d postgres -qc "DROP DATABASE IF EXISTS ti_restore_test" >/dev/null 2>&1 &&
        pgx psql -U "$DB_USER" -d postgres -qc "CREATE DATABASE ti_restore_test" >/dev/null || {
        fail "restore test: cannot create the scratch database"; return 1; }
    if ! pgx pg_restore -U "$DB_USER" -d ti_restore_test --no-owner -t vix_data < "$latest"; then
        fail "restore test: pg_restore of vix_data from $(basename "$latest") failed"
    else
        live="$(pgx psql -U "$DB_USER" -d "$DB_NAME" -tAc "select count(*) from vix_data")"
        got="$(pgx psql -U "$DB_USER" -d ti_restore_test -tAc "select count(*) from vix_data")"
        if [ -z "$got" ] || [ "$got" = "0" ]; then
            fail "restore test: vix_data came back empty from $(basename "$latest")"
        elif [ "$got" = "$live" ]; then
            log "restore test OK: vix_data $got rows from $(basename "$latest") = live"
        else
            log "restore test OK with a note: vix_data $got rows restored vs $live live (rows written since the dump?)"
        fi
    fi
    pgx psql -U "$DB_USER" -d postgres -qc "DROP DATABASE IF EXISTS ti_restore_test" >/dev/null 2>&1 || true
}

main() {
    echo "=== $(et '+%Y-%m-%d %H:%M:%S') start backup_db ==="
    if ! mkdir "$LOCK" 2>/dev/null; then
        if [ -n "$(find "$LOCK" -maxdepth 0 -mmin +360 2>/dev/null)" ]; then
            log "removing a lock older than 6 h (a crashed run)"
            rmdir "$LOCK" 2>/dev/null; mkdir "$LOCK" 2>/dev/null || { log "lock busy - skipped"; return 0; }
        else
            log "another backup is running - skipped"
            return 0
        fi
    fi
    trap 'rmdir "$LOCK" 2>/dev/null' EXIT

    if check_destination; then
        dump_database
        [ "$NO_RAW" = "1" ] || copy_raw
        copy_config
        if [ "$RESTORE_TEST" = "1" ] || [ "$(et +%u)" = "7" ]; then
            restore_test
        fi
        log "backup folder now $(du -sh "$DEST" 2>/dev/null | cut -f1), $(df -Ph "$DEST" | awk 'NR==2{print $4}') free on it"
    fi

    local rc=0
    if [ "${#FAILS[@]}" -gt 0 ]; then
        rc=1
        tg "trading-intel BACKUP FAILED $(et '+%m/%d %H:%M'): ${FAILS[*]:-}. Log: ~/ti_backup_db.log"
    fi
    echo "=== $(et '+%Y-%m-%d %H:%M:%S') backup_db EXIT $rc ==="
    return "$rc"
}

main >> "$LOG" 2>&1
