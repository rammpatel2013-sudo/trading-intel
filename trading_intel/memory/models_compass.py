"""Compass tables (2026-11-20, Macro view). Both DERIVED and rebuildable from owner tables:

auction_daily   one row per name per session from bars_1m (1-minute bars): VWAP, VWAP sigma, volume profile
                (POC / VAH / VAL), initial balance (first 60 min), day type, open vs prior value, poor high / low.
compass_daily   one row per name per session: the Compass read (return, sigma-move vs options, HV10 / HV30,
                IV30 + its 1-year rank, carry, term, skew, Bollinger %B, dark short ratio, $ premium,
                walls / flip) plus the scanner hits of that day (``flags``), so every scanner can be scored later.
Created by the hand-written migration ``0068_compass`` (never autogenerate). Descriptors only (rule 4).
Units: returns / ratios as fractions, vols as decimals (0.25 = 25%), sigma_move in sigmas, premium in USD.
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, Float, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from trading_intel.memory.models import Base

AUCTION_FLOATS = "open high low close vwap vwap_sd poc vah val ib_high ib_low".split()
COMPASS_FLOATS = (
    "close ret1 ret5 ret20 sigma_move hv10 hv30 iv30 iv_rank carry ts_30_90 skew25 skew_rank bb_pctb "
    "dark_ratio dark_z prem_usd prem_x20 call_share gex gex_flip call_wall put_wall"
).split()


class AuctionDaily(Base):
    __tablename__ = "auction_daily"

    symbol: Mapped[str] = mapped_column(String(16), primary_key=True)
    date: Mapped[date] = mapped_column(Date, primary_key=True)
    day_type: Mapped[str | None] = mapped_column(String(12))        # trend / normal / neutral
    open_vs_value: Mapped[str | None] = mapped_column(String(8))    # above / in / below the prior value area
    close_vs_value: Mapped[str | None] = mapped_column(String(8))   # above / in / below today's value area
    poor_high: Mapped[bool | None] = mapped_column(Boolean)
    poor_low: Mapped[bool | None] = mapped_column(Boolean)
    n_minutes: Mapped[int | None] = mapped_column(Integer)
    source: Mapped[str] = mapped_column(String(16), default="bars_1m")
    computed_at: Mapped[datetime | None] = mapped_column(DateTime)  # naive Eastern


class CompassDaily(Base):
    __tablename__ = "compass_daily"

    symbol: Mapped[str] = mapped_column(String(16), primary_key=True)
    date: Mapped[date] = mapped_column(Date, primary_key=True)
    grp: Mapped[str | None] = mapped_column(String(16))          # grid group (Indices, Bonds ...) or 'stock'
    iv_src: Mapped[str | None] = mapped_column(String(16))       # cvforge_bank / convex_eod / hv20
    flags: Mapped[str | None] = mapped_column(String(400))       # comma list of scanner ids hit that day
    computed_at: Mapped[datetime | None] = mapped_column(DateTime)


for _c in AUCTION_FLOATS:
    setattr(AuctionDaily, _c, mapped_column(_c, Float, nullable=True))
for _c in COMPASS_FLOATS:
    setattr(CompassDaily, _c, mapped_column(_c, Float, nullable=True))
