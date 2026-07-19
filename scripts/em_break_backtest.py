"""Interim cross-sectional scorecard for EM_BREAK_REENTRY (P6 path a).

Scores every banked re-entry signal against ``quotes_daily`` forward prices and prints
a hit-rate / expectancy scorecard split by conviction bucket, then writes the full
result to ``reports/em_break_backtest_<date>.json``. Interim SANITY (chain history is
thin) — not the final backtest; see ``docs/em_break_backtest.md``.

    python scripts/em_break_backtest.py [--max-days 20] [--since 2026-07-01]
"""

from __future__ import annotations

import argparse
import json
from datetime import date, datetime
from pathlib import Path

from trading_intel.backtest.cases import backtest_banked
from trading_intel.config import get_settings
from trading_intel.memory.db import make_session_factory


def _fmt(s: dict) -> str:
    hr = s["hit_rate"]
    ar = s["avg_r"]
    hr_s = f"{hr:.0%}" if hr is not None else "—"
    ar_s = f"{ar:+.2f}R" if ar is not None else "—"
    return f"n={s['n']:>3} closed={s['n_closed']:>3} hit={hr_s:>4} avgR={ar_s:>7}"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--max-days", type=int, default=20)
    ap.add_argument("--since", type=str, default=None, help="ISO date, e.g. 2026-07-01")
    args = ap.parse_args()
    since = date.fromisoformat(args.since) if args.since else None

    settings = get_settings()
    session = make_session_factory(settings)()
    try:
        res = backtest_banked(session, since=since, max_days=args.max_days)
    finally:
        session.close()

    print(
        f"EM_BREAK_REENTRY backtest — {res['n_signals']} signals, "
        f"{res['n_scored']} scored, max_days={res['max_days']}"
    )
    print("overall:", _fmt(res["summary"]))
    if res["by_conviction"]:
        print("by conviction bucket:")
        for bucket, s in res["by_conviction"].items():
            print(f"  {bucket:>12}: {_fmt(s)}")
    else:
        print("(no closed outcomes with conviction yet — bank forward)")

    out = {
        "as_of": datetime.utcnow().isoformat(),
        "n_signals": res["n_signals"],
        "n_scored": res["n_scored"],
        "max_days": res["max_days"],
        "summary": res["summary"],
        "by_conviction": res["by_conviction"],
        "results": [
            {
                "symbol": r.symbol,
                "entry_date": r.entry_date.isoformat(),
                "conviction": r.conviction,
                "result": (r.outcome.result if r.outcome else None),
                "r_multiple": (r.outcome.r_multiple if r.outcome else None),
                "reason": r.reason,
            }
            for r in res["results"]
        ],
    }
    dest = Path("reports") / f"em_break_backtest_{date.today().isoformat()}.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(out, indent=2))
    print(f"\nwrote {dest}")


if __name__ == "__main__":
    main()
