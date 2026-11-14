"""Candidate lists for swing / long-term momentum (E4). Pure numpy.

Three lists, from the S&P 1500 names that trade enough (price >= $10, 20-day average >= $20M/day,
a year of bars):

* leader   - strongest OWN move (factor model residual: 60% last 6 months + 40% 12-1 months, ranked
             cross-sectionally), and the trend filter passes. Own move = strength the market,
             sector, momentum and beta tides do not explain.
* coiled   - the coiled-spring box Mithil hunts (memory: coiled-spring-setup): price stuck in a
             range for 40-180 sessions, range no wider than 20%, still above the 200-day line.
             The second condition (90-180 DTE calls gaining value while price sits still) is checked
             on the shortlist from ``equity_call_iv`` as that history builds.
* breakout - was in such a box up to yesterday and closed above the box top today on >= 1.5x its
             50-day average volume.

Trend filter (Minervini-style): close > 50-day > 150-day > 200-day, 200-day rising over a month,
close >= 30% above its 52-week low and within 25% of its 52-week high.
"""

from __future__ import annotations

import warnings
from collections.abc import Sequence

import numpy as np

from trading_intel.equity.factor_model import ModelRun, own_move
from trading_intel.equity.panel import Panel

BOX_LENGTHS = (40, 60, 90, 120, 150, 180)
BOX_MAX_WIDTH = 0.20
MIN_PRICE = 10.0
MIN_DOLLAR_VOL = 20e6


def _sma(c: np.ndarray, n: int, end: int) -> float | None:
    if end + 1 < n:
        return None
    w = c[end + 1 - n : end + 1]
    return float(w.mean()) if not np.isnan(w).any() else None


def trend_ok(c: np.ndarray, end: int | None = None) -> tuple[bool, dict]:
    end = len(c) - 1 if end is None else end
    s50, s150, s200 = _sma(c, 50, end), _sma(c, 150, end), _sma(c, 200, end)
    s200_prev = _sma(c, 200, end - 21) if end >= 221 else None
    win = c[max(0, end - 251) : end + 1]
    win = win[~np.isnan(win)]
    if None in (s50, s150, s200, s200_prev) or len(win) < 200:
        return False, {}
    px, hi, lo = float(c[end]), float(win.max()), float(win.min())
    ok = px > s50 > s150 > s200 and s200 > s200_prev and px >= 1.30 * lo and px >= 0.75 * hi
    return bool(ok), {"sma50": s50, "sma150": s150, "sma200": s200, "high52": hi, "low52": lo}


def box(high: np.ndarray, low: np.ndarray, end: int) -> tuple[int, float, float] | None:
    """Longest window (40-180 sessions, ending at ``end``) whose high-low range is <= 20%."""
    best = None
    for n in BOX_LENGTHS:
        if end + 1 < n:
            break
        h, lo = high[end + 1 - n : end + 1], low[end + 1 - n : end + 1]
        if np.isnan(h).any() or np.isnan(lo).any():
            break
        hi, mn = float(h.max()), float(lo.min())
        width = hi / mn - 1.0 if mn > 0 else np.inf
        if width <= BOX_MAX_WIDTH:
            best = (n, width, hi)
        else:
            break
    return best


def _z(x: np.ndarray) -> np.ndarray:
    ok = ~np.isnan(x)
    out = np.full(x.shape, np.nan)
    if ok.sum() > 2 and np.nanstd(x) > 0:
        out[ok] = (x[ok] - np.nanmean(x)) / np.nanstd(x)
    return out


def eligible(p: Panel) -> np.ndarray:
    t = len(p.dates) - 1
    c, v = p.close, p.volume
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)  # names with no recent bars -> NaN
        dv = np.nanmean((c[-20:] * v[-20:]), axis=0) if t >= 19 else np.full(c.shape[1], np.nan)
    n_bars = (~np.isnan(c)).sum(axis=0)
    with np.errstate(invalid="ignore"):
        return p.member[t] & (c[t] >= MIN_PRICE) & (dv >= MIN_DOLLAR_VOL) & (n_bars >= 252)


def build(p: Panel, run: ModelRun, *, top: int = 25) -> list[dict]:
    t = len(p.dates) - 1
    ok = eligible(p)
    o6, o12 = own_move(run, 126), own_move(run, 231, skip=21)
    score = 0.6 * _z(np.where(ok, o6, np.nan)) + 0.4 * _z(np.where(ok, o12, np.nan))
    with np.errstate(invalid="ignore", divide="ignore"):
        tot12 = (
            p.close[t - 21] / p.close[t - 252] - 1.0
            if t >= 252
            else np.full(len(p.symbols), np.nan)
        )
    out: list[dict] = []
    leaders, coiled, breaks = [], [], []
    for i in np.flatnonzero(ok):
        c = p.close[:, i]
        tok, info = trend_ok(c)
        base = {
            "symbol": p.symbols[i],
            "sector": p.sector[i],
            "close": float(c[t]),
            "own_move_6m": _f(o6[i]),
            "own_move_12m": _f(o12[i]),
            "total_move_12m": _f(tot12[i]),
            "trend_ok": tok,
            "score": _f(score[i]),
        }
        det = {k: round(v, 2) for k, v in info.items()}
        if tok and not np.isnan(score[i]):
            leaders.append({**base, "details": det})
        bx_prev = box(p.high[:, i], p.low[:, i], t - 1)
        win = p.volume[max(0, t - 50) : t, i]
        vol50 = float(np.nanmean(win)) if np.isfinite(win).any() else None
        vr = float(p.volume[t, i] / vol50) if vol50 and vol50 > 0 else None
        s200 = info.get("sma200") or _sma(c, 200, t)
        if bx_prev and c[t] > bx_prev[2] and vr is not None and vr >= 1.5:
            breaks.append(
                {
                    **base,
                    "box_days": bx_prev[0],
                    "box_width": bx_prev[1],
                    "box_high": bx_prev[2],
                    "details": {**det, "vol_ratio": round(vr, 2)},
                }
            )
            continue
        bx = box(p.high[:, i], p.low[:, i], t)
        if bx and s200 and c[t] > s200:
            coiled.append(
                {
                    **base,
                    "box_days": bx[0],
                    "box_width": bx[1],
                    "box_high": bx[2],
                    "details": {**det, "pct_below_box_top": round((1 - c[t] / bx[2]) * 100, 2)},
                }
            )
    leaders.sort(key=lambda r: -(r["score"] or -9))
    coiled.sort(key=lambda r: (-r["box_days"], r["box_width"]))
    breaks.sort(key=lambda r: -(r["details"].get("vol_ratio") or 0))
    for name, rows in (("leader", leaders), ("coiled", coiled), ("breakout", breaks)):
        for k, r in enumerate(rows[:top], 1):
            out.append({"date": p.dates[t], "list_name": name, "rank": k, **r})
    return out


def _f(x: float) -> float | None:
    return None if x is None or (isinstance(x, float) and np.isnan(x)) else float(x)


def shortlist(rows: Sequence[dict], *, n_each: int = 15) -> list[str]:
    """Names for the option checks: top leaders, coiled springs and every breakout."""
    seen: dict[str, None] = {}
    for name in ("breakout", "leader", "coiled"):
        for r in [x for x in rows if x["list_name"] == name][:n_each]:
            seen.setdefault(r["symbol"], None)
    return list(seen)
