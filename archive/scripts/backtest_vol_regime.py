"""CLI: validate ``INDEX_VOL_REGIME`` signals against forward returns.

Reads all historical regime signals + SPY (or chosen benchmark) closes from
the configured ``DATABASE_URL``, computes per-state conditional forward-return
distributions at 1d / 5d / 20d, and writes a markdown report under
``reports/vol_regime_backtest_<run-date>.md``.

This is the validation gate before any leverage-policy module is built on top
of the regime classifier — see ``trading_intel/backtest/regime_validate.py``
for the math and ``docs/decisions/`` for the ADR that consumes the output.

Manual run (from repo root):

    python -m scripts.backtest_vol_regime
    python -m scripts.backtest_vol_regime --benchmark SPY --horizons 1 5 20
    python -m scripts.backtest_vol_regime --start 2026-01-01 --end 2026-05-31
"""

from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

import structlog

from trading_intel.backtest.regime_validate import (
    RegimeBacktestConfig,
    render_markdown,
    run_backtest,
)
from trading_intel.config import get_settings
from trading_intel.memory.db import make_session_factory
from trading_intel.timeutils import eastern_now

log = structlog.get_logger(__name__)


def _parse_date(s: str) -> date:
    return date.fromisoformat(s)


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Backtest INDEX_VOL_REGIME signals.")
    p.add_argument("--benchmark", default="SPY", help="Quote symbol to score against.")
    p.add_argument(
        "--horizons",
        nargs="+",
        type=int,
        default=[1, 5, 20],
        help="Forward-return horizons in trading days.",
    )
    p.add_argument("--start", type=_parse_date, default=None, help="Start date (YYYY-MM-DD).")
    p.add_argument("--end", type=_parse_date, default=None, help="End date (YYYY-MM-DD).")
    p.add_argument(
        "--no-overlay-split",
        action="store_true",
        help="Skip the per-(state, overlay) section in the report.",
    )
    p.add_argument(
        "--out-dir",
        type=Path,
        default=Path("reports"),
        help="Directory for the markdown report (default: ./reports).",
    )
    return p


def _summary_line(result_label: str, n: int, mean: float | None, ir: float | None) -> str:
    mean_str = "—" if mean is None else f"{mean * 100:+.2f}%"
    ir_str = "—" if ir is None else f"{ir:+.3f}"
    return f"  {result_label:>20}  n={n:>4}  mean={mean_str:>8}  ir={ir_str:>7}"


def main() -> int:
    args = _build_parser().parse_args()
    settings = get_settings()
    factory = make_session_factory(settings)

    cfg = RegimeBacktestConfig(
        benchmark_symbol=args.benchmark,
        horizons_days=tuple(args.horizons),
        start=args.start,
        end=args.end,
        split_by_overlay=not args.no_overlay_split,
    )

    with factory() as session:
        result = run_backtest(session, cfg)

    if result.n_signals_total == 0:
        log.warning("backtest_vol_regime.no_signals")
        print("No INDEX_VOL_REGIME signals found in the configured DB.")
        return 1

    args.out_dir.mkdir(parents=True, exist_ok=True)
    run_date = eastern_now().date().isoformat()
    out_path = args.out_dir / f"vol_regime_backtest_{run_date}.md"
    out_path.write_text(render_markdown(result), encoding="utf-8")
    log.info("backtest_vol_regime.report_written", path=str(out_path))

    # stdout summary at 5d horizon (the default interpretation horizon).
    five_d = next((h for h in cfg.horizons_days if h == 5), cfg.horizons_days[0])
    print(f"\nVol-Regime backtest — benchmark={cfg.benchmark_symbol}, 5d horizon")
    print(f"  signals: {result.n_signals_total}, window: {result.date_range}")
    print(f"  report:  {out_path}\n")
    print("  Baseline (unconditional):")
    base = result.baseline.get(five_d)
    if base is not None:
        print(_summary_line("BASELINE", base.n, base.mean, base.ir))
    print("\n  By state:")
    for s in result.by_state:
        if s.horizon_days != five_d:
            continue
        print(_summary_line(s.label, s.stats.n, s.stats.mean, s.stats.ir))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
