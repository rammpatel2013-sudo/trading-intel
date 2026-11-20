"""Reference tables (2026-11-19, DB wave A2 step 6): one symbol master + one contract master.

Same ``Base`` as ``models.py``; created by the hand-written migration ``0063_symbol_contract_map``.
symbol_map   one row per symbol in our spelling + every vendor spelling + asset type + optionsdata.ai iid.
             Filled by job ``symbol_map_sync`` from equity_bars / tickers / desk_names / equity_profiles.
contracts    one row per listed option, keyed by the OCC 21-char symbol (exchange standard, derivable
             offline) + optionsdata.ai iid, Polygon/CVForge ticker and dxfeed/Convex symbol. Filled by
             job ``contracts_sync`` from the nightly optionsdata.ai files (no vendor calls).
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import BigInteger, Boolean, Date, DateTime, Float, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from trading_intel.memory.models import Base


class SymbolMap(Base):
    __tablename__ = "symbol_map"

    symbol: Mapped[str] = mapped_column(String(16), primary_key=True)
    asset_type: Mapped[str] = mapped_column(String(8))  # stock | etf | index
    name: Mapped[str | None] = mapped_column(String(160))
    convex: Mapped[str | None] = mapped_column(String(24))
    cvforge: Mapped[str | None] = mapped_column(String(24))
    od: Mapped[str | None] = mapped_column(String(24))
    od_iid: Mapped[int | None] = mapped_column(BigInteger)
    fmp: Mapped[str | None] = mapped_column(String(24))
    finra: Mapped[str | None] = mapped_column(String(24))
    yahoo: Mapped[str | None] = mapped_column(String(24))
    tags: Mapped[str | None] = mapped_column(String(64))       # comma list, e.g. alt_etf_selects (0064)
    trades_24h: Mapped[bool | None] = mapped_column(Boolean)  # trades overnight 24/5 (0064)
    ingested_at: Mapped[datetime | None] = mapped_column(DateTime)


class Contract(Base):
    __tablename__ = "contracts"
    __table_args__ = (
        Index("ix_contracts_und_exp", "underlying", "expiry"),
        Index("ix_contracts_od_iid", "od_iid", unique=True),
    )

    occ: Mapped[str] = mapped_column(String(21), primary_key=True)
    underlying: Mapped[str] = mapped_column(String(16))
    root: Mapped[str] = mapped_column(String(6))
    expiry: Mapped[date] = mapped_column(Date)
    cp: Mapped[str] = mapped_column(String(1))
    strike: Mapped[float] = mapped_column(Float)
    settlement: Mapped[str | None] = mapped_column(String(2))  # am | pm
    exercise: Mapped[str | None] = mapped_column(String(10))  # american | european
    multiplier: Mapped[float | None] = mapped_column(Float)
    od_iid: Mapped[int | None] = mapped_column(BigInteger)
    polygon: Mapped[str | None] = mapped_column(String(32))  # = CVForge ticker
    dxfeed: Mapped[str | None] = mapped_column(String(32))  # = ConvexValue symbol
    first_seen: Mapped[date | None] = mapped_column(Date)
    last_seen: Mapped[date | None] = mapped_column(Date)
    ingested_at: Mapped[datetime | None] = mapped_column(DateTime)


REF_TABLES = (SymbolMap, Contract)
