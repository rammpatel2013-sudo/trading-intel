"""Per-snapshot MM dealer-force attribution for the live gamma map.

At each ``live_gex`` snapshot ``t``, decomposes the observed spot move
``dS = spot(t) - spot(t-1)`` into hedging-implied components from charm and
vanna, plus a directional GEX 'gravity' read. Uses the codebase's existing
``sign(C)=+1, sign(P)=-1`` convention - positive ``Sum sign*g*oi_eff`` means MMs
are net **long** that greek (the dampening / suppressive regime for gamma).

Methodology (descriptive read-through, FlashAlpha rule 4 - never a signal):

    dDelta_charm = Sum sign * charm * oi_eff * dt_years       # MM delta drift from charm
    dDelta_vanna = Sum sign * vanna * oi_eff * dIV            # MM delta drift from IV move
    ds_charm     = -dDelta_charm / net_gamma                  # spot move that re-neutralizes
    ds_vanna     = -dDelta_vanna / net_gamma
    residual     = dS - (ds_charm + ds_vanna)                 # unattributed / fundamentals
    gex_gravity  = sign(net_gamma) * |spot - flip| / spot     # +ve = suppressive (long-gamma);
                                                              # magnitude = distance to flip

``oi_eff = oi + (volm_buy - volm_sell)`` on 0DTE rows only (per
``flow_on_0dte_only=True``); other expiries use plain ``oi``. The
``volm_buy-volm_sell`` flow is a proxy (doesn't separate opening from closing
trades), and the three forces interact non-linearly in reality - treat this as a
relative attribution lens, not exact accounting.

Pure, side-effect-free, unit-testable.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

_SECONDS_PER_TRADING_YEAR = 252.0 * 6.5 * 3600.0  # one 6.5h session = 1/252 yr
_COLS = [
    "ts", "spot", "atm_iv", "delta_s", "ds_charm", "ds_vanna",
    "residual", "gex_gravity", "net_gamma", "regime",
]


def _sign(cp: pd.Series) -> pd.Series:
    return cp.astype(str).str.upper().str[0].map({"C": 1.0, "P": -1.0}).fillna(0.0)


def _atm_iv(snap: pd.DataFrame, anchor: float) -> float | None:
    """Mean IV of the few strikes nearest ``anchor`` (decimal; tolerates percent)."""
    iv = pd.to_numeric(snap.get("iv"), errors="coerce")
    strike = pd.to_numeric(snap.get("strike"), errors="coerce")
    mask = iv.notna() & (iv > 0) & strike.notna()
    if not bool(mask.any()):
        return None
    near = pd.DataFrame({"iv": iv[mask], "d": (strike[mask] - anchor).abs()})
    val = float(near.sort_values("d").head(4)["iv"].mean())
    if val <= 0:
        return None
    return val / 100.0 if val > 3.0 else val


def _gamma_flip(snap: pd.DataFrame) -> float | None:
    """Positioning gamma-flip: strike where cumulative net gxoi crosses zero."""
    strike = pd.to_numeric(snap["strike"], errors="coerce")
    gx = pd.to_numeric(snap.get("gxoi"), errors="coerce").fillna(0.0)
    by = (_sign(snap["cp"]) * gx).groupby(strike).sum().sort_index()
    by = by[by.index.notna()]
    if by.empty:
        return None
    strikes = by.index.to_numpy(dtype=float)
    cum = np.cumsum(by.to_numpy(dtype=float))
    cross = np.where(np.diff(np.sign(cum)) != 0)[0]
    if cross.size == 0:
        return None
    i = int(cross[0])
    x0, x1, y0, y1 = strikes[i], strikes[i + 1], cum[i], cum[i + 1]
    return float(x0) if y1 == y0 else float(x0 - y0 * (x1 - x0) / (y1 - y0))


def _oi_eff(snap: pd.DataFrame, session_day: object, *, flow_on_0dte_only: bool) -> pd.Series:
    """``oi_eff = oi + (volm_buy - volm_sell)``; flow applied on 0DTE rows only by default."""
    oi = pd.to_numeric(snap.get("oi"), errors="coerce").fillna(0.0)
    if "volm_buy" not in snap.columns or "volm_sell" not in snap.columns:
        return oi
    flow = (pd.to_numeric(snap["volm_buy"], errors="coerce").fillna(0.0)
            - pd.to_numeric(snap["volm_sell"], errors="coerce").fillna(0.0))
    if not flow_on_0dte_only:
        return oi + flow
    if "expiry" not in snap.columns:
        return oi
    exp = pd.to_datetime(snap["expiry"], errors="coerce").dt.date
    return oi + flow.where(exp == session_day, 0.0)


def _net(snap: pd.DataFrame, col: str, weights: pd.Series) -> float:
    """Net signed exposure ``Sum(sign * col * weights)`` (calls +, puts -)."""
    val = pd.to_numeric(snap.get(col), errors="coerce").fillna(0.0)
    return float((_sign(snap["cp"]) * val * weights).sum())


def _regime_label(ds_charm: float, ds_vanna: float, residual: float,
                  net_gamma: float, gex_gravity: float) -> str:
    """Short human read of the dominant force + GEX state at this snapshot."""
    parts = {"charm": ds_charm, "vanna": ds_vanna, "residual": residual}
    dominant = max(parts, key=lambda k: abs(parts[k]))
    gex_word = ("long-gamma (suppressive)" if net_gamma > 0
                else "short-gamma (amplifying)" if net_gamma < 0 else "neutral")
    return f"{gex_word}, |gravity|={abs(gex_gravity):.4f} - dominant: {dominant}"


def force_attribution(
    frame: pd.DataFrame, *, flow_on_0dte_only: bool = True
) -> pd.DataFrame:
    """One row per ``live_gex`` snapshot (after the first) with the decomposition.

    Needs ``ts, strike, cp, spot, oi, gxoi, gamma, charm, vanna, iv, expiry`` and,
    optionally, ``volm_buy / volm_sell`` for the flow correction. Returns empty
    when fewer than 2 snapshots are present.
    """
    if frame is None or frame.empty or "ts" not in frame.columns:
        return pd.DataFrame(columns=_COLS)
    snaps = sorted(pd.Series(frame["ts"].unique()).dropna().tolist())
    if len(snaps) < 2:
        return pd.DataFrame(columns=_COLS)
    session_day = pd.Timestamp(snaps[-1]).date()

    # First pass: per-snapshot scalars (spot, atm_iv, net_gamma/charm/vanna, flip).
    rows = []
    for ts in snaps:
        snap = frame[frame["ts"] == ts]
        spot_series = pd.to_numeric(snap.get("spot"), errors="coerce").dropna()
        if spot_series.empty:
            rows.append(None)
            continue
        spot = float(spot_series.iloc[0])
        w = _oi_eff(snap, session_day, flow_on_0dte_only=flow_on_0dte_only)
        rows.append({
            "ts": ts, "spot": spot,
            "atm_iv": _atm_iv(snap, spot),
            "net_gamma": _net(snap, "gamma", w),
            "net_charm": _net(snap, "charm", w),
            "net_vanna": _net(snap, "vanna", w),
            "flip": _gamma_flip(snap),
        })

    out = []
    for i in range(1, len(rows)):
        prev, curr = rows[i - 1], rows[i]
        if prev is None or curr is None:
            continue
        ng = curr["net_gamma"]
        if not np.isfinite(ng) or ng == 0.0:
            continue
        dt_years = (pd.Timestamp(curr["ts"]) - pd.Timestamp(prev["ts"])).total_seconds() \
            / _SECONDS_PER_TRADING_YEAR
        d_iv = ((curr["atm_iv"] or np.nan) - (prev["atm_iv"] or np.nan))
        if not np.isfinite(d_iv):
            d_iv = 0.0
        delta_s = curr["spot"] - prev["spot"]
        ds_charm = -curr["net_charm"] * dt_years / ng
        ds_vanna = -curr["net_vanna"] * d_iv / ng
        residual = delta_s - ds_charm - ds_vanna
        flip = curr["flip"]
        gex_gravity = (
            float(np.sign(ng)) * abs(curr["spot"] - flip) / curr["spot"]
            if flip is not None and curr["spot"] else np.nan
        )
        out.append({
            "ts": curr["ts"], "spot": curr["spot"], "atm_iv": curr["atm_iv"],
            "delta_s": delta_s, "ds_charm": ds_charm, "ds_vanna": ds_vanna,
            "residual": residual, "gex_gravity": gex_gravity, "net_gamma": ng,
            "regime": _regime_label(ds_charm, ds_vanna, residual, ng,
                                    0.0 if not np.isfinite(gex_gravity) else gex_gravity),
        })
    return pd.DataFrame(out, columns=_COLS) if out else pd.DataFrame(columns=_COLS)


def cumulative_attribution(att: pd.DataFrame) -> pd.DataFrame:
    """Rolling sums from open of ``delta_s / ds_charm / ds_vanna / residual``."""
    if att is None or att.empty:
        return pd.DataFrame(columns=["ts", "cum_delta_s", "cum_ds_charm",
                                     "cum_ds_vanna", "cum_residual"])
    out = att[["ts"]].copy()
    out["cum_delta_s"] = att["delta_s"].cumsum()
    out["cum_ds_charm"] = att["ds_charm"].cumsum()
    out["cum_ds_vanna"] = att["ds_vanna"].cumsum()
    out["cum_residual"] = att["residual"].cumsum()
    return out
