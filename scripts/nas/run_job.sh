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
# Ollama runs on the LAPTOP, not the NAS (the DS923+ has 3.8GB RAM and cannot
# host a 7B). The .env value is "http://localhost:11434", which is correct when a
# job is run FROM the laptop but resolves to the CONTAINER inside `docker run`,
# so every LLM leg failed here silently. Same problem, same fix as DB_URL above:
# override it at the docker boundary. If the laptop is asleep the LLM legs just
# degrade — letters_fetch stores the letter bodies regardless (see its run()).
OLLAMA_URL="http://192.168.1.175:11434"
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

# Report jobs write to a RELATIVE Path("reports") and the image sets WORKDIR /app,
# so the HTML lands at /app/reports inside the container. Without this bind mount
# `docker run --rm` deletes it on exit and the Telegram message is the only copy.
mkdir -p "$REPO_DIR/reports"

status=0
for job in "$@"; do
    log="$HOME_DIR/ti_${job}.log"
    {
        echo "=== $(date '+%Y-%m-%d %H:%M:%S') start ${job} ==="
        "$DOCKER" run --rm --network "$NETWORK" \
            -v "${ENV_FILE}:/app/.env" \
            -v "${REPO_DIR}/secrets:/app/secrets" \
            -v "${REPO_DIR}/scripts:/app/scripts" \
            -v "${REPO_DIR}/reports:/app/reports" \
            -e "DATABASE_URL=${DB_URL}" \
            -e "OLLAMA_HOST=${OLLAMA_URL}" \
            "$IMAGE" sh -c "python -m trading_intel.scheduler.jobs.${job}"
        rc=$?
        echo "=== $(date '+%Y-%m-%d %H:%M:%S') ${job} EXIT ${rc} ==="
    } >> "$log" 2>&1
    [ "${rc:-1}" -ne 0 ] && status=1
done
exit "$status"
