"""Pure data-prep for the intraday price cone overlaid on the live forward field.

From the latest ``live_gex`` snapshot, projects two bounding price paths (an up and
a down scenario) from *now* to the 16:00 close, over the same time grid as the
forward field (``greeks.forward_field.session_close_grid``). The cone driver is
selectable:

- ``"vol"``   - rigorous +/-1sigma expected move from near-the-money ATM IV, scaled
                by trading-time to the close (``sigma_t = sigma * sqrt(tau)``).
- ``"gex"``   - heuristic: band half-width = distance from spot to the (positioning)
                gamma-flip, faded in by sqrt of the session fraction elapsed.
- ``"charm"`` - experimental: a directional drift to the close set by net
                near-the-money charm, with its mirror as the opposite scenario.
- ``"vanna"`` - experimental: spot move implied by re-hedging net vanna under a
                1 IV-point vol shift (``dS = net_vanna * dsigma / net_gamma``).

Side-effect-free and unit-testable. Descriptive regime view only (FlashAlpha rule
4) - these are scenario overlays, never signals. The non-vol drivers are capped
heuristics (``_MAX_BAND``) for visual context, not forecasts.
"""
from __future__ import annotations

from datetime import datetime

import numpy as np
import pandas as pd

DRIVERS = ("vol", "gex", "charm", "vanna")
DRIVER_LABELS = {
    "vol": "Vol expected move (+/-1sigma ATM IV)",
    "gex": "GEX-implied (gamma-flip distance)",
    "charm": "Charm drift (experimental)",
    "vanna": "Vanna-implied (experimental)",
}

_COLS = ["t", "up", "down"]
_SECONDS_PER_TRADING_YEAR = 252.0 * 6.5 * 3600.0  # one 6.5h session = 1/252 yr
_MAX_BAND = 0.05            # cap any driver's full-session half-width at +/-5% (defensive)
_VOL_PERTURB = 0.01         # 1 IV point, for the vanna-implied re-hedge move
_CHARM_DRIFT_SCALE = 0.005  # |normalized net charm| = 1 -> 0.5% drift to the close


def _sign(cp: pd.Series) -> pd.Series:
    return cp.astype(str).str.upper().str[0].map({"C": 1.0, "P": -1.0}).fillna(0.0)


def _latest_snapshot(frame: pd.DataFrame) -> pd.DataFrame:
    if frame is None or frame.empty or "ts" not in frame.columns:
        return pd.DataFrame()
    return frame[frame["ts"] == frame["ts"].max()].copy()


def _tau(grid: list[datetime]) -> tuple[np.ndarray, np.ndarray]:
    """Return ``(tau_years, frac)`` over the grid, both 0 at ``grid[0]``.

    ``tau_years`` is trading-time years elapsed from ``grid[0]``; ``frac`` is the
    fraction of the now->close span elapsed (0..1).
    """
    t0 = pd.Timestamp(grid[0])
    secs = np.array([(pd.Timestamp(t) - t0).total_seconds() for t in grid], dtype=float)
    secs = np.maximum(secs, 0.0)
    full = secs[-1] if secs[-1] > 0 else 1.0
    return secs / _SECONDS_PER_TRADING_YEAR, secs / full


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
    return val / 100.0 if val > 3.0 else val  # tolerate percent-quoted IV


def _net(snap: pd.DataFrame, col: str, *, times_oi: bool = False) -> float:
    """Net signed exposure ``Sum(sign * col [* oi])`` (calls +, puts -)."""
    val = pd.to_numeric(snap.get(col), errors="coerce").fillna(0.0)
    if times_oi:
        val = val * pd.to_numeric(snap.get("oi"), errors="coerce").fillna(0.0)
    return float((_sign(snap["cp"]) * val).sum())


def _gross_charm_oi(snap: pd.DataFrame) -> float:
    """``Sum(|charm| * oi)`` - the normalizer for the charm-drift driver."""
    charm = pd.to_numeric(snap.get("charm"), errors="coerce").fillna(0.0).abs()
    oi = pd.to_numeric(snap.get("oi"), errors="coerce").fillna(0.0)
    return float((charm * oi).sum())


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


def _cap_hw(hw: np.ndarray, anchor: float) -> np.ndarray:
    return np.minimum(np.abs(hw), _MAX_BAND * anchor)


def intraday_cone(
    driver: str, frame: pd.DataFrame, anchor: float | None, grid: list[datetime]
) -> pd.DataFrame:
    """Two bounding price paths (``t``, ``up``, ``down``) from now to the close.

    ``driver`` is one of :data:`DRIVERS`. Returns an empty frame when the chosen
    driver's inputs are missing (no IV / no flip / no net gamma). Both paths start
    at ``anchor`` (zero band at ``grid[0]``). Descriptive scenario overlay only
    (FlashAlpha rule 4).
    """
    if driver not in DRIVERS or anchor is None or not grid or len(grid) < 2:
        return pd.DataFrame(columns=_COLS)
    snap = _latest_snapshot(frame)
    if snap.empty or "cp" not in snap.columns:
        return pd.DataFrame(columns=_COLS)
    tau_years, frac = _tau(grid)
    s0 = float(anchor)

    if driver == "vol":
        sigma = _atm_iv(snap, s0)
        if sigma is None:
            return pd.DataFrame(columns=_COLS)
        hw = _cap_hw(s0 * (np.exp(sigma * np.sqrt(tau_years)) - 1.0), s0)
        up, down = s0 + hw, s0 - hw
    elif driver == "gex":
        flip = _gamma_flip(snap)
        if flip is None:
            return pd.DataFrame(columns=_COLS)
        hw = _cap_hw(abs(s0 - flip) * np.sqrt(frac), s0)
        up, down = s0 + hw, s0 - hw
    elif driver == "charm":
        denom = _gross_charm_oi(snap)
        if denom == 0:
            return pd.DataFrame(columns=_COLS)
        norm = max(-1.0, min(1.0, _net(snap, "charm", times_oi=True) / denom))
        drift = s0 * (1.0 + norm * _CHARM_DRIFT_SCALE * frac)
        up, down = drift, 2.0 * s0 - drift
    else:  # vanna
        net_gamma = _net(snap, "gxoi")
        if net_gamma == 0:
            return pd.DataFrame(columns=_COLS)
        move = abs(_net(snap, "vanna", times_oi=True) * _VOL_PERTURB / net_gamma)
        hw = _cap_hw(move * np.sqrt(frac), s0)
        up, down = s0 + hw, s0 - hw

    return pd.DataFrame({
        "t": list(grid),
        "up": np.asarray(up, dtype=float),
        "down": np.asarray(down, dtype=float),
    })[_COLS]
