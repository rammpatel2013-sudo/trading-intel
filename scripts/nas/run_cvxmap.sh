#!/bin/bash
# Convex cockpit collector on the NAS — the long-running sibling of run_job.sh.
#
# run_job.sh runs ONE-SHOT scheduler MODULES (`python -m trading_intel...`).
# These are long-lived SCRIPTS under scripts/cvxmap/, so they need their own
# wrapper. scripts/ is bind-mounted into the image, so shipping a change here is
# a TARBALL PULL ONLY — no `docker build` (see [[report-deploy-workflow]]).
#
# DSM tasks (user: root, run with bash):
#     bash /var/services/homes/drmithil/trading-intel/scripts/nas/run_cvxmap.sh start    # 09:25 weekdays
#     bash /var/services/homes/drmithil/trading-intel/scripts/nas/run_cvxmap.sh stop     # 17:05 weekdays (belt and braces)
#
# The collector and writer BOTH self-exit at 17:00 ET, so `stop` is only there to
# clear a wedged container. `serve` is left running so the cockpit stays readable
# after hours.
#
# Cockpit:  http://192.168.1.211:8899/cvx_cockpit.html
#
HOME_DIR="/var/services/homes/drmithil"
REPO_DIR="$HOME_DIR/trading-intel"
ENV_FILE="$REPO_DIR/.env"
IMAGE="trading-intel"
NETWORK="trading-intel-net"
DOCKER="/usr/local/bin/docker"
PORT=8899
# Data lands in the bind-mounted scripts/ tree, so it survives `--rm` and is
# visible on the NAS filesystem. A tarball pull does NOT delete it (tar only
# overwrites what is in the archive) — and data/ is gitignored.
DATA_DIR="$REPO_DIR/scripts/cvxmap/data"

set -uo pipefail
LOG="$HOME_DIR/ti_cvxmap.log"

run_one() {   # name, extra docker args, command
    local name="$1"; shift
    local extra="$1"; shift
    "$DOCKER" rm -f "$name" >/dev/null 2>&1 || true
    # shellcheck disable=SC2086
    "$DOCKER" run -d --rm --name "$name" --network "$NETWORK" \
        -v "${ENV_FILE}:/app/.env" \
        -v "${REPO_DIR}/scripts:/app/scripts" \
        -e "PYTHONPATH=/app" \
        $extra \
        "$IMAGE" sh -c "$*" >/dev/null
    echo "  started $name"
}

case "${1:-}" in
  start)
    {
      echo "=== $(date '+%Y-%m-%d %H:%M:%S') cvxmap START ==="
      "$DOCKER" network create "$NETWORK" 2>/dev/null || true
      mkdir -p "$DATA_DIR"
      run_one cvxmap-collect ""                 "python scripts/cvxmap/cvx_collect.py"
      run_one cvxmap-write   ""                 "python scripts/cvxmap/cvx_live.py"
      run_one cvxmap-serve   "-p ${PORT}:${PORT}" "python scripts/cvxmap/serve_cvx.py ${PORT}"
      echo "  cockpit: http://192.168.1.211:${PORT}/cvx_cockpit.html"
    } >> "$LOG" 2>&1
    ;;
  stop)
    {
      echo "=== $(date '+%Y-%m-%d %H:%M:%S') cvxmap STOP ==="
      for n in cvxmap-collect cvxmap-write; do
          "$DOCKER" rm -f "$n" >/dev/null 2>&1 && echo "  stopped $n" || echo "  $n not running"
      done
      echo "  (serve left up so the cockpit stays readable)"
    } >> "$LOG" 2>&1
    ;;
  stopall)
    "$DOCKER" rm -f cvxmap-collect cvxmap-write cvxmap-serve >/dev/null 2>&1
    echo "all cvxmap containers removed"
    ;;
  status)
    "$DOCKER" ps --filter name=cvxmap --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'
    echo
    echo "--- collector ---"; tail -3 "$DATA_DIR"/collector_*.log 2>/dev/null || echo "  (none)"
    echo "--- writer ---";    tail -3 "$DATA_DIR"/stage2_*.log    2>/dev/null || echo "  (none)"
    ;;
  *)
    echo "usage: run_cvxmap.sh start|stop|stopall|status" >&2
    exit 2
    ;;
esac
