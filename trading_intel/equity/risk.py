"""Drawdown control for the equity book (E6). Pure.

* ``stops``          - two plain stops: 2.5 x the 20-day average true range below the close, and
                       2% under the 50-day line. The card shows both; the tighter one sizes the trade.
* ``position_size``  - shares so that hitting the stop costs ``risk_pct`` of the book (default 1% of
                       $50,000, the same book the seven daily tests use).
* ``book_risk``      - for a set of holdings: weighted style exposures, sector weights, predicted yearly
                       volatility from the factor model, and how much of it comes from each factor.
                       Flags a "hidden momentum bet" (momentum exposure > 1), a sector over 40%, and a
                       book whose risk is mostly one factor.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

import numpy as np

from trading_intel.equity.styles import STYLES

BOOK_USD = 50_000.0
RISK_PCT = 1.0
ATR_MULT = 2.5


def atr(
    high: Sequence[float], low: Sequence[float], close: Sequence[float], n: int = 20
) -> float | None:
    h, lo, c = (np.asarray(x, dtype=float) for x in (high, low, close))
    if len(c) < n + 1:
        return None
    tr = np.maximum(h[1:] - lo[1:], np.maximum(np.abs(h[1:] - c[:-1]), np.abs(lo[1:] - c[:-1])))
    tr = tr[-n:]
    return float(np.nanmean(tr)) if np.isfinite(tr).any() else None


def stops(close: float, atr_val: float | None, sma50: float | None) -> dict:
    out: dict[str, float | None] = {
        "atr_stop": round(close - ATR_MULT * atr_val, 2) if atr_val else None,
        "ma50_stop": round(sma50 * 0.98, 2) if sma50 and sma50 < close else None,
    }
    cands = [v for v in out.values() if v is not None and v < close]
    out["stop"] = max(cands) if cands else None  # the tighter (higher) stop sizes the trade
    return out


def position_size(
    price: float, stop: float | None, *, book_usd: float = BOOK_USD, risk_pct: float = RISK_PCT
) -> dict | None:
    if stop is None or stop >= price:
        return None
    risk_usd = book_usd * risk_pct / 100.0
    per_share = price - stop
    shares = int(risk_usd // per_share)
    pos = shares * price
    return {
        "shares": shares,
        "risk_usd": round(risk_usd, 0),
        "position_usd": round(pos, 0),
        "pct_of_book": round(pos / book_usd * 100, 1),
        "stop_distance_pct": round(per_share / price * 100, 1),
    }


def spread_contracts(
    debit: float, *, book_usd: float = BOOK_USD, risk_pct: float = RISK_PCT
) -> int:
    """Debit spreads: the debit is the whole risk, so contracts = risk budget / (debit x 100)."""
    return int((book_usd * risk_pct / 100.0) // (debit * 100)) if debit > 0 else 0


def book_risk(
    weights: Mapping[str, float],
    exposures: Mapping[str, Mapping],
    factor_cov: np.ndarray,
    factor_names: Sequence[str],
    resid_var: Mapping[str, float],
) -> dict:
    """``weights`` = fraction of book per stock (sums to the equity share, e.g. 1.0).

    ``exposures[sym]`` has the STYLES z-scores + ``sector``; ``factor_cov`` is DAILY covariance over
    ``factor_names`` (market, sector:<name>..., styles); ``resid_var`` daily own-move variance.
    """
    names = list(factor_names)
    x = np.zeros(len(names))
    covered = 0.0
    sectors: dict[str, float] = {}
    for sym, w in weights.items():
        e = exposures.get(sym)
        if not e:
            continue
        covered += w
        if "market" in names:
            x[names.index("market")] += w
        sec = e.get("sector") or "Unknown"
        sectors[sec] = sectors.get(sec, 0.0) + w
        key = f"sector:{sec}"
        if key in names:
            x[names.index(key)] += w
        for s in STYLES:
            if s in names and e.get(s) is not None:
                x[names.index(s)] += w * float(e[s])
    fvar = float(x @ factor_cov @ x) if factor_cov.size else 0.0
    svar = float(sum((w**2) * resid_var.get(s, 0.0) for s, w in weights.items() if s in exposures))
    total = fvar + svar
    contrib = {}
    if total > 0 and factor_cov.size:
        mc = factor_cov @ x
        for k, nm in enumerate(names):
            c = x[k] * mc[k] / total * 100
            if abs(c) >= 1:
                contrib[nm] = round(float(c), 1)
        contrib["own moves (stock-specific)"] = round(svar / total * 100, 1)
    style_exp = {s: round(float(x[names.index(s)]), 2) for s in STYLES if s in names}
    flags = []
    if style_exp.get("momentum", 0) > 1.0:
        flags.append("momentum exposure above 1: the book is largely one momentum bet")
    top_sec = max(sectors.items(), key=lambda kv: kv[1]) if sectors else None
    if top_sec and covered > 0 and top_sec[1] / covered > 0.40:
        flags.append(f"{top_sec[0]} is {top_sec[1] / covered * 100:.0f}% of the stocks we can see")
    big = [k for k, v in contrib.items() if k != "market" and v >= 50]
    if big:
        flags.append(f"more than half the risk comes from {big[0]}")
    return {
        "covered_weight": round(covered, 3),
        "style_exposure": style_exp,
        "sector_weights": {
            k: round(v, 3) for k, v in sorted(sectors.items(), key=lambda kv: -kv[1])
        },
        "predicted_vol_pct": round(float(np.sqrt(max(total, 0) * 252) * 100), 1),
        "risk_share_pct": dict(sorted(contrib.items(), key=lambda kv: -abs(kv[1]))),
        "flags": flags,
    }
