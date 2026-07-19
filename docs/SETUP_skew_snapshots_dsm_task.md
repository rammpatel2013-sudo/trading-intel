# Setup — daily `skew_snapshots` DSM task

_Authored 2026-06-23. Scope: get per-name skew collecting **forward** on the NAS.
This is the skew-only slice of `RUNBOOK_fix_skew_index_collection.md` (Part C).
No historical backfill (not chosen); the index_skew upstream gap is a separate
problem and is intentionally out of scope here._

## Verified state (laptop → NAS DB, 2026-06-23)

| Table | Status | Bearing on this task |
|---|---|---|
| `skew_snapshots` | only **2026-05-28** (355 rows) | the gap we're closing — job never had a DSM task |
| `oi_chain_eod` (per-name) | current to **6/22**, runs daily | ✅ the only hard input; a 16:55 task gets the same-day chain |
| `vix_data` | 5/24–6/22 | ✅ feeds `vix_beta_60d` |
| `index_skew_daily` | frozen at **6/12** (separate bug) | ⚠️ `rr_25d_abnormal` will be null on days after 6/12 until index_skew is fixed — **non-fatal**, every other column populates |

The job reads **stored data only** (no vendor call): it builds the delta surface
from each symbol's latest `oi_chain_eod` chain. So it only needs `oi_chain_eod`
to have already run for the day → schedule it **after** the 16:35 oi_chain_eod task.

## Steps (on the NAS, SSH as `drmithil`; docker needs `sudo`)

### 1. Smoke-test that the job exists in the baked image and runs
```bash
sudo bash /var/services/homes/drmithil/trading-intel/scripts/nas/run_job.sh skew_snapshots
tail -n 30 ~/ti_skew_snapshots.log
```
- Look for `skew_snapshots.done ... rows=NNN symbols=NN` and `EXIT 0`.
- **If you see `No module named 'trading_intel.scheduler.jobs.skew_snapshots'`** (or an
  ImportError), the deployed image predates this collector → rebuild it
  `--no-cache` from the GitHub tarball (git isn't installed on the NAS), per
  `DEPLOYMENT.md` / MEMORY "### NAS deployment", then re-run the smoke-test.
- **Note on timing the smoke-test:** if you run it *before* today's 16:35
  `oi_chain_eod`, the job reuses the previous day's chain and stamps it with
  today's date — a mildly mis-dated row. Either run the smoke-test after 16:35 ET,
  or ignore that one row (the next real 16:55 run overwrites it via the idempotent
  upsert on `(symbol, ts, horizon_dte)`).

### 2. Add the DSM task
Control Panel → Task Scheduler → Create → Scheduled Task → **User-defined script**,
**user: root**. Run command:
```
bash /var/services/homes/drmithil/trading-intel/scripts/nas/run_job.sh skew_snapshots
```
Schedule: **Daily, 16:55** (NAS clock must be US/Eastern — same as your other EOD
tasks). 16:55 places it after `oi_chain_eod` (16:35) and `index_skew` (16:50),
matching `scheduler/runner.py`.

> Alternative (more robust, optional): instead of a standalone task, fold skew
> into a single chained EOD task so ordering is guaranteed by construction —
> `run_job.sh ... oi_chain_eod ... index_skew skew_snapshots vol_regime`. See
> runbook Part C. Only worth it if you also tackle the index_skew gap.

### 3. Verify (laptop, against the NAS DB) after the next 16:55 run
```sql
SELECT max(ts)::date AS latest, count(DISTINCT symbol) AS names
FROM skew_snapshots;          -- latest should be today; names ≈ 71
```
Two consecutive collection days are enough for the day-over-day columns
(`shift_slide_label`, `rr_25d` deltas) to populate; the 63d/252d percentiles fill
in as history accrues.
