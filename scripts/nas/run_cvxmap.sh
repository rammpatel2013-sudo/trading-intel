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
# 2026-10-28: the .env DATABASE_URL (host IP) does NOT work inside a container - every
# DB leg of cvxmap-doc failed with OperationalError. Same override run_job.sh uses.
# Credentials from .env (backlog #10); falls back to docker-network default.
DB_URL="$(grep -oP '(?<=^NAS_DATABASE_URL=).*' "$ENV_FILE" 2>/dev/null | tr -d '"'"'" || true)"
DB_URL="${DB_URL:-postgresql+psycopg://intel:intel@trading-intel-pg:5432/trading_intel}"
PG_CONTAINER="trading-intel-pg"

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
        -v "${REPO_DIR}/secrets:/app/secrets" \
        -e "PYTHONPATH=/app" \
        -e "DATABASE_URL=${DB_URL}" \
        $extra \
        "$IMAGE" sh -c "$*" >/dev/null
    echo "  started $name"
}

case "${1:-}" in
  start)
    {
      echo "=== $(date '+%Y-%m-%d %H:%M:%S') cvxmap START ==="
      "$DOCKER" network create "$NETWORK" 2>/dev/null || true
      "$DOCKER" network connect "$NETWORK" "$PG_CONTAINER" 2>/dev/null || true
      mkdir -p "$DATA_DIR"
      run_one cvxmap-collect ""                 "python scripts/cvxmap/cvx_collect.py"
      run_one cvxmap-write   ""                 "python scripts/cvxmap/cvx_live.py"
      run_one cvxmap-doc     "-e TI_DIR=/app -e DASH_DIR=/app/scripts/cvxmap/data" "python scripts/cvxmap/am_feed.py"
      run_one cvxmap-blocks  "-e TI_DIR=/app -e DASH_DIR=/app/scripts/cvxmap/data" "python scripts/cvxmap/blocks_feed.py"
      # 2026-11-05: per-trade sides (quote rule at bid/ask, tick rule between) from tas_prints -> sides_YYYYMMDD.json
      run_one cvxmap-sides   "-e DASH_DIR=/app/scripts/cvxmap/data" "python scripts/cvxmap/sides_feed.py"
      run_one cvxmap-serve   "-p ${PORT}:${PORT}" "python scripts/cvxmap/serve_cvx.py ${PORT}"
      echo "  cockpit: http://192.168.1.211:${PORT}/cvx_cockpit.html"
    } >> "$LOG" 2>&1
    ;;
  stop)
    {
      echo "=== $(date '+%Y-%m-%d %H:%M:%S') cvxmap STOP ==="
      for n in cvxmap-collect cvxmap-write cvxmap-doc cvxmap-blocks cvxmap-sides cvxmap-ledger; do
          "$DOCKER" rm -f "$n" >/dev/null 2>&1 && echo "  stopped $n" || echo "  $n not running"
      done
      echo "  (serve left up so the cockpit stays readable)"
    } >> "$LOG" 2>&1
    ;;
  ledger)
    # One-shot: build signed-position ledger from today's raw dump.
    # DSM task: 17:10 weekdays (after collector self-exits at 17:00).
    #     bash /var/services/homes/drmithil/trading-intel/scripts/nas/run_cvxmap.sh ledger
    # Optional: pass a date  →  run_cvxmap.sh ledger 20261028
    {
      echo "=== $(date '+%Y-%m-%d %H:%M:%S') cvxmap LEDGER ==="
      "$DOCKER" network create "$NETWORK" 2>/dev/null || true
      "$DOCKER" network connect "$NETWORK" "$PG_CONTAINER" 2>/dev/null || true
      DATE_ARG="${2:-}"
      CMD="python scripts/cvxmap/pos_ledger.py"
      [ -n "$DATE_ARG" ] && CMD="$CMD --date $DATE_ARG"
      run_one cvxmap-ledger "-e TI_DIR=/app" "$CMD"
      echo "  ledger job launched"
    } >> "$LOG" 2>&1
    ;;
  s12backfill)
    # One-shot (added 2026-11-04): build the Straddle tab (straddle_*.ndjson) and Flow
    # Constellation tab (const_*.json) files for every past raw day that lacks them.
    # ~3 min per day per tab. Safe to re-run: it skips days already built.
    #     sudo bash /var/services/homes/drmithil/trading-intel/scripts/nas/run_cvxmap.sh s12backfill
    {
      echo "=== $(date '+%Y-%m-%d %H:%M:%S') cvxmap S1/S2 BACKFILL ==="
      "$DOCKER" network create "$NETWORK" 2>/dev/null || true
      "$DOCKER" network connect "$NETWORK" "$PG_CONTAINER" 2>/dev/null || true
      run_one cvxmap-s12bf "" "python scripts/cvxmap/straddle_feed.py --all --build && python scripts/cvxmap/constellation.py --all"
      echo "  backfill launched (docker logs -f cvxmap-s12bf)"
    } >> "$LOG" 2>&1
    ;;
  sidesup)
    # Start ONLY the live per-trade sides loop (mid-session, without restarting the collector):
    #     sudo bash /var/services/homes/drmithil/trading-intel/scripts/nas/run_cvxmap.sh sidesup
    {
      echo "=== $(date '+%Y-%m-%d %H:%M:%S') cvxmap SIDES UP ==="
      "$DOCKER" network create "$NETWORK" 2>/dev/null || true
      "$DOCKER" network connect "$NETWORK" "$PG_CONTAINER" 2>/dev/null || true
      run_one cvxmap-sides   "-e DASH_DIR=/app/scripts/cvxmap/data" "python scripts/cvxmap/sides_feed.py"
    } >> "$LOG" 2>&1
    ;;
  sides)
    # One-shot rebuild of a day's per-trade sides file (default today):
    #     sudo bash /var/services/homes/drmithil/trading-intel/scripts/nas/run_cvxmap.sh sides 20261105
    {
      echo "=== $(date '+%Y-%m-%d %H:%M:%S') cvxmap SIDES ==="
      "$DOCKER" network create "$NETWORK" 2>/dev/null || true
      "$DOCKER" network connect "$NETWORK" "$PG_CONTAINER" 2>/dev/null || true
      CMD="python scripts/cvxmap/sides_feed.py --once"
      [ -n "${2:-}" ] && CMD="$CMD --date $2"
      run_one cvxmap-sidesbf "-e DASH_DIR=/app/scripts/cvxmap/data" "$CMD"
    } >> "$LOG" 2>&1
    ;;
  stopall)
    "$DOCKER" rm -f cvxmap-collect cvxmap-write cvxmap-doc cvxmap-blocks cvxmap-sides cvxmap-serve cvxmap-ledger cvxmap-s12bf cvxmap-sidesbf >/dev/null 2>&1
    echo "all cvxmap containers removed"
    ;;
  status)
    "$DOCKER" ps --filter name=cvxmap --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'
    echo
    echo "--- collector ---"; tail -3 "$DATA_DIR"/collector_*.log 2>/dev/null || echo "  (none)"
    echo "--- writer ---";    tail -3 "$DATA_DIR"/stage2_*.log    2>/dev/null || echo "  (none)"
    echo "--- ledger ---";   tail -3 "$DATA_DIR"/pos_ledger_*.log 2>/dev/null || echo "  (none)"
    echo "--- shared feeds ---"
    for f in ti.json blocks.json; do
        if [ -f "$DATA_DIR/$f" ]; then
            echo "  $f  $(stat -c %y "$DATA_DIR/$f" 2>/dev/null | cut -c1-19)  $(stat -c %s "$DATA_DIR/$f") bytes"
        else
            echo "  $f  MISSING"
        fi
    done
    ;;
  *)
    echo "usage: run_cvxmap.sh start|stop|stopall|ledger [YYYYMMDD]|s12backfill|status" >&2
    exit 2
    ;;
esac
