#!/usr/bin/env python
"""Per-trade buy / sell sides for today's SPX 0DTE — the cockpit's "Side: Per trade" (2026-11-05).

Mithil's rule: trust a trade's side only when it is clearly AT the ask (buy) or AT the bid (sell)
against its OWN quote at print time; every other print (mid, inside the spread, outside, no quote)
is decided by the tick rule — the side of the last price change of that same contract
(Ellis-Michaely-O'Hara 2000: the quote rule is ~89% right at the bid/ask, ~60% in between, where
the tick rule does better). Spread legs stay unsigned: a leg's price belongs to the package.

Reads the tape the tas_capture daemon already banks in tas_prints (quote_loc tagged at capture,
migration 0052) — zero extra vendor calls. Writes data/sides_YYYYMMDD.json, one row per
(strike, call/put, minute) that the cockpit merges into its minute log:

    [strike, "C"|"P", minute, quote_buy, quote_sell, tick_buy, tick_sell, unsigned,
     buy_premium, sell_premium]

minute = minutes after midnight ET rounded UP (a 09:30:15 print lands in the 09:31 bucket: the
minute log's 09:31 row holds the volume traded since the 09:30 pull).

Run (NAS: `run_cvxmap.sh start` adds it as cvxmap-sides; it exits after 16:25 ET):
    python scripts/cvxmap/sides_feed.py                          # live loop, every 60 s
    python scripts/cvxmap/sides_feed.py --date 20261105 --once   # rebuild one day and exit
"""
from __future__ import annotations

import argparse
import json
import os
import time
from collections.abc import Iterable
from datetime import date, datetime
from typing import Any

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.environ.get("DASH_DIR") or os.path.join(HERE, "data")
ROOTS = ("SPX", "SPXW")
ENV_CANDIDATES = ["/app/.env", os.path.join(HERE, "..", "..", ".env"), os.path.join(HERE, ".env")]
STOP_AT = (16, 25)
COLS = ["k", "cp", "t", "qb", "qs", "tb", "ts", "u", "buy_prem", "sell_prem"]

Row = dict[str, Any]


# ---------- pure logic (tests/cvxmap/test_sides_feed.py) ----------
def classify(prints: Iterable[Row]) -> list[tuple[int, str]]:
    """Prints of ONE contract in time order (price, quote_loc, spread_leg).

    Returns (side, how) per print: side +1 buy / -1 sell / 0 unsigned; how is 'quote', 'tick',
    'leg' or 'none'. The tick state runs over every print (legs too): each is a real price."""
    out: list[tuple[int, str]] = []
    last_px: float | None = None
    last_dir = 0
    for p in prints:
        px = p.get("price")
        tick = 0
        if px is not None and last_px is not None:
            if px > last_px + 1e-9:
                last_dir = 1
            elif px < last_px - 1e-9:
                last_dir = -1
            tick = last_dir            # a zero tick keeps the previous direction
        loc = p.get("quote_loc")
        if p.get("spread_leg"):
            out.append((0, "leg"))
        elif loc == "at_ask":
            out.append((1, "quote"))
        elif loc == "at_bid":
            out.append((-1, "quote"))
        elif tick:
            out.append((tick, "tick"))
        else:
            out.append((0, "none"))
        if px is not None:
            last_px = px
    return out


def minute_up(ts: datetime) -> int:
    """Minutes after midnight, rounded up (an exact minute stays)."""
    return ts.hour * 60 + ts.minute + (1 if (ts.second or ts.microsecond) else 0)


Bucket = dict[tuple[float, str, int], list[float]]


def aggregate(rows: Iterable[Row]) -> tuple[Bucket, dict[str, int]]:
    """Rows: symbol, ts, strike, cp, price, size, notional, quote_loc, spread_leg, side (vendor).

    Returns ({(strike, cp, minute): [qb, qs, tb, ts, u, buy$, sell$]}, stats)."""
    by_sym: dict[str, list[Row]] = {}
    for r in rows:
        by_sym.setdefault(r["symbol"], []).append(r)
    agg: Bucket = {}
    st = {"prints": 0, "quote": 0, "tick": 0, "leg": 0, "none": 0,
          "quote_vendor_n": 0, "quote_vendor_agree": 0, "tick_vendor_n": 0, "tick_vendor_agree": 0}
    for ps in by_sym.values():
        ps.sort(key=lambda r: (r["ts"], r.get("id") or 0))
        for r, (side, how) in zip(ps, classify(ps), strict=True):
            n = int(r.get("size") or 0)
            if n <= 0 or r.get("strike") is None or r.get("cp") not in ("C", "P"):
                continue
            key = (float(r["strike"]), r["cp"], minute_up(r["ts"]))
            a = agg.setdefault(key, [0, 0, 0, 0, 0, 0.0, 0.0])
            prem = float(r.get("notional") or float(r.get("price") or 0) * n * 100)
            st["prints"] += 1
            st[how] += 1
            if side == 0:
                a[4] += n
                continue
            a[(0 if side > 0 else 1) + (2 if how == "tick" else 0)] += n
            a[5 if side > 0 else 6] += prem
            vendor = {"buy": 1, "sell": -1}.get(str(r.get("side") or "").lower())
            if vendor is not None:
                st[how + "_vendor_n"] += 1
                st[how + "_vendor_agree"] += int(vendor == side)
    return agg, st


