# NAS deployment — tasks to add (as of 2026-05-26)

Everything built recently (vol-richness, delta-flow, live-GEX, the ET timezone
fix, the `.N/.TO` cleanup) needs three things to go live on the NAS. The NAS runs
**baked Docker images via DSM Task Scheduler tasks** — it does NOT run
`scheduler/runner.py`, so new code needs an image rebuild and new code needs new
DSM tasks.

---

## 1. One-time, from the laptop (PowerShell)

```powershell
# Apply all pending migrations to the NAS DB (0013 vol_richness, 0014 delta_flow, 0015 live_gex)
.venv\Scripts\alembic upgrade head

# Clean existing research-watchlist tickers that still carry .N / .TO suffixes
.venv\Scripts\python scripts\normalize_watchlist_symbols.py
```

## 2. Push + rebuild the NAS image

```
git push    # main
# On the NAS: rebuild the image --no-cache from the GitHub tarball (git isn't
# installed there). REQUIRED for the timezone fix (timeutils.eastern_now) and
# every new collector below to exist in the image. See DEPLOYMENT.md / MEMORY
# "### NAS deployment" for the exact image-rebuild + docker-run wrapper.
```

## 3. DSM Task Scheduler tasks

**Use the dispatcher** `scripts/nas/run_job.sh` so the docker wrapper lives in one
place. Open it once and set the 5 variables at the top (`DB_URL`, paths, image,
network) to match your existing working tasks. Then each DSM task is a
**user-defined script** (Control Panel → Task Scheduler → Create → Scheduled Task
→ User-defined script; **user: root**) whose run command is:

```
bash /var/services/homes/drmithil/trading-intel/scripts/nas/run_job.sh <MODULE>
```

For a chained task, pass several modules (run in sequence):

```
bash .../scripts/nas/run_job.sh quotes_daily prune_intraday
```

Set the **Schedule** per the tables below. Intraday jobs **self-guard market
hours** (they no-op outside 09:30-16:00 ET), so a task can run all day; scoping
the DSM window to RTH (Weekly Mon-Fri + first/last run + "repeat every N min")
just avoids the wasted runs. Times follow the NAS clock — keep it on US Eastern.

### Already running (no action)
| Module | Cadence |
|---|---|
| `intraday_flow` | every 5 min, Mon-Fri RTH |
| `flow_snapshot` | every 30 min, Mon-Fri RTH |
| `quotes_daily` (+ `prune_intraday`) | daily 16:45 ET |

### ⚠️ Add these — they never had a task (this is why GEX surface / OI study don't accumulate)
| Module | Schedule (ET) | Notes |
|---|---|---|
| `greeks_snapshot` | daily 06:45 | aggregate GEX/DEX/flip per name |
| `chain_snapshot`  | daily 06:45 | per-strike chain (feeds GEX surface) |

### Add — new EOD jobs
| Module | Schedule (ET) | Notes |
|---|---|---|
| `gex_rolling` | daily 16:30 | 6-month rolling GEX |
| `oi_chain_eod` | daily 16:35 | wide EOD chain (re-deploy: has the param-cap batch fix) |
| `prune_oi_chain` | daily 02:20 | retention |
| `vol_richness` | daily 16:40 | IV-vs-forecast-RV rich/cheap scan |
| `am_summary` | daily 06:55 | morning regime report (local LLM) |
| `vix_snapshot` | daily 16:45 | VIX/VVIX term structure + credit |

### Add — new intraday jobs
| Module | Schedule (ET) | Notes |
|---|---|---|
| `delta_flow` | every 5 min, RTH | cumulative call/put delta-notional |
| `live_gex` | every 10 min, RTH | live per-strike GEX (delta-band); **heavy at full watchlist — see knob** |
| `prune_live_gex` | daily 02:30 | deletes live_gex older than 24h |

---

## Notes

- **`live_gex` load:** at the full effective watchlist it pulls a near-ATM chain
  for ~61 names every 10 min. If you hit Convex rate limits, set
  `LIVE_GEX_SYMBOLS=SPX,SPY,QQQ` (or any comma list) in `.env` to scope it down,
  and/or widen the cadence. Other knobs: `LIVE_GEX_STRIKE_RANGE` (default 0.10),
  `LIVE_GEX_DELTA_LO`/`HI` (0.30/0.70), `LIVE_GEX_RETENTION_HOURS` (24).
- **Timezone:** after the image rebuild, all collectors stamp wall-clock Eastern
  regardless of the host clock, so charts read 09:30-16:00 ET. Rows collected
  before the rebuild keep their old (UTC) stamp until re-collected.
- **Live-GEX dashboard:** the collector + table are ready, but the dashboard
  doesn't yet *prefer* live_gex over the daily snapshot (that's the pending wiring
  step). Adding the DSM task now starts accruing the data so it's ready when the
  wiring lands.
