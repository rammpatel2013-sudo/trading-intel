"""Pure data-prep for the forward gamma/charm field (Live Gamma Map tab).

Anchors on the latest intraday ``live_gex`` snapshot (0DTE-scoped by default),
holds spot fixed, and projects the gamma & charm fields forward over a time grid
to the 16:00 ET close (``greeks.forward_field``). Recompute is sanctioned for this
simulated view (ADR-002); descriptive, not a signal (rule 4).
"""
from __future__ import annotations

from datetime import date, datetime

import pandas as pd
from sqlalchemy.orm import Session

from trading_intel.dashboard.gamma_profile_data import (
    ZERO_DTE,
    filter_scope,
    load_latest_chain,
    snapshot_spot,
)
from trading_intel.greeks.forward_field import forward_field, session_close_grid
from trading_intel.timeutils import eastern_now


def build_forward_fields(
    session: Session,
    symbol: str,
    *,
    spot: float | None = None,
    scope_0dte: bool = True,
    now: datetime | None = None,
) -> tuple[object, float | None, list[datetime], pd.DataFrame, pd.DataFrame]:
    """Return ``(ts, anchor_spot, grid, gamma_field, charm_field)`` for ``symbol``.

    ``anchor_spot`` defaults to the snapshot spot when ``spot`` is not given (the
    page passes a live quote when it has one). Fields are empty when there's no
    live snapshot, no in-scope contract, no spot, or the session has closed.
    """
    ts, frame = load_latest_chain(session, symbol)
    ref = ts.date() if ts is not None else date.today()
    if scope_0dte and frame is not None and not frame.empty:
        frame = filter_scope(frame, ZERO_DTE, ref=ref)
    anchor = spot if spot is not None else snapshot_spot(frame)
    grid = session_close_grid(now or eastern_now())
    if frame is None or frame.empty or anchor is None:
        return ts, anchor, grid, pd.DataFrame(), pd.DataFrame()
    gamma = forward_field(frame, anchor, greek="gamma", times=grid)
    charm = forward_field(frame, anchor, greek="charm", times=grid)
    return ts, anchor, grid, gamma, charm
