#!/bin/bash
# cvx_watchdog.sh - created 2026-11-13 (data check: the 11/13 minute-log hole 11:03-12:24).
#
# What happened 11/13: at 11:05 a Convex read timed out, then the collector container could not resolve
# convexvalue.com ("Temporary failure in name resolution") every minute until 11:42, then hung silently
# (a DNS lookup has no timeout) until the container was started again at 12:24 - 81 minutes with no data.
# A fresh container resolved and pulled at once.
#
# This watchdog: Mon-Fri 09:33-16:00 ET, if today's raw pull file has not grown for 4 minutes, restart the
# collector container (cvxmap-collect); if it is not running at all, start the cockpit set again.
# Logs to ~/ti_cvx_watchdog.log. Changes nothing when the collector is healthy.
#
# DSM Task Scheduler (root, Mon-Fri, first run 09:35, repeat every 5 minutes, last run 16:00):
#   bash /var/services/homes/drmithil/trading-intel/scripts/nas/cvx_watchdog.sh
set -uo pipefail
HOME_DIR="/var/services/homes/drmithil"
REPO_DIR="$HOME_DIR/trading-intel"
DATA_DIR="$REPO_DIR/scripts/cvxmap/data"
DOCKER="/usr/local/bin/docker"
LOG="$HOME_DIR/ti_cvx_watchdog.log"
STALE_S="${CVX_WATCHDOG_STALE_S:-240}"

now_et() { TZ="America/New_York" date "$@"; }
dow="$(now_et +%u)"; hm="$(now_et +%H%M)"; ymd="$(now_et +%Y%m%d)"
[ "$dow" -ge 6 ] && exit 0
[ "$hm" -lt 0933 ] || [ "$hm" -gt 1600 ] && exit 0

raw="$DATA_DIR/raw_${ymd}.ndjson.gz"
if [ ! -f "$raw" ]; then
    age=99999
else
    age=$(( $(date +%s) - $(stat -c %Y "$raw") ))
fi
[ "$age" -le "$STALE_S" ] && exit 0

running="$("$DOCKER" ps --filter name=^/cvxmap-collect$ --format '{{.Names}}' 2>/dev/null)"
{
    echo "=== $(now_et '+%Y-%m-%d %H:%M:%S') ET raw file ${age}s old (limit ${STALE_S}s), collector running: ${running:-no}"
    if [ -n "$running" ]; then
        "$DOCKER" restart -t 5 cvxmap-collect && echo "  restarted cvxmap-collect"
    else
        bash "$REPO_DIR/scripts/nas/run_cvxmap.sh" start && echo "  started the cockpit set (collector was not running)"
    fi
} >> "$LOG" 2>&1
exit 0
