"""ETF board table (2026-11-19). Derived + rebuildable from equity_bars, bars_1m, flow_und_daily.
Created by the hand-written migration ``0064_etf_board`` (never autogenerate). Column meanings:
``trading_intel/etf_board/calc.py``. % columns are fractions (chg_*) or percent points (mo1, mo2, price_vol);
IVs as served by Convex und (decimal)."""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, Float, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from trading_intel.memory.models import Base

FLOAT_COLS = (
    "last prev_close chg_1d chg_5d open high low vwap chg_open volume relvol buy_usd sell_usd bsr strength "
    "building price_vol stretch mo1 mo2 corr10 corr20 call_vol put_vol pc_ratio iv iv_front iv_back iv_diff "
    "iv_chg_1d iv_chg_5d"
).split()


class EtfBoardDaily(Base):
    __tablename__ = "etf_board_daily"

    date: Mapped[date] = mapped_column(Date, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(16), primary_key=True)
    mo3: Mapped[int | None] = mapped_column(Integer)
    n_minutes: Mapped[int | None] = mapped_column(Integer)
    trades_24h: Mapped[bool | None] = mapped_column(Boolean)
    computed_at: Mapped[datetime | None] = mapped_column(DateTime)


for _c in FLOAT_COLS:
    setattr(EtfBoardDaily, _c, mapped_column(_c, Float, nullable=True))
