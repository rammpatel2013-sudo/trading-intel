"""oi_chain_daily — never-pruned per-(symbol, day) roll-up of oi_chain_eod

Hand-written additive migration (repo rule: NEVER `alembic revision --autogenerate`
here, it produces destructive diffs against the drifted models).

Revision ID: 0049_oi_chain_daily
Revises: 0048_tas_legged
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0049_oi_chain_daily"
down_revision = "0048_tas_legged"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "oi_chain_daily",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("symbol", sa.String(16), nullable=False),
        sa.Column("ts", sa.Date(), nullable=False),
        sa.Column("source", sa.String(24)),
        sa.Column("spot", sa.Float()),
        sa.Column("call_oi", sa.BigInteger()),
        sa.Column("put_oi", sa.BigInteger()),
        sa.Column("call_volume", sa.BigInteger()),
        sa.Column("put_volume", sa.BigInteger()),
        sa.Column("call_oi_change", sa.BigInteger()),
        sa.Column("put_oi_change", sa.BigInteger()),
        sa.Column("net_gxoi", sa.Float()),
        sa.Column("call_gxoi", sa.Float()),
        sa.Column("put_gxoi", sa.Float()),
        sa.Column("net_dxoi", sa.Float()),
        sa.Column("net_vxoi", sa.Float()),
        sa.Column("call_wall", sa.Float()),
        sa.Column("call_wall_gxoi", sa.Float()),
        sa.Column("put_wall", sa.Float()),
        sa.Column("put_wall_gxoi", sa.Float()),
        sa.Column("atm_iv_30d", sa.Float()),
        sa.Column("n_contracts", sa.Integer()),
        sa.UniqueConstraint("symbol", "ts", name="uq_oi_chain_daily"),
    )
    op.create_index("ix_oi_chain_daily_symbol", "oi_chain_daily", ["symbol"])
    op.create_index("ix_oi_chain_daily_ts", "oi_chain_daily", ["ts"])


def downgrade() -> None:
    op.drop_index("ix_oi_chain_daily_ts", table_name="oi_chain_daily")
    op.drop_index("ix_oi_chain_daily_symbol", table_name="oi_chain_daily")
    op.drop_table("oi_chain_daily")
