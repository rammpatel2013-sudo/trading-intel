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
#    --fresh                      full rebuild with --no-cache (re-installs every
#                                 package + Chromium; only for a package problem or
#                                 a periodic base-image refresh)
#
#  Does the 3 canonical steps in order (see report-deploy-workflow):
#    1. curl the PUBLIC GitHub tarball  (no token; a 403 only happens from the
#       Cowork sandbox egress filter, never here on the NAS)
#    2. tar xzf --strip-components=1     (.env + secrets/ are gitignored -> preserved)
#    3. docker build -t trading-intel .   (cached layers)
#  Only trading_intel/ or alembic/ changes actually need the rebuild; a scripts/
#  report-layout change deploys on the pull alone (pass --no-build).
#
#  2026-11-14 (O1, code audit C1/C2): builds are CACHED by default. Docker re-uses
#  the apt / pip / Chromium layers (they only change when pyproject.toml or the
#  Dockerfile change) and re-copies trading_intel/ + scripts/ + alembic/ by file
#  content, so a code-only ship rebuilds in seconds. .dockerignore keeps the
#  cockpit data (scripts/cvxmap/data, ~600 MB/day) out of the build and the image.
#  convexlib is pinned to a commit in pyproject.toml, so a cached layer is exact.
#  The old "always --no-cache" rule (05/23) came from a build that ran before the
#  new code was in the folder - not a cache fault; --fresh keeps that option.
# ============================================================================
set -e

REPO=/var/services/homes/drmithil/trading-intel
DOCKER=/usr/local/bin/docker
TARBALL=https://github.com/rammpatel2013-sudo/trading-intel/tarball/main
BUILD=1
NOCACHE=""
RUN=""

while [ $# -gt 0 ]; do
  case "$1" in
    --no-build) BUILD=0 ;;
    --fresh) NOCACHE="--no-cache" ;;
    --run) shift; RUN="$*"; break ;;  # greedy: everything after --run is the job list
    *) echo "deploy.sh: unknown arg: $1" >&2; exit 2 ;;
  esac
  shift
done

cd "$REPO"

echo "== [1/3] pull tarball =="
curl -L "$TARBALL" -o /tmp/ti.tar.gz

echo "== [2/3] extract (.env / secrets preserved) =="
tar xzf /tmp/ti.tar.gz --strip-components=1
rm -f /tmp/ti.tar.gz   # 2026-10-28: the 104 MB tarball was being left behind in /tmp

if [ "$BUILD" = "1" ]; then
  echo "== [3/3] docker build ${NOCACHE:-(cached layers)} =="
  "$DOCKER" build $NOCACHE -t trading-intel .
  # Each rebuild leaves the previous image untagged (dangling). On the
  # DS923+ these pile up fast (GBs each), so reclaim them now. `prune` without -a
  # only removes dangling images, never tagged ones or images a container still
  # uses; `|| true` keeps a prune hiccup from aborting the deploy under `set -e`.
  echo "== prune dangling images (reclaim NAS disk) =="
  "$DOCKER" image prune -f || true
  "$DOCKER" image ls trading-intel || true
else
  echo "== [3/3] skipped image build (--no-build) =="
fi

if [ -n "$RUN" ]; then
  echo "== run job(s): $RUN =="
  # run_job.sh calls docker without sudo; we are already root here, so it works.
  sh "$REPO/scripts/nas/run_job.sh" $RUN
fi

echo "DONE."
