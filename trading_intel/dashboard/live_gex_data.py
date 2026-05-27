"""Pure loader for the LIVE per-strike GEX tier (``live_gex``).

Returns the most recent intraday ``live_gex`` snapshot for a symbol, shaped like
the chain frame the dashboard's ``gex_by_strike`` / ``dex_by_strike`` already
consume (``strike``, ``opt_kind``, ``gxoi``, ``dxoi``, ``spot``). The pages prefer
this over the daily stored snapshot when it is FRESH (within ``max_age_min`` of
now), and fall back to the stored snapshot otherwise — so the GEX/DEX-by-strike
bars track the live tape during the session.

Side-effect-free and unit-testable on in-memory SQLite. Descriptive regime view
only (FlashAlpha rule 4). ``live_gex`` is delta-band filtered (near-the-money), so
this is a near-ATM live profile, not the full chain.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from trading_intel.memory.models import LiveGex
from trading_intel.timeutils import eastern_now

_COLS = ["strike", "opt_kind", "gxoi", "dxoi", "spot", "delta", "gamma", "iv"]


def load_live_chain(
    session: Session,
    symbol: str,
    *,
    max_age_min: int = 15,
    now: datetime | None = None,
) -> tuple[datetime | None, pd.DataFrame]:
    """Latest FRESH ``live_gex`` snapshot for ``symbol`` as a chain-shaped frame.

    Returns ``(ts, frame)`` when the newest snapshot is within ``max_age_min`` of
    ``now`` (default ``eastern_now()``); otherwise ``(None, empty_frame)`` so the
    caller falls back to the daily stored chain.
    """
    latest = session.execute(
        select(func.max(LiveGex.ts)).where(LiveGex.symbol == symbol)
    ).scalar_one_or_none()
    if latest is None:
        return None, pd.DataFrame(columns=_COLS)
    if (now or eastern_now()) - latest > timedelta(minutes=max_age_min):
        return None, pd.DataFrame(columns=_COLS)  # stale -> let the page use the snapshot

    rows = session.execute(
        select(LiveGex).where(LiveGex.symbol == symbol, LiveGex.ts == latest)
    ).scalars().all()
    frame = pd.DataFrame(
        [
            {
                "strike": r.strike,
                "opt_kind": "call" if str(r.cp).upper().startswith("C") else "put",
                "gxoi": r.gxoi,
                "dxoi": r.dxoi,
                "spot": r.spot,
                "delta": r.delta,
                "gamma": r.gamma,
                "iv": r.iv,
            }
            for r in rows
        ]
    )
    return latest, frame


def live_spot(frame: pd.DataFrame) -> float | None:
    """Spot carried on a live-chain frame (first non-null), or ``None``."""
    if frame is None or frame.empty or "spot" not in frame.columns:
        return None
    s = pd.to_numeric(frame["spot"], errors="coerce").dropna()
    return float(s.iloc[0]) if not s.empty else None


def live_gex_symbols(session: Session) -> list[str]:
    """Distinct symbols with stored ``live_gex`` data, alphabetical."""
    rows = session.execute(
        select(LiveGex.symbol).group_by(LiveGex.symbol).order_by(LiveGex.symbol)
    ).scalars()
    return list(rows)
