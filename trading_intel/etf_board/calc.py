"""ETF board maths (pure functions, unit-tested in tests/etf_board/test_calc.py).

Definitions (spec ``claude/etf-board-spec-20261119.md``; * = our definition, the ToS formula is unknown):
  chg_1d    last / prior close - 1                      chg_5d   close / close 5 sessions ago - 1
  buy/sell* per 1-min bar: buy share = (close - low) / (high - low); a flat bar = 1 if up vs the previous
            close, 0 if down, 0.5 if unchanged; $ = volume x (high + low + close) / 3; summed for the session
  bsr       buy >= sell: buy / sell, else -(sell / buy); None if a side is 0 (decoded exactly from ToS)
  strength* 5-day return / (20-day sigma of daily returns x sqrt 5)
  price_vol 20-day sigma of daily % returns           stretch* (last - 20-day SMA) / ATR(14)
  mo1* 3-day % change   mo2* mo1 today - mo1 yesterday   mo3* 2 above SMA20 & SMA50, 0 below both, 1 mixed
  relvol    today's volume / 20-day average volume    corr10 / corr20  daily returns vs QQQ
  building* least-squares slope of the cumulative (buy - sell) $ over the last 10 sessions / 20-day avg $ volume
"""

from __future__ import annotations

import math
from collections.abc import Sequence


def _ret(c: Sequence[float]) -> list[float]:
    return [c[i] / c[i - 1] - 1 for i in range(1, len(c)) if c[i - 1]]


def stdev(x: Sequence[float]) -> float | None:
    if len(x) < 2:
        return None
    m = sum(x) / len(x)
    return math.sqrt(sum((v - m) ** 2 for v in x) / (len(x) - 1))


def corr(a: Sequence[float], b: Sequence[float]) -> float | None:
    n = min(len(a), len(b))
    if n < 3:
        return None
    a, b = a[-n:], b[-n:]
    ma, mb = sum(a) / n, sum(b) / n
    sa = math.sqrt(sum((x - ma) ** 2 for x in a))
    sb = math.sqrt(sum((x - mb) ** 2 for x in b))
    if not sa or not sb:
        return None
    return sum((x - ma) * (y - mb) for x, y in zip(a, b, strict=True)) / (sa * sb)


def slope(y: Sequence[float]) -> float | None:
    n = len(y)
    if n < 3:
        return None
    mx, my = (n - 1) / 2, sum(y) / n
    den = sum((i - mx) ** 2 for i in range(n))
    return sum((i - mx) * (v - my) for i, v in enumerate(y)) / den if den else None


def bsr(buy: float | None, sell: float | None) -> float | None:
    if not buy or not sell:
        return None
    return buy / sell if buy >= sell else -(sell / buy)


def buy_sell(minutes: Sequence[tuple], prev_close: float | None) -> tuple[float, float]:
    """minutes: [(open, high, low, close, volume)] oldest first -> (buy $, sell $)."""
    b = s = 0.0
    pc = prev_close
    for _o, h, lo, c, v in minutes:
        if c is None or not v:
            pc = c if c is not None else pc
            continue
        h = h if h is not None else c
        lo = lo if lo is not None else c
        if h > lo:
            share = (c - lo) / (h - lo)
        elif pc is None or c == pc:
            share = 0.5
        else:
            share = 1.0 if c > pc else 0.0
        usd = v * (h + lo + c) / 3
        b += share * usd
        s += (1 - share) * usd
        pc = c
    return b, s


def atr(h: Sequence[float], lo: Sequence[float], c: Sequence[float], n: int = 14) -> float | None:
    tr = []
    for i in range(1, len(c)):
        tr.append(max(h[i] - lo[i], abs(h[i] - c[i - 1]), abs(lo[i] - c[i - 1])))
    return sum(tr[-n:]) / n if len(tr) >= n else None


def daily_stats(
    closes: Sequence[float], highs: Sequence[float], lows: Sequence[float], vols: Sequence[float]
) -> dict:
    """Daily-bar columns from oldest-first series that END with today's bar."""
    c = list(closes)
    out: dict = {}
    if len(c) >= 2:
        out["chg_1d"] = c[-1] / c[-2] - 1
    if len(c) >= 6:
        out["chg_5d"] = c[-1] / c[-6] - 1
    r = _ret(c)
    sd = stdev(r[-20:]) if len(r) >= 20 else None
    if sd:
        out["price_vol"] = sd * 100
        if "chg_5d" in out:
            out["strength"] = out["chg_5d"] / (sd * math.sqrt(5))
    if len(c) >= 20:
        sma20 = sum(c[-20:]) / 20
        a = atr(highs, lows, c)
        if a:
            out["stretch"] = (c[-1] - sma20) / a
        above50 = c[-1] > sum(c[-50:]) / 50 if len(c) >= 50 else None
        above20 = c[-1] > sma20
        out["mo3"] = (2 if above20 else 0) if above50 is None or above50 == above20 else 1
    if len(c) >= 5:
        m1, m1y = c[-1] / c[-4] - 1, c[-2] / c[-5] - 1
        out["mo1"] = m1 * 100
        out["mo2"] = (m1 - m1y) * 100
    v = [x or 0 for x in vols]
    if len(v) >= 21 and sum(v[-21:-1]):
        out["relvol"] = v[-1] / (sum(v[-21:-1]) / 20)
    return out


def corr_vs(ref_closes: dict, closes: dict, n: int) -> float | None:
    """closes: {date: close}; daily returns on common dates, last n."""
    ds = sorted(set(ref_closes) & set(closes))
    ra = _ret([ref_closes[d] for d in ds])
    rb = _ret([closes[d] for d in ds])
    return corr(ra[-n:], rb[-n:]) if len(ra) >= n else None


def building(net_hist: Sequence[float], avg_usd: float | None) -> float | None:
    """net_hist = daily (buy - sell) $ oldest first, ending today; slope of its running sum over 10 sessions."""
    if not avg_usd or len(net_hist) < 3:
        return None
    cum, run = [], 0.0
    for x in net_hist[-10:]:
        run += x
        cum.append(run)
    s = slope(cum)
    return None if s is None else s / avg_usd
