"""Stage analysis per stock per day (2026-11-22) - the "Candle Stage Analysis" rules (ChartsLector TV-Indicators,
after @TradeDudeNYC / @SugarTrader77) on daily bars, vectorised over a date x symbol panel (numpy only).

Inputs per day: EMA5/10/20, SMA50, Wilder ATR14, the 20-day high / low before today.
    xATR  = ((close - SMA50) / SMA50) / (ATR / close)      ("ATR-percent normalized", the script's default)
    up    = close >= EMA10 >= EMA20 >= SMA50                down = close < EMA20 and EMA10 < EMA20 < SMA50
Order of the rules (first match wins), the script's own priority:
    2D exhausted  up and xATR >= 11        2C extended   up and 7 <= xATR < 11     4C extended down  down and xATR < -7
    3A fade       close >= SMA50, EMA10 >= EMA20, and (close < EMA10 or EMA10 falling)
    3B fade conf. close >= SMA50 and EMA10 < EMA20 (not 4C)
    2B breakout   up and close >= the prior 20-day high     2A trend  up
    4B breakdown  down and close < the prior 20-day low     4A trend  down
    1A basing     |close - SMA50| < 1 ATR and the three averages within 1.5 ATR, or close between EMA20 and SMA50
                  with EMA20 above EMA10 (basing under the 50-day)
    1B mean rev.  neither trend, close > EMA20 and EMA10 >= EMA20
    NA            anything else
Main stage = the number (1-4); 0 = not enough history / undefined. Descriptor only (CLAUDE.md rule 4).
"""

from __future__ import annotations

import numpy as np

CODES = ["", "1A", "1B", "2A", "2B", "2C", "2D", "3A", "3B", "4A", "4B", "4C", "NA"]
NAMES = {
    "1A": "Basing", "1B": "Mean reversion", "2A": "Uptrend", "2B": "Breakout", "2C": "Extended",
    "2D": "Exhausted", "3A": "Fading", "3B": "Fade confirmed", "4A": "Downtrend", "4B": "Breakdown",
    "4C": "Extended down", "NA": "Undefined",
}
MAIN = np.array([0, 1, 1, 2, 2, 2, 2, 3, 3, 4, 4, 4, 0], dtype=np.int8)   # code index -> main stage


def ema(x: np.ndarray, n: int) -> np.ndarray:
    """EMA down axis 0, restarting after gaps (NaN in -> NaN out); seeded with the first value like Pine's ta.ema."""
    a = 2.0 / (n + 1)
    out = np.full_like(x, np.nan, dtype=float)
    prev = np.full(x.shape[1:], np.nan)
    for t in range(x.shape[0]):
        v = x[t]
        ok = ~np.isnan(v)
        prev = np.where(ok, np.where(np.isnan(prev), v, a * v + (1 - a) * prev), np.nan)
        out[t] = prev
    return out


def rma(x: np.ndarray, n: int) -> np.ndarray:
    """Wilder moving average (ATR) down axis 0, seeded with the first n-value mean; NaN breaks the run."""
    out = np.full_like(x, np.nan, dtype=float)
    prev = np.full(x.shape[1:], np.nan)
    run = np.zeros(x.shape[1:], dtype=int)
    acc = np.zeros(x.shape[1:])
    for t in range(x.shape[0]):
        v = x[t]
        ok = ~np.isnan(v)
        run = np.where(ok, run + 1, 0)
        acc = np.where(ok, np.where(run == 1, v, acc + np.nan_to_num(v)), 0.0)
        seed = ok & (run == n)
        cont = ok & (run > n)
        prev = np.where(seed, acc / n, np.where(cont, (prev * (n - 1) + v) / n, np.where(ok, prev, np.nan)))
        prev = np.where(run < n, np.nan, prev)
        out[t] = prev
    return out


def sma(x: np.ndarray, n: int) -> np.ndarray:
    """Simple average of the last n values down axis 0; NaN unless all n are present."""
    c = np.cumsum(np.nan_to_num(x), axis=0)
    k = np.cumsum(~np.isnan(x), axis=0)
    out = np.full_like(x, np.nan, dtype=float)
    out[n - 1] = c[n - 1] / n
    out[n:] = (c[n:] - c[:-n]) / n
    cnt = k.copy()
    cnt[n:] = k[n:] - k[:-n]
    out[:n - 1] = np.nan
    out[cnt < n] = np.nan
    return out


def _roll(x: np.ndarray, n: int, fn) -> np.ndarray:
    """fn (np.nanmax / np.nanmin) over the n days BEFORE each day (Pine's ta.highest(high, n)[1])."""
    out = np.full_like(x, np.nan, dtype=float)
    for t in range(n, x.shape[0]):
        w = x[t - n:t]
        good = (~np.isnan(w)).sum(axis=0) == n
        with np.errstate(all="ignore"):
            out[t] = np.where(good, fn(w, axis=0), np.nan)
    return out


def classify(close: np.ndarray, high: np.ndarray, low: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """(code index array int8 [0..12], xATR float) for date x symbol arrays. 0 = no stage yet (too little history)."""
    e10, e20 = ema(close, 10), ema(close, 20)
    s50 = sma(close, 50)
    prev_c = np.vstack([np.full((1, close.shape[1]), np.nan), close[:-1]])
    tr = np.fmax(high - low, np.fmax(np.abs(high - prev_c), np.abs(low - prev_c)))
    tr[0] = (high - low)[0]
    atr = rma(tr, 14)
    with np.errstate(all="ignore"):
        xatr = ((close - s50) / s50) / (atr / close)
    hh, ll = _roll(high, 20, np.nanmax), _roll(low, 20, np.nanmin)
    e10_prev = np.vstack([np.full((1, close.shape[1]), np.nan), e10[:-1]])
    e10_up = e10 >= e10_prev
    spread = np.fmax(np.fmax(e10, e20), s50) - np.fmin(np.fmin(e10, e20), s50)
    with np.errstate(all="ignore"):
        up = (close >= e10) & (e10 >= e20) & (e20 >= s50)
        down = (close < e20) & (e10 < e20) & (e20 < s50)
        breakout = close >= hh
        breakdown = close < ll
        basing = (np.abs(close - s50) < atr) & (spread < 1.5 * atr)
        basing_b = (close > e20) & (close < s50) & (e20 > e10) & (e20 < s50)
        ext_bull = up & (xatr >= 7) & (xatr < 11)
        exh_bull = up & (xatr >= 11)
        ext_bear = down & (xatr < -7)
        fade_a = (close >= s50) & (e10 >= e20) & ((close < e10) | ~e10_up)
        fade_b = ~ext_bear & (close >= s50) & (e10 < e20)
        mean_rev = ~up & ~down & ~(basing | basing_b) & (close > e20) & (e10 >= e20)
    conds = [
        (exh_bull, 6), (ext_bull, 5), (ext_bear, 11), (fade_a, 7), (fade_b, 8),
        (up & breakout, 4), (up, 3), (down & breakdown, 10), (down, 9), (basing | basing_b, 1), (mean_rev, 2),
    ]
    code = np.full(close.shape, 12, dtype=np.int8)
    done = np.zeros(close.shape, dtype=bool)
    for m, k in conds:
        m = m & ~done
        code[m] = k
        done |= m
    ready = ~np.isnan(s50) & ~np.isnan(atr) & ~np.isnan(e20) & ~np.isnan(close)
    code[~ready] = 0
    xatr[~ready] = np.nan
    return code, xatr


def main_stage(code: np.ndarray) -> np.ndarray:
    return MAIN[code.astype(int)]
