#!/bin/sh
# ============================================================================
#  trading-intel — one-go NAS deploy (DS923+; run as root, i.e. via sudo)
#  Usage:
#    sudo sh /var/services/homes/drmithil/trading-intel/scripts/nas/deploy.sh [flags]
#  Flags:
#    --run "<job1> [<job2> ...]"  after building, fire these scheduler jobs
#                                 (e.g. --run weekly_swing_dossiers -> posts to Telegram)
#    --no-build                   pull the tarball only, skip the image rebuild
#                                 (use for scripts/-layout-only changes: run_job.sh
#                                  bind-mounts host scripts/, so no rebuild needed)
#
#  Does the 3 canonical steps in order (see report-deploy-workflow):
#    1. curl the PUBLIC GitHub tarball  (no token; a 403 only happens from the
#       Cowork sandbox egress filter, never here on the NAS)
#    2. tar xzf --strip-components=1     (.env + secrets/ are gitignored -> preserved)
#    3. docker build --no-cache -t trading-intel .
#  Only trading_intel/ or alembic/ changes actually need the rebuild; a scripts/
#  report-layout change deploys on the pull alone (pass --no-build).
# ============================================================================
set -e

REPO=/var/services/homes/drmithil/trading-intel
DOCKER=/usr/local/bin/docker
TARBALL=https://github.com/rammpatel2013-sudo/trading-intel/tarball/main
BUILD=1
RUN=""

while [ $# -gt 0 ]; do
  case "$1" in
    --no-build) BUILD=0 ;;
    --run) shift; RUN="$1" ;;
    *) echo "deploy.sh: unknown arg: $1" >&2; exit 2 ;;
  esac
  shift
done

cd "$REPO"

echo "== [1/3] pull tarball =="
curl -L "$TARBALL" -o /tmp/ti.tar.gz

echo "== [2/3] extract (.env / secrets preserved) =="
tar xzf /tmp/ti.tar.gz --strip-components=1

if [ "$BUILD" = "1" ]; then
  echo "== [3/3] docker build --no-cache =="
  "$DOCKER" build --no-cache -t trading-intel .
else
  echo "== [3/3] skipped image build (--no-build) =="
fi

if [ -n "$RUN" ]; then
  echo "== run job(s): $RUN =="
  # run_job.sh calls docker without sudo; we are already root here, so it works.
  sh "$REPO/scripts/nas/run_job.sh" $RUN
fi

echo "DONE."
