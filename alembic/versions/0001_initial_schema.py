"""initial schema: 14 tables + pgvector extension

Revision ID: 0001
Revises:
Create Date: 2026-05-19

Creates the full trading-intel schema. After running this migration, the DB
should have all tables ready for Phase 1 (Convex data ingestion).
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Enable pgvector extension
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # 2. tickers
    op.create_table(
        "tickers",
        sa.Column("symbol", sa.String(16), primary_key=True),
        sa.Column("name", sa.String(255)),
        sa.Column("sector", sa.String(64)),
        sa.Column("industry", sa.String(64)),
        sa.Column("gics_id", sa.String(16)),
        sa.Column("is_active", sa.Boolean, server_default=sa.text("true")),
    )

    # 3. quotes_daily
    op.create_table(
        "quotes_daily",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("symbol", sa.String(16), sa.ForeignKey("tickers.symbol"), nullable=False),
        sa.Column("date", sa.Date, nullable=False),
        sa.Column("open", sa.Float, nullable=False),
        sa.Column("high", sa.Float, nullable=False),
        sa.Column("low", sa.Float, nullable=False),
        sa.Column("close", sa.Float, nullable=False),
        sa.Column("volume", sa.BigInteger, nullable=False),
        sa.Column("rv20", sa.Float),
        sa.Column("rv60", sa.Float),
        sa.UniqueConstraint("symbol", "date", name="uq_quotes_daily"),
    )
    op.create_index("ix_quotes_daily_symbol_date", "quotes_daily", ["symbol", "date"])

    # 4. greeks_snapshots (aggregate exposures time series)
    op.create_table(
        "greeks_snapshots",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("symbol", sa.String(16), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("spot", sa.Float),
        sa.Column("gex_total", sa.Float),
        sa.Column("dex_total", sa.Float),
        sa.Column("vex_total", sa.Float),
        sa.Column("chex_total", sa.Float),
        sa.Column("gex_flip", sa.Float),
        sa.Column("gex_rvol_ratio", sa.Float),
        sa.Column("atm_iv", sa.Float),
        sa.Column("source", sa.String(32), server_default="convex", nullable=False),
        sa.UniqueConstraint("symbol", "ts", "source", name="uq_greeks_snap"),
    )
    op.create_index("ix_greeks_snapshots_symbol_ts", "greeks_snapshots", ["symbol", "ts"])

    # 5. greeks_chain (per-strike snapshots)
    op.create_table(
        "greeks_chain",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("symbol", sa.String(16), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expiry", sa.Date, nullable=False),
        sa.Column("strike", sa.Float, nullable=False),
        sa.Column("cp", sa.String(1), nullable=False),
        sa.Column("oi", sa.Integer),
        sa.Column("volume", sa.Integer),
        sa.Column("delta", sa.Float),
        sa.Column("gamma", sa.Float),
        sa.Column("theta", sa.Float),
        sa.Column("vega", sa.Float),
        sa.Column("vanna", sa.Float),
        sa.Column("charm", sa.Float),
        sa.Column("iv", sa.Float),
        sa.Column("gxoi", sa.Float),
        sa.Column("dxoi", sa.Float),
        sa.Column("vxoi", sa.Float),
        sa.Column("cxoi", sa.Float),
    )
    op.create_index("ix_greeks_chain_symbol_ts", "greeks_chain", ["symbol", "ts"])
    op.create_index("ix_greeks_chain_symbol_expiry_strike", "greeks_chain", ["symbol", "expiry", "strike"])

    # 6. flow_buckets (time-bucketed flow from Convex)
    op.create_table(
        "flow_buckets",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("symbol", sa.String(16), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("bucket_min", sa.Integer, nullable=False),
        sa.Column("volm", sa.Float),
        sa.Column("value", sa.Float),
        sa.Column("volmbs", sa.Float),
        sa.Column("valuebs", sa.Float),
        sa.Column("flowratio", sa.Float),
    )
    op.create_index("ix_flow_buckets_symbol_ts", "flow_buckets", ["symbol", "ts"])

    # 7. vix_data
    op.create_table(
        "vix_data",
        sa.Column("date", sa.Date, primary_key=True),
        sa.Column("vix", sa.Float),
        sa.Column("vvix", sa.Float),
        sa.Column("move", sa.Float),
        sa.Column("hy_oas", sa.Float),
        sa.Column("ig_oas", sa.Float),
        sa.Column("vix_sd20", sa.Float),
        sa.Column("vvix_sd20", sa.Float),
        sa.Column("vega_zone", sa.String(16)),
    )

    # 8. earnings_events
    op.create_table(
        "earnings_events",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("symbol", sa.String(16), nullable=False),
        sa.Column("date", sa.Date, nullable=False),
        sa.Column("time", sa.String(8)),
        sa.Column("actual", sa.Float),
        sa.Column("estimate", sa.Float),
        sa.Column("surprise_pct", sa.Float),
        sa.Column("read_through_class", sa.String(32)),
        sa.Column("peer_impacts", sa.JSON),
        sa.UniqueConstraint("symbol", "date", name="uq_earnings_events"),
    )
    op.create_index("ix_earnings_events_date", "earnings_events", ["date"])

    # 9. themes
    op.create_table(
        "themes",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(128), unique=True, nullable=False),
        sa.Column("scope", sa.String(16), nullable=False),
        sa.Column("parent_id", sa.Integer, sa.ForeignKey("themes.id")),
        sa.CheckConstraint("scope IN ('macro', 'sector', 'company')", name="ck_themes_scope"),
    )

    # 10. documents (must exist before theme_observations due to FK)
    op.create_table(
        "documents",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("path", sa.String(512), nullable=False),
        sa.Column("source", sa.String(32)),
        sa.Column("type", sa.String(32)),
        sa.Column("ingested_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("sha256", sa.String(64), unique=True, nullable=False),
        sa.Column("page_count", sa.Integer),
    )

    # 11. theme_observations
    op.create_table(
        "theme_observations",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("theme_id", sa.Integer, sa.ForeignKey("themes.id"), nullable=False),
        sa.Column("symbol", sa.String(16)),
        sa.Column("date", sa.Date, nullable=False),
        sa.Column("sentiment", sa.Float),
        sa.Column("source_doc_id", sa.Integer, sa.ForeignKey("documents.id")),
        sa.Column("quote_text", sa.Text),
        sa.Column("confidence", sa.Float),
    )
    op.create_index("ix_theme_obs_symbol_date", "theme_observations", ["symbol", "date"])

    # 12. chunks (pgvector embedding store) — 768 dims for nomic-embed-text
    op.create_table(
        "chunks",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("document_id", sa.Integer, sa.ForeignKey("documents.id"), nullable=False),
        sa.Column("chunk_idx", sa.Integer, nullable=False),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column("embedding", Vector(768)),
        sa.Column("theme_ids", sa.ARRAY(sa.Integer)),
        sa.Column("symbols", sa.ARRAY(sa.String(16))),
        sa.Column("date", sa.Date),
    )
    # IVFFlat index for cosine similarity search
    op.execute(
        "CREATE INDEX ix_chunks_embedding ON chunks "
        "USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100)"
    )

    # 13. signals
    op.create_table(
        "signals",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("symbol", sa.String(16), nullable=False),
        sa.Column("signal_type", sa.String(64), nullable=False),
        sa.Column("payload", sa.JSON),
        sa.Column("confidence", sa.Float),
    )
    op.create_index("ix_signals_symbol_ts", "signals", ["symbol", "ts"])
    op.create_index("ix_signals_type_ts", "signals", ["signal_type", "ts"])

    # 14. alerts_sent
    op.create_table(
        "alerts_sent",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("signal_id", sa.BigInteger, sa.ForeignKey("signals.id"), nullable=False),
        sa.Column("channel", sa.String(32), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("response_code", sa.Integer),
    )

    # 15. am_summaries
    op.create_table(
        "am_summaries",
        sa.Column("date", sa.Date, primary_key=True),
        sa.Column("markdown", sa.Text, nullable=False),
        sa.Column("metadata", sa.JSON),
        sa.Column("claude_model", sa.String(64)),
        sa.Column("tokens_used", sa.Integer),
    )


def downgrade() -> None:
    op.drop_table("am_summaries")
    op.drop_table("alerts_sent")
    op.drop_index("ix_signals_type_ts", table_name="signals")
    op.drop_index("ix_signals_symbol_ts", table_name="signals")
    op.drop_table("signals")
    op.execute("DROP INDEX IF EXISTS ix_chunks_embedding")
    op.drop_table("chunks")
    op.drop_index("ix_theme_obs_symbol_date", table_name="theme_observations")
    op.drop_table("theme_observations")
    op.drop_table("documents")
    op.drop_table("themes")
    op.execute("DROP TYPE IF EXISTS theme_scope")
    op.drop_index("ix_earnings_events_date", table_name="earnings_events")
    op.drop_table("earnings_events")
    op.drop_table("vix_data")
    op.drop_index("ix_flow_buckets_symbol_ts", table_name="flow_buckets")
    op.drop_table("flow_buckets")
    op.drop_index("ix_greeks_chain_symbol_expiry_strike", table_name="greeks_chain")
    op.drop_index("ix_greeks_chain_symbol_ts", table_name="greeks_chain")
    op.drop_table("greeks_chain")
    op.drop_index("ix_greeks_snapshots_symbol_ts", table_name="greeks_snapshots")
    op.drop_table("greeks_snapshots")
    op.drop_index("ix_quotes_daily_symbol_date", table_name="quotes_daily")
    op.drop_table("quotes_daily")
    op.drop_table("tickers")
    # Don't drop vector extension — may be used by other databases
