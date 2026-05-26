"""vol_richness: daily IV-vs-forward-RV (VRP) richness scan per symbol/horizon

Revision ID: 0013
Revises: 0012
Create Date: 2026-05-26

Adds the ``vol_richness`` table: one row per (symbol, trading-day, horizon_dte)
holding the ATM IV, the HAR forward-RV forecast, the variance-risk-premium
(``vrp_pts``) and its standardization to the name's own trailing history
(``vrp_pctile`` / ``iv_rank``), the term-structure slope + 25-delta skew context,
the VEGA/VIX ``regime_zone`` and the gated descriptive ``label``. Unique on the
natural key for idempotent EOD upserts (CLAUDE.md rule 5).

**UN-PRUNED** by design: this table is the long IV/VRP percentile baseline the
standardization reads back (``oi_chain_eod`` retains only 90d, so it cannot serve
that role). Regime-descriptor data only (FlashAlpha rule 4).
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "vol_richness"
_UQ = "uq_vol_richness"
_IX = "ix_vol_richness_symbol_ts"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("symbol", sa.String(length=16), sa.ForeignKey("tickers.symbol"), nullable=False),
        sa.Column("ts", sa.Date(), nullable=False),
        sa.Column("horizon_dte", sa.Integer(), nullable=False),
        sa.Column("iv_atm", sa.Float(), nullable=True),
        sa.Column("fcst_rv", sa.Float(), nullable=True),
        sa.Column("vrp_pts", sa.Float(), nullable=True),
        sa.Column("vrp_pctile", sa.Float(), nullable=True),
        sa.Column("iv_rank", sa.Float(), nullable=True),
        sa.Column("term_slope", sa.Float(), nullable=True),
        sa.Column("skew_25d", sa.Float(), nullable=True),
        sa.Column("regime_zone", sa.String(length=16), nullable=True),
        sa.Column("richness_score", sa.Float(), nullable=True),
        sa.Column("label", sa.String(length=64), nullable=True),
        sa.UniqueConstraint("symbol", "ts", "horizon_dte", name=_UQ),
    )
    op.create_index(_IX, _TABLE, ["symbol", "ts"])


def downgrade() -> None:
    op.drop_index(_IX, table_name=_TABLE)
    op.drop_table(_TABLE)
