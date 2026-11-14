"""ORM models for the desk brief (2026-11-14; nextSignals "Equity Desk Brief" rebuilt on our data).

Same ``Base`` as ``models.py``; every table is created by the hand-written migration
``0058_desk_brief`` (never autogenerate). Descriptor tables only (rule 4): nothing here writes
to ``signals``. Method: project doc ``claude/desk-brief-methodology-20261114.md``.

Tables
------
finra_short_volume  FINRA Reg SHO daily file, every symbol (short / exempt / total volume)
desk_und_daily      Convex ``und`` screener at ~17:15 ET: option volume + its buy/sell split
                    (the split resets ~18:00 ET, so this is the only capture of it)
desk_gamma_daily    short-dated and total dealer gamma per underlying (CVForge snapshot, naive sign)
desk_bars           daily OHLCV for names outside the S&P 1500 equity tables
desk_names          name / sector / industry / ETF flag / average volume (FMP company screener)
desk_picks          picks per brief date: ours and TailThatWagsDog's (paper track), by role
desk_brief_daily    the computed brief (JSON) behind the page and the MCP tool
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import JSON, BigInteger, Boolean, Date, DateTime, Float, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from trading_intel.memory.models import Base


class FinraShortVolume(Base):
    __tablename__ = "finra_short_volume"

    date: Mapped[date] = mapped_column(Date, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(16), primary_key=True, index=True)
    short_volume: Mapped[float | None] = mapped_column(Float)
    short_exempt: Mapped[float | None] = mapped_column(Float)
    total_volume: Mapped[float | None] = mapped_column(Float)


class DeskUndDaily(Base):
    __tablename__ = "desk_und_daily"

    date: Mapped[date] = mapped_column(Date, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(16), primary_key=True)
    price: Mapped[float | None] = mapped_column(Float)
    change: Mapped[float | None] = mapped_column(Float)
    option_volume: Mapped[float | None] = mapped_column(Float)
    call_volume: Mapped[float | None] = mapped_column(Float)
    put_volume: Mapped[float | None] = mapped_column(Float)
    call_buy: Mapped[float | None] = mapped_column(Float)
    call_sell: Mapped[float | None] = mapped_column(Float)
    put_buy: Mapped[float | None] = mapped_column(Float)
    put_sell: Mapped[float | None] = mapped_column(Float)
    gxoi: Mapped[float | None] = mapped_column(Float)
    iv: Mapped[float | None] = mapped_column(Float)
    captured_at: Mapped[datetime | None] = mapped_column(DateTime)


class DeskGammaDaily(Base):
    __tablename__ = "desk_gamma_daily"

    date: Mapped[date] = mapped_column(Date, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(16), primary_key=True)
    spot: Mapped[float | None] = mapped_column(Float)
    st_gex: Mapped[float | None] = mapped_column(Float)
    total_gex: Mapped[float | None] = mapped_column(Float)
    st_days: Mapped[int | None] = mapped_column(Integer)


class DeskBar(Base):
    __tablename__ = "desk_bars"

    symbol: Mapped[str] = mapped_column(String(16), primary_key=True)
    date: Mapped[date] = mapped_column(Date, primary_key=True, index=True)
    open: Mapped[float | None] = mapped_column(Float)
    high: Mapped[float | None] = mapped_column(Float)
    low: Mapped[float | None] = mapped_column(Float)
    close: Mapped[float] = mapped_column(Float)
    volume: Mapped[int | None] = mapped_column(BigInteger)


class DeskName(Base):
    __tablename__ = "desk_names"

    symbol: Mapped[str] = mapped_column(String(16), primary_key=True)
    name: Mapped[str | None] = mapped_column(String(160))
    sector: Mapped[str | None] = mapped_column(String(48))
    industry: Mapped[str | None] = mapped_column(String(96))
    is_etf: Mapped[bool | None] = mapped_column(Boolean)
    avg_volume: Mapped[float | None] = mapped_column(Float)
    price: Mapped[float | None] = mapped_column(Float)
    market_cap: Mapped[float | None] = mapped_column(Float)
    beta: Mapped[float | None] = mapped_column(Float)
    updated: Mapped[date | None] = mapped_column(Date)


class DeskPick(Base):
    __tablename__ = "desk_picks"

    date: Mapped[date] = mapped_column(Date, primary_key=True)  # session the brief covers
    source: Mapped[str] = mapped_column(String(8), primary_key=True)  # ours / ttwd
    symbol: Mapped[str] = mapped_column(String(16), primary_key=True)
    role: Mapped[str] = mapped_column(String(8), primary_key=True)  # top/two_leg/div_a/div_b/screen
    info: Mapped[Any] = mapped_column(JSON, nullable=True)


class DeskBriefDaily(Base):
    __tablename__ = "desk_brief_daily"

    date: Mapped[date] = mapped_column(Date, primary_key=True)
    n_screen: Mapped[int | None] = mapped_column(Integer)
    top_pick: Mapped[str | None] = mapped_column(String(16))
    payload: Mapped[Any] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(DateTime)


DESK_TABLES = (
    FinraShortVolume,
    DeskUndDaily,
    DeskGammaDaily,
    DeskBar,
    DeskName,
    DeskPick,
    DeskBriefDaily,
)
