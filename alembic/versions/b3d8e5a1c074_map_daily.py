"""map_daily — the morning MAP card and its post-close grade (build plan §6-7).

Revision ID: b3d8e5a1c074
Revises: f2a9c4d7e1b5
Create Date: 2026-10-25
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "b3d8e5a1c074"
down_revision = "f2a9c4d7e1b5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "map_daily",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("symbol", sa.String(length=16), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        # what the map said
        sa.Column("spot", sa.Float(), nullable=True),
        sa.Column("book_flip", sa.Float(), nullable=True),
        sa.Column("day_flip", sa.Float(), nullable=True),
        sa.Column("long_gamma", sa.Boolean(), nullable=True),
        sa.Column("net_gamma_dollars", sa.Float(), nullable=True),
        sa.Column("floor", sa.Float(), nullable=True),
        sa.Column("cap", sa.Float(), nullable=True),
        sa.Column("day_floor", sa.Float(), nullable=True),
        sa.Column("day_cap", sa.Float(), nullable=True),
        sa.Column("p_below", sa.Float(), nullable=True),
        sa.Column("p_between", sa.Float(), nullable=True),
        sa.Column("p_above", sa.Float(), nullable=True),
        sa.Column("atm_iv", sa.Float(), nullable=True),
        sa.Column("range70_lo", sa.Float(), nullable=True),
        sa.Column("range70_hi", sa.Float(), nullable=True),
        # fragility (§6)
        sa.Column("frag_tol", sa.Float(), nullable=True),
        sa.Column("frag_wall_move", sa.Float(), nullable=True),
        sa.Column("frag_flip_move", sa.Float(), nullable=True),
        sa.Column("walls_firm", sa.Boolean(), nullable=True),
        sa.Column("flip_firm", sa.Boolean(), nullable=True),
        sa.Column("levels_firm", sa.Boolean(), nullable=True),
        # what the tape did (§7)
        sa.Column("high", sa.Float(), nullable=True),
        sa.Column("low", sa.Float(), nullable=True),
        sa.Column("close", sa.Float(), nullable=True),
        sa.Column("bucket", sa.String(length=8), nullable=True),
        sa.Column("floor_touched", sa.Boolean(), nullable=True),
        sa.Column("cap_touched", sa.Boolean(), nullable=True),
        sa.Column("floor_held", sa.Boolean(), nullable=True),
        sa.Column("cap_held", sa.Boolean(), nullable=True),
        sa.Column("inside_range70", sa.Boolean(), nullable=True),
        sa.Column("wall_broke", sa.Boolean(), nullable=True),
        sa.Column("graded_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("symbol", "date", name="uq_map_daily"),
    )
    op.create_index("ix_map_daily_symbol_date", "map_daily", ["symbol", "date"])


def downgrade() -> None:
    op.drop_index("ix_map_daily_symbol_date", table_name="map_daily")
    op.drop_table("map_daily")
