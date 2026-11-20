"""Compass (Macro view, 2026-11-20): pure maths.

Mithil 2026-11-20 02:29: "create something on main cockpit to use this data meaningfully. we also can collect
darkpool data and use visualization as in image i share above ... or develop scanner from this data" -> 04:17
"build macro view now". Plan: claude/compass-plan-20261120.md.

Everything here is a DESCRIPTOR (rule 4): what moved more than its options priced, where vol is rich or cheap,
where the auction is balanced or one-sided, where off-exchange short volume is unusual. Each scanner says in one
plain line what its hit means; none is a signal until scored (compass_daily keeps every day's hits for that).
"""

from __future__ import annotations

import math
from collections.abc import Sequence

import numpy as np
import pandas as pd

# ---------------------------------------------------------------- the cross-asset grid
GROUPS: dict[str, tuple[str, ...]] = {
    "Indices": ("SPY", "QQQ", "IWM", "DIA", "RSP", "MDY"),
    "Bonds": ("SHY", "IEF", "TLT", "TIP", "LQD", "HYG", "JNK", "EMB"),
    "Commodities": ("GLD", "SLV", "USO", "UNG", "DBC", "GDX", "COPX", "URA"),
    "World": ("EFA", "EEM", "INDA", "FXI", "EWJ", "EWZ", "EWW", "EWY", "EWT", "KWEB"),
    "Sectors": ("XLK", "XLF", "XLE", "XLV", "XLI", "XLY", "XLP", "XLU", "XLB", "XLRE", "XLC", "SMH", "IGV",
                "KRE", "XBI", "ITB"),
    "FX & crypto": ("UUP", "FXE", "FXY", "IBIT"),
    "Vol": ("VXX", "UVXY", "SVXY"),
}
GRID = tuple(s for g in GROUPS.values() for s in g)
GROUP_OF = {s: g for g, ss in GROUPS.items() for s in ss}

TRADING_DAYS = 252
MIN_RANK_OBS = 20          # a 1-year rank needs at least this many sessions of history


# ---------------------------------------------------------------- price / realized vol
def log_returns(close: Sequence[float]) -> np.ndarray:
    c = np.asarray(close, float)
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.diff(np.log(c))