def payload(day: str, agg: Bucket, st: dict[str, int],
            floor: float | None = None) -> dict[str, Any]:
    order = sorted(agg.items(), key=lambda x: (x[0][2], x[0][0], x[0][1]))
    rows = [[k, cp, t, *(int(x) for x in v[:5]), round(v[5]), round(v[6])]
            for (k, cp, t), v in order]
    return {"day": day, "asof": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"), "floor": floor,
            "cols": COLS, "stats": st, "rows": rows}


# ---------- I/O ----------
def get_engine() -> Any:   # noqa: ANN401 - sqlalchemy Engine, imported lazily
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        for p in ENV_CANDIDATES:
            if not os.path.exists(p):
                continue
            with open(p, encoding="utf-8", errors="ignore") as fh:
                for line in fh:
                    s = line.strip()
                    if s.startswith("DATABASE_URL="):
                        db_url = s.split("=", 1)[1].strip().strip('"').strip("'")
                        break
                    if s.startswith("NAS_DATABASE_URL=") and not db_url:
                        db_url = s.split("=", 1)[1].strip().strip('"').strip("'")
            if db_url:
                break
    if not db_url:
        raise SystemExit("no DATABASE_URL found in env or .env files")
    from sqlalchemy import create_engine
    return create_engine(db_url, pool_pre_ping=True)


def fetch(engine: Any, day: date) -> list[Row]:  # noqa: ANN401
    from sqlalchemy import bindparam, text
    q = text(
        "select id, symbol, ts, strike, cp, price, size, notional, quote_loc, spread_leg, side "
        "from tas_prints where trade_date = :d and expiry = :d and root in :roots "
        "order by symbol, ts, id"
    ).bindparams(bindparam("roots", expanding=True))
    with engine.connect() as c:
        return [dict(r._mapping) for r in c.execute(q, {"d": day, "roots": list(ROOTS)})]


def write(day: date, obj: dict[str, Any]) -> str:
    os.makedirs(DATA, exist_ok=True)
    fp = os.path.join(DATA, f"sides_{day:%Y%m%d}.json")
    tmp = fp + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, separators=(",", ":"))
    os.replace(tmp, fp)
    return fp


def log(msg: str) -> None:
    line = f"{datetime.now():%H:%M:%S} {msg}"
    print(line, flush=True)
    try:
        name = f"sides_{date.today():%Y%m%d}.log"
        with open(os.path.join(DATA, name), "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def build(engine: Any, day: date) -> tuple[str, dict[str, int]]:  # noqa: ANN401
    agg, st = aggregate(fetch(engine, day))
    try:
        floor: float | None = float(os.environ.get("TAS_SPX_TAPE_MIN_PREMIUM", "5000"))
    except ValueError:
        floor = None
    return write(day, payload(day.isoformat(), agg, st, floor)), st


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="YYYYMMDD (default today)")
    ap.add_argument("--once", action="store_true")
    a = ap.parse_args()
    day = datetime.strptime(a.date, "%Y%m%d").date() if a.date else date.today()
    engine = get_engine()
    if a.once:
        fp, st = build(engine, day)
        log(f"built {fp}: {json.dumps(st)}")
        return
    while True:
        now = datetime.now()
        if (now.hour, now.minute) >= STOP_AT:
            log("done for the day")
            return
        if (now.hour, now.minute) >= (9, 30):
            t0 = time.time()
            try:
                _, st = build(engine, now.date())
                log(f"prints {st['prints']} quote {st['quote']} tick {st['tick']} leg {st['leg']} "
                    f"none {st['none']} ({time.time() - t0:.1f}s)")
            except Exception as exc:  # keep looping; the cockpit falls back to unsigned
                log(f"build failed: {str(exc)[:200]}")
        time.sleep(max(5.0, 60.0 - (time.time() % 60)))


if __name__ == "__main__":
    main()
