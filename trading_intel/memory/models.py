"""SQLAlchemy ORM models for trading-intel.

This is the source of truth for the database schema. Every table here
must be created via an Alembic migration — never directly.

When adding a new model:
1. Add the class here
2. Generate a migration: `alembic revision --autogenerate -m "add foo table"`
3. Review and edit the generated migration
4. Apply: `alembic upgrade head`
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    ARRAY,
    JSON,
    Boolean,
    Date,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Root of the ORM hierarchy."""


# ── Tickers and market data ────────────────────────────────────────────


class Ticker(Base):
    __tablename__ = "tickers"

    symbol: Mapped[str] = mapped_column(String(16), primary_key=True)
    name: Mapped[str | None] = mapped_column(String(255))
    sector: Mapped[str | None] = mapped_column(String(64))
    industry: Mapped[str | None] = mapped_column(String(64))
    gics_id: Mapped[str | None] = mapped_column(String(16))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class QuoteDaily(Base):
    __tablename__ = "quotes_daily"
    __table_args__ = (UniqueConstraint("symbol", "date", name="uq_quotes_daily"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(16), ForeignKey("tickers.symbol"))
    date: Mapped[date] = mapped_column(Date)
    open: Mapped[float] = mapped_column(Float)
    high: Mapped[float] = mapped_column(Float)
    low: Mapped[float] = mapped_column(Float)
    close: Mapped[float] = mapped_column(Float)
    volume: Mapped[int] = mapped_column(Integer)
    rv20: Mapped[float | None] = mapped_column(Float)
    rv60: Mapped[float | None] = mapped_column(Float)


# ── Greeks ─────────────────────────────────────────────────────────────


class GreeksSnapshot(Base):
    """Aggregate per-ticker GEX/DEX/VEX/CHEX time series."""

    __tablename__ = "greeks_snapshots"
    __table_args__ = (UniqueConstraint("symbol", "ts", "source", name="uq_greeks_snap"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(16))
    ts: Mapped[datetime] = mapped_column(DateTime)
    spot: Mapped[float | None] = mapped_column(Float)
    gex_total: Mapped[float | None] = mapped_column(Float)
    dex_total: Mapped[float | None] = mapped_column(Float)
    vex_total: Mapped[float | None] = mapped_column(Float)
    chex_total: Mapped[float | None] = mapped_column(Float)
    gex_flip: Mapped[float | None] = mapped_column(Float)
    gex_rvol_ratio: Mapped[float | None] = mapped_column(Float)
    atm_iv: Mapped[float | None] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(32), default="convex")  # convex, schwab_legacy, etc.


class GreeksChain(Base):
    """Per-strike snapshot. Heavier — 1/day + key intraday strikes."""

    __tablename__ = "greeks_chain"
    __table_args__ = (
        UniqueConstraint(
            "symbol", "ts", "source", "expiry", "strike", "cp", name="uq_greeks_chain"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(16))
    ts: Mapped[datetime] = mapped_column(DateTime)
    expiry: Mapped[date] = mapped_column(Date)
    strike: Mapped[float] = mapped_column(Float)
    cp: Mapped[str] = mapped_column(String(1))  # 'C' or 'P'
    oi: Mapped[int | None] = mapped_column(Integer)
    volume: Mapped[int | None] = mapped_column(Integer)
    delta: Mapped[float | None] = mapped_column(Float)
    gamma: Mapped[float | None] = mapped_column(Float)
    theta: Mapped[float | None] = mapped_column(Float)
    vega: Mapped[float | None] = mapped_column(Float)
    vanna: Mapped[float | None] = mapped_column(Float)
    charm: Mapped[float | None] = mapped_column(Float)
    iv: Mapped[float | None] = mapped_column(Float)
    gxoi: Mapped[float | None] = mapped_column(Float)
    dxoi: Mapped[float | None] = mapped_column(Float)
    vxoi: Mapped[float | None] = mapped_column(Float)
    cxoi: Mapped[float | None] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(32), default="convex")


class FlowBucket(Base):
    """Convex time-bucketed flow data."""

    __tablename__ = "flow_buckets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(16))
    ts: Mapped[datetime] = mapped_column(DateTime)
    bucket_min: Mapped[int] = mapped_column(Integer)  # 5, 15, 30
    volm: Mapped[float | None] = mapped_column(Float)
    value: Mapped[float | None] = mapped_column(Float)
    volmbs: Mapped[float | None] = mapped_column(Float)
    valuebs: Mapped[float | None] = mapped_column(Float)
    flowratio: Mapped[float | None] = mapped_column(Float)


class GexRolling(Base):
    """Long-dated (rolling) total GEX per symbol — EOD cadence.

    ``gex_total`` is net signed gxoi (calls +, puts -) summed across every
    expiration within ``window_days`` (default ~180 / 6 months). Tracks
    directional positioning drift over time. Paired with per-expiration detail
    in ``gex_term``.
    """

    __tablename__ = "gex_rolling"
    __table_args__ = (UniqueConstraint("symbol", "ts", "source", name="uq_gex_rolling"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(16))
    ts: Mapped[datetime] = mapped_column(DateTime)
    spot: Mapped[float | None] = mapped_column(Float)
    window_days: Mapped[int] = mapped_column(Integer)
    gex_total: Mapped[float | None] = mapped_column(Float)  # net signed gxoi over window
    n_expirations: Mapped[int | None] = mapped_column(Integer)
    source: Mapped[str] = mapped_column(String(32), default="convex")


class GexTerm(Base):
    """Per-expiration net gxoi (term structure) for a rolling snapshot.

    One row per expiration; ties back to a ``gex_rolling`` row by the natural
    key (symbol, ts, source).
    """

    __tablename__ = "gex_term"
    __table_args__ = (UniqueConstraint("symbol", "ts", "source", "expiration", name="uq_gex_term"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(16))
    ts: Mapped[datetime] = mapped_column(DateTime)
    expiration: Mapped[date] = mapped_column(Date)
    dte: Mapped[int | None] = mapped_column(Integer)
    gex: Mapped[float | None] = mapped_column(Float)  # net signed gxoi for this expiration
    source: Mapped[str] = mapped_column(String(32), default="convex")


# ── VIX / macro ────────────────────────────────────────────────────────


class VixData(Base):
    __tablename__ = "vix_data"

    date: Mapped[date] = mapped_column(Date, primary_key=True)
    vix: Mapped[float | None] = mapped_column(Float)
    vvix: Mapped[float | None] = mapped_column(Float)
    move: Mapped[float | None] = mapped_column(Float)
    hy_oas: Mapped[float | None] = mapped_column(Float)
    ig_oas: Mapped[float | None] = mapped_column(Float)
    vix_sd20: Mapped[float | None] = mapped_column(Float)
    vvix_sd20: Mapped[float | None] = mapped_column(Float)
    vega_zone: Mapped[str | None] = mapped_column(String(16))  # low/mid/high


# ── Earnings ───────────────────────────────────────────────────────────


class EarningsEvent(Base):
    __tablename__ = "earnings_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(16))
    date: Mapped[date] = mapped_column(Date)
    time: Mapped[str | None] = mapped_column(String(8))  # 'BMO' or 'AMC'
    actual: Mapped[float | None] = mapped_column(Float)
    estimate: Mapped[float | None] = mapped_column(Float)
    surprise_pct: Mapped[float | None] = mapped_column(Float)
    read_through_class: Mapped[str | None] = mapped_column(String(32))
    peer_impacts: Mapped[dict[str, Any] | None] = mapped_column(JSON)


# ── Macro themes (Layer 1: pgvector) ──────────────────────────────────


class Theme(Base):
    __tablename__ = "themes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), unique=True)
    scope: Mapped[str] = mapped_column(Enum("macro", "sector", "company", name="theme_scope"))
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("themes.id"))


class ThemeObservation(Base):
    __tablename__ = "theme_observations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    theme_id: Mapped[int] = mapped_column(ForeignKey("themes.id"))
    symbol: Mapped[str | None] = mapped_column(String(16))
    date: Mapped[date] = mapped_column(Date)
    sentiment: Mapped[float | None] = mapped_column(Float)  # -1.0 to 1.0
    source_doc_id: Mapped[int | None] = mapped_column(ForeignKey("documents.id"))
    quote_text: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[float | None] = mapped_column(Float)


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    path: Mapped[str] = mapped_column(String(512))
    source: Mapped[str | None] = mapped_column(String(32))  # internal, broker, sec, etc.
    type: Mapped[str | None] = mapped_column(String(32))  # pdf, transcript, etc.
    kind: Mapped[str] = mapped_column(
        String(16), server_default="methodology", default="methodology", nullable=False
    )  # methodology (LLM reasoning) | research (company/theme RAG)
    ingested_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    sha256: Mapped[str] = mapped_column(String(64), unique=True)
    page_count: Mapped[int | None] = mapped_column(Integer)


# Note: Chunk model with pgvector requires the pgvector extension to be
# installed in Postgres. The Vector column type is imported lazily.
class Chunk(Base):
    __tablename__ = "chunks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id"))
    chunk_idx: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    # embedding column added via migration using pgvector's Vector type
    theme_ids: Mapped[list[int] | None] = mapped_column(ARRAY(Integer))
    symbols: Mapped[list[str] | None] = mapped_column(ARRAY(String(16)))
    date: Mapped[date | None] = mapped_column(Date)


# ── Signals + alerts ───────────────────────────────────────────────────


class Signal(Base):
    """All triggered signals. Only `strategies/*` modules write to this table."""

    __tablename__ = "signals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(DateTime)
    symbol: Mapped[str] = mapped_column(String(16))
    signal_type: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    confidence: Mapped[float | None] = mapped_column(Float)


class AlertSent(Base):
    __tablename__ = "alerts_sent"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    signal_id: Mapped[int] = mapped_column(ForeignKey("signals.id"))
    channel: Mapped[str] = mapped_column(String(32))
    sent_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    response_code: Mapped[int | None] = mapped_column(Integer)


# ── AM summaries ──────────────────────────────────────────────────────


class AmSummary(Base):
    __tablename__ = "am_summaries"

    date: Mapped[date] = mapped_column(Date, primary_key=True)
    markdown: Mapped[str] = mapped_column(Text)
    metadata_json: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSON)
    claude_model: Mapped[str | None] = mapped_column(String(64))
    tokens_used: Mapped[int | None] = mapped_column(Integer)
