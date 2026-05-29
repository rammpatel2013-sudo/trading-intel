"""Pure data-prep for the MM gamma-profile (spot-ladder) page.

Loads the latest intraday ``live_gex`` snapshot for a symbol, shapes it into the
chain the BS engine wants, applies an expiry scope (0DTE vs all), and returns the
spot-ladder dollar-gamma profile (per expiry + All Expiries) from
``greeks.gamma_profile``. Recompute is sanctioned for this simulated view only
(ADR-002). Side-effect-free; descriptive regime view, not a signal (rule 4).
"""
from __future__ import annotations

from datetime import date

import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from trading_intel.greeks.gamma_profile import gamma_profile
from trading_intel.memory.models import LiveGex

ALL = "All expiries"
ZERO_DTE = "0DTE"
_CHAIN_COLS = ["strike", "opt_kind", "iv", "oi", "expiration", "spot"]


def load_latest_chain(session: Session, symbol: str) -> tuple[object, pd.DataFrame]:
    """Latest ``live_gex`` snapshot for ``symbol`` as a gamma-profile-ready chain.

    Returns ``(ts, frame)`` with columns ``strike, opt_kind, iv, oi, expiration,
    spot``. Empty frame when no live rows exist.
    """
    latest = session.execute(
        select(func.max(LiveGex.ts)).where(LiveGex.symbol == symbol)
    ).scalar_one_or_none()
    if latest is None:
        return None, pd.DataFrame(columns=_CHAIN_COLS)
    rows = session.execute(
        select(LiveGex).where(LiveGex.symbol == symbol, LiveGex.ts == latest)
    ).scalars().all()
    # Effective position = resting OI + today's net flow (volm_buy - volm_sell);
    # falls back to OI when flow is absent. Drives the spot-ladder + forward field.
    recs = [
        {
            "strike": r.strike,
            "opt_kind": "call" if str(r.cp).upper().startswith("C") else "put",
            "iv": r.iv,
            "oi": (r.oi or 0.0) + (r.volm_buy or 0.0) - (r.volm_sell or 0.0),
            "expiration": r.expiry, "spot": r.spot,
        }
        for r in rows
    ]
    return latest, (pd.DataFrame(recs) if recs else pd.DataFrame(columns=_CHAIN_COLS))


def snapshot_spot(frame: pd.DataFrame) -> float | None:
    """First non-null spot carried on the chain frame, or ``None``."""
    if frame is None or frame.empty or "spot" not in frame.columns:
        return None
    s = pd.to_numeric(frame["spot"], errors="coerce").dropna()
    return float(s.iloc[0]) if not s.empty else None


def available_expiries(frame: pd.DataFrame) -> list[date]:
    """Sorted distinct expiration dates present in the snapshot."""
    if frame is None or frame.empty or "expiration" not in frame.columns:
        return []
    exp = pd.to_datetime(frame["expiration"], errors="coerce").dt.date.dropna()
    return sorted(set(exp))


def filter_scope(frame: pd.DataFrame, scope: str, *, ref: date) -> pd.DataFrame:
    """Restrict the chain by expiry scope.

    ``ALL`` keeps everything; ``ZERO_DTE`` keeps only contracts expiring on or
    before ``ref`` (the session date) — the true 0DTE strip. An ISO date string
    keeps just that expiry.
    """
    if frame is None or frame.empty or scope == ALL or "expiration" not in frame.columns:
        return frame
    exp = pd.to_datetime(frame["expiration"], errors="coerce").dt.date
    if scope == ZERO_DTE:
        return frame[exp <= ref]
    return frame[exp.astype("string") == scope]


def build_profile(
    frame: pd.DataFrame,
    spot: float | None,
    *,
    scope: str = ALL,
    ref: date | None = None,
    n_points: int = 81,
    span: float = 0.07,
) -> pd.DataFrame:
    """Spot-ladder dollar-gamma profile for the scoped chain (per expiry + All).

    Empty frame / no spot -> empty. Thin wrapper that scopes then defers the math
    to ``greeks.gamma_profile`` (sticky-strike).
    """
    if frame is None or frame.empty or spot is None:
        return pd.DataFrame()
    ref = ref or date.today()
    scoped = filter_scope(frame, scope, ref=ref)
    if scoped is None or scoped.empty:
        return pd.DataFrame()
    return gamma_profile(scoped, spot, ref=ref, n_points=n_points, span=span)
