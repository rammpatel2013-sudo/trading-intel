"""ORM models for the equity / swing side (2026-11-14, build-queue E1-E6).

Kept out of ``models.py`` (already far past the 400-line rule) but on the same ``Base``,
so every table here is created by the hand-written migration
``0057_equity_foundation`` (never autogenerate). Descriptor tables only (rule 4):
nothing here writes to ``signals``.

Tables
------
equity_members         index membership intervals (S&P 500 point-in-time; S&P 400/600 = today's
                       IJH/IJR holdings, survivorship flagged by ``source``)
equity_profiles        sector / industry / exchange per symbol (FMP profile)
equity_bars            daily OHLCV + market cap (close x point-in-time diluted shares)
equity_fundamentals    quarterly statements keyed by period end, with the day they became public
equity_factor_returns  daily factor returns (market, sectors, styles) from the factor risk model
equity_stock_factors   daily style exposures + the stock's own move (residual) per symbol
external_nhnl          NYSE / Nasdaq new highs - new lows from thetrading.tools (ADR-009)
equity_gate_daily      the "safe to buy stocks?" gate (green / amber / red) and its inputs
equity_candidates      daily candidate list (leaders, coiled springs, breakouts)
equity_call_iv         90-180 DTE call IV per shortlisted name (the coiled-spring "call bid" check)
"""

from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import JSON, BigInteger, Boolean, Date, Float, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from trading_intel.memory.models import Base


class EquityMember(Base):
    """One membership interval. ``end_date`` None = still a member."""

    __tablename__ = "equity_members"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(16), index=True)
    index_code: Mapped[str] = mapped_column(String(8))  # SP500 / SP400 / SP600
    start_date: Mapped[date | None] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)
    source: Mapped[str] = mapped_column(String(24))  # fmp_sp500_pit / ijh_today / ijr_today


class EquityProfile(Base):
    __tablename__ = "equity_profiles"

    symbol: Mapped[str] = mapped_column(String(16), primary_key=True)
    name: Mapped[str | None] = mapped_column(String(160))
    sector: Mapped[str | None] = mapped_column(String(48))
    industry: Mapped[str | None] = mapped_column(String(96))
    exchange: Mapped[str | None] = mapped_column(String(24))
    is_etf: Mapped[bool | None] = mapped_column(Boolean)
    is_adr: Mapped[bool | None] = mapped_column(Boolean)
    ipo_date: Mapped[date | None] = mapped_column(Date)
    updated: Mapped[date | None] = mapped_column(Date)


class EquityBar(Base):
    __tablename__ = "equity_bars"

    symbol: Mapped[str] = mapped_column(String(16), primary_key=True)
    date: Mapped[date] = mapped_column(Date, primary_key=True)
    open: Mapped[float | None] = mapped_column(Float)
    high: Mapped[float | None] = mapped_column(Float)
    low: Mapped[float | None] = mapped_column(Float)
    close: Mapped[float] = mapped_column(Float)
    volume: Mapped[int | None] = mapped_column(BigInteger)
    market_cap: Mapped[float | None] = mapped_column(Float)


class EquityFundamental(Base):
    """One fiscal quarter. ``available_date`` = when it became public (no look-ahead)."""

    __tablename__ = "equity_fundamentals"

    symbol: Mapped[str] = mapped_column(String(16), primary_key=True)
    period_end: Mapped[date] = mapped_column(Date, primary_key=True)
    available_date: Mapped[date | None] = mapped_column(Date)
    fiscal_year: Mapped[str | None] = mapped_column(String(8))
    period: Mapped[str | None] = mapped_column(String(4))
    revenue: Mapped[float | None] = mapped_column(Float)
    gross_profit: Mapped[float | None] = mapped_column(Float)
    operating_income: Mapped[float | None] = mapped_column(Float)
    net_income: Mapped[float | None] = mapped_column(Float)
    ebitda: Mapped[float | None] = mapped_column(Float)
    eps_diluted: Mapped[float | None] = mapped_column(Float)
    interest_expense: Mapped[float | None] = mapped_column(Float)
    sbc: Mapped[float | None] = mapped_column(Float)
    operating_cash_flow: Mapped[float | None] = mapped_column(Float)
    capex: Mapped[float | None] = mapped_column(Float)
    free_cash_flow: Mapped[float | None] = mapped_column(Float)
    total_assets: Mapped[float | None] = mapped_column(Float)
    total_equity: Mapped[float | None] = mapped_column(Float)
    total_debt: Mapped[float | None] = mapped_column(Float)
    cash: Mapped[float | None] = mapped_column(Float)
    shares_diluted: Mapped[float | None] = mapped_column(Float)


class EquityFactorReturn(Base):
    __tablename__ = "equity_factor_returns"

    date: Mapped[date] = mapped_column(Date, primary_key=True)
    factor: Mapped[str] = mapped_column(String(48), primary_key=True)
    ret: Mapped[float | None] = mapped_column(Float)
    kind: Mapped[str | None] = mapped_column(String(8))  # market / sector / style
    n_stocks: Mapped[int | None] = mapped_column(Integer)
    r2: Mapped[float | None] = mapped_column(Float)


