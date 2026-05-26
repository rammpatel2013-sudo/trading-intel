"""live_gex: intraday (live) per-strike GEX snapshot, delta-band, pruned EOD

Revision ID: 0015
Revises: 0014
Create Date: 2026-05-26

Adds the ``live_gex`` table: per (symbol, ts, strike, cp) intraday GEX rows
(spot/delta/gamma/iv/gxoi/dxoi), refreshed every few minutes during RTH and
filtered to the near-the-money delta band. Pruned at end of day — the daily
``greeks_chain`` / ``greeks_snapshots`` remain the historical record. Unique on
the natural key for idempotent upserts (CLAUDE.md rule 5). Regime-descriptor
data only (FlashAlpha rule 4).
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "live_gex"
_UQ = "uq_live_gex"
_IX = "ix_live_gex_symbol_ts"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("symbol", sa.String(length=16), nullable=False),
        sa.Column("ts", sa.DateTime(), nullable=False),
        sa.Column("strike", sa.Float(), nullable=False),
        sa.Column("cp", sa.String(length=1), nullable=False),
        sa.Column("spot", sa.Float(), nullable=True),
        sa.Column("delta", sa.Float(), nullable=True),
        sa.Column("gamma", sa.Float(), nullable=True),
        sa.Column("iv", sa.Float(), nullable=True),
        sa.Column("gxoi", sa.Float(), nullable=True),
        sa.Column("dxoi", sa.Float(), nullable=True),
        sa.Column("source", sa.String(length=32), nullable=False, server_default="convex"),
        sa.UniqueConstraint("symbol", "ts", "strike", "cp", name=_UQ),
    )
    op.create_index(_IX, _TABLE, ["symbol", "ts"])


def downgrade() -> None:
    op.drop_index(_IX, table_name=_TABLE)
    op.drop_table(_TABLE)
