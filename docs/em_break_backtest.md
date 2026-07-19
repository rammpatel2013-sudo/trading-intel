# EM-break re-entry — P6 backtest / validation

*Built 2026-07-19. Validates `EM_BREAK_REENTRY` so we can eventually drop
`experimental=True`. Engine + data paths live in `trading_intel/backtest/`; the
signal + gate are in `strategies/em_break_reentry.py` and the assembling job in
`scheduler/jobs/em_break_reentry.py`.*

## What it measures

Each re-entry is a defined-risk UPSIDE structure: enter near the put wall after
stabilization, target the call wall, stop on a put-wall break. The pure engine
(`backtest/em_break.py::evaluate_outcome`) walks the forward OHLC path and decides
whether **target or stop hit first**, returning the R-multiple (reward/risk on a win,
−1 on a stop, marked-to-last on an open trade). `summarize` / `summarize_by_bucket`
roll a set of outcomes into hit-rate + expectancy, split by conviction so we can check
the gate actually discriminates (higher conviction → better outcome).

## The three paths

**(a) Interim cross-sectional scorecard — available now.**
Scores every banked `EM_BREAK_REENTRY` signal against `quotes_daily` and prints a
hit-rate / expectancy scorecard by conviction bucket.

```
python scripts/em_break_backtest.py --max-days 20
```

Writes `reports/em_break_backtest_<date>.json`. This is SANITY, not proof — with
only forward-banked signals the sample is small and mostly open. Its real value early
on is the by-conviction split: if the ≥85 bucket doesn't out-hit the <70 bucket once a
dozen-plus close, the gate weights need a look.

**(b) Historical reconstruction — CVForge, needs a field probe.**
`backtest/reconstruct.py` rebuilds past cases from CVForge historical option OHLC: the
day-before-earnings ATM call+put → straddle → implied move, test whether the realized
gap broke it, then walk the underlying forward. The **math is pure + tested**; the
`load_cases` fetcher is a documented skeleton that **raises until the CVForge
historical-option field names are confirmed with a live probe** (same discipline as
the `earn_cal` schema check). Absent historical OI, the call wall is proxied at the
expected-move level — an explicit approximation. CVForge is the existing secondary
datasource (ADR-004), so no new vendor (rule 1).

**(c) Bank-forward — the real backtest, accrues over time.**
`scheduler/jobs/em_break_validation.py` walks `quotes_daily` forward from each banked
signal weekly and upserts realized outcomes into `signal_outcomes` (migration 0038).
OPEN trades are refreshed until they close (idempotent on `signal_id`). This is the
authoritative sample; it just needs weeks of prints to fill.

## Success criteria to drop `experimental=True`

Flip the flag in `strategies/em_break_reentry.py::_payload` only when, on the closed
sample in `signal_outcomes` (path c, corroborated by path b if available):

1. **Sample:** ≥ 25 closed setups (win/loss), across ≥ 8 distinct names.
2. **Edge:** overall hit-rate ≥ 55% AND average R ≥ +0.30 (positive expectancy).
3. **Monotonic gate:** the ≥ 85 conviction bucket's hit-rate ≥ the < 70 bucket's — the
   score has to mean something.
4. **Robustness:** (2) still holds at `--max-days` 15 and 25 (not a horizon artifact).

Until all four hold, the signal stays `experimental` and is not alerted.

## Deploy (path c)

- **Migration:** `alembic upgrade head` (applies 0038 `signal_outcomes`; round-trip
  `alembic downgrade -1 && alembic upgrade head`). Runs from the laptop against the
  shared NAS Postgres (`db-topology`).
- **NAS DSM task:** weekly, e.g. **Sat 09:00** (after `factor_scores` 08:00):
  `bash /var/services/homes/drmithil/trading-intel/scripts/nas/run_job.sh em_break_validation`
  (rebuild the image `--no-cache` first so the new job + backtest package are baked in).
- Local dev runner already registers it (Sat 09:00) — NAS uses the DSM task, not
  `runner.py`.

## Related

`docs/em-break-system-plan.md`, `docs/DEPLOY_2026-07-18_em_break.md`,
`docs/decisions/ADR-006-earnings-anchor.md`, `ADR-007-systematic-flow-proxy.md`.

> Timing note: `em_break_reentry` (and now the enrichment reads it calls —
> `get_straddle` / `get_oi_changes` / walls) all read `oi_chain_eod`, which the NAS
> refreshes at **18:00**. The current DSM slot is 17:10, so it reads yesterday's chain.
> Move the scanner to **~18:50** (after the 18:00 OI chain + 18:45 iv_term) so the
> walls, straddle decay and overwriter ΔOI/ΔIV are same-day.
