# Deploy `tas_daily_rollup` to the NAS

**Written:** 2026-07-05
**Why:** `tas_prints` (raw tape) capture is healthy and current (through 2026-07-02),
but the durable roll-up tables `tas_daily_flow` / `tas_daily_contract` stopped at
**2026-06-25**. The `get_flow_scorecard` MCP tool + accumulation/distribution scoring
read those tables, so they are ~10 days stale. Root cause: the whole flow feature is
**uncommitted on the laptop and absent from GitHub `main`**, so the NAS (which rebuilds
from the GitHub tarball) has never had the job.

## Preconditions (already true — do NOT redo)
- DB migrations **0029** + **0030** are applied to the NAS Postgres. Tables
  `tas_daily_flow` / `tas_daily_contract` exist with data through 2026-06-25.
  **Skip `alembic upgrade` on the NAS.**
- Raw `tas_prints` retains 2026-06-15 → 2026-07-02 (backfill source).

---

## Step 1 — Commit + push from the LAPTOP (blocker for everything below)

```powershell
cd C:\Users\drmit\PycharmProjects\trading-intel
.venv\Scripts\activate

# lint only the changed/new flow files (sandbox/global ruff flags pre-existing drift)
black trading_intel/flow scripts/flow_scorecard.py trading_intel/scheduler/jobs/tas_daily_rollup.py `
      alembic/versions/0029_tas_daily_rollup.py alembic/versions/0030_tas_daily_contract_spot_delta.py
ruff check trading_intel/flow scripts/flow_scorecard.py trading_intel/scheduler/jobs/tas_daily_rollup.py

pytest -q            # must be green before commit (CLAUDE.md rule 6)
```

Then stage the feature (keep the commit scoped — the repo has other in-flight drift):

```powershell
git add trading_intel/flow `
        trading_intel/scheduler/jobs/tas_daily_rollup.py `
        scripts/flow_scorecard.py `
        alembic/versions/0029_tas_daily_rollup.py `
        alembic/versions/0030_tas_daily_contract_spot_delta.py
# these three files also carry unrelated edits — add just the flow hunks
git add -p trading_intel/memory/models.py trading_intel/scheduler/runner.py trading_intel/mcp/extra_tools.py

git commit -m "feat(flow): durable tas_daily rollup + accumulation/distribution scorecard"
git push
```

Confirm it landed: `git ls-files trading_intel/flow` should now list the files, and the
GitHub `main` tree should show `trading_intel/flow/`.

---

## Step 2 — Rebuild the NAS image `--no-cache` (from the GitHub tarball)

```bash
ssh drmithil@192.168.1.211
cd /var/services/homes/drmithil/trading-intel

sudo sh -c '
  curl -L https://codeload.github.com/rammpatel2013-sudo/trading-intel/tar.gz/refs/heads/main -o /tmp/ti.tgz &&
  tar xzf /tmp/ti.tgz -C /tmp &&
  cp -r /tmp/trading-intel-main/. /var/services/homes/drmithil/trading-intel/ &&
  /usr/local/bin/docker build --no-cache -t trading-intel:latest /var/services/homes/drmithil/trading-intel
'
```
Look for `Successfully tagged trading-intel:latest`. A plain build (no `--no-cache`) hits
the `COPY` cache and ships the OLD code — the flag is required.

---

## Step 3 — One-time backfill (fills 2026-06-26 → 2026-07-02)

`run_job.sh` calls the module with no args (nightly catch-up mode). For the one-time
backfill add the `--backfill` flag with a direct `docker run` mirroring the wrapper:

```bash
sudo /usr/local/bin/docker run --rm --network trading-intel-net \
  -v /var/services/homes/drmithil/trading-intel/.env:/app/.env \
  -e DATABASE_URL=postgresql+psycopg://intel:intel@trading-intel-pg:5432/trading_intel \
  trading-intel sh -c "python -m trading_intel.scheduler.jobs.tas_daily_rollup --backfill"
```

Verify (from the laptop, `.venv` active):
```powershell
python -c "from sqlalchemy import text; from trading_intel.config import get_settings; from trading_intel.memory.db import make_session_factory; s=make_session_factory(get_settings())(); print(s.execute(text('select max(trade_date) from tas_daily_flow')).scalar())"
# expect 2026-07-02 (not 2026-06-25)
```

---

## Step 4 — Add the nightly DSM Task Scheduler task

Synology DSM → Control Panel → Task Scheduler → Create → Scheduled Task → User-defined script.
- **User:** root
- **Schedule:** daily, **17:05** (NAS clock is America/New_York — after the 16:00 tape stop,
  before the 02:40 prune)
- **Run command:**
  ```bash
  bash /var/services/homes/drmithil/trading-intel/scripts/nas/run_job.sh tas_daily_rollup
  ```

Nightly runs use catch-up mode (rolls any missing session + re-rolls the latest), which is
idempotent (`ON CONFLICT DO UPDATE`).

---

## Step 5 — Raise retention 30 → 60 (prune task already deployed)

The `prune_tas_prints` DSM task is **confirmed deployed** (runs
`bash .../scripts/nas/run_job.sh prune_tas_prints`). `run_job.sh` mounts `.env` into the
container live, so the retention change needs **no rebuild** — just add the knob to the NAS
`.env` and the next scheduled prune uses it:
```bash
echo 'TAS_RETENTION_DAYS=60' >> /var/services/homes/drmithil/trading-intel/.env
```
Verify on the next run:
```bash
tail -20 ~/ti_prune_tas_prints.log     # look for "days=60"
```
(`TAS_RETENTION_DAYS=60` is already set in the laptop `.env`.)

---

## Log locations (NAS)
`~/ti_tas_daily_rollup.log`, `~/ti_prune_tas_prints.log`. `EXIT 0` = success; the
"container stopped unexpectedly" Container Manager notice on each `--rm` run is benign.
