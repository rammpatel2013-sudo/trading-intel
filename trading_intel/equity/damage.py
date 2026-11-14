"""How deep is the damage under the index, and is it healing? Pure numpy (E2).

Idea from thetrading.tools' Hidden Bear Index / Market Repair Flow, computed on our own S&P 1500
bars (S&P 500 part point-in-time; S&P 400/600 = today's holdings, so their history is
survivorship-flattered - fine for today's reading, not for backtests).

* drawdown       - each stock's close vs its own 252-session closing high (0 = at the high)
* buckets        - healthy (<10% off), bruised (10-20%), damaged (20-40%), broken (>40%)
* repair flow    - over the last 20 sessions: % of stocks that moved to a healthier bucket
                   minus % that moved to a worse one (+ = healing, - = damage spreading)
* hidden-bear gap - equal-weight S&P (RSP) drawdown minus SPY's: below 0 = the average stock is
                   worse off than the index headline
"""

from __future__ import annotations

import numpy as np

from trading_intel.equity.panel import Panel

BUCKET_EDGES = (0.10, 0.20, 0.40)
BUCKET_NAMES = ("healthy", "bruised", "damaged", "broken")


def rolling_max(x: np.ndarray, n: int) -> np.ndarray:
    """Trailing n-row max along axis 0 (NaN-aware; NaN when the window has no data)."""
    T = x.shape[0]
    out = np.full(x.shape, np.nan)
    filled = np.where(np.isnan(x), -np.inf, x)
    for t in range(T):
        w = filled[max(0, t - n + 1) : t + 1]
        m = w.max(axis=0)
        out[t] = np.where(np.isinf(m), np.nan, m)
    return out


def drawdowns(close: np.ndarray, *, n: int = 252) -> np.ndarray:
    """Fraction below the trailing n-session closing high (0 at a high, -0.25 = 25% below)."""
    hi = rolling_max(close, n)
    with np.errstate(invalid="ignore", divide="ignore"):
        return close / hi - 1.0


def buckets(dd: np.ndarray) -> np.ndarray:
    """0 healthy .. 3 broken; -1 where unknown."""
    off = -dd
    b = np.full(dd.shape, -1, dtype=int)
    ok = ~np.isnan(off)
    b[ok] = np.digitize(off[ok], BUCKET_EDGES)
    return b


def damage_stats(p: Panel, *, lookback: int = 20) -> dict[str, np.ndarray]:
    """Per day: median drawdown, % >10% / >20% down, repair flow, bucket shares, % above MAs."""
    dd = drawdowns(p.close)
    mem = p.member
    T = len(p.dates)
    b = buckets(dd)
    med = np.full(T, np.nan)
    d10 = np.full(T, np.nan)
    d20 = np.full(T, np.nan)
    flow = np.full(T, np.nan)
    shares = np.full((T, 4), np.nan)
    n = np.zeros(T, dtype=int)
    for t in range(T):
        ok = mem[t] & ~np.isnan(dd[t])
        n[t] = int(ok.sum())
        if n[t] < 50:
            continue
        x = dd[t, ok]
        med[t] = float(np.median(x))
        d10[t] = float((x <= -0.10).mean() * 100)
        d20[t] = float((x <= -0.20).mean() * 100)
        bb = b[t, ok]
        shares[t] = [float((bb == k).mean() * 100) for k in range(4)]
        if t >= lookback:
            both = ok & mem[t - lookback] & (b[t - lookback] >= 0) & (b[t] >= 0)
            if both.sum() >= 50:
                now, then = b[t, both], b[t - lookback, both]
                flow[t] = float(((now < then).mean() - (now > then).mean()) * 100)
    return {
        "median_drawdown": med * 100,
        "pct_down_10": d10,
        "pct_down_20": d20,
        "repair_flow": flow,
        "bucket_shares": shares,
        "n": n,
        "drawdown": dd,
    }


def pct_above_ma(p: Panel, n: int) -> np.ndarray:
    """% of the day's members closing above their n-day simple average."""
    c = p.close
    valid = ~np.isnan(c)
    cs = np.vstack([np.zeros((1, c.shape[1])), np.cumsum(np.where(valid, c, 0.0), axis=0)])
    cc = np.vstack([np.zeros((1, c.shape[1])), np.cumsum(valid, axis=0)])
    T = c.shape[0]
    out = np.full(T, np.nan)
    for t in range(n - 1, T):
        s = cs[t + 1] - cs[t + 1 - n]
        k = cc[t + 1] - cc[t + 1 - n]
        ok = p.member[t] & (k == n)
        if ok.sum() < 50:
            continue
        ma = s[ok] / n
        out[t] = float((c[t, ok] > ma).mean() * 100)
    return out


def series_drawdown(close: np.ndarray, *, n: int = 252) -> np.ndarray:
    """1-D drawdown (fraction) for one series, e.g. SPY or RSP."""
    return drawdowns(close.reshape(-1, 1), n=n)[:, 0]


def hidden_bear_gap(p: Panel, *, equal: str = "RSP", cap: str = "SPY") -> np.ndarray:
    """RSP drawdown minus SPY drawdown, in % points (NaN if either is missing)."""
    i, j = p.col(equal), p.col(cap)
    if i is None or j is None:
        return np.full(len(p.dates), np.nan)
    return (series_drawdown(p.close[:, i]) - series_drawdown(p.close[:, j])) * 100
