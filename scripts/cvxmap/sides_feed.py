#!/usr/bin/env python
"""Per-trade buy / sell sides for today's SPX 0DTE — the cockpit's "Side: Per trade" (2026-11-05).

Mithil's rule: trust a trade's side only when it is clearly AT the ask (buy) or AT the bid (sell)
against its OWN quote at print time; every other print (mid, inside the spread, outside, no quote)
is decided by the tick rule — the side of the last price change of that same contract
(Ellis-Michaely-O'Hara 2000: the quote rule is ~89% right at the bid/ask, ~60% in between, where
the tick rule does better). Spread legs stay unsigned: a leg's price belongs to the package.

PACKAGE SIGNING (2026-11-10, Mithil: "pair orders to guess the type of order"). A spread leg is still
never signed by the tick rule. Instead the legs of one package (same tas_prints.leg_group) are read
together, each against ITS OWN quote: at the ask / above mid = that leg was bought by whoever started
the trade, at the bid / below mid = sold (quote_loc "in_hi" / "in_lo", tagged at capture from 11/10).
  2 legs, same call/put (vertical, calendar): the legs must point opposite ways. One clear leg signs
    the other as its opposite; both pointing the same way = a conflict, left unsigned.
  2 legs, call + put (risk reversal, strangle): each leg only by its own quote; no inference.
  3+ legs: each leg by its own quote, unless every clear leg of an all-call or all-put package points
    the same way (a fly or condor can't be all bought) = conflict, left unsigned.
Package-signed contracts get their own columns (pkg_buy / pkg_sell) so the cockpit, which reads the
first five numbers, keeps treating them as unsigned until it is switched to read these.

Reads the tape the tas_capture daemon already banks in tas_prints (quote_loc tagged at capture,
migration 0052) — zero extra vendor calls. Writes data/sides_YYYYMMDD.json, one row per
(strike, call/put, minute) that the cockpit merges into its minute log:

    [strike, "C"|"P", minute, quote_buy, quote_sell, tick_buy, tick_sell, unsigned,
     buy_premium, sell_premium, pkg_buy, pkg_sell, pkg_buy_premium, pkg_sell_premium]

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
from datetime import date, datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.environ.get("DASH_DIR") or os.path.join(HERE, "data")
ROOTS = ("SPX", "SPXW")
ENV_CANDIDATES = ["/app/.env", os.path.join(HERE, "..", "..", ".env"), os.path.join(HERE, ".env")]
STOP_AT = (16, 25)
COLS = ["k", "cp", "t", "qb", "qs", "tb", "ts", "u", "buy_prem", "sell_prem",
        "pkb", "pks", "pkb_prem", "pks_prem"]
_LEG_VOTE = {"at_ask": 1, "in_hi": 1, "at_bid": -1, "in_lo": -1}

Row = dict[str, Any]
ET = ZoneInfo("America/New_York")


def now_et() -> datetime:
    """Wall clock in New York, naive. The NAS containers run on UTC (11/05: the loop read 19:00
    as past 16:25 and quit at 14:00 ET)."""
    return datetime.now(ET).replace(tzinfo=None)


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


def sign_packages(rows: Iterable[Row]) -> dict[Any, int]:
    """Spread legs -> {row id: +1 bought / -1 sold} for the legs a package read can sign (see the module
    docstring). Rows need id, leg_group, cp, quote_loc; rows without a leg_group are ignored."""
    groups: dict[str, list[Row]] = {}
    for r in rows:
        if r.get("leg_group"):
            groups.setdefault(str(r["leg_group"]), []).append(r)
    out: dict[Any, int] = {}
    for legs in groups.values():
        if len(legs) < 2:
            continue
        votes = [_LEG_VOTE.get(str(r.get("quote_loc") or ""), 0) for r in legs]
        same_cp = len({r.get("cp") for r in legs}) == 1
        clear = [v for v in votes if v]
        if not clear:
            continue
        if len(legs) == 2 and same_cp:
            a, b = votes
            if a and b and a == b:
                continue                      # both legs "bought" (or sold): not a real vertical read
            a = a or -b
            b = b or -a
            out[legs[0].get("id")] = a
            out[legs[1].get("id")] = b
            continue
        if same_cp and len(set(clear)) == 1 and len(clear) >= 2:
            continue                          # all-call / all-put package, every clear leg one way
        for r, v in zip(legs, votes, strict=True):
            if v:
                out[r.get("id")] = v
    return out


def minute_up(ts: datetime) -> int:
    """Minutes after midnight, rounded up (an exact minute stays)."""
    return ts.hour * 60 + ts.minute + (1 if (ts.second or ts.microsecond) else 0)


def tape_clock(rows: list[Row], now: datetime | None = None) -> str:
    """Which clock the naive tas_prints.ts values are on: 'et' or 'utc'.

    SPX 0DTE prints 09:30-16:15 ET = 13:30-21:15 UTC (14:30-21:15 in summer). Any print after 16:30
    -> UTC; any before 13:00 -> ET; otherwise (tape only 13:00-16:30) pick the clock whose latest
    print is closest to now (live loop), else ET."""
    naive = [r["ts"] for r in rows if r.get("ts") is not None and r["ts"].tzinfo is None]
    if not naive:
        return "et"
    mins = [t.hour * 60 + t.minute for t in naive]
    if max(mins) > 16 * 60 + 30:
        return "utc"
    if min(mins) < 13 * 60:
        return "et"
    if now is not None:
        last = max(naive)
        n_utc = now.replace(tzinfo=ET).astimezone(timezone.utc).replace(tzinfo=None)
        return "utc" if abs(last - n_utc) < abs(last - now) else "et"
    return "et"


def to_et(rows: list[Row], clock: str) -> list[Row]:
    """ts -> naive New York time (aware values converted; naive UTC shifted)."""
    for r in rows:
        t = r.get("ts")
        if t is None:
            continue
        if t.tzinfo is not None:
            r["ts"] = t.astimezone(ET).replace(tzinfo=None)
        elif clock == "utc":
            r["ts"] = t.replace(tzinfo=timezone.utc).astimezone(ET).replace(tzinfo=None)
    return rows


Bucket = dict[tuple[float, str, int], list[float]]


def aggregate(rows: Iterable[Row]) -> tuple[Bucket, dict[str, int]]:
    """Rows: symbol, ts, strike, cp, price, size, notional, quote_loc, spread_leg, side (vendor).

    Returns ({(strike, cp, minute): [qb, qs, tb, ts, u, buy$, sell$]}, stats)."""
    rows = list(rows)
    by_sym: dict[str, list[Row]] = {}
    for r in rows:
        by_sym.setdefault(r["symbol"], []).append(r)
    agg: Bucket = {}
    pkg = sign_packages(r for r in rows if r.get("spread_leg") or r.get("leg_group"))
    st = {"prints": 0, "quote": 0, "tick": 0, "leg": 0, "none": 0, "pkg": 0, "pkg_contracts": 0,
          "leg_contracts": 0,
          "quote_vendor_n": 0, "quote_vendor_agree": 0, "tick_vendor_n": 0, "tick_vendor_agree": 0}
    for ps in by_sym.values():
        ps.sort(key=lambda r: (r["ts"], r.get("id") or 0))
        for r, (side, how) in zip(ps, classify(ps), strict=True):
            n = int(r.get("size") or 0)
            if n <= 0 or r.get("strike") is None or r.get("cp") not in ("C", "P"):
                continue
            key = (float(r["strike"]), r["cp"], minute_up(r["ts"]))
            a = agg.setdefault(key, [0, 0, 0, 0, 0, 0.0, 0.0, 0, 0, 0.0, 0.0])
            prem = float(r.get("notional") or float(r.get("price") or 0) * n * 100)
            st["prints"] += 1
            if how == "leg":
                st["leg_contracts"] += n
                ps = pkg.get(r.get("id"), 0)
                if ps:
                    st["pkg"] += 1
                    st["pkg_contracts"] += n
                    a[7 if ps > 0 else 8] += n
                    a[9 if ps > 0 else 10] += prem
                    continue
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


def payload(day: str, agg: Bucket, st: dict[str, Any],
            floor: float | None = None) -> dict[str, Any]:
    order = sorted(agg.items(), key=lambda x: (x[0][2], x[0][0], x[0][1]))
    rows = [[k, cp, t, *(int(x) for x in v[:5]), round(v[5]), round(v[6]),
             int(v[7]), int(v[8]), round(v[9]), round(v[10])]
            for (k, cp, t), v in order]
    return {"day": day, "asof": now_et().strftime("%Y-%m-%dT%H:%M:%S"), "floor": floor,
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
        "select id, symbol, ts, strike, cp, price, size, notional, quote_loc, spread_leg, side, leg_group "
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
    line = f"{now_et():%H:%M:%S} {msg}"
    print(line, flush=True)
    try:
        name = f"sides_{now_et():%Y%m%d}.log"
        with open(os.path.join(DATA, name), "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def build(engine: Any, day: date,  # noqa: ANN401
          now: datetime | None = None) -> tuple[str, dict[str, Any]]:
    rows = fetch(engine, day)
    clock = tape_clock(rows, now)
    agg, st = aggregate(to_et(rows, clock))
    st["tape_clock"] = clock
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
    day = datetime.strptime(a.date, "%Y%m%d").date() if a.date else now_et().date()
    engine = get_engine()
    if a.once:
        fp, st = build(engine, day)
        log(f"built {fp}: {json.dumps(st)}")
        return
    while True:
        now = now_et()
        if (now.hour, now.minute) >= STOP_AT:
            log("done for the day")
            return
        if (now.hour, now.minute) >= (9, 30):
            t0 = time.time()
            try:
                _, st = build(engine, now.date(), now)
                log(f"prints {st['prints']} quote {st['quote']} tick {st['tick']} leg {st['leg']} "
                    f"none {st['none']} clock {st['tape_clock']} ({time.time() - t0:.1f}s)")
            except Exception as exc:  # keep looping; the cockpit falls back to unsigned
                log(f"build failed: {str(exc)[:200]}")
        time.sleep(max(5.0, 60.0 - (time.time() % 60)))


if __name__ == "__main__":
    main()
