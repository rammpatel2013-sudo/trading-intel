"""Position tables (2026-11-21). DERIVED and rebuildable from tas_prints + the nightly optionsdata.ai OI files.

pos_packages   one row per SPX spread package EVENT per session (vertical / calendar; a roll = two linked rows):
               legs (dear / cheap), lots, net price, side decided once per package (+1 bought = long the dear leg,
               -1 sold), action from the next-session OI change (open / close / roll_close / roll_open / unknown)
               and ``roll_key`` linking the two halves of a roll (the holder carried to the new expiry).
               See trading_intel/positions/packages.py for the method. Created by migration 0069_pos_packages.
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Date, DateTime, Float, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from trading_intel.memory.models import Base


class PosPackage(Base):
    __tablename__ = "pos_packages"
    __table_args__ = (UniqueConstraint("symbol", "trade_date", "pkg_key", name="uq_pos_packages"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(8), default="SPX")
    trade_date: Mapped[date] = mapped_column(Date, index=True)
    pkg_key: Mapped[str] = mapped_column(String(96))              # structure|dear leg|cheap leg|first fill HHMMSS
    structure: Mapped[str] = mapped_column(String(12))            # vertical / calendar
    cp: Mapped[str] = mapped_column(String(1))
    dear_root: Mapped[str] = mapped_column(String(8))
    dear_expiry: Mapped[date] = mapped_column(Date, index=True)
    dear_strike: Mapped[float] = mapped_column(Float)
    cheap_root: Mapped[str] = mapped_column(String(8))
    cheap_expiry: Mapped[date] = mapped_column(Date)
    cheap_strike: Mapped[float] = mapped_column(Float)
    first_ts: Mapped[datetime] = mapped_column(DateTime)          # naive Eastern
    last_ts: Mapped[datetime] = mapped_column(DateTime)
    lots: Mapped[int] = mapped_column(Integer)
    fills: Mapped[int] = mapped_column(Integer)
    net: Mapped[float | None] = mapped_column(Float)              # dear minus cheap, per package, lots-weighted
    side: Mapped[int] = mapped_column(Integer)                    # +1 bought, -1 sold, 0 undecided (customer)
    vote: Mapped[float] = mapped_column(Float)
    action: Mapped[str] = mapped_column(String(12))               # open / close / roll_close / roll_open / unknown
    oi_score: Mapped[float | None] = mapped_column(Float)         # mean clip(dOI / lots) over the legs
    roll_key: Mapped[str | None] = mapped_column(String(96))      # the other half of a roll
    source: Mapped[str] = mapped_column(String(16), default="convex_tape")
    ingested_at: Mapped[datetime | None] = mapped_column(DateTime)  # naive Eastern
