#!/bin/bash
# run_wide.sh - created 2026-11-09. Orbs + Volume profile feed (scripts/cvxmap/wide_feed.py) on the NAS.
# Its OWN container, cvxmap-wide. run_cvxmap.sh and the production cvxmap-* containers are never touched.
# It only READS raw_YYYYMMDD.ndjson.gz (the collector's file) and writes wide_*.json + flow_orbs.html into
# scripts/cvxmap/data/, which the existing cvxmap-serve already serves:
#     http://192.168.1.211:8899/flow_orbs.html
# 2026-11-09: one block per pull (1 minute), looks for a new pull every 3 s and writes wide_live_<SYM>.json (newest
# minute only) for the cockpit's Orbs tab, plus vixmap_YYYYMMDD.json for the VIX tab's map (from the same raw file).
#
#   sudo bash /var/services/homes/drmithil/trading-intel/scripts/nas/run_wide.sh start            # live loop (DSM task Mon-Fri 09:26 ET); exits by itself 16:20
#   sudo bash /var/services/homes/drmithil/trading-intel/scripts/nas/run_wide.sh stop
#   sudo bash /var/services/homes/drmithil/trading-intel/scripts/nas/run_wide.sh status
#   sudo bash /var/services/homes/drmithil/trading-intel/scripts/nas/run_wide.sh days             # rebuild EVERY stored day that has a raw file
#   sudo bash /var/services/homes/drmithil/trading-intel/scripts/nas/run_wide.sh days 20261106    # rebuild named days
REPO_DIR="/var/services/homes/drmithil/trading-intel"
DATA_DIR="${REPO_DIR}/scripts/cvxmap/data"
DOCKER="/usr/local/bin/docker"
IMAGE="trading-intel"
NAME="cvxmap-wide"
LOG="/var/services/homes/drmithil/ti_cvxmap_wide.log"
set -uo pipefail

case "${1:-}" in
  start)
    {
      echo "=== $(date '+%Y-%m-%d %H:%M:%S') wide feed START ==="
      "$DOCKER" rm -f "$NAME" >/dev/null 2>&1 || true
      "$DOCKER" run -d --rm --name "$NAME" \
          -v "${REPO_DIR}/scripts:/app/scripts" -e "PYTHONPATH=/app" \
          "$IMAGE" python scripts/cvxmap/wide_feed.py >/dev/null \
        && echo "  started $NAME -> http://192.168.1.211:8899/flow_orbs.html" \
        || echo "  FAILED to start $NAME"
    } 2>&1 | tee -a "$LOG"
    ;;
  stop)
    "$DOCKER" rm -f "$NAME" >/dev/null 2>&1 && echo "  stopped $NAME" || echo "  $NAME was not running"
    ;;
  status)
    "$DOCKER" ps --filter "name=${NAME}" --format '{{.Names}}  {{.Status}}' | sed 's/^/  /'
    "$DOCKER" logs --tail 5 "$NAME" 2>/dev/null | sed 's/^/  log: /'
    ls -la "$DATA_DIR"/wide_*.json "$DATA_DIR"/vixmap_*.json "$DATA_DIR"/flow_orbs.html 2>/dev/null | tail -10 | sed 's/^/  /'
    ;;
  days)
    shift
    if [ "$#" -eq 0 ]; then
      set -- $(ls "$DATA_DIR"/raw_2*.ndjson.gz 2>/dev/null | sed -E 's#.*/raw_([0-9]{8})\.ndjson\.gz#\1#')
    fi
    if [ "$#" -eq 0 ]; then echo "  no raw files in $DATA_DIR"; exit 1; fi
    echo "=== $(date '+%Y-%m-%d %H:%M:%S') wide feed rebuild: $* ===" | tee -a "$LOG"
    "$DOCKER" run --rm --name "${NAME}-days" \
        -v "${REPO_DIR}/scripts:/app/scripts" -e "PYTHONPATH=/app" \
        "$IMAGE" python scripts/cvxmap/wide_feed.py --date "$@" 2>&1 | tee -a "$LOG"
    ;;
  *)
    echo "usage: run_wide.sh start | stop | status | days [YYYYMMDD ...]"
    exit 2
    ;;
esac
