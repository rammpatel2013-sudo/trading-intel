# Deploy — GEX-Transition signal + Vol-Regime monitor + brief block

Everything below was developed and verified in a cloud clone (py_compile + import
checks + 5 unit tests pass + both reports render server-side with no `<script>`).
The cloud can't reach your NAS/network, so these git + NAS + DSM steps are yours.

## What's in this change set (13 files, +1444/-1)

**New**
- `trading_intel/market/gex_transition.py` — pure state machine (unit-tested)
- `trading_intel/scheduler/jobs/gex_transition.py` — EOD collector (banks + backfills `gex_transition_daily`)
- `trading_intel/scheduler/jobs/gex_transition_report.py` — Telegram job wrapper
- `trading_intel/scheduler/jobs/vol_regime_report.py` — Telegram job wrapper
- `scripts/gex_transition_report.py` — GEX-Transition Signal report layout
- `scripts/vol_regime_report.py` — Vol-Regime & Skew Monitor layout
- `alembic/versions/0045_gex_transition_daily.py` — creates `gex_transition_daily` (down_revision `0044`)
- `tests/market/test_gex_transition.py` — 5 unit tests

**Modified**
- `trading_intel/memory/models.py` — `GexTransitionDaily` model
- `trading_intel/reports.py` — `build_gex_transition()` + `build_vol_regime()`
- `trading_intel/config.py` — `IV_TENOR_SYMBOLS` += `IWM` (starts RTY-SPX banking; job auto-seeds the ticker)
- `trading_intel/synthesis/daily_brief.py` — `_gex_transition_block` (Doc §04) + `_vol_skew_block` (§06)
- `trading_intel/synthesis/daily_brief_render.py` — renders both blocks

All new tables/jobs are **descriptor / research track only** (FlashAlpha rule 4) —
nothing writes to `signals`.

## 1 · Extract + review (laptop, repo root)

```
cd C:\Users\drmit\PycharmProjects\trading-intel
tar xzf _gex_vol_deploy.tgz          # overlays the 13 files into place
git status
git diff                              # review; also see gex_vol_changes.diff
```

## 2 · Test

```
.venv\Scripts\activate
pytest -q tests\market\test_gex_transition.py
pytest -q                            # full suite (rule 6: green before commit)
```

## 3 · Commit (EXPLICIT paths — never `git add -A`, CRLF churn rule)

```
git add trading_intel/market/gex_transition.py ^
        trading_intel/scheduler/jobs/gex_transition.py ^
        trading_intel/scheduler/jobs/gex_transition_report.py ^
        trading_intel/scheduler/jobs/vol_regime_report.py ^
        scripts/gex_transition_report.py scripts/vol_regime_report.py ^
        alembic/versions/0045_gex_transition_daily.py ^
        tests/market/test_gex_transition.py tests/market/__init__.py ^
        trading_intel/memory/models.py trading_intel/reports.py trading_intel/config.py ^
        trading_intel/synthesis/daily_brief.py trading_intel/synthesis/daily_brief_render.py
git commit -m "GEX-transition signal + vol-regime monitor + brief Doc/vol blocks"
git push
```

## 4 · Migrate the shared DB (laptop .env → NAS Postgres, so this hits prod)

```
alembic upgrade head          # creates gex_transition_daily
alembic current               # should show 0045
```

## 5 · Backfill the state series from available history (laptop venv)

```
python -m trading_intel.scheduler.jobs.gex_transition --backfill
```
This banks every session you have (5/22–6/4 and 7/28→now). The June/Aug GEX holes
are unrecoverable (collection gaps, no historical full-chain source) — that's
expected, not an error. Verify:
```
psql ... -c "SELECT count(*), min(ts), max(ts) FROM gex_transition_daily;"
```

## 6 · NAS — rebuild the image (git isn't on the NAS → GitHub tarball)

```
ssh drmithil@192.168.1.211
cd /var/services/homes/drmithil/trading-intel
# pull latest code via your usual tarball method, then:
sudo docker build --no-cache -t trading-intel .
```

## 7 · NAS — add DSM Task Scheduler tasks (User: root, bash, ET, Mon–Fri)

| Time (ET) | Command | Purpose |
|---|---|---|
| 17:10 | `bash .../scripts/nas/run_job.sh gex_transition` | bank the state (runs AFTER iv_tenor 17:05 so ATM IV is clean) |
| 08:40 | `bash .../scripts/nas/run_job.sh gex_transition_report vol_regime_report` | post both reports to Telegram pre-open |

(`run_job.sh` is generic — it runs `python -m trading_intel.scheduler.jobs.<name>`;
no dispatcher edit needed. The daily brief at 09:00 already picks up the new Doc/vol
blocks automatically — no new task for that.)

Run each once manually first and eyeball the log + Telegram:
```
bash .../scripts/nas/run_job.sh gex_transition
bash .../scripts/nas/run_job.sh gex_transition_report vol_regime_report
```

## 8 · Verify

- Telegram: two new cards (GEX-Transition Signal, Vol-Regime & Skew Monitor).
- Next 09:00 brief: §04 shows the **GEX transition** block; §06 shows the skew/dispersion chips.
- `SELECT count(*), max(ts) FROM gex_transition_daily;` grows daily; `state` populated.

## Deferred (staged separately — NOT in this deploy)

Two of the three CBOE-coverage gaps touch the live iv_tenor collector's schema/pull
and carry real blast radius, so they're intentionally held back:

- **10Δ put convexity** — needs a migration (new `iv_call_10d`/`iv_put_10d` columns),
  model + `iv_tenor_snapshots` job change, and `get_iv_tenor` to return them.
- **1Y (365d) skew/term** — needs the expiry pull widened (`_MAX_EXPS` 80 ≈ 112d
  won't bracket 1Y) + `IV_TENOR_DTE` += `365`.

**IWM (RTY-SPX)** IS included here (config only; the job auto-seeds the ticker), so
IWM iv_tenor starts banking now — the RTY-SPX report panel is a quick follow-up once
it has a couple weeks of history. Say the word and I'll build the 10Δ/365 migration +
job changes as their own reviewed patch.
