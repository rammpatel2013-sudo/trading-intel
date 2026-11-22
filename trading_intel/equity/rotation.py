"""Sector rotation (relative rotation graph), 2026-11-22 - the blended RS-Ratio / RS-Momentum of "Relative Robustness
Rotation" (ChartsLector TV-Indicators), daily closes vs SPY, without its optional acceleration tweak.

    RS line          100 x sector / SPY
    R(a, b)          100 x EMA_a(RS) / EMA_b(EMA_a(RS))
    RS-Ratio         0.6 x R(15, 21) + 0.4 x R(26, 34)
    M(a, b)          100 x EMA_a(Ratio) / EMA_b(EMA_a(Ratio))
    RS-Momentum      0.6 x M(8, 15) + 0.4 x M(15, 26)
Quadrant: Leading (ratio >= 100, momentum >= 100) · Weakening (ratio >= 100, momentum < 100) · Lagging (both < 100) ·
Improving (ratio < 100, momentum >= 100). The usual path is clockwise: Improving -> Leading -> Weakening -> Lagging.
Descriptor only (CLAUDE.md rule 4).
"""

from __future__ import annotations

import numpy as np

from trading_intel.equity.stages import ema

SECTOR_ETFS = {
    "XLK": "Technology", "XLC": "Communication", "XLY": "Consumer discretionary", "XLF": "Financials",
    "XLI": "Industrials", "XLV": "Health care", "XLE": "Energy", "XLB": "Materials", "XLRE": "Real estate",
    "XLP": "Consumer staples", "XLU": "Utilities",
}
# FMP / GICS sector names in equity_profiles -> the sector fund that holds them
GICS_TO_ETF = {
    "Technology": "XLK", "Information Technology": "XLK", "Communication Services": "XLC",
    "Consumer Cyclical": "XLY", "Consumer Discretionary": "XLY", "Financial Services": "XLF", "Financials": "XLF",
    "Industrials": "XLI", "Healthcare": "XLV", "Health Care": "XLV", "Energy": "XLE", "Basic Materials": "XLB",
    "Materials": "XLB", "Real Estate": "XLRE", "Consumer Defensive": "XLP", "Consumer Staples": "XLP",
    "Utilities": "XLU",
}


def _r(x: np.ndarray, a: int, b: int) -> np.ndarray:
    s = ema(x[:, None], a)
    base = ema(s, b)
    with np.errstate(all="ignore"):
        return (100.0 * s / base)[:, 0]


def rrg(sector: np.ndarray, spy: np.ndarray, w: float = 0.6) -> tuple[np.ndarray, np.ndarray]:
    """(RS-Ratio, RS-Momentum) series for one sector's closes vs SPY's (same dates)."""
    with np.errstate(all="ignore"):
        rs = 100.0 * sector / spy
    ratio = w * _r(rs, 15, 21) + (1 - w) * _r(rs, 26, 34)
    mom = w * _r(ratio, 8, 15) + (1 - w) * _r(ratio, 15, 26)
    return ratio, mom


def quadrant(ratio: float, mom: float) -> str:
    if ratio >= 100:
        return "Leading" if mom >= 100 else "Weakening"
    return "Improving" if mom >= 100 else "Lagging"


def rotation(dates: list, closes: dict[str, np.ndarray], *, tail: int = 6, step: int = 5) -> list[dict]:
    """One row per sector fund: name, now (ratio, momentum, quadrant) and a tail of points every `step` days."""
    spy = closes.get("SPY")
    if spy is None:
        return []
    out = []
    for sym, name in SECTOR_ETFS.items():
        c = closes.get(sym)
        if c is None or np.isnan(c[-1]):
            continue
        ratio, mom = rrg(c, spy)
        idx = [len(dates) - 1 - k * step for k in range(tail)][::-1]
        pts = [(dates[i], round(float(ratio[i]), 2), round(float(mom[i]), 2)) for i in idx
               if i >= 0 and np.isfinite(ratio[i]) and np.isfinite(mom[i])]
        if not pts:
            continue
        r_, m_ = pts[-1][1], pts[-1][2]
        r20 = float(c[-1] / c[-21] - 1) * 100 if len(c) > 21 and c[-21] else None
        s20 = float(spy[-1] / spy[-21] - 1) * 100 if len(spy) > 21 and spy[-21] else None
        out.append({"symbol": sym, "name": name, "ratio": r_, "mom": m_, "quadrant": quadrant(r_, m_), "tail": pts,
                    "ret20": None if r20 is None else round(r20, 2),
                    "vs_spy20": None if r20 is None or s20 is None else round(r20 - s20, 2)})
    return out
