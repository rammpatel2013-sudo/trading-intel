"""Probe: ConvexValue time-and-sales (TAS) — the MARKET-WIDE options tape.

TAS is not per-ticker: by default it streams every name's prints and the
``symbol`` column says which contract each one is (matches convexvalue.com/go/tas/).
Uses ``ConvexClient.time_and_sales`` (rule 1: the only convex entry point).
Prints shape, columns, dtypes and a sample so you can see the live tape.

Run locally (needs .env creds + network; live data only DURING market hours):
    python probe_convex_tas.py                 # whole market, biggest by premium
    python probe_convex_tas.py --orderby time  # chronological tape
    python probe_convex_tas.py NVDA            # filter to one root
    python probe_convex_tas.py --full          # validated expanded columns
"""
from __future__ import annotations

import argparse

import pandas as pd

from trading_intel.clients.convex import ConvexClient
from trading_intel.config import get_settings

# Exactly the columns the ConvexValue tas terminal uses (verified working).
_VALID_COLS = (
    "time", "symbol", "bid_price", "ask_price", "price", "theo", "size", "value",
    "exchange_sale_conditions", "aggressor_side", "spot", "delta", "gamma",
    "vega", "theta", "volatility",
)


def _describe(df: pd.DataFrame, title: str) -> None:
    print(f"\n===== {title} =====")
    print(f"rows x cols: {df.shape[0]} x {df.shape[1]}")
    if df.empty:
        print("(no rows returned — live-only feed; try during market hours)")
        return
    print("\ncolumns + dtypes:")
    for col in df.columns:
        print(f"  {col:<26} {df[col].dtype}")
    if "aggressor_side" in df.columns:
        print("\naggressor_side values:", df["aggressor_side"].value_counts(dropna=False).to_dict())
    print("\nsample:")
    with pd.option_context("display.max_columns", None, "display.width", 220):
        print(df.head(15).to_string(index=False))


def main() -> None:
    parser = argparse.ArgumentParser(description="Probe ConvexValue market-wide time & sales.")
    parser.add_argument("symbol", nargs="?", default=None, help="optional root filter, e.g. NVDA")
    parser.add_argument("--limit", type=int, default=40, help="max prints")
    parser.add_argument("--orderby", default="value", help="value (biggest premium) or time")
    parser.add_argument("--day", type=int, default=0, help="0 = today (live), 1 = prior session")
    parser.add_argument("--full", action="store_true", help="use the validated expanded columns")
    args = parser.parse_args()

    client = ConvexClient(get_settings())
    scope = args.symbol or "WHOLE MARKET"
    kw = {"limit": args.limit, "orderby": args.orderby, "day": args.day}
    if args.full:
        kw["cols"] = _VALID_COLS

    df = client.time_and_sales(args.symbol, **kw)
    _describe(df, f"TAS [{scope}] orderby={args.orderby} day={args.day}")
    print("\ndone.")


if __name__ == "__main__":
    main()