class EquityStockFactor(Base):
    __tablename__ = "equity_stock_factors"

    date: Mapped[date] = mapped_column(Date, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(16), primary_key=True)
    sector: Mapped[str | None] = mapped_column(String(48))
    momentum: Mapped[float | None] = mapped_column(Float)
    size: Mapped[float | None] = mapped_column(Float)
    value: Mapped[float | None] = mapped_column(Float)
    beta: Mapped[float | None] = mapped_column(Float)
    volatility: Mapped[float | None] = mapped_column(Float)
    quality: Mapped[float | None] = mapped_column(Float)
    growth: Mapped[float | None] = mapped_column(Float)
    ret: Mapped[float | None] = mapped_column(Float)  # the stock's return that day
    resid: Mapped[float | None] = mapped_column(Float)  # its own move (return not explained)


class ExternalNhNl(Base):
    __tablename__ = "external_nhnl"

    date: Mapped[date] = mapped_column(Date, primary_key=True)
    nyse_new_highs: Mapped[int | None] = mapped_column(Integer)
    nyse_new_lows: Mapped[int | None] = mapped_column(Integer)
    nyse_issues: Mapped[int | None] = mapped_column(Integer)
    nasdaq_new_highs: Mapped[int | None] = mapped_column(Integer)
    nasdaq_new_lows: Mapped[int | None] = mapped_column(Integer)
    nasdaq_issues: Mapped[int | None] = mapped_column(Integer)
    new_highs: Mapped[int | None] = mapped_column(Integer)
    new_lows: Mapped[int | None] = mapped_column(Integer)
    total_issues: Mapped[int | None] = mapped_column(Integer)
    spx_new_highs: Mapped[int | None] = mapped_column(Integer)
    spx_new_lows: Mapped[int | None] = mapped_column(Integer)
    ndx_new_highs: Mapped[int | None] = mapped_column(Integer)
    ndx_new_lows: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str | None] = mapped_column(String(16))  # ok / suspect


class EquityGateDaily(Base):
    __tablename__ = "equity_gate_daily"

    date: Mapped[date] = mapped_column(Date, primary_key=True)
    color: Mapped[str | None] = mapped_column(String(8))  # green / amber / red
    score: Mapped[int | None] = mapped_column(Integer)
    trend_note: Mapped[str | None] = mapped_column(String(16))  # improving / worsening / flat
    median_drawdown: Mapped[float | None] = mapped_column(Float)
    pct_down_10: Mapped[float | None] = mapped_column(Float)
    pct_down_20: Mapped[float | None] = mapped_column(Float)
    repair_flow: Mapped[float | None] = mapped_column(Float)
    hidden_bear_gap: Mapped[float | None] = mapped_column(Float)
    pct_above_50: Mapped[float | None] = mapped_column(Float)
    pct_above_200: Mapped[float | None] = mapped_column(Float)
    nhnl_10d: Mapped[float | None] = mapped_column(Float)
    n_stocks: Mapped[int | None] = mapped_column(Integer)
    votes: Mapped[dict[str, Any] | None] = mapped_column(JSON)


class EquityCandidate(Base):
    __tablename__ = "equity_candidates"

    date: Mapped[date] = mapped_column(Date, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(16), primary_key=True)
    list_name: Mapped[str] = mapped_column(
        String(16), primary_key=True
    )  # leader / coiled / breakout
    rank: Mapped[int | None] = mapped_column(Integer)
    score: Mapped[float | None] = mapped_column(Float)
    sector: Mapped[str | None] = mapped_column(String(48))
    close: Mapped[float | None] = mapped_column(Float)
    own_move_6m: Mapped[float | None] = mapped_column(Float)
    own_move_12m: Mapped[float | None] = mapped_column(Float)
    total_move_12m: Mapped[float | None] = mapped_column(Float)
    trend_ok: Mapped[bool | None] = mapped_column(Boolean)
    box_days: Mapped[int | None] = mapped_column(Integer)
    box_width: Mapped[float | None] = mapped_column(Float)
    box_high: Mapped[float | None] = mapped_column(Float)
    details: Mapped[dict[str, Any] | None] = mapped_column(JSON)


class EquityCallIv(Base):
    __tablename__ = "equity_call_iv"

    date: Mapped[date] = mapped_column(Date, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(16), primary_key=True)
    expiry: Mapped[date | None] = mapped_column(Date)
    dte: Mapped[int | None] = mapped_column(Integer)
    spot: Mapped[float | None] = mapped_column(Float)
    iv_atm: Mapped[float | None] = mapped_column(Float)
    iv_call_25d: Mapped[float | None] = mapped_column(Float)
    call_volume: Mapped[float | None] = mapped_column(Float)
    call_oi: Mapped[float | None] = mapped_column(Float)


EQUITY_TABLES = (
    EquityMember,
    EquityProfile,
    EquityBar,
    EquityFundamental,
    EquityFactorReturn,
    EquityStockFactor,
    ExternalNhNl,
    EquityGateDaily,
    EquityCandidate,
    EquityCallIv,
)
