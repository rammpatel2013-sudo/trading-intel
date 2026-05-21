"""gex_rolling + gex_term: long-dated (6-month) rolling GEX

Revision ID: 0002
Revises: 0001
Create Date: 2026-05-21

Adds two tables for EOD long-dated GEX:
- gex_rolling: per-symbol 6-month total net gxoi (directional-flow time series)
- gex_term:    per-expiration net gxoi (term structure) for each rolling snapshot
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "gex_rolling",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("symbol", sa.String(length=16), nullable=False),
        sa.Column("ts", sa.DateTime(), nullable=False),
        sa.Column("spot", sa.Float(), nullable=True),
        sa.Column("window_days", sa.Integer(), nullable=False),
        sa.Column("gex_total", sa.Float(), nullable=True),
        sa.Column("n_expirations", sa.Integer(), nullable=True),
        sa.Column("source", sa.String(length=32), nullable=False, server_default="convex"),
        sa.UniqueConstraint("symbol", "ts", "source", name="uq_gex_rolling"),
    )
    op.create_index("ix_gex_rolling_symbol_ts", "gex_rolling", ["symbol", "ts"])

    op.create_table(
        "gex_term",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("symbol", sa.String(length=16), nullable=False),
        sa.Column("ts", sa.DateTime(), nullable=False),
        sa.Column("expiration", sa.Date(), nullable=False),
        sa.Column("dte", sa.Integer(), nullable=True),
        sa.Column("gex", sa.Float(), nullable=True),
        sa.Column("source", sa.String(length=32), nullable=False, server_default="convex"),
        sa.UniqueConstraint("symbol", "ts", "source", "expiration", name="uq_gex_term"),
    )
    op.create_index("ix_gex_term_symbol_ts", "gex_term", ["symbol", "ts"])


def downgrade() -> None:
    op.drop_index("ix_gex_term_symbol_ts", table_name="gex_term")
    op.drop_table("gex_term")
    op.drop_index("ix_gex_rolling_symbol_ts", table_name="gex_rolling")
    op.drop_table("gex_rolling")
