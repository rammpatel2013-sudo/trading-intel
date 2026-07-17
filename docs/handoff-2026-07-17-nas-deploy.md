# NAS deploy — pending batch (as of 2026-07-17)

Everything below is BUILT + unit-tested locally and waiting on ONE NAS deploy pass. Do it
as a batch: one image rebuild bakes all the new code, then add the DSM tasks. Migrations
apply to the **shared** NAS Postgres from the laptop (`alembic upgrade head`, which the
`run_*.bat` scripts already do), so the DB schema is handled by running the bats locally —
the NAS only needs the new *code* baked in + the DSM tasks.

See also `docs/handoff-2026-07-16.md` for the factor/frawd/swing specifics.

---

## 0. Smoke-test locally first (do NOT skip)

A DSM task that was never run fails silently every night. Confirm each banks real rows:

```
cd C:\Users\drmit\PycharmProjects\trading-intel
.venv\Scripts\python -m pytest -q tests\scheduler\test_surface_snapshots.py tests\vol\test_footprint.py
.\run_iv_term.bat                 # proven — banked 87 rows already
.\run_surface.bat                 # AFTER THE CLOSE (needs a live chain); run 2 days for the footprint
.venv\Scripts\python scripts\vol_surface_report.py            # eyeball the report + 'The read' panel
# factor_scores / frawd / skew-backfill: run per docs/handoff-2026-07-16.md and confirm rows
```

> Surface schema is now **fixed-STRIKE** via **NEW migration 0036** (`0036_surface_snapshots_fixed_strike.py`)
> which keys `surface_snapshots` on (symbol,ts,expiry_date,**strike**) with `delta` stored.
> The original moneyness **0035 was already applied**, so the change had to be a new
> migration (editing 0035 was inert → `column ... strike does not exist`). `run_surface.bat`
> runs `alembic upgrade head` (applies 0036; drops+recreates the table — EOD snapshot rows are
> disposable). If `alembic current` isn't at 0035 before this, stop and check. The footprint
> tracks the SAME front-week strikes day-over-day (not a 25Δ bucket).

## 1. Push + rebuild the NAS image (bakes ALL the new code)

```
git add -A && git commit -m "iv_term + surface + footprint collectors" && git push
```
On the NAS (git isn't installed there — pull the GitHub tarball, then, DS923+ needs sudo):
```
ssh drmithil@192.168.1.211        # /var/services/homes/drmithil/trading-intel
sudo docker build --no-cache -t trading-intel .
```

## 2. Add the DSM Task Scheduler tasks (mirror an existing one, `sudo docker run ...`)

| Task | Cron (ET) | Module | Notes |
|---|---|---|---|
| iv_term_snapshots | Mon–Fri 16:52 | `python -m trading_intel.scheduler.jobs.iv_term_snapshots` | reads stored oi_chain_eod; no migration |
| surface_snapshots | Mon–Fri 17:08 | `python -m trading_intel.scheduler.jobs.surface_snapshots` | live chain; migration 0036 fixed-strike (applied via run_surface.bat) |
| factor_scores | weekly (Mon) | `python -m trading_intel.scheduler.jobs.factor_scores` | per handoff-2026-07-16 |
| frawd/dldr etf_flows | per handoff | (see handoff-2026-07-16) | net-issuance |
| skew backfill | Mon–Fri 16:55 | `scripts/skew_backfill.py` DSM | run the backfill once first |

## 3. Do NOT deploy

- **sentiment** (`sentiment_snapshots`) — PARKED. The FMP institutional/analyst endpoints are
  paywalled (CVForge proxy 502 / direct key 402). Its schedule is commented out in `runner.py`;
  a DSM task would only bank null rows. Re-enable + add its task only once CVForge grants FMP.

## 4. Verify after deploy

- Next morning, check the new tables have fresh rows (`check_watchlist_coverage.py` for the
  watchlist; query `iv_tenor_snapshots` / `surface_snapshots` for the latest `ts`).
- MCP: restart Claude Desktop so `generate_vol_surface_report` registers.

_The runner.py cron entries are LOCAL/dev only — the NAS runs these DSM tasks, not runner.py._
