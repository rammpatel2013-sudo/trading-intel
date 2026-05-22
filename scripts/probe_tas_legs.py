"""Probe live tas to learn `spread_leg` / `tas_type` semantics.

The current `detect_structures` groups per-trade prints into packages on raw
(root, time-ms), which is noisy across expirations. The feed exposes
`spread_leg` and `tas_type`; this script dumps their live distributions and how
they line up with same-millisecond tickets, so we can switch the grouping key.

Run (Windows; Convex creds in .env, runs on the laptop) from the repo root:
    cd C:\\Users\\drmit\\PycharmProjects\\trading-intel
    .venv\\Scripts\\python scripts\\probe_tas_legs.py
    .venv\\Scripts\\python scripts\\probe_tas_legs.py --symbol SPY --limit 1000
"""

from __future__ import annotations

import argparse

import pandas as pd

from trading_intel.clients.convex import ConvexClient
from trading_intel.config import get_settings


def main() -> None:
    parser = argparse.ArgumentParser(description="Probe tas spread_leg / tas_type semantics.")
    parser.add_argument("--symbol", default="SPX")
    parser.add_argument("--limit", type=int, default=800)
    args = parser.parse_args()

    pd.set_option("display.width", 200)
    pd.set_option("display.max_columns", 30)

    client = ConvexClient(get_settings())
    tas = client.time_and_sales(args.symbol.upper(), limit=args.limit)
    print(f"\n=== {args.symbol.upper()}  {len(tas)} prints  cols={list(tas.columns)} ===\n")
    if tas.empty:
        print("No prints returned (market closed / empty).")
        return

    for col in ("spread_leg", "tas_type"):
        if col in tas.columns:
            print(f"--- {col} value counts ---")
            print(tas[col].value_counts(dropna=False).head(20).to_string())
            print()

    # Same-(root, time-ms) tickets that span >1 contract — show their leg detail.
    has = [c for c in ("spread_leg", "tas_type") if c in tas.columns]
    cols = ["time", "root", "expiration", "strike", "opt_kind", "size", "premium", *has]
    cols = [c for c in cols if c in tas.columns]
    grp = tas.groupby(["root", "time"], sort=False)
    multi = [(k, g) for k, g in grp if len(g) > 1]
    print(f"--- {len(multi)} multi-print tickets (showing first 8) ---\n")
    for (root, t), g in multi[:8]:
        print(f"[{root} @ {t}]  ({len(g)} prints)")
        print(g[cols].to_string(index=False))
        print()


if __name__ == "__main__":
    main()
