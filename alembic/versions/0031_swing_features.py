"""swing_features: daily per-name Stage-1 feature snapshots + trailing percentiles

Revision ID: 0031
Revises: 0030
Create Date: 2026-07-15

Adds the ``swing_features`` table: one row per (symbol, trading-day) banking the
Stage-1 swing feature vector (spot, ATM IV, RV20, IV/RV, RSI14, SMA50, price-vs-
SMA50, 25d skew, net GEX/DEX) plus the trailing-252d percentiles (IV-rank, IV/RV,
skew, GEX, DEX) the Stage-2 fitted model reads. Unique on (symbol, ts) for
idempotent daily upserts (CLAUDE.md rule 5). Fed by ``scripts/swing_features.py``
from CVForge (ADR-004); descriptive only (rule 4).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0031"
down_revision: str | None = "0030"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "swing_features"
_UQ = "uq_swing_features"
_IX = "ix_swing_features_symbol_ts"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "symbol",
            sa.String(length=16),
            sa.ForeignKey("tickers.symbol"),
            nullable=False,
        ),
        sa.Column("ts", sa.Date(), nullable=False),
        # Raw daily features (decimals unless noted).
        sa.Column("spot", sa.Float(), nullable=True),
        sa.Column("atm_iv", sa.Float(), nullable=True),
        sa.Column("rv20", sa.Float(), nullable=True),
        sa.Column("iv_rv", sa.Float(), nullable=True),  # atm_iv / rv20
        sa.Column("rsi14", sa.Float(), nullable=True),
        sa.Column("sma50", sa.Float(), nullable=True),
        sa.Column("px_vs_sma50", sa.Float(), nullable=True),  # spot / sma50 - 1
        sa.Column("skew_25d", sa.Float(), nullable=True),  # 25d put_iv - call_iv
        sa.Column("gex", sa.Float(), nullable=True),
        sa.Column("dex", sa.Float(), nullable=True),
        # Trailing-252d percentiles (0..1).
        sa.Column("atm_iv_rank_252d", sa.Float(), nullable=True),
        sa.Column("iv_rv_pctile_252d", sa.Float(), nullable=True),
        sa.Column("skew_pctile_252d", sa.Float(), nullable=True),
        sa.Column("gex_pctile_252d", sa.Float(), nullable=True),
        sa.Column("dex_pctile_252d", sa.Float(), nullable=True),
        sa.UniqueConstraint("symbol", "ts", name=_UQ),
    )
    op.create_index(_IX, _TABLE, ["symbol", "ts"])


def downgrade() -> None:
    op.drop_index(_IX, table_name=_TABLE)
    op.drop_table(_TABLE)
