"""IV-HV spread screener: rank symbols by implied minus realized vol (30/60d).

Pure ranking + a loader that pulls per-symbol ATM IV (interpolated to 30/60 DTE
from the stored ``oi_chain_eod`` surface) and realized vol (``quotes_daily``
rv20/rv60). Positive spread = options priced richer than the name actually moves
(premium-selling edge / IVAR); negative = cheap (long-vol). Descriptive screen -
FlashAlpha rule 4, not a signal. (rv20 ~= 1-month HV, rv60 ~= 3-month - the
closest stored realized-vol windows; exact 30/60-calendar HV is a later tweak.)
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from trading_intel.errors import ComputationError
from trading_intel.greeks.surface import build_delta_surface
from trading_intel.memory.models import OiChainEod, QuoteDaily

_COLS = ["symbol", "iv30", "hv30", "spread30", "iv60", "hv60", "spread60", "label"]
RICH_THRESH = 0.03  # >= +3 vol pts (decimal) => rich
CHEAP_THRESH = -0.03


def _label(spread30: float | None) -> str:
    if spread30 is None or pd.isna(spread30):
        return "n/a"
    if spread30 >= RICH_THRESH:
        return "rich (sell-vol)"
    if spread30 <= CHEAP_THRESH:
        return "cheap (buy-vol)"
    return "fair"


def rank_iv_hv(rows: list[dict]) -> pd.DataFrame:
    """Rank rows (``symbol/iv30/hv30/iv60/hv60`` as decimals) by 30d spread desc."""
    if not rows:
        return pd.DataFrame(columns=_COLS)
    df = pd.DataFrame(rows)
    df["spread30"] = df["iv30"] - df["hv30"]
    df["spread60"] = df["iv60"] - df["hv60"]
    df["label"] = df["spread30"].map(_label)
    return df.sort_values("spread30", ascending=False, na_position="last").reset_index(drop=True)[_COLS]


def _atm_iv_interp(chain: pd.DataFrame, dte_target: float) -> float | None:
    """ATM IV (decimal) interpolated to ``dte_target`` from the delta surface."""
    try:
        ds = build_delta_surface(chain)
    except ComputationError:
        return None
    if ds.n_expiries == 0:
        return None
    return float(np.interp(dte_target, ds.dte, ds.atm_iv))


def _latest_iv_chain(session: Session, symbol: str) -> pd.DataFrame | None:
    ts = session.execute(
        select(OiChainEod.ts)
        .where(OiChainEod.symbol == symbol)
        .order_by(OiChainEod.ts.desc())
        .limit(1)
    ).scalar_one_or_none()
    if ts is None:
        return None
    rows = session.execute(
        select(OiChainEod.cp, OiChainEod.iv, OiChainEod.delta, OiChainEod.expiry).where(
            OiChainEod.symbol == symbol, OiChainEod.ts == ts, OiChainEod.iv.is_not(None)
        )
    ).all()
    if not rows:
        return None
    df = pd.DataFrame(rows, columns=["cp", "iv", "delta", "expiry"]).dropna(subset=["iv", "expiry"])
    if df.empty:
        return None
    df["opt_kind"] = df["cp"]
    df["expiration"] = pd.to_datetime(df["expiry"])
    return df


def _latest_hv(session: Session, symbol: str) -> tuple[float | None, float | None]:
    row = session.execute(
        select(QuoteDaily.rv20, QuoteDaily.rv60)
        .where(QuoteDaily.symbol == symbol, QuoteDaily.rv20.is_not(None))
        .order_by(QuoteDaily.date.desc())
        .limit(1)
    ).first()
    if row is None:
        return None, None
    hv30 = float(row[0]) if row[0] is not None else None
    hv60 = float(row[1]) if row[1] is not None else None
    return hv30, hv60


def iv_hv_table(session: Session, symbols: list[str]) -> pd.DataFrame:
    """Build + rank the IV-HV table for ``symbols`` (skips those lacking data)."""
    rows: list[dict] = []
    for sym in symbols:
        chain = _latest_iv_chain(session, sym)
        if chain is None:
            continue
        iv30, iv60 = _atm_iv_interp(chain, 30.0), _atm_iv_interp(chain, 60.0)
        if iv30 is None and iv60 is None:
            continue
        hv30, hv60 = _latest_hv(session, sym)
        rows.append({"symbol": sym, "iv30": iv30, "hv30": hv30, "iv60": iv60, "hv60": hv60})
    return rank_iv_hv(rows)
