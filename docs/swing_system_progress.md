# Swing-trade options system — progress & next steps

*Status 2026-07-12. Companion to ADR-004. Descriptive/candidate system — not signals until validated (FlashAlpha rule 4).*

## ▶ Resume here (next session)
`scripts/swing_report.py` + `run_swing_report.bat` are **written but NOT yet run on the laptop — untested at runtime.** Start here:
1. `run_swing_report.bat AAPL` — one name first, to surface any runtime error fast.
2. Fix whatever breaks. Likely spots: FMP field names in `swing_report.analyze` (`rsi[0]["rsi"]`, `sma[0]["sma"]` — confirm the real keys), and it pulls the chain twice per name (once via `exposures()`, once via `skew_25d`).
3. Full run `run_swing_report.bat`; confirm `reports/swing_<date>.html` opens and GEX/DEX/ATM-IV look sane vs a name you know.

Only then move to the calibration + feature collector below.

## Goal
Combine RV/IV, skew, options positioning, and multi-timeframe (weekly/daily/4h) technicals to surface **1-3 month swing setups** as **low-risk/high-reward defined-risk option structures**, on demand. Fed by CVForge (ADR-004); convexlib stays primary for the live regime engine.

## Locked decisions
- **Universe:** watchlist deep; market-wide a lighter CVForge screen ranked below it.
- **Roles:** convexlib = live regime engine; CVForge = breadth (`/screen`,`/query`) + history (`/mas`) + FMP.
- **Scoring:** Stage-1 transparent weighted composite (0-100) + hard gates now → Stage-2 local fitted model (scikit-learn logistic/GBM, **not** an LLM) once ~3-6 months of features + forward-return labels bank.
- **Percentiles bank forward** — CVForge history is option *price* OHLC, not IV/chains, so IV-rank/skew percentiles cannot be backfilled.
- **Output:** on-demand `.bat`; Discord later.
- **Overlay (P7):** virattt ai-hedge-fund multi-analyst→risk-manager pattern, reimplemented in Python on **local Ollama**, fed by CVForge FMP. **dexter (P8) parked** (TS/Bun, violates pure-Python rule).

## Phases
P1 foundation · P2 feature layer · P3 conviction scorer · P4 `strategies/swing_options.py` (SignalGenerator) · P5 on-demand `swing_report` · P6 backtest (CVForge historical OHLC) · P7 analyst overlay (Ollama) · P8 dexter (parked).

## Done — written & on disk (2026-07-12)
- `docs/decisions/ADR-004-cvforge-secondary-datasource.md`.
- `trading_intel/greeks/black_scholes.py`: `bs_vanna` (validated analytic = vega-identity = finite-difference).
- `trading_intel/config.py`: `CVFORGE_API_KEY`, `CVFORGE_BASE_URL`.
- `trading_intel/clients/cvforge.py`: `CVForgeClient` — `chain()` synthesizes vanna/charm (per-day)/gxoi/dxoi/vxoi so `greeks.exposures.compute_exposures` runs unchanged; plus `spot`/`aggs`/`fmp`/`screen`/`query`. Data path live-validated on AAPL (inline), but the client module itself not yet imported/run on the laptop.
- `scripts/swing_report.py` (Stage-1) + `run_swing_report.bat` → `reports/swing_<date>.html`. **Not yet run — see Resume here.**

## Next steps (in order)
0. **Run + verify `run_swing_report.bat`** (see Resume here) — nothing below is trustworthy until the slice actually runs.
1. **Calibrate VEX/CHEX** scale vs convexlib native vanna/charm on a shared name — blocks trusting CVForge VEX/CHEX. GEX/DEX already solid.
2. **P1 leftovers:** `docs/playbooks/swing_options.md`; Alembic `0031` + daily feature-snapshot collector (bank IV-rank / skew / positioning percentiles — start ASAP so they mature); unit tests for `cvforge.py` + `swing_report.py`.
3. **P2 feature layer:** RV estimators, IV-rank + term slope, 25Δ skew + percentile, positioning (GEX/DEX/walls/VEX/CHEX), W/D/4h technical state → per-name setup-context table.
4. **P3** scorer module (extract Stage-1 from the report) → **P4** `swing_options.py` SignalGenerator (writes `signals`) → **P5** thicken report → **P6** backtest → **P7** Ollama overlay.

## Run
```
run_swing_report.bat              # whole WATCHLIST
run_swing_report.bat AAPL NVDA    # specific names
```
Needs `CVFORGE_API_KEY` in `.env` (Go/Research tier) + `.venv`. Run `pytest -q`, `black`, `ruff` locally before committing.

## 2026-07-16 — P3/P4 + Track B shipped (built + unit-tested in one session)

- **P3 extraction:** `trading_intel/swing/{scoring,features}.py` is now the single
  source of the Stage-1 scorer + RV / 25d-skew math. The P4 generator imports it.
  (`scripts/swing_report.py` + `scheduler/jobs/swing_features.py` still carry their
  own copies — DRY follow-up deferred; both were truncated in the build sandbox.)
- **P4 generator:** `strategies/swing_options.py` — writes `signals`,
  `experimental=True`, gated on Stage-1 score + a matured 252d IV-rank edge (never
  a raw greek crossing). Plus `scheduler/jobs/swing_signals.py` (once/day
  idempotent) and the playbook P4 section. 8 tests.
- **Track B:** `trading_intel/swing/credit_income.py` (cross-sectional IV/RV-rank
  ranking; credit side from the shared lean) + `scripts/credit_income_scan.py` +
  `run_credit_income_scan.bat`. 6 tests.
- ▶ Next: apply migrations + run the new DSM tasks + skew backfill
  (see `docs/handoff-2026-07-16.md`), then P6 backtest on CVForge OHLC.
