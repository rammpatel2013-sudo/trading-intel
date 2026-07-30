#!/bin/bash
# trading-intel — run one (or more) scheduler jobs in the baked Docker image.
#
# This is the single wrapper every Synology DSM Task Scheduler task calls, so the
# docker invocation lives in ONE place. Each DSM task is just a user-defined
# script (user: root, run with bash) like:
#
#     bash /var/services/homes/drmithil/trading-intel/scripts/nas/run_job.sh greeks_snapshot
#
# and for chained jobs (run in sequence, e.g. the daily-prices task):
#
#     bash .../scripts/nas/run_job.sh quotes_daily prune_intraday
#
# Output goes to ~/ti_<job>.log (DSM doesn't show task stdout). EXIT 0 = success;
# the "container ... stopped unexpectedly" Container Manager notice on each --rm
# run is benign.
#
# ── Set these ONCE to match your existing working DSM tasks ──────────────
HOME_DIR="/var/services/homes/drmithil"
REPO_DIR="$HOME_DIR/trading-intel"
ENV_FILE="$REPO_DIR/.env"
IMAGE="trading-intel"
NETWORK="trading-intel-net"
PG_CONTAINER="trading-intel-pg"
# DSM tasks run as root WITHOUT /usr/local/bin on PATH, so call docker by full
# path (matches your existing tasks' "Run: /usr/local/bin/docker ...").
DOCKER="/usr/local/bin/docker"
# Inside the docker network the Postgres container is reachable by name; this
# overrides the host-IP DATABASE_URL in .env. Copy the exact value your existing
# intraday/flow tasks already use.
DB_URL="postgresql+psycopg://intel:intel@trading-intel-pg:5432/trading_intel"
# ─────────────────────────────────────────────────────────────────────────

set -uo pipefail

if [ "$#" -lt 1 ]; then
    echo "usage: run_job.sh <job_module> [<job_module> ...]" >&2
    exit 2
fi

# Ensure the network exists and the Postgres container is attached (matches your
# existing tasks; both are no-ops if already done).
"$DOCKER" network create "$NETWORK" 2>/dev/null || true
"$DOCKER" network connect "$NETWORK" "$PG_CONTAINER" 2>/dev/null || true

status=0
for job in "$@"; do
    log="$HOME_DIR/ti_${job}.log"
    {
        echo "=== $(date '+%Y-%m-%d %H:%M:%S') start ${job} ==="
        "$DOCKER" run --rm --network "$NETWORK" \
            -v "${ENV_FILE}:/app/.env" \
            -v "${REPO_DIR}/secrets:/app/secrets" \
            -v "${REPO_DIR}/scripts:/app/scripts" \
            -e "DATABASE_URL=${DB_URL}" \
            "$IMAGE" sh -c "python -m trading_intel.scheduler.jobs.${job}"
        rc=$?
        echo "=== $(date '+%Y-%m-%d %H:%M:%S') ${job} EXIT ${rc} ==="
    } >> "$log" 2>&1
    [ "${rc:-1}" -ne 0 ] && status=1
done
exit "$status"
