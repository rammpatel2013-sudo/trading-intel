# Deploy EM-break / gamma burn-off system to the NAS

**Written:** 2026-07-18
**What:** finish shipping the EM-break system built on `feature/eod-flow-report`
(commit `b37ee9a`) — merge to `main`, rebuild the NAS image, add 3 DSM tasks,
restart Claude Desktop for the 5 new MCP tools. Source of truth for the build:
`docs/em-break-system-plan.md`.

Both machines (per standing rule — always cd into the code folder on each):
- **Laptop (Windows PowerShell):** `cd C:\Users\drmit\PycharmProjects\trading-intel`
- **NAS (Synology DS923+, SSH as drmithil, docker needs sudo):**
  `ssh drmithil@192.168.1.211` then `cd /var/services/homes/drmithil/trading-intel`

---

## Step 0 — Repair the corrupted working tree (LAPTOP)  ⚠ DO THIS FIRST

The working tree on `feature/eod-flow-report` has **corrupted (truncated) copies**
of several source files — their tails were chopped to whitespace/mid-line. The
committed code (`b37ee9a`) is intact and is what passed the 32 tests, so discard
the working-tree junk. Do **not** commit these files.

```powershell
cd C:\Users\drmit\PycharmProjects\trading-intel
git checkout feature/eod-flow-report

git restore trading_intel/mcp/server.py `
            trading_intel/memory/models.py `
            trading_intel/scheduler/runner.py `
            tests/clients/test_earnings_parse.py `
            probe_vix.py probe_index_skew.py `
            alembic/versions/0037_earnings_anchor.py `
            docs/em-break-system-plan.md

git status      # the 3 code files, the test, both probes, 0037, the plan doc should be GONE from the list
```

The `data/tas/*.csv` and `*.html` files may still show as modified — those are
regenerated capture artifacts (larger than HEAD, clean final rows), **not**
corruption. Leave them, or `git restore data/tas/` if you want a spotless tree
before the branch switch. They do not affect the merge.

Then confirm the good code is whole and tests pass (CLAUDE.md rule 6):

```powershell
.venv\Scripts\activate
pytest -q            # must be green before any merge
```

---

## Step 1 — Merge feature/eod-flow-report → main (LAPTOP)

```powershell
git checkout feature/eod-flow-report
git pull                                   # sync with origin
git checkout main
git pull
git merge --no-ff feature/eod-flow-report -m "merge: EM-break / gamma burn-off system"
pytest -q                                  # green post-merge
git push origin main
```

---

## Step 2 — Migration (LAPTOP → shared NAS Postgres)  — VERIFY, likely already done

Per MEMORY, migration **0037 is already applied** (collectors seeded). The laptop
`.env` points at the one shared NAS Postgres (`db-topology`), so alembic runs from
the laptop. Confirm rather than reapply:

```powershell
alembic current        # should show head includes 0037
alembic heads
```

If `current` is behind `heads`, apply and round-trip check:

```powershell
alembic upgrade head
alembic downgrade -1 ; alembic upgrade head
```

---

## Step 3 — Seed / bank baselines (LAPTOP)  — VERIFY, likely already done

Per MEMORY these already ran (`earnings_events` 4463 rows, `pre_earnings_straddle`
4/4). Both jobs are idempotent upserts, so re-running is harmless. Confirm rows
exist; only run the jobs if empty:

```powershell
python -c "from sqlalchemy import text; from trading_intel.config import get_settings; from trading_intel.memory.db import make_session_factory; s=make_session_factory(get_settings())(); print('earnings', s.execute(text('select count(*),max(date) from earnings_events')).all()); print('straddle', s.execute(text('select count(*),max(ts) from pre_earnings_straddle')).all())"

# only if empty:
# python -m trading_intel.scheduler.jobs.earnings_calendar
# python -m trading_intel.scheduler.jobs.pre_earnings_straddle
```

The pre-earnings straddle can only be captured going forward — it banks from today;
past prints can't be reconstructed. So the sooner the baseline runs, the sooner
signals become eligible.

---

## Step 4 — Rebuild the NAS image `--no-cache` (from the GitHub tarball)

