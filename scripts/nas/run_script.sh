#!/bin/bash
# run_script.sh - created 2026-10-28.
# Sibling of run_job.sh for STANDALONE SCRIPTS under scripts/ (run_job.sh only runs
# `python -m trading_intel.scheduler.jobs.<job>` modules). Same mounts, same network,
# same DB override, same log convention (~/ti_<name>.log with start/EXIT lines).
# scripts/ and reports/ are bind-mounted, so a script change deploys by tarball pull
# alone - no docker build.
#
# Usage (DSM task, user root):
#     bash /var/services/homes/drmithil/trading-intel/scripts/nas/run_script.sh cvforge_shadow
#     bash /var/services/homes/drmithil/trading-intel/scripts/nas/run_script.sh cvforge_compare
# Extra args after the name are passed to the script.

HOME_DIR="/var/services/homes/drmithil"
REPO_DIR="$HOME_DIR/trading-intel"
ENV_FILE="$REPO_DIR/.env"
IMAGE="trading-intel"
NETWORK="trading-intel-net"
PG_CONTAINER="trading-intel-pg"
DOCKER="/usr/local/bin/docker"
DB_URL="postgresql+psycopg://intel:intel@trading-intel-pg:5432/trading_intel"

set -uo pipefail
if [ "$#" -lt 1 ]; then
    echo "usage: run_script.sh <script_name_without_.py> [args...]" >&2
    exit 2
fi
name="$1"; shift
if [ ! -f "$REPO_DIR/scripts/${name}.py" ]; then
    echo "no such script: $REPO_DIR/scripts/${name}.py" >&2
    exit 2
fi

"$DOCKER" network create "$NETWORK" 2>/dev/null || true
"$DOCKER" network connect "$NETWORK" "$PG_CONTAINER" 2>/dev/null || true
mkdir -p "$REPO_DIR/reports"

log="$HOME_DIR/ti_${name}.log"
{
    echo "=== $(date '+%Y-%m-%d %H:%M:%S') start ${name} ==="
    "$DOCKER" run --rm --name "ti_${name}" --network "$NETWORK" \
        -v "${ENV_FILE}:/app/.env" \
        -v "${REPO_DIR}/secrets:/app/secrets" \
        -v "${REPO_DIR}/scripts:/app/scripts" \
        -v "${REPO_DIR}/reports:/app/reports" \
        -e "PYTHONPATH=/app" \
        -e "DATABASE_URL=${DB_URL}" \
        "$IMAGE" python "scripts/${name}.py" "$@"
    rc=$?
    echo "=== $(date '+%Y-%m-%d %H:%M:%S') ${name} EXIT ${rc} ==="
} >> "$log" 2>&1
exit "${rc:-1}"
