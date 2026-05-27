"""Pure data-prep for the live gamma/charm/vanna strike x time map.

Builds, from the day's intraday ``live_gex`` rows, a strike x time matrix of net
dealer exposure for each greek, plus the latest per-strike profile and the spot
path — the inputs for the Menthor-Q-style heatmap (left) + profile bars (right).

Net exposure per (strike, ts) uses the standard dealer sign (calls +, puts -),
matching ``ticker_data.gex_by_strike``:
- **gamma** = ``gxoi`` (Convex-precomputed gamma x OI),
- **charm** = ``charm`` x ``oi``,
- **vanna** = ``vanna`` x ``oi``.

Side-effect-free and unit-testable on in-memory SQLite. Descriptive regime view
only (FlashAlpha rule 4); ``live_gex`` is delta-band filtered, so this is the
near-the-money map, not the full chain.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from trading_intel.memory.models import LiveGex

GREEKS = ("gamma", "charm", "vanna")
_LOAD_COLS = ["ts", "strike", "cp", "expiry", "spot", "gxoi", "oi", "iv", "vanna", "charm"]

# Regular cash session (ET), used to time-weight charm toward the close.
_SESSION_OPEN_MIN = 9 * 60 + 30  # 09:30
_SESSION_CLOSE_MIN = 16 * 60  # 16:00
_SESSION_LEN_MIN = float(_SESSION_CLOSE_MIN - _SESSION_OPEN_MIN)  # 390


def load_live_gex_day(
    session: Session, symbol: str, *, day: object | None = None
) -> pd.DataFrame:
    """All ``live_gex`` rows for ``symbol`` on its most recent (or given) session."""
    if day is None:
        latest = session.execute(
            select(func.max(LiveGex.ts)).where(LiveGex.symbol == symbol)
        ).scalar_one_or_none()
        if latest is None:
            return pd.DataFrame(columns=_LOAD_COLS)
        day = latest.date()
    rows = session.execute(
        select(LiveGex).where(LiveGex.symbol == symbol).order_by(LiveGex.ts.asc())
    ).scalars().all()
    recs = [
        {
            "ts": r.ts, "strike": r.strike, "cp": r.cp, "expiry": r.expiry, "spot": r.spot,
            "gxoi": r.gxoi, "oi": r.oi, "iv": r.iv, "vanna": r.vanna, "charm": r.charm,
        }
        for r in rows
        if r.ts is not None and r.ts.date() == day
    ]
    return pd.DataFrame(recs) if recs else pd.DataFrame(columns=_LOAD_COLS)


def _signed_exposure(df: pd.DataFrame, greek: str) -> pd.Series:
    """Per-row net dealer exposure for ``greek`` (calls +, puts -)."""
    if greek == "gamma":
        val = pd.to_numeric(df["gxoi"], errors="coerce")
    elif greek == "charm":
        val = pd.to_numeric(df["charm"], errors="coerce") * pd.to_numeric(df["oi"], errors="coerce")
    elif greek == "vanna":
        val = pd.to_numeric(df["vanna"], errors="coerce") * pd.to_numeric(df["oi"], errors="coerce")
    else:
        raise ValueError(f"unknown greek: {greek!r}")
    sign = df["cp"].astype(str).str.upper().str[0].map({"C": 1.0, "P": -1.0}).fillna(0.0)
    return val.fillna(0.0) * sign


def exposure_matrix(frame: pd.DataFrame, greek: str) -> pd.DataFrame:
    """Strike (index) x ts (columns) matrix of net ``greek`` exposure.

    Empty frame -> empty. Each cell sums the signed exposure of all strikes' rows
    at that snapshot.
    """
    if frame is None or frame.empty:
        return pd.DataFrame()
    df = frame.copy()
    df["_e"] = _signed_exposure(df, greek)
    return df.pivot_table(
        index="strike", columns="ts", values="_e", aggfunc="sum", fill_value=0.0
    ).sort_index()


def latest_profile(frame: pd.DataFrame, greek: str) -> pd.DataFrame:
    """Net ``greek`` exposure by strike at the latest snapshot (``strike``, ``exposure``)."""
    if frame is None or frame.empty:
        return pd.DataFrame(columns=["strike", "exposure"])
    latest_ts = frame["ts"].max()
    sub = frame[frame["ts"] == latest_ts].copy()
    sub["exposure"] = _signed_exposure(sub, greek)
    out = sub.groupby("strike", as_index=False)["exposure"].sum().sort_values("strike")
    return out.reset_index(drop=True)


def spot_path(frame: pd.DataFrame) -> pd.DataFrame:
    """Spot per snapshot (``ts``, ``spot``) for the heatmap overlay line."""
    if frame is None or frame.empty or "spot" not in frame.columns:
        return pd.DataFrame(columns=["ts", "spot"])
    out = frame.dropna(subset=["spot"]).groupby("ts", as_index=False)["spot"].first()
    return out.sort_values("ts").reset_index(drop=True)


def session_date(frame: pd.DataFrame) -> object | None:
    """Trading-day date of the latest snapshot in ``frame`` (or ``None``)."""
    if frame is None or frame.empty or "ts" not in frame.columns:
        return None
    return pd.Timestamp(frame["ts"].max()).date()


def filter_expiry_scope(frame: pd.DataFrame, scope: str, *, ref: object | None = None) -> pd.DataFrame:
    """Restrict a ``live_gex`` frame to an expiry scope.

    ``"All"`` is a no-op; ``"0DTE"`` keeps only contracts expiring on or before the
    session date (``ref``, defaulting to the latest snapshot's day) — the true 0DTE
    strip, which also makes the charm session-clock decay exact for that scope.
    """
    if frame is None or frame.empty or scope == "All" or "expiry" not in frame.columns:
        return frame
    ref = ref or session_date(frame)
    if ref is None:
        return frame
    exp = pd.to_datetime(frame["expiry"], errors="coerce").dt.date
    return frame[exp <= ref]


def session_fraction_remaining(ts: object) -> float:
    """Fraction of the 09:30-16:00 ET cash session still ahead of ``ts``.

    1.0 at/before the 09:30 open, 0.0 at/after the 16:00 close. Used to time-weight
    charm: the delta drift a dealer still has to hedge from now to expiry is
    ~ ``charm * time_remaining``, so for 0DTE the *remaining* charm-driven flow
    decays to zero into the close (docs/playbooks 'Cracking the Code on Charm').
    """
    t = pd.Timestamp(ts)
    minute_of_day = t.hour * 60 + t.minute
    rem = (_SESSION_CLOSE_MIN - minute_of_day) / _SESSION_LEN_MIN
    return float(min(1.0, max(0.0, rem)))


def _normalize(matrix: pd.DataFrame) -> pd.DataFrame:
    """Scale a strike x ts matrix to [-1, 1] by its day-wide absolute max (sign kept)."""
    if matrix is None or matrix.empty:
        return matrix if matrix is not None else pd.DataFrame()
    absmax = float(np.nanmax(np.abs(matrix.to_numpy()))) if matrix.size else 0.0
    return matrix / absmax if absmax > 0 else matrix * 0.0


def composite_matrix(frame: pd.DataFrame, *, charm_decay: bool = True) -> pd.DataFrame:
    """Strike x ts composite net dealer hedging pressure: gamma + vanna + charm.

    Each greek's signed exposure (calls +, puts -) is normalized to a comparable
    [-1, 1] scale by its own day-wide absolute max, then summed — so no single
    greek's raw units dominate the picture. Charm is first weighted by the
    session-clock fraction remaining (``session_fraction_remaining``), so its
    contribution decays to zero into the 16:00 close (the 0DTE 'charm goes to
    zero at 4pm' effect). Empty frame -> empty. Descriptive only (rule 4).
    """
    if frame is None or frame.empty:
        return pd.DataFrame()
    g = exposure_matrix(frame, "gamma")
    v = exposure_matrix(frame, "vanna")
    c = exposure_matrix(frame, "charm")
    if charm_decay and not c.empty:
        weights = pd.Series({ts: session_fraction_remaining(ts) for ts in c.columns})
        c = c.mul(weights, axis=1)
    parts = [_normalize(m) for m in (g, v, c) if not m.empty]
    if not parts:
        return pd.DataFrame()
    composite = parts[0]
    for part in parts[1:]:
        composite = composite.add(part, fill_value=0.0)
    return composite.sort_index()


def composite_profile(frame: pd.DataFrame, *, charm_decay: bool = True) -> pd.DataFrame:
    """Latest-snapshot composite hedging pressure by strike (``strike``, ``exposure``)."""
    matrix = composite_matrix(frame, charm_decay=charm_decay)
    if matrix is None or matrix.empty:
        return pd.DataFrame(columns=["strike", "exposure"])
    latest_ts = max(matrix.columns)
    out = matrix[latest_ts].reset_index()
    out.columns = ["strike", "exposure"]
    return out.sort_values("strike").reset_index(drop=True)
