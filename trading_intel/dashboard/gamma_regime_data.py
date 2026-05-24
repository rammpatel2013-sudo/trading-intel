"""Live gamma-regime loader: latest SPX EOD chain -> a GammaRegime.

Bridges the persisted ``oi_chain_eod`` snapshots to the pure
``greeks.gamma_regime`` classifier. Reads the most recent SPX snapshot, keeps the
near-term expiries (where dealer gamma hedging actually bites), maps the stored
columns to the normalized names the classifier expects, derives a spot proxy from
the ~0.50-delta call strike, and classifies. Unlike the VIX decomposition this
needs only ONE snapshot. Descriptive only - FlashAlpha rule 4.
"""

from __future__ import annotations

from datetime import datetime

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from trading_intel.errors import ComputationError
from trading_intel.greeks.gamma_regime import GammaRegime, classify_gamma_regime
from trading_intel.memory.models import OiChainEod

#: Near-term DTE window for the regime (front gamma dominates dealer hedging).
DEFAULT_MAX_DTE = 45


def latest_spx_gamma_regime(
    session: Session, *, symbol: str = "SPX", max_dte: int = DEFAULT_MAX_DTE
) -> GammaRegime | None:
    """Classify the gamma regime from the latest SPX ``oi_chain_eod`` snapshot.

    Returns ``None`` if there is no snapshot or it cannot be classified.
    """
    ts: datetime | None = session.execute(
        select(OiChainEod.ts)
        .where(OiChainEod.symbol == symbol)
        .order_by(OiChainEod.ts.desc())
        .limit(1)
    ).scalar_one_or_none()
    if ts is None:
        return None

    rows = session.execute(
        select(
            OiChainEod.strike, OiChainEod.cp, OiChainEod.gxoi, OiChainEod.iv,
            OiChainEod.oi, OiChainEod.delta, OiChainEod.dte,
        ).where(
            OiChainEod.symbol == symbol,
            OiChainEod.ts == ts,
            OiChainEod.gxoi.is_not(None),
            OiChainEod.dte <= max_dte,
        )
    ).all()
    if not rows:
        return None

    df = pd.DataFrame(rows, columns=["strike", "cp", "gxoi", "iv", "oi", "delta", "dte"])
    df = df.dropna(subset=["strike", "gxoi"])
    if df.empty:
        return None
    # Map stored names to the classifier's normalized schema. IV stays decimal
    # (the flip's Black-Scholes gamma needs decimal vol, not vol points).
    df["opt_kind"] = df["cp"]
    df["expiration"] = df["dte"]  # plain days-to-expiry; gex_flip handles this

    # Spot proxy: the call strike nearest 0.50 delta (ATM-forward).
    calls = df[df["cp"].astype(str).str.upper().str[0] == "C"].dropna(subset=["delta"])
    anchor = calls if not calls.empty else df.dropna(subset=["delta"])
    if anchor.empty:
        return None
    idx = (anchor["delta"].abs() - 0.50).abs().idxmin()
    spot = float(anchor.loc[idx, "strike"])

    try:
        return classify_gamma_regime(df, spot)
    except ComputationError:
        return None
