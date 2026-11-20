"""letf_close_flow: leveraged-ETF forced-rebalance estimate into the close (2026-11-19)

Revision ID: 0061_letf_close_flow
Revises: 0060_flow_bank
Create Date: 2026-11-19

Hand-written (never autogenerate). Creates ONLY ``letf_close_flow`` (model in
``trading_intel/memory/models_close_flow.py``). Additive; touches no existing table.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0061_letf_close_flow"
down_revision: str | None = "0060_flow_bank"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "letf_close_flow",
        sa.Column("grp", sa.String(8), primary_key=True),
        sa.Column("ts", sa.DateTime(), primary_key=True),
        sa.Column("date", sa.Date()),
        sa.Column("index_prev", sa.Float()),
        sa.Column("index_last", sa.Float()),
        sa.Column("index_ret", sa.Float()),
        sa.Column("rebalance_usd", sa.Float()),
        sa.Column("futures", sa.Float()),
        sa.Column("per_1pct_usd", sa.Float()),
        sa.Column("aum_usd", sa.Float()),
        sa.Column("direction", sa.String(8)),
        sa.Column("strength", sa.String(10)),
        sa.Column("n_funds", sa.Integer()),
        sa.Column("n_index_fallback", sa.Integer()),
        sa.Column("n_missing", sa.Integer()),
        sa.Column("funds", sa.JSON()),
        sa.Column("source", sa.String(16)),
        sa.Column("ingested_at", sa.DateTime()),
    )
    op.create_index("ix_letf_close_flow_date", "letf_close_flow", ["date"])


def downgrade() -> None:
    op.drop_index("ix_letf_close_flow_date", table_name="letf_close_flow")
    op.drop_table("letf_close_flow")
