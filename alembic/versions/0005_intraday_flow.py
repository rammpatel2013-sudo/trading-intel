"""intraday_flow: per-strike 0DTE/1DTE volume-weighted exposures

Revision ID: 0005
Revises: 0004
Create Date: 2026-05-22

Adds the ``intraday_flow`` table for the 5-minute 0DTE/1DTE collector
(SPX/SPY/QQQ at a tight strike range). Stores raw greeks + traded volume and
the volume-weighted gamma/delta/vanna/charm on both cumulative day volume
(``*_vol``) and the per-cycle increment (``*_vol_iv``). Unique on
(symbol, ts, source, expiry, strike, cp) so the collector upserts idempotently
(CLAUDE.md rule 5). Regime descriptor data only (FlashAlpha rule 4).
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "intraday_flow"
_UQ = "uq_intraday_flow"
_UQ_COLS = ["symbol", "ts", "source", "expiry", "strike", "cp"]


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("symbol", sa.String(length=16), nullable=False),
        sa.Column("ts", sa.DateTime(), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False, server_default="convex"),
        sa.Column("expiry", sa.Date(), nullable=False),
        sa.Column("dte", sa.Integer(), nullable=True),
        sa.Column("strike", sa.Float(), nullable=False),
        sa.Column("cp", sa.String(length=1), nullable=False),
        sa.Column("spot", sa.Float(), nullable=True),
        sa.Column("iv", sa.Float(), nullable=True),
        sa.Column("gamma", sa.Float(), nullable=True),
        sa.Column("delta", sa.Float(), nullable=True),
        sa.Column("vanna", sa.Float(), nullable=True),
        sa.Column("charm", sa.Float(), nullable=True),
        sa.Column("volume", sa.Integer(), nullable=True),
        sa.Column("volume_interval", sa.Integer(), nullable=True),
        sa.Column("gamma_vol", sa.Float(), nullable=True),
        sa.Column("delta_vol", sa.Float(), nullable=True),
        sa.Column("vanna_vol", sa.Float(), nullable=True),
        sa.Column("charm_vol", sa.Float(), nullable=True),
        sa.Column("gamma_vol_iv", sa.Float(), nullable=True),
        sa.Column("delta_vol_iv", sa.Float(), nullable=True),
        sa.Column("vanna_vol_iv", sa.Float(), nullable=True),
        sa.Column("charm_vol_iv", sa.Float(), nullable=True),
        sa.UniqueConstraint(*_UQ_COLS, name=_UQ),
    )
    op.create_index("ix_intraday_flow_symbol_ts", _TABLE, ["symbol", "ts"])


def downgrade() -> None:
    op.drop_index("ix_intraday_flow_symbol_ts", table_name=_TABLE)
    op.drop_table(_TABLE)
