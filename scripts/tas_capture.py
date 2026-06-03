"""Capture the ConvexValue market-wide options tape to a daily CSV (Phase 1).

The tas feed is live-only, so this polls the whole-market tape on an interval
during RTH, de-duplicates overlapping prints, keeps the big ones (size filter),
enriches each with notional + an inferred buy/sell, and rewrites a daily CSV you
can open in Excel. Prototype on the laptop; once proven it becomes a NAS job
writing to a Postgres table with a 30-day prune.

Rule 1: the only Convex entry point is ``ConvexClient`` (in clients/). Rule 4:
this is descriptive flow capture - no signals.

Run during market hours (9:30-16:00 ET):
    python scripts/tas_capture.py
    python scripts/tas_capture.py --min-size 250 --min-premium 25000 --interval 30
"""
from __future__ import annotations

import argparse
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from trading_intel.clients.convex import ConvexClient
from trading_intel.config import get_settings

_ET = ZoneInfo("America/New_York")
_VALID_COLS = (
    "time", "symbol", "bid_price", "ask_price", "price", "theo", "size", "value",
    "exchange_sale_conditions", "aggressor_side", "spot", "delta", "gamma",
    "vega", "theta", "volatility",
)


def _infer_side(row: pd.Series) -> str:
    """Recover buy/sell when aggressor_side is 'undefined' (price vs bid/ask)."""
    side = str(row.get("aggressor_side", "")).lower()
    if side in {"buy", "sell", "mid"}:
        return side
    price, bid, ask = row.get("price"), row.get("bid_price"), row.get("ask_price")
    try:
        price, bid, ask = float(price), float(bid), float(ask)
    except (TypeError, ValueError):
        return "unknown"
    if ask > 0 and price >= ask:
        return "buy"
    if bid > 0 and price <= bid:
        return "sell"
    return "mid"


def _enrich(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    price = pd.to_numeric(df.get("price"), errors="coerce")
    size = pd.to_numeric(df.get("size"), errors="coerce")
    df["notional"] = (price * size * 100).round(0)
    df["side"] = df.apply(_infer_side, axis=1)
    return df


def _key(row: pd.Series) -> tuple:
    return (str(row.get("time")), str(row.get("symbol")),
            row.get("price"), row.get("size"))


def main() -> None:
    p = argparse.ArgumentParser(description="Capture the market-wide options tape.")
    p.add_argument("--min-premium", type=float, default=25000.0,
                   help="MAIN filter: keep trades with notional (price*size*100) >= this $")
    p.add_argument("--min-size", type=int, default=1, help="floor to drop zero/empty prints")
    p.add_argument("--min-abs-delta", type=float, default=0.0,
                   help="optional: drop |delta| below this (CAREFUL: hides far-OTM catalyst bets)")
    p.add_argument("--interval", type=int, default=30, help="seconds between polls")
    p.add_argument("--limit", type=int, default=500, help="prints pulled per poll")
    p.add_argument("--out-dir", default="data/tas", help="output folder")
    p.add_argument("--close", default="16:00", help="stop time ET (HH:MM)")
    args = p.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    day = datetime.now(_ET).strftime("%Y-%m-%d")
    out_path = out_dir / f"{day}.csv"
    close_h, close_m = (int(x) for x in args.close.split(":"))

    client = ConvexClient(get_settings())
    seen: set[tuple] = set()
    kept: list[dict] = []
    print(f"capturing tape -> {out_path} (size>={args.min_size}, "
          f"premium>=${args.min_premium:,.0f}, every {args.interval}s, until {args.close} ET)")

    polls = 0
    while True:
        now = datetime.now(_ET)
        if (now.hour, now.minute) >= (close_h, close_m):
            print("reached close time; stopping.")
            break
        try:
            df = client.time_and_sales(None, limit=args.limit, orderby="time", cols=_VALID_COLS)
        except Exception as exc:  # noqa: BLE001 - keep the day's capture alive on a blip
            print(f"  poll error ({type(exc).__name__}): {exc}; retrying next interval")
            time.sleep(args.interval)
            continue

        new = 0
        if df is not None and not df.empty:
            df = _enrich(df)
            for _, row in df.iterrows():
                k = _key(row)
                if k in seen:
                    continue
                seen.add(k)
                size = pd.to_numeric(row.get("size"), errors="coerce")
                notl = pd.to_numeric(row.get("notional"), errors="coerce")
                adelta = pd.to_numeric(row.get("delta"), errors="coerce")
                if pd.isna(size) or size < args.min_size:
                    continue
                if args.min_premium and (pd.isna(notl) or notl < args.min_premium):
                    continue
                if args.min_abs_delta and (pd.isna(adelta) or abs(adelta) < args.min_abs_delta):
                    continue
                kept.append(row.to_dict())
                new += 1

        if kept:
            pd.DataFrame(kept).to_csv(out_path, index=False)  # checkpoint each poll
        polls += 1
        print(f"  poll {polls} @ {now:%H:%M:%S} ET: +{new} kept (total {len(kept)})")
        time.sleep(args.interval)

    print(f"done. {len(kept)} prints saved to {out_path}")


if __name__ == "__main__":
    main()