def hv(close: Sequence[float], n: int) -> float | None:
    """Close-to-close realized vol over the last ``n`` returns, annualized (decimal)."""
    r = log_returns(close)[-n:]
    r = r[np.isfinite(r)]
    if len(r) < max(5, n // 2):
        return None
    return float(np.std(r, ddof=1) * math.sqrt(TRADING_DAYS))


def bollinger_pctb(close: Sequence[float], n: int = 20, k: float = 2.0) -> float | None:
    """%B: 0 = on the lower band, 1 = on the upper band, 0.5 = on the 20-day average."""
    c = np.asarray(close, float)[-n:]
    if len(c) < n or not np.isfinite(c).all():
        return None
    m, sd = c.mean(), c.std(ddof=0)
    if sd <= 0:
        return None
    return float((c[-1] - (m - k * sd)) / (2 * k * sd))


def pct_ret(close: Sequence[float], n: int) -> float | None:
    c = np.asarray(close, float)
    if len(c) <= n or not (c[-1 - n] > 0):
        return None
    return float(c[-1] / c[-1 - n] - 1.0)


def sigma_move(ret1: float | None, vol_prev: float | None) -> float | None:
    """Today's move in units of the one-day move the vol (yesterday's IV30, or HV20 when no IV) allowed for."""
    if ret1 is None or not vol_prev or vol_prev <= 0:
        return None
    return float(ret1 / (vol_prev / math.sqrt(TRADING_DAYS)))


def rank(history: Sequence[float], value: float | None) -> float | None:
    """Share of the history at or below ``value`` (0..1); None with fewer than MIN_RANK_OBS points."""
    h = np.asarray([x for x in history if x is not None and np.isfinite(x)], float)
    if value is None or len(h) < MIN_RANK_OBS:
        return None
    return float((h <= value).mean())


def clean_iv(s: pd.Series, jump: float = 0.5) -> pd.Series:
    """Drop single prints more than ``jump`` (50%) away from the rolling 5-day median (the Convex EOD ATM IV
    has one-day glitches, e.g. NVDA 0.18 on 11/17 between 0.51 and 0.63)."""
    s = s.astype(float)
    med = s.rolling(5, center=True, min_periods=3).median()
    bad = (s / med - 1.0).abs() > jump
    return s[~bad.fillna(False)]


# ---------------------------------------------------------------- dark pool (FINRA daily short volume)
def dark_stats(short: Sequence[float], total: Sequence[float], n: int = 20) -> tuple[float | None, float | None]:
    """(today's off-exchange short ratio, its z-score vs the prior ``n`` sessions). Off-exchange short volume is
    mostly market makers filling BUY orders, so a high ratio = more buying flow printed off-exchange."""
    s, t = np.asarray(short, float), np.asarray(total, float)
    ok = t > 0
    r = np.where(ok, s / np.where(ok, t, 1), np.nan)
    if len(r) == 0 or not np.isfinite(r[-1]):
        return None, None
    prior = r[:-1][-n:]
    prior = prior[np.isfinite(prior)]
    if len(prior) < 10 or prior.std(ddof=1) <= 1e-9:
        return float(r[-1]), None
    return float(r[-1]), float((r[-1] - prior.mean()) / prior.std(ddof=1))


# ---------------------------------------------------------------- auction (one session of 1-minute bars)
def value_area(tp: np.ndarray, vol: np.ndarray, share: float = 0.70, bins: int = 60) -> dict | None:
    if len(tp) < 30 or vol.sum() <= 0:
        return None
    lo, hi = float(np.nanmin(tp)), float(np.nanmax(tp))
    if not hi > lo:
        return None
    edges = np.linspace(lo, hi, bins + 1)
    idx = np.clip(np.searchsorted(edges, tp, side="right") - 1, 0, bins - 1)
    v = np.bincount(idx, weights=vol, minlength=bins)
    poc = int(v.argmax())
    a = b = poc
    got, want = v[poc], v.sum() * share
    while got < want and (a > 0 or b < bins - 1):
        up = v[b + 1] if b < bins - 1 else -1.0
        dn = v[a - 1] if a > 0 else -1.0
        if up >= dn:
            b += 1
            got += up
        else:
            a -= 1
            got += dn
    return {"poc": float((edges[poc] + edges[poc + 1]) / 2), "vah": float(edges[b + 1]), "val": float(edges[a]),
            "profile": [round(float(x), 0) for x in v], "lo": lo, "hi": hi}


def _where(x: float, lo: float | None, hi: float | None) -> str | None:
    if lo is None or hi is None:
        return None
    return "above" if x > hi else "below" if x < lo else "in"


def auction(bars: pd.DataFrame, prior: dict | None = None) -> dict | None:
    """One regular session (09:30-16:00 rows of ts/open/high/low/close/volume) -> the auction read.

    day_type: trend   = range at least 2x the first hour's AND the close in the outer 15% of the range
              normal  = range within 1.2x the first hour's (the first hour set the day)
              neutral = everything else
    poor high / low: 3+ minutes printed within 0.03% of the extreme (no single-print "excess" = unfinished
    auction, textbook: likely to be revisited)."""
    b = bars.dropna(subset=["high", "low", "close"]).sort_values("ts")
    if len(b) < 30:
        return None
    vol = b["volume"].fillna(0).to_numpy(float)
    tp = ((b["high"] + b["low"] + b["close"]) / 3.0).to_numpy(float)
    va = value_area(tp, vol)
    if va is None:
        return None
    hi, lo = float(b["high"].max()), float(b["low"].min())
    op, cl = float(b["open"].iloc[0]), float(b["close"].iloc[-1])
    w = vol.sum()
    vwap = float((tp * vol).sum() / w) if w > 0 else float(tp.mean())
    vsd = float(math.sqrt(((tp - vwap) ** 2 * vol).sum() / w)) if w > 0 else float(tp.std())
    t0 = b["ts"].iloc[0].normalize() + pd.Timedelta(hours=9, minutes=30)
    ib = b[b["ts"] < t0 + pd.Timedelta(minutes=60)]
    ibh, ibl = (float(ib["high"].max()), float(ib["low"].min())) if len(ib) else (hi, lo)
    rng, ibr = hi - lo, max(ibh - ibl, 1e-9)
    pos = (cl - lo) / rng if rng > 0 else 0.5
    if rng >= 2 * ibr and (pos >= 0.85 or pos <= 0.15):
        dtype = "trend"
    elif rng <= 1.2 * ibr:
        dtype = "normal"
    else:
        dtype = "neutral"
    tol = 0.0003
    out = {"open": op, "high": hi, "low": lo, "close": cl, "vwap": vwap, "vwap_sd": vsd,
           "poc": va["poc"], "vah": va["vah"], "val": va["val"], "ib_high": ibh, "ib_low": ibl,
           "day_type": dtype, "close_vs_value": _where(cl, va["val"], va["vah"]),
           "open_vs_value": _where(op, (prior or {}).get("val"), (prior or {}).get("vah")),
           "poor_high": bool((b["high"] >= hi * (1 - tol)).sum() >= 3),
           "poor_low": bool((b["low"] <= lo * (1 + tol)).sum() >= 3),
           "n_minutes": int(len(b)), "profile": va["profile"], "p_lo": va["lo"], "p_hi": va["hi"]}
    return out


def naked_pocs(rows: Sequence[dict]) -> list[float]:
    """POCs of earlier sessions (oldest first in ``rows``) that no later session traded through."""
    out = []
    for i, r in enumerate(rows[:-1]):
        p = r.get("poc")
        if p is None:
            continue
        if all(not (x["low"] <= p <= x["high"]) for x in rows[i + 1:]):
            out.append(float(p))
    return out


def balance(rows: Sequence[dict], n: int = 3) -> bool:
    """The last ``n`` value areas all overlap = the market is balancing (breakout watch)."""
    last = [r for r in rows if r.get("val") is not None][-n:]
    if len(last) < n:
        return False
    return max(r["val"] for r in last) < min(r["vah"] for r in last)


# ---------------------------------------------------------------- scanners
# id -> (category, title, one plain line of what a hit means). Rules live in flags().
SCANNERS: dict[str, tuple[str, str, str]] = {
    "big_move": ("Moves", "Moved 2σ+", "Moved more than twice what its options priced for one day."),
    "iv_high": ("Vol", "IV rank 90%+", "Options are at the expensive end of their past year - fear or an event is priced."),
    "iv_low": ("Vol", "IV rank 10% or less", "Options are at the cheap end of their past year - quiet, cheap to own."),
    "carry_rich": ("Vol", "IV far above realized", "Options price much more movement than the stock has actually shown."),
    "carry_cheap": ("Vol", "IV below realized", "The stock moves more than its options price - options look cheap."),
    "term_inverted": ("Vol", "Term inverted", "Near-dated options cost more than 3-month ones - stress or an event soon."),
    "skew_jump": ("Vol", "Put skew jumped", "Demand for downside protection rose sharply in a week."),
    "prem_surge": ("Flow", "$ premium 3x usual", "Option money traded today is 3x its 20-day average."),
    "call_tilt": ("Flow", "Calls dominate", "75%+ of today's option dollars went to calls."),
    "put_tilt": ("Flow", "Puts dominate", "75%+ of today's option dollars went to puts."),
    "neg_gamma_flip": ("Positioning", "Short gamma near flip", "Below the gamma flip and within 2% of it - moves can speed up around here."),
    "near_wall": ("Positioning", "At a wall", "Within 1% of the biggest call or put wall - a likely pause or pin level."),
    "outside_value": ("Auction", "Opened outside value, held", "Opened outside yesterday's value and closed outside today's on the same side - one-sided auction."),
    "poor_extreme": ("Auction", "Poor high / low", "The day's extreme was printed many times with no rejection - often revisited."),
    "naked_poc": ("Auction", "Naked POC within 1%", "An untested prior point-of-control is close - a common magnet."),
    "balance3": ("Auction", "3-day balance", "Three overlapping value areas - a coil; watch which side breaks."),
    "dark_high_falling": ("Dark pool", "Dark buying while falling", "Off-exchange short ratio 1+ standard deviation above its normal while the price fell 3%+ in a week - quiet buying into weakness."),
    "dark_spike": ("Dark pool", "Dark ratio spike", "Off-exchange short ratio 2+ standard deviations above its 20-day normal."),
    "accumulation": ("Combos", "Accumulation", "Dark ratio above normal + call-heavy option money + closed above VWAP + IV not rich."),
    "distribution": ("Combos", "Distribution", "Dark ratio below normal + put-heavy option money + closed below VWAP."),
}


def flags(r: dict) -> list[str]:
    """Scanner ids hit by one compass row (keys as in compass_daily + auction extras)."""
    f: list[str] = []
    g = r.get
    sm = g("sigma_move")
    if sm is not None and abs(sm) >= 2:
        f.append("big_move")
    ivr = g("iv_rank")
    if ivr is not None:
        if ivr >= 0.9:
            f.append("iv_high")
        elif ivr <= 0.1:
            f.append("iv_low")
    c = g("carry")
    if c is not None and g("iv30"):
        if c >= 0.10 and c >= 0.35 * g("iv30"):
            f.append("carry_rich")
        elif c <= -0.05:
            f.append("carry_cheap")
    if (g("ts_30_90") or 0) > 1.05:
        f.append("term_inverted")
    if (g("skew_chg5") or 0) >= 0.02:
        f.append("skew_jump")
    if (g("prem_x20") or 0) >= 3 and (g("prem_usd") or 0) >= 1e6:
        f.append("prem_surge")
    cs = g("call_share")
    if cs is not None and (g("prem_usd") or 0) >= 2e6:
        if cs >= 0.75:
            f.append("call_tilt")
        elif cs <= 0.25:
            f.append("put_tilt")
    cl, flip = g("close"), g("gex_flip")
    if cl and flip and cl < flip and (flip / cl - 1) <= 0.02 and (g("gex") or 0) < 0:
        f.append("neg_gamma_flip")
    for w in (g("call_wall"), g("put_wall")):
        if cl and w and abs(w / cl - 1) <= 0.01:
            f.append("near_wall")
            break
    ov, cv = g("open_vs_value"), g("close_vs_value")
    if ov in ("above", "below") and ov == cv:
        f.append("outside_value")
    if g("poor_high") or g("poor_low"):
        f.append("poor_extreme")
    if cl and any(abs(p / cl - 1) <= 0.01 for p in (g("naked_pocs") or [])):
        f.append("naked_poc")
    if g("balance3"):
        f.append("balance3")
    dr, dz, r5 = g("dark_ratio"), g("dark_z"), g("ret5")
    if dr is not None and dz is not None and dz >= 1 and r5 is not None and r5 <= -0.03:
        f.append("dark_high_falling")
    if dz is not None and dz >= 2:
        f.append("dark_spike")
    vw = g("vwap")
    if dz is not None and cs is not None and cl and vw:
        if dz >= 1 and cs >= 0.6 and cl > vw and (ivr is None or ivr < 0.8):
            f.append("accumulation")
        if dz <= -1 and cs <= 0.4 and cl < vw:
            f.append("distribution")
    return f
