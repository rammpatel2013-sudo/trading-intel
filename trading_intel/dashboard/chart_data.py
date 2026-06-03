"""Chart data loader: per-symbol price + indicators time series.

Joins ``quotes_daily`` (price + realized vol) with ``greeks_snapshots``
(GEX/DEX/ATM IV) by date and computes RSI + the IV-HV spread, for the charting
page. Multiple greeks snapshots per day collapse to the last. Descriptive only -
FlashAlpha rule 4.
"""
from __future__ import annotations

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from trading_intel.memory.models import GreeksSnapshot, QuoteDaily
from trading_intel.prices.technicals import rsi


def list_chart_symbols(session: Session) -> list[str]:
    """Symbols with price or greeks history (union, alphabetical)."""
    qd = set(session.execute(select(QuoteDaily.symbol).distinct()).scalars())
    gs = set(session.execute(select(GreeksSnapshot.symbol).distinct()).scalars())
    return sorted(qd | gs)


def load_ohlc(session: Session, symbol: str, *, days: int = 180) -> pd.DataFrame:
    """Per-symbol daily OHLCV frame, oldest first (the most recent ``days`` rows).

    Columns: ``date, open, high, low, close, volume``. Empty frame when the
    symbol has no stored ``quotes_daily`` history. Descriptive only - rule 4.
    """
    rows = list(
        session.execute(
            select(
                QuoteDaily.date, QuoteDaily.open, QuoteDaily.high,
                QuoteDaily.low, QuoteDaily.close, QuoteDaily.volume,
            )
            .where(QuoteDaily.symbol == symbol)
            .order_by(QuoteDaily.date.desc())
            .limit(days)
        ).all()
    )
    frame = pd.DataFrame(rows, columns=["date", "open", "high", "low", "close", "volume"])
    if frame.empty:
        return frame
    return frame.iloc[::-1].reset_index(drop=True)  # flip to oldest-first


def chart_frame(session: Session, symbol: str, *, rsi_period: int = 14) -> pd.DataFrame:
    """Per-symbol time series: date, close, rv20, rsi, gex, dex, atm_iv, iv_hv.

    Empty frame when neither price nor greeks history exists for ``symbol``.
    """
    prows = session.execute(
        select(QuoteDaily.date, QuoteDaily.close, QuoteDaily.rv20)
        .where(QuoteDaily.symbol == symbol)
        .order_by(QuoteDaily.date)
    ).all()
    price = pd.DataFrame(prows, columns=["date", "close", "rv20"])

    grows = session.execute(
        select(
            GreeksSnapshot.ts, GreeksSnapshot.gex_total, GreeksSnapshot.dex_total,
            GreeksSnapshot.atm_iv, GreeksSnapshot.gex_rvol_ratio,
        )
        .where(GreeksSnapshot.symbol == symbol)
        .order_by(GreeksSnapshot.ts)
    ).all()
    greeks = pd.DataFrame(grows, columns=["ts", "gex", "dex", "atm_iv", "gex_rvol"])
    if not greeks.empty:
        greeks["date"] = pd.to_datetime(greeks["ts"]).dt.date
        greeks = (
            greeks.sort_values("ts").groupby("date", as_index=False).last().drop(columns=["ts"])
        )

    if price.empty and greeks.empty:
        return pd.DataFrame()
    if greeks.empty:
        df = price
    elif price.empty:
        df = greeks
    else:
        df = price.merge(greeks, on="date", how="outer").sort_values("date").reset_index(drop=True)

    if "close" in df.columns:
        df["rsi"] = rsi(df["close"].astype(float), period=rsi_period)
    if {"atm_iv", "rv20"}.issubset(df.columns):
        df["iv_hv"] = (
            pd.to_numeric(df["atm_iv"], errors="coerce")
            - pd.to_numeric(df["rv20"], errors="coerce")
        ) * 100.0
    return df.reset_index(drop=True)
