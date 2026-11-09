#!/bin/bash
# TEST cockpit on the NAS (created 2026-11-08). Separate from production: run_cvxmap.sh, the cvxmap-* production
# containers and port 8899 are never touched. One container, cvxmap-test, port 8898. It only READS the data files the
# production writers produce, and writes only under scripts/cvxmap/data/test/.
#
#   sudo bash /var/services/homes/drmithil/trading-intel/scripts/nas/run_cvxmap_test.sh start    # start (stays up across reboots)
#   sudo bash /var/services/homes/drmithil/trading-intel/scripts/nas/run_cvxmap_test.sh stop     # remove the test side completely
#   sudo bash /var/services/homes/drmithil/trading-intel/scripts/nas/run_cvxmap_test.sh status
#
# Test page:        http://192.168.1.211:8898/cvx_cockpit_test.html
# Production page:  http://192.168.1.211:8899/cvx_cockpit.html   (unchanged)
REPO_DIR="/var/services/homes/drmithil/trading-intel"
DOCKER="/usr/local/bin/docker"
IMAGE="trading-intel"
NETWORK="trading-intel-net"
PORT=8898
NAME="cvxmap-test"
LOG="/var/services/homes/drmithil/ti_cvxmap_test.log"
set -uo pipefail
case "${1:-}" in
  start)
    {
      echo "=== $(date '+%Y-%m-%d %H:%M:%S') cvxmap TEST start ==="
      "$DOCKER" network create "$NETWORK" 2>/dev/null || true
      "$DOCKER" rm -f "$NAME" >/dev/null 2>&1 || true
      "$DOCKER" run -d --restart unless-stopped --name "$NAME" --network "$NETWORK" \
          -v "${REPO_DIR}/scripts:/app/scripts" -e "PYTHONPATH=/app" -p "${PORT}:${PORT}" \
          "$IMAGE" python scripts/cvxmap/test_cockpit/serve_cvx_test.py "$PORT" >/dev/null \
        && echo "  started $NAME  ->  http://192.168.1.211:${PORT}/cvx_cockpit_test.html" \
        || echo "  FAILED to start $NAME"
    } 2>&1 | tee -a "$LOG"
    ;;
  stop)
    { echo "=== $(date '+%Y-%m-%d %H:%M:%S') cvxmap TEST stop ==="; "$DOCKER" rm -f "$NAME" >/dev/null 2>&1 && echo "  removed $NAME" || echo "  $NAME not running"; } 2>&1 | tee -a "$LOG"
    ;;
  status)
    "$DOCKER" ps -a --filter "name=$NAME" --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'
    ls -la "${REPO_DIR}/scripts/cvxmap/data/test/" 2>/dev/null | tail -8
    ;;
  *) echo "usage: run_cvxmap_test.sh start|stop|status" >&2; exit 2 ;;
esac
