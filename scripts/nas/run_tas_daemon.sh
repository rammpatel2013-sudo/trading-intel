#!/bin/bash
# run_tas_daemon.sh - written 2026-11-19 (tape capture fix). Starts the TAS tape daemon under a FIXED container
# name so a second start is a no-op. Schedule it DAILY every 15 min 00:00-23:45 (DSM task, user root;
# outside the session windows below it exits at once):
#     bash /var/services/homes/drmithil/trading-intel/scripts/nas/run_tas_daemon.sh
# If the daemon died (internet drop, out of memory, a deploy restarting docker), the next check starts it again,
# so at most ~15 minutes of tape are lost instead of the rest of the day. Log: ~/ti_tas_capture_daemon.log
HOME_DIR="/var/services/homes/drmithil"
REPO_DIR="$HOME_DIR/trading-intel"
ENV_FILE="$REPO_DIR/.env"
DOCKER="/usr/local/bin/docker"
NAME="ti-tas-daemon"
set -uo pipefail

# 2026-11-19 A1b: the tape runs in every session - Mon-Fri 00:00-17:00 (gth tail, pre, rth, post, curb)
# and Sun-Thu 20:15-24:00 (gth). Holidays: the daemon itself exits at once (tape_sessions knows the
# calendar). Use New York time even if the NAS clock is set to another zone; fall back to the NAS
# clock only if this box has no zone files.
if [ "$(TZ=America/New_York date +%Z)" = "EST" ] || [ "$(TZ=America/New_York date +%Z)" = "EDT" ]; then
  export TZ=America/New_York
fi
dow=$(date +%u); hm=$(date +%H%M)
ok=0
if [ "$dow" -le 5 ] && [ "$hm" -lt 1700 ]; then ok=1; fi              # Mon-Fri until the curb ends
if { [ "$dow" -le 4 ] || [ "$dow" -eq 7 ]; } && [ "$hm" -ge 2015 ]; then ok=1; fi   # Sun-Thu evening GTH
if [ "$ok" -ne 1 ]; then exit 0; fi

if "$DOCKER" ps --format '{{.Names}}' | grep -qx "$NAME"; then exit 0; fi
"$DOCKER" rm -f "$NAME" >/dev/null 2>&1 || true   # a stopped leftover with the same name

DB_URL="$(grep -oP '(?<=^NAS_DATABASE_URL=).*' "$ENV_FILE" 2>/dev/null | tr -d '"'"'" || true)"
DB_URL="${DB_URL:-postgresql+psycopg://intel:intel@trading-intel-pg:5432/trading_intel}"
log="$HOME_DIR/ti_tas_capture_daemon.log"
echo "=== $(date '+%Y-%m-%d %H:%M:%S') start tas_capture_daemon (run_tas_daemon.sh, container ${NAME}) ===" >> "$log"
"$DOCKER" network create trading-intel-net 2>/dev/null || true
"$DOCKER" network connect trading-intel-net trading-intel-pg 2>/dev/null || true
nohup sh -c "\"$DOCKER\" run --rm --name \"$NAME\" --network trading-intel-net \
  -v \"${ENV_FILE}:/app/.env\" -v \"${REPO_DIR}/secrets:/app/secrets\" -v \"${REPO_DIR}/scripts:/app/scripts\" \
  -v \"${REPO_DIR}/reports:/app/reports\" -e \"DATABASE_URL=${DB_URL}\" trading-intel \
  sh -c 'python -m trading_intel.scheduler.track tas_capture_daemon'; echo \"=== \$(date '+%Y-%m-%d %H:%M:%S') tas_capture_daemon EXIT \$? ===\"" >> "$log" 2>&1 &
exit 0
