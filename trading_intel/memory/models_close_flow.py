"""ORM model for the leveraged-ETF close flow card (2026-11-19).

Same ``Base`` as ``models.py``; created by the hand-written migration ``0061_letf_close_flow`` (never
autogenerate). One row per index group per pass (every 2 min in the last hour before the close).
Derived (rebuildable from ``bars_1m`` + ``letf_shares_snapshots``); descriptor only (rule 4).
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import JSON, Date, DateTime, Float, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from trading_intel.memory.models import Base


class LetfCloseFlow(Base):
    __tablename__ = "letf_close_flow"

    grp: Mapped[str] = mapped_column(String(8), primary_key=True)  # SPX | NDX
    ts: Mapped[datetime] = mapped_column(DateTime, primary_key=True)  # pass time, naive Eastern
    date: Mapped[date] = mapped_column(Date, index=True)
    index_prev: Mapped[float | None] = mapped_column(Float)  # prior close = flip level
    index_last: Mapped[float | None] = mapped_column(Float)
    index_ret: Mapped[float | None] = mapped_column(Float)  # decimal
    rebalance_usd: Mapped[float | None] = mapped_column(Float)  # + buy / - sell
    futures: Mapped[float | None] = mapped_column(Float)  # ES (SPX) or NQ (NDX) contracts, signed
    per_1pct_usd: Mapped[float | None] = mapped_column(Float)  # $ per 1% index move
    aum_usd: Mapped[float | None] = mapped_column(Float)
    direction: Mapped[str | None] = mapped_column(String(8))
    strength: Mapped[str | None] = mapped_column(String(10))
    n_funds: Mapped[int | None] = mapped_column(Integer)
    n_index_fallback: Mapped[int | None] = mapped_column(Integer)
    n_missing: Mapped[int | None] = mapped_column(Integer)
    funds: Mapped[list | None] = mapped_column(JSON)  # per-fund rows
    source: Mapped[str] = mapped_column(String(16), default="fmp_cvforge")
    ingested_at: Mapped[datetime | None] = mapped_column(DateTime)  # eastern_now()
