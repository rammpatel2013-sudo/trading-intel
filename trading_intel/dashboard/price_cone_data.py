"""Pure data-prep for the forward price-cone page.

Loads a symbol's daily closes, forecasts annualized vol with the HAR-RV model
(``prices.forecast_vol``, EWMA fallback for short samples), and builds the
forward lognormal cone (``prices.price_cone``). Side-effect-free; descriptive
regime view, not a signal (rule 4).
"""
from __future__ import annotations

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from trading_intel.memory.models import QuoteDaily
from trading_intel.prices.forecast_vol import forecast_vol
from trading_intel.prices.price_cone import forward_cone


def load_close_series(session: Session, symbol: str) -> pd.Series:
    """Daily closes for ``symbol`` (date-indexed, ascending), NaNs dropped."""
    rows = session.execute(
        select(QuoteDaily.date, QuoteDaily.close)
        .where(QuoteDaily.symbol == symbol)
        .order_by(QuoteDaily.date)
    ).all()
    if not rows:
        return pd.Series(dtype=float)
    df = pd.DataFrame(rows, columns=["date", "close"])
    series = pd.to_numeric(df["close"], errors="coerce")
    series.index = pd.to_datetime(df["date"])
    return series.dropna()


def forecast_ann_vol(close: pd.Series, *, horizon_dte: int = 30) -> float | None:
    """Annualized vol forecast at ``horizon_dte`` — HAR-RV, EWMA fallback."""
    if close is None or close.empty:
        return None
    fc = forecast_vol(close, horizons=(horizon_dte,)).get(horizon_dte)
    if fc is None:
        return None
    return fc.har_rv if fc.har_rv is not None else fc.ewma_rv


def build_cone(
    session: Session,
    symbol: str,
    *,
    spot: float | None = None,
    horizon_days: int = 21,
    horizon_dte: int = 30,
) -> tuple[float | None, float | None, pd.DataFrame]:
    """Return ``(ann_vol, anchor_spot, cone_df)`` for ``symbol``.

    ``anchor_spot`` defaults to the last close when ``spot`` is not supplied (the
    page passes a live quote when it has one). Cone is empty if vol/spot missing.
    """
    close = load_close_series(session, symbol)
    ann_vol = forecast_ann_vol(close, horizon_dte=horizon_dte)
    anchor = spot if spot is not None else (
        float(close.iloc[-1]) if not close.empty else None
    )
    cone = forward_cone(anchor, ann_vol, horizon_days=horizon_days)
    return ann_vol, anchor, cone