Git isn't installed on the NAS; it builds from the GitHub tarball. Since you merged
to `main` in Step 1, pull `refs/heads/main`. A `--no-cache` rebuild ships **all**
committed code, so this one rebuild also picks up any other merged-but-not-yet-imaged
jobs (factor_scores / letf / iv_term / surface / skew-backfill) — add their DSM
tasks from their own DEPLOY docs in the same sitting if you haven't.

```bash
ssh drmithil@192.168.1.211
sudo sh -c '
  curl -L https://codeload.github.com/rammpatel2013-sudo/trading-intel/tar.gz/refs/heads/main -o /tmp/ti.tgz &&
  tar xzf /tmp/ti.tgz -C /tmp &&
  cp -r /tmp/trading-intel-main/. /var/services/homes/drmithil/trading-intel/ &&
  /usr/local/bin/docker build --no-cache -t trading-intel:latest /var/services/homes/drmithil/trading-intel
'
```

Look for `Successfully tagged trading-intel:latest`. `--no-cache` is required — a
plain build hits the `COPY` cache and ships the OLD code.

---

## Step 5 — Add the 3 DSM Task Scheduler tasks

DSM → Control Panel → Task Scheduler → Create → Scheduled Task → User-defined script.
**User: root.** NAS clock is America/New_York. Each task's Run command:

**a. Earnings calendar — daily 06:30**
```bash
bash /var/services/homes/drmithil/trading-intel/scripts/nas/run_job.sh earnings_calendar
```

**b. Pre-earnings straddle baseline — Mon–Fri 06:50**
```bash
bash /var/services/homes/drmithil/trading-intel/scripts/nas/run_job.sh pre_earnings_straddle
```

**c. EM-break re-entry scanner — Mon–Fri 17:10**
```bash
bash /var/services/homes/drmithil/trading-intel/scripts/nas/run_job.sh em_break_reentry
```
(17:10 so it runs after the EOD collectors — gex_rolling / oi_chain / quotes, and
iv_tenor 17:05 / surface 17:08 — have fresh data. The build plan says "17:05, after
gex/oi/quotes"; 17:10 keeps it clear of the 17:08 surface task.)

---

## Step 6 — Restart Claude Desktop

Fully quit and reopen Claude Desktop to re-register the 5 new MCP reader tools:
`get_earnings_calendar`, `get_em_break`, `get_gamma_burnoff`, `get_vol_control_flow`,
`get_systematic_flow`.

---

## Step 7 — Verify

Trigger each new DSM task once (select task → Run), then on the NAS:
```bash
tail -20 ~/ti_earnings_calendar.log ~/ti_pre_earnings_straddle.log ~/ti_em_break_reentry.log   # look for EXIT 0
```
`EXIT 0` = success; the "container stopped unexpectedly" Container Manager notice on
each `--rm` run is benign.

In Claude Desktop, call `get_earnings_calendar` (or `get_em_break`) to confirm the
tools registered and return data.

Note: `EM_BREAK_REENTRY` signals are written `experimental=True` until backtested
(P6 in the build plan) — expect none until an earnings name actually breaks its
expected move and burns off front gamma through OPEX.
```
```

---

## Fast path (if you just want the commands)

```powershell
# LAPTOP
cd C:\Users\drmit\PycharmProjects\trading-intel
git checkout feature/eod-flow-report
git restore trading_intel/mcp/server.py trading_intel/memory/models.py trading_intel/scheduler/runner.py tests/clients/test_earnings_parse.py probe_vix.py probe_index_skew.py alembic/versions/0037_earnings_anchor.py docs/em-break-system-plan.md
.venv\Scripts\activate; pytest -q
git checkout main; git pull; git merge --no-ff feature/eod-flow-report -m "merge: EM-break / gamma burn-off system"; pytest -q; git push origin main
alembic current      # verify 0037 applied
```
```bash
# NAS
ssh drmithil@192.168.1.211
sudo sh -c 'curl -L https://codeload.github.com/rammpatel2013-sudo/trading-intel/tar.gz/refs/heads/main -o /tmp/ti.tgz && tar xzf /tmp/ti.tgz -C /tmp && cp -r /tmp/trading-intel-main/. /var/services/homes/drmithil/trading-intel/ && /usr/local/bin/docker build --no-cache -t trading-intel:latest /var/services/homes/drmithil/trading-intel'
# then add the 3 DSM tasks + restart Claude Desktop
```
