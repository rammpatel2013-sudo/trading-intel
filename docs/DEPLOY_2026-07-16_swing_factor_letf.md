# Deploy swing_signals + factor_scores + letf_flows to the NAS

**Written:** 2026-07-16
**What:** wire the three new EOD/weekly jobs built this session as DSM tasks —
`letf_flows`, `swing_signals`, `factor_scores`. Mechanics mirror
`DEPLOY_tas_daily_rollup.md`: the NAS rebuilds from the GitHub tarball, and each
DSM task calls `scripts/nas/run_job.sh <job>`.

## Preconditions (already true — do NOT redo)
- DB migrations **0031 / 0032 / 0033** are applied to the NAS Postgres — you ran
  `alembic upgrade head` from the laptop, whose `.env` points at the NAS DB, and
  the manual job runs already wrote rows (`letf_shares_snapshots` 24,
  `fundamentals_snapshots` 12). **Skip `alembic upgrade` on the NAS.**

---

## Step 1 — Commit + push from the LAPTOP  (blocker: the NAS builds from GitHub)

⚠ **Branch check first.** The NAS pulls `refs/heads/main`. Your work is on
`feature/eod-flow-report`. Either merge to `main` before Step 2, **or** change the
tarball ref in Step 2 to `refs/heads/feature/eod-flow-report`. Confirm with
`git branch --show-current`.

```powershell
cd C:\Users\drmit\PycharmProjects\trading-intel
.venv\Scripts\activate

# lint only the new files (repo has unrelated in-flight drift)
black trading_intel/etf_flows trading_intel/swing trading_intel/factors `
      trading_intel/strategies/swing_options.py `
      trading_intel/scheduler/jobs/swing_signals.py trading_intel/scheduler/jobs/factor_scores.py `
      scripts/credit_income_scan.py `
      alembic/versions/0033_fundamentals_snapshots.py
ruff check trading_intel/etf_flows trading_intel/swing trading_intel/factors `
      trading_intel/strategies/swing_options.py trading_intel/scheduler/jobs/factor_scores.py

pytest -q            # must be green before commit (CLAUDE.md rule 6)
```

Stage the new work + its dependencies (everything the jobs import must be on the
branch the NAS builds). New files/dirs — safe to add whole:

```powershell
git add trading_intel/etf_flows trading_intel/swing trading_intel/factors `
        trading_intel/strategies/swing_options.py `
        trading_intel/scheduler/jobs/swing_signals.py `
        trading_intel/scheduler/jobs/factor_scores.py `
        trading_intel/scheduler/jobs/letf_flows.py `
        trading_intel/clients/__init__.py trading_intel/clients/fmp.py `
        alembic/versions/0031_swing_features.py `
        alembic/versions/0032_letf_shares_snapshots.py `
        alembic/versions/0033_fundamentals_snapshots.py `
        tests/etf_flows tests/swing tests/factors `
        tests/strategies/test_swing_options.py tests/scheduler/test_letf_flows.py `
        scripts/credit_income_scan.py scripts/probe_fmp_fields.py scripts/probe_transcripts.py `
        run_credit_income_scan.bat `
        docs/decisions/ADR-005-factor-scoring-layer.md `
        docs/playbooks/swing_options.md docs/handoff-2026-07-16.md `
        docs/DEPLOY_2026-07-16_swing_factor_letf.md
```

`config.py`, `memory/models.py`, and `pyproject.toml` carry the new fields **plus**
other in-flight edits — `git diff` them, then add the relevant hunks:

```powershell
git add -p trading_intel/config.py trading_intel/memory/models.py pyproject.toml
```

Sanity-check nothing the jobs import is missing, then commit + push:

```powershell
git ls-files trading_intel/etf_flows trading_intel/swing trading_intel/factors   # must list the new modules
git commit -m "feat(factors,swing,etf): factor layer + swing generator + LETF descriptors + Track B"
git push
```

---

## Step 2 — Rebuild the NAS image `--no-cache` (from the GitHub tarball)

```bash
ssh drmithil@192.168.1.211
sudo sh -c '
  curl -L https://codeload.github.com/rammpatel2013-sudo/trading-intel/tar.gz/refs/heads/main -o /tmp/ti.tgz &&
  tar xzf /tmp/ti.tgz -C /tmp &&
  cp -r /tmp/trading-intel-main/. /var/services/homes/drmithil/trading-intel/ &&
  /usr/local/bin/docker build --no-cache -t trading-intel:latest /var/services/homes/drmithil/trading-intel
'
```

Look for `Successfully tagged trading-intel:latest`. `--no-cache` is required —
a plain build hits the `COPY` cache and ships the OLD code. (If you deployed the
feature branch, swap `refs/heads/main` → `refs/heads/feature/eod-flow-report` and
`trading-intel-main` → `trading-intel-feature-eod-flow-report`.)

---

## Step 3 — Add the DSM Task Scheduler tasks

DSM → Control Panel → Task Scheduler → Create → Scheduled Task → User-defined script.
**User: root.** NAS clock is America/New_York.

**a. LETF shares snapshot — daily 17:10**
```bash
bash /var/services/homes/drmithil/trading-intel/scripts/nas/run_job.sh letf_flows
```

**b. Swing signals — chain onto your existing `swing_features` task (recommended).**
You already have a 17:00 task running `run_job.sh swing_features`. Edit its Run
command to run signals right after features finish, with guaranteed fresh data:
```bash
bash /var/services/homes/drmithil/trading-intel/scripts/nas/run_job.sh swing_features swing_signals
```
(`run_job.sh` runs them sequentially, each in its own container + log.) If you'd
rather keep them separate, make a new task at **17:20** running `... run_job.sh swing_signals`.

**c. Factor scores — weekly, Saturday 08:00**
```bash
bash /var/services/homes/drmithil/trading-intel/scripts/nas/run_job.sh factor_scores
```

---

## Step 4 — Verify

After each task's first run (or trigger it: select the task → Run):
```bash
tail -20 ~/ti_letf_flows.log ~/ti_swing_signals.log ~/ti_factor_scores.log   # look for EXIT 0
```
From the laptop (`.venv` active), confirm fresh rows landed:
```powershell
python -c "from sqlalchemy import text; from trading_intel.config import get_settings; from trading_intel.memory.db import make_session_factory; s=make_session_factory(get_settings())(); print('letf', s.execute(text('select count(*),max(ts) from letf_shares_snapshots')).all()); print('factors', s.execute(text('select count(*),max(ts) from fundamentals_snapshots')).all())"
```
`letf_flows` needs ≥2 daily runs before Δshares/issuance are non-null.

## Log locations (NAS)
`~/ti_letf_flows.log`, `~/ti_swing_signals.log`, `~/ti_swing_features.log`,
`~/ti_factor_scores.log`. `EXIT 0` = success; the "container stopped unexpectedly"
Container Manager notice on each `--rm` run is benign.
