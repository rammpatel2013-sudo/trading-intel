# ruff: noqa: E501
"""terrain.py - created 2026-11-19. Price terrain for the test cockpit's Flow panel (F3).

For SPX / SPY / QQQ: how many regular-hours minutes price spent in each price bin over the last 3 sessions
(time at price, from the cockpit's own minute logs data/flow_YYYYMMDD.ndjson - field "x" = spot, one line per
symbol per minute). No vendor calls. Bins: SPX 5 pts, SPY / QQQ 0.5.
  HVN  = a bin at or above the 70th percentile of the visited bins (price spent a lot of time there: acceptance)
  LVN  = a visited bin at or below the 30th percentile (price passed through quickly)
  MID  = in between;  NEW = no minute there in the 3 sessions (fresh ground)
Rule tr-v0 (display only, not a signal).
OUTPUT  data/terrain.json  {"day", "from": [3 days], "syms": {SYM: {"bin", "m": {bin_low: minutes}, "hvn", "lvn"}}}
Written by wide_feed.py at its start (cvxmap-wide, 09:26) and by hand: python terrain.py [YYYYMMDD]
"""
from __future__ import annotations

import datetime
import glob
import json
import math
import os
import sys
from collections.abc import Iterable

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
BIN = {"SPX": 5.0, "SPY": 0.5, "QQQ": 0.5}
N_DAYS = 3
RTH = ("09:30", "16:00")


def bin_low(x: float, b: float) -> float:
    return round(math.floor(x / b) * b, 2)


def pct(vals: list[float], q: float) -> float:
    """Nearest-rank percentile (q in 0..1) of a non-empty list."""
    s = sorted(vals)
    return s[min(len(s) - 1, max(0, math.ceil(q * len(s)) - 1))]


def minutes_at_price(lines: Iterable[str], syms: tuple[str, ...] = tuple(BIN)) -> dict[str, dict[float, int]]:
    """Minute-log lines -> {sym: {bin_low: minutes}}, regular hours, one count per symbol minute."""
    out: dict[str, dict[float, int]] = {s: {} for s in syms}
    seen: set = set()
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            o = json.loads(line)
        except ValueError:
            continue
        s, t, x = o.get("s"), str(o.get("t") or ""), o.get("x")
        if s not in out or not (RTH[0] <= t < RTH[1]) or not isinstance(x, (int, float)) or x <= 0:
            continue
        if (s, o.get("d"), t) in seen:
            continue
        seen.add((s, o.get("d"), t))
        b = bin_low(float(x), BIN[s])
        out[s][b] = out[s].get(b, 0) + 1
    return out


def classify(m: dict[float, int], x: float, b: float, hvn: float, lvn: float) -> str:
    n = m.get(bin_low(x, b), 0)
    if n <= 0:
        return "NEW"
    return "HVN" if n >= hvn else ("LVN" if n <= lvn else "MID")


def build(data_dir: str, ymd: str, n_days: int = N_DAYS) -> dict:
    files = sorted(f for f in glob.glob(os.path.join(data_dir, "flow_2*.ndjson"))
                   if os.path.basename(f)[5:13] < ymd)[-n_days:]
    tot: dict[str, dict[float, int]] = {s: {} for s in BIN}
    for f in files:
        with open(f, encoding="utf-8") as fh:
            for s, m in minutes_at_price(fh).items():
                for k, v in m.items():
                    tot[s][k] = tot[s].get(k, 0) + v
    syms = {}
    for s, m in tot.items():
        if not m:
            continue
        v = list(m.values())
        syms[s] = {"bin": BIN[s], "m": {str(k): c for k, c in sorted(m.items())},
                   "hvn": pct(v, 0.7), "lvn": pct(v, 0.3)}
    return {"v": 1, "day": ymd, "rule": "tr-v0",
            "from": [os.path.basename(f)[5:13] for f in files], "syms": syms}


def write(data_dir: str, ymd: str) -> str:
    j = build(data_dir, ymd)
    path = os.path.join(data_dir, "terrain.json")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(j, fh, separators=(",", ":"))
    os.replace(tmp, path)
    return path


if __name__ == "__main__":
    day = sys.argv[1] if len(sys.argv) > 1 else datetime.date.today().strftime("%Y%m%d")
    print(write(DATA, day))
