# Deploy — ^SPX Volatility Surface Changes (constant-maturity delta-vol board)

Developed + verified in a cloud clone (py_compile all, imports clean, **23/23**
market tests pass — 7 new for this board, render is server-side/no `<script>`).
Chains cleanly off your live `main` (base = the GEX work, `0045`). The git + NAS
steps are yours.

## What's in this change set (11 files)

**New**
- `trading_intel/market/vol_surface_cm.py` — pure assembly + the quadrant READ (unit-tested)
- `trading_intel/scheduler/jobs/vol_surface_cm.py` — daily EOD collector (chain → delta surface → CM interp → `vol_surface_cm`)
- `trading_intel/scheduler/jobs/vol_surface_cm_report.py` — Telegram job wrapper
- `scripts/vol_surface_cm_report.py` — the board layout (heatmaps + skew + term + read)
- `alembic/versions/0046_vol_surface_cm.py` — creates `vol_surface_cm` (down_revision `0045`)
- `tests/market/test_vol_surface_cm.py` — 7 unit tests

**Modified**
- `trading_intel/memory/models.py` — `VolSurfaceCM` model
- `trading_intel/config.py` — `VOL_SURFACE_CM_SYMBOLS=SPX`, `VOL_SURFACE_CM_DTES=7,14,21,30,60,90` + props
- `trading_intel/reports.py` — `build_vol_surface_cm()`
- `trading_intel/mcp/extra_tools.py` — `get_vol_surface_cm()` reader
- `trading_intel/mcp/server.py` — registers the `get_vol_surface_cm` MCP tool

Constant-maturity rungs 7/14/21/30/60/90d, full smile (5Δ→50Δ, both wings), weekly
change, heatmap panels, SPX only. Descriptor / research track (rule 4) — no `signals`.

## 1 · Extract + review (clean tree first!)
```
cd C:\Users\drmit\PycharmProjects\trading-intel
git status                      # make sure it's clean / stash unrelated WIP first
git apply --check gex_vol_surface_cm_changes.diff   # preferred: fails loudly on conflict
git apply gex_vol_surface_cm_changes.diff
#   — or —
tar xzf _vol_surface_cm_deploy.tgz
git diff
```

## 2 · Test
```
.venv\Scripts\activate
pytest -q tests\market\test_vol_surface_cm.py
pytest -q
```

## 3 · Commit (EXPLICIT paths — never `git add -A`, CRLF rule)
```
git add trading_intel/market/vol_surface_cm.py ^
        trading_intel/scheduler/jobs/vol_surface_cm.py ^
        trading_intel/scheduler/jobs/vol_surface_cm_report.py ^
        scripts/vol_surface_cm_report.py ^
        alembic/versions/0046_vol_surface_cm.py ^
        tests/market/test_vol_surface_cm.py ^
        trading_intel/memory/models.py trading_intel/config.py trading_intel/reports.py ^
        trading_intel/mcp/extra_tools.py trading_intel/mcp/server.py
git commit -m "CM vol-surface-changes board: collector + report + get_vol_surface_cm"
git push
```

## 4 · Migrate (laptop .env → NAS DB)
```
alembic upgrade head            # creates vol_surface_cm
alembic current                 # 0046
```

## 5 · NAS — pull tarball + rebuild (use deploy.sh, NOT a bare docker build)
```
ssh drmithil@192.168.1.211
sudo sh /var/services/homes/drmithil/trading-intel/scripts/nas/deploy.sh --run "vol_surface_cm"
```
`deploy.sh` curls the GitHub tarball → extracts → `docker build --no-cache` → then runs
the collector once (banks today's surface). Verify:
```
sudo docker run --rm trading-intel python -c "import trading_intel.scheduler.jobs.vol_surface_cm; print('ok')"
```

## 6 · DSM tasks (User: root, bash, ET, Mon–Fri)
| Time | Command | Purpose |
|---|---|---|
| 17:15 | `bash .../scripts/nas/run_job.sh vol_surface_cm` | bank the CM delta surface (its own chain pull; after the close) |
| 08:40 | `bash .../scripts/nas/run_job.sh vol_surface_cm_report` | post the board to Telegram pre-open |

(You can chain it onto your 08:40 task with the other reports:
`run_job.sh gex_transition_report vol_regime_report vol_surface_cm_report`.)

## 7 · IMPORTANT — this banks FORWARD (no backfill)
The collector captures a **live chain** each EOD — there are no historical chains to
rebuild past surfaces, so the board fills in from first run:
- **surface grid** — populated day 1.
- **weekly change + the READ** — need the prior compare date; meaningful after **~5 sessions**. Until then the report renders the surface and shows a "banks forward / no-read" note. That's expected, not a bug.

Verify rows growing:
```
python -c "from trading_intel.config import get_settings; from trading_intel.memory.db import make_session_factory; from sqlalchemy import text; s=make_session_factory(get_settings())(); print(s.execute(text('SELECT count(*), min(ts), max(ts) FROM vol_surface_cm')).one()); s.close()"
```
(≈144 rows/day: 6 rungs × 12 deltas × 2 wings.)

## Notes
- Restart Claude Desktop after deploy to pick up the new `get_vol_surface_cm` MCP tool.
- QQQ/SPY/IWM: add later by extending `VOL_SURFACE_CM_SYMBOLS` (job auto-seeds tickers).
- The 90d rung is safely bracketed by the existing 80-expiry pull (~112d); no pull widening needed (unlike the deferred iv_tenor 1Y gap).
