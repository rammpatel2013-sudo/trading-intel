#!/bin/bash
# run_volx.sh - created 2026-11-09. The vol-regime poller (scripts/cvxmap/volx.py) in its OWN container, cvxmap-volx.
# Free delayed index quotes + daily history (the same CDN vixdash.py uses) - no options-vendor calls, no API budget.
# Moved out of the test server (cvxmap-test) so the MAIN cockpit's Density / VIX options can use it.
# It writes only scripts/cvxmap/data/volx.json and volx_YYYYMMDD.json, which the existing cvxmap-serve (8899) and
# cvxmap-test (8898) already serve. run_cvxmap.sh and the production cvxmap-* containers are never touched.
# One step a minute, ~40 MB RAM; exits by itself at 16:30 ET.
#
#   sudo bash /var/services/homes/drmithil/trading-intel/scripts/nas/run_volx.sh start     # DSM task Mon-Fri 09:25 ET
#   sudo bash /var/services/homes/drmithil/trading-intel/scripts/nas/run_volx.sh stop
#   sudo bash /var/services/homes/drmithil/trading-intel/scripts/nas/run_volx.sh status
#   sudo bash /var/services/homes/drmithil/trading-intel/scripts/nas/run_volx.sh once      # one write now (any time), then exit
REPO_DIR="/var/services/homes/drmithil/trading-intel"
DATA_DIR="${REPO_DIR}/scripts/cvxmap/data"
DOCKER="/usr/local/bin/docker"
IMAGE="trading-intel"
NAME="cvxmap-volx"
LOG="/var/services/homes/drmithil/ti_cvxmap_volx.log"
set -uo pipefail

case "${1:-}" in
  start)
    {
      echo "=== $(date '+%Y-%m-%d %H:%M:%S') volx START ==="
      "$DOCKER" rm -f "$NAME" >/dev/null 2>&1 || true
      "$DOCKER" run -d --rm --name "$NAME" \
          -v "${REPO_DIR}/scripts:/app/scripts" -e "PYTHONPATH=/app" \
          "$IMAGE" python scripts/cvxmap/volx.py >/dev/null \
        && echo "  started $NAME -> ${DATA_DIR}/volx.json" \
        || echo "  FAILED to start $NAME"
    } 2>&1 | tee -a "$LOG"
    ;;
  stop)
    "$DOCKER" rm -f "$NAME" >/dev/null 2>&1 && echo "  stopped $NAME" || echo "  $NAME was not running"
    ;;
  status)
    "$DOCKER" ps --filter "name=${NAME}" --format '{{.Names}}  {{.Status}}' | sed 's/^/  /'
    "$DOCKER" logs --tail 5 "$NAME" 2>/dev/null | sed 's/^/  log: /'
    ls -la "$DATA_DIR"/volx*.json 2>/dev/null | tail -4 | sed 's/^/  /'
    ;;
  once)
    echo "=== $(date '+%Y-%m-%d %H:%M:%S') volx ONCE ===" | tee -a "$LOG"
    "$DOCKER" run --rm --name "${NAME}-once" \
        -v "${REPO_DIR}/scripts:/app/scripts" -e "PYTHONPATH=/app" \
        "$IMAGE" python scripts/cvxmap/volx.py --once 2>&1 | tee -a "$LOG"
    ;;
  *)
    echo "usage: run_volx.sh start | stop | status | once"
    exit 2
    ;;
esac
