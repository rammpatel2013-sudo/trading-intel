"""Per-expiry delta-space vol surface + day-over-day changes (centered at 50d).

Replicates the desk "VOL SURFACE CHANGES" sheet: for a set of FIXED expiries
(your next weeklies), today's IV by |delta| and the day-over-day change computed
TWO ways -
  - FIXED DELTA (sticky-delta): IV at the same delta, today vs prior.
  - FIXED STRIKE (sticky-strike): IV at the same literal strike, today vs prior,
    placed at today's delta for the centered display.
Pure transforms over normalized chains (expiration/strike/opt_kind/delta/iv).
Descriptive regime read only - FlashAlpha rule 4.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd

from trading_intel.errors import ComputationError
from trading_intel.greeks.surface import DEFAULT_DELTAS, build_delta_surface


def next_weekly_expiries(
    chain: pd.DataFrame, *, n: int = 3, min_dte: int = 5, ref: date | None = None
) -> list[date]:
    """The next ``n`` expiry dates with DTE >= ``min_dte`` (skips 0DTE / this-week)."""
    ref_ts = pd.Timestamp(ref or date.today()).normalize()
    exp = pd.to_datetime(chain["expiration"], errors="coerce").dropna().dt.normalize()
    out = [d.date() for d in sorted(set(exp)) if (d - ref_ts).days >= min_dte]
    return out[:n]


def _curve(chain: pd.DataFrame, expiry: date, grid: np.ndarray):
    """(put_iv%, call_iv%) on the |delta| grid for ONE expiry, or None."""
    exp = pd.to_datetime(chain["expiration"], errors="coerce").dt.date
    sub = chain[exp == expiry]
    if sub.empty:
        return None
    try:
        ds = build_delta_surface(sub, deltas=tuple(grid), n_expiries=1)
    except ComputationError:
        return None
    if ds.n_expiries == 0:
        return None
    return ds.iv_put[0] * 100.0, ds.iv_call[0] * 100.0


def _fixed_strike_to_delta(
    prev: pd.DataFrame, curr: pd.DataFrame, expiry: date, grid: np.ndarray, side: str
) -> np.ndarray:
    """Fixed-strike IV change (curr-prev, vol pts) interpolated onto today's delta grid."""
    def _sub(df: pd.DataFrame) -> pd.DataFrame:
        e = pd.to_datetime(df["expiration"], errors="coerce").dt.date
        return df[(e == expiry) & (df["opt_kind"].astype(str).str.upper().str[0] == side)]

    p, c = _sub(prev), _sub(curr)
    if p.empty or c.empty:
        return np.full(len(grid), np.nan)
    m = p[["strike", "iv"]].dropna().merge(
        c[["strike", "iv", "delta"]].dropna(), on="strike", suffixes=("_p", "_c")
    )
    if m.empty:
        return np.full(len(grid), np.nan)
    chg = (m["iv_c"] - m["iv_p"]).to_numpy() * 100.0
    absd = (m["delta"].abs() * 100.0).to_numpy()
    order = np.argsort(absd)
    return np.interp(np.asarray(grid, float), absd[order], chg[order], left=np.nan, right=np.nan)


@dataclass
class ExpiryPanel:
    """Today's delta curve + both change views for one fixed expiry (vol points)."""

    expiry: date
    deltas: np.ndarray
    put_iv: np.ndarray       # today, %
    call_iv: np.ndarray
    d_put_delta: np.ndarray  # fixed-delta change (sticky-delta), vol pts
    d_call_delta: np.ndarray
    d_put_strike: np.ndarray  # fixed-strike change mapped to delta (sticky-strike)
    d_call_strike: np.ndarray


def surface_panel(
    curr: pd.DataFrame,
    prev: pd.DataFrame | None,
    expiries: list[date],
    *,
    deltas: tuple[float, ...] = DEFAULT_DELTAS,
) -> list[ExpiryPanel]:
    """Build an ExpiryPanel per expiry: today's IV-by-delta + fixed-delta & fixed-strike change."""
    grid = np.array(sorted(deltas), dtype=float)
    nan = np.full(len(grid), np.nan)
    panels: list[ExpiryPanel] = []
    for exp in expiries:
        cc = _curve(curr, exp, grid)
        if cc is None:
            continue
        pc = _curve(prev, exp, grid) if prev is not None else None
        d_put_d = (cc[0] - pc[0]) if pc is not None else nan.copy()
        d_call_d = (cc[1] - pc[1]) if pc is not None else nan.copy()
        d_put_s = _fixed_strike_to_delta(prev, curr, exp, grid, "P") if prev is not None else nan.copy()
        d_call_s = _fixed_strike_to_delta(prev, curr, exp, grid, "C") if prev is not None else nan.copy()
        panels.append(
            ExpiryPanel(exp, grid, cc[0], cc[1], d_put_d, d_call_d, d_put_s, d_call_s)
        )
    return panels


_KIND_ATTRS = {
    "iv": ("put_iv", "call_iv"),
    "delta": ("d_put_delta", "d_call_delta"),
    "strike": ("d_put_strike", "d_call_strike"),
}


def centered_frame(panels: list[ExpiryPanel], kind: str = "iv") -> pd.DataFrame:
    """Wide table: centered delta-label index (5P .. ATM .. 5C) x expiry columns.

    ``kind``: ``iv`` (today's IV %), ``delta`` (fixed-delta change), ``strike``
    (fixed-strike change). Rows run OTM-put 5d -> ATM (50d) -> OTM-call 5d, the
    desk's centered layout. Empty frame if no panels.
    """
    if not panels:
        return pd.DataFrame()
    deltas = panels[0].deltas
    nd = len(deltas)
    labels = [("ATM" if k == nd - 1 else f"{deltas[k]:g}P") for k in range(nd)]
    labels += [f"{deltas[k]:g}C" for k in range(nd - 2, -1, -1)]
    put_attr, call_attr = _KIND_ATTRS[kind]
    data: dict[str, list[float]] = {}
    for p in panels:
        put = list(getattr(p, put_attr))
        call = list(getattr(p, call_attr))
        data[str(p.expiry)] = put + [call[k] for k in range(nd - 2, -1, -1)]
    return pd.DataFrame(data, index=labels)
