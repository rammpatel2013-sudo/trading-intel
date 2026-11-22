"""Position tables (2026-11-21). DERIVED and rebuildable from tas_prints + the nightly optionsdata.ai OI files.

pos_packages   one row per SPX spread package EVENT per session (vertical / calendar; a roll = two linked rows):
               legs (dear / cheap), lots, net price, side decided once per package (+1 bought = long the dear leg,
               -1 sold), action from the next-session OI change (open / close / roll_close / roll_open / unknown)
               and ``roll_key`` linking the two halves of a roll (the holder carried to the new expiry).
               See trading_intel/positions/packages.py for the method. Created by migration 0069_pos_packages.
decay_line_marks  one row per session x mark (10:00 / 13:00 / 15:50) x variant (N / W / L): where decay of the
               week's expiring SPX options flips dealers buy <-> sell, the hedge at spot, and the score (did SPX
               move the hedge's way into the close). Background test before any MAP change (2026-11-21,
               trading_intel/market/decay_line.py). Created by migration 0071_decay_line_marks.
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, Float, Integer, String, UniqueConstraint
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


class DecayLineMark(Base):
    __tablename__ = "decay_line_marks"
    __table_args__ = (UniqueConstraint("trade_date", "mark", "variant", name="uq_decay_line_marks"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    trade_date: Mapped[date] = mapped_column(Date, index=True)
    mark: Mapped[str] = mapped_column(String(5))           # "10:00" / "13:00" / "15:50" Eastern
    variant: Mapped[str] = mapped_column(String(1))        # N / W / L
    spot: Mapped[float | None] = mapped_column(Float)      # SPX at the mark (tape)
    close: Mapped[float | None] = mapped_column(Float)     # official close (equity_bars)
    es_at_spot: Mapped[float | None] = mapped_column(Float)  # dealer decay hedge at spot, ES (+ buy)
    flip_below: Mapped[float | None] = mapped_column(Float)  # nearest sign change below spot
    flip_above: Mapped[float | None] = mapped_column(Float)  # nearest sign change above spot
    flips: Mapped[str | None] = mapped_column(String(200))   # up to 6 nearest sign changes, comma list
    move: Mapped[float | None] = mapped_column(Float)      # close - spot
    hit: Mapped[bool | None] = mapped_column(Boolean)      # sign(es_at_spot) == sign(move)
    ingested_at: Mapped[datetime | None] = mapped_column(DateTime)
