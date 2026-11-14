#!/usr/bin/env python
# ruff: noqa: E501, RUF100
"""iv_grid_report.py - 2026-11-13 (overnight run), Claude for Mithil. #60 group: cheap / rich IV percentile grid.

One grid from the stored constant-maturity IV rows (iv_tenor_snapshots, written EOD by jobs/iv_tenor_snapshots.py):
rows = symbols, columns = 30-day ATM IV, 90-day ATM IV, term (30d / 90d), 25-delta put skew (put 25d - call 25d, 30d),
each as today's PERCENTILE of that symbol's own stored history (up to 1 year). <= 20th = cheap, >= 80th = rich.
Needs MIN_ROWS days of history per symbol; younger rows say so instead of guessing.

Writes reports/iv_grid/latest.json; Telegram table with --telegram (default on; --no-telegram to skip).
RUN (NAS, DSM task "TI iv grid", Mon-Fri 18:20, user root):
    bash /var/services/homes/drmithil/trading-intel/scripts/nas/run_script.sh iv_grid_report
Read-only on the DB, no vendor calls. Descriptor only (rule 4).
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

OUT = REPO / "reports" / "iv_grid"
MIN_ROWS = 60
LOOKBACK = 252
COLS = (("atm30", "30d ATM"), ("atm90", "90d ATM"), ("term", "30d/90d"), ("skew30", "25Δ skew"))


def pct_rank(hist: list[float], v: float) -> float:
    return round(100.0 * sum(1 for x in hist if x <= v) / len(hist), 1)


def tag(p: float | None) -> str:
    if p is None:
        return "–"
    return "cheap" if p <= 20 else "rich" if p >= 80 else "mid"


def metrics(rows: list[dict]) -> dict[str, dict]:
    """rows = iv_tenor_snapshots dicts (symbol, ts, tenor_dte, iv_atm, iv_put_25d, iv_call_25d). -> per symbol, per
    day: atm30, atm90, term, skew30 (vol pts / ratio)."""
    by: dict = defaultdict(lambda: defaultdict(dict))
    for r in rows:
        by[r["symbol"]][r["ts"]][int(r["tenor_dte"])] = r
    out: dict = {}
    for sym, days in by.items():
        ser: dict = defaultdict(list)
        for d in sorted(days):
            t30, t90 = days[d].get(30), days[d].get(90)
            a30 = t30 and t30.get("iv_atm")
            a90 = t90 and t90.get("iv_atm")
            if a30:
                ser["atm30"].append((d, 100.0 * a30))
            if a90:
                ser["atm90"].append((d, 100.0 * a90))
            if a30 and a90:
                ser["term"].append((d, a30 / a90))
            if t30 and t30.get("iv_put_25d") and t30.get("iv_call_25d"):
                ser["skew30"].append((d, 100.0 * (t30["iv_put_25d"] - t30["iv_call_25d"])))
        out[sym] = ser
    return out


def grid(rows: list[dict]) -> list[dict]:
    out = []
    for sym, ser in sorted(metrics(rows).items()):
        row: dict = {"symbol": sym, "cells": {}}
        for k, _ in COLS:
            s = ser.get(k) or []
            if not s:
                row["cells"][k] = {"v": None, "pct": None, "n": 0, "tag": "–"}
                continue
            hist = [v for _, v in s[-LOOKBACK:]]
            v = s[-1][1]
            p = pct_rank(hist, v) if len(hist) >= MIN_ROWS else None
            row["cells"][k] = {
                "v": round(v, 3 if k == "term" else 2),
                "pct": p,
                "n": len(hist),
                "tag": tag(p),
                "date": s[-1][0].isoformat(),
            }
        out.append(row)
    return out


def text(g: list[dict]) -> str:
    L = [
        "<b>IV grid</b> (today's percentile of each symbol's own history; ≤20 cheap · ≥80 rich)",
        "<pre>",
    ]
    L.append("sym    " + "".join(f"{lab:>12}" for _, lab in COLS))
    for r in g:
        cells = []
        for k, _ in COLS:
            c = r["cells"][k]
            if c["v"] is None:
                cells.append(f"{'–':>12}")
            elif c["pct"] is None:
                cells.append(f"{('n=' + str(c['n'])):>12}")
            else:
                mark = "▼" if c["tag"] == "cheap" else "▲" if c["tag"] == "rich" else " "
                cells.append(f"{c['pct']:>9.0f}th{mark}")
        L.append(f"{r['symbol']:<7}" + "".join(cells))
    L.append("</pre>")
    young = sorted(
        {
            r["symbol"]
            for r in g
            for c in r["cells"].values()
            if c["v"] is not None and c["pct"] is None
        }
    )
    if young:
        L.append(
            f"<i>n=… = fewer than {MIN_ROWS} stored days, no percentile yet: {', '.join(young)}</i>"
        )
    L.append("<i>Descriptor, not a signal.</i>")
    return "\n".join(L)


def load_rows() -> list[dict]:
    from sqlalchemy import select

    from trading_intel.config import get_settings
    from trading_intel.memory.db import make_session_factory
    from trading_intel.memory.models import IvTenorSnapshot as T

    sf = make_session_factory(get_settings())
    with sf() as s:
        q = select(T.symbol, T.ts, T.tenor_dte, T.iv_atm, T.iv_put_25d, T.iv_call_25d)
        return [dict(r._mapping) for r in s.execute(q)]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-telegram", action="store_true")
    a = ap.parse_args(argv)
    g = grid(load_rows())
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "latest.json").write_text(
        json.dumps({"grid": g, "min_rows": MIN_ROWS}, indent=1), encoding="utf-8"
    )
    txt = text(g)
    print(txt)
    if not a.no_telegram and g:
        from trading_intel.clients.telegram import TelegramClient
        from trading_intel.config import get_settings

        TelegramClient(get_settings()).send_message(txt)
    print(f"iv_grid.done symbols={len(g)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
