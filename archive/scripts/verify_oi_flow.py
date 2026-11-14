"""Read-only verification for the OI & flow-change study + GEX surface.

Run this AFTER the second real EOD `oi_chain_eod` snapshot lands (next real EOD
= Tue 2026-05-26; Mon 25th is Memorial Day). It checks, per index symbol:

1. that there are >= 2 distinct EOD snapshots (so day-over-day is computable),
2. that Convex's native ``oi_change`` agrees with our own day-over-day ΔOI diff
   (sign-agreement rate on overlapping strikes — a sanity cross-check), and
3. that the GEX surface has accumulated >= 2 daily columns.

Read-only: it issues only SELECTs and prints a report. Nothing is written.

    python scripts/verify_oi_flow.py
"""

from __future__ import annotations

import math

import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from trading_intel.config import get_settings
from trading_intel.dashboard.gex_surface import gex_strike_matrix, load_gex_strike_series
from trading_intel.dashboard.oi_changes import load_oi_change_frame, summarize_oi_change
from trading_intel.memory.db import make_session_factory
from trading_intel.memory.models import OiChainEod

_SYMBOLS = ("SPX", "SPY", "QQQ")


def _distinct_ts(session: Session, symbol: str) -> int:
    return int(
        session.execute(
            select(func.count(func.distinct(OiChainEod.ts))).where(OiChainEod.symbol == symbol)
        ).scalar_one()
    )


def _oi_ch_agreement(frame: pd.DataFrame) -> tuple[int, int]:
    """Count strikes where vendor oi_change and our ΔOI share the same sign."""
    agree = total = 0
    for _, row in frame.iterrows():
        ours, vendor = row.get("d_oi"), row.get("oi_change_vendor")
        if ours is None or vendor is None or (isinstance(vendor, float) and math.isnan(vendor)):
            continue
        total += 1
        if (ours >= 0) == (vendor >= 0):
            agree += 1
    return agree, total


def main() -> None:
    settings = get_settings()
    session_factory = make_session_factory(settings)
    with session_factory() as session:
        for sym in _SYMBOLS:
            print(f"\n=== {sym} ===")
            n_ts = _distinct_ts(session, sym)
            print(f"  oi_chain_eod distinct EOD snapshots: {n_ts}")
            if n_ts >= 2:
                frame = load_oi_change_frame(session, sym)
                if frame is not None and not frame.empty:
                    agree, total = _oi_ch_agreement(frame)
                    pct = (100.0 * agree / total) if total else float("nan")
                    print(f"  native oi_ch vs our ΔOI sign-agree: {agree}/{total} ({pct:.0f}%)")
                    summary = summarize_oi_change(frame)
                    print(f"  total ΔGEX = {summary.total_d_gex:,.0f}")
                    print(f"  mean ΔIV   = {summary.mean_d_iv:+.4f}")
                    print(f"  read-through: {summary.note}")
            else:
                print("  (need a 2nd EOD snapshot before day-over-day lights up)")

            series = load_gex_strike_series(session, sym, days=10)
            matrix = gex_strike_matrix(series)
            print(f"  GEX surface daily columns: {matrix.shape[1] if not matrix.empty else 0}")


if __name__ == "__main__":
    main()
