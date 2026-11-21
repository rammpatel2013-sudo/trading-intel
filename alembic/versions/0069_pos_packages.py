"""pos_packages: SPX spread package events (open / close / roll), derived from tas_prints + optionsdata OI (2026-11-21)

Revision ID: 0069_pos_packages
Revises: 0068_compass
Create Date: 2026-11-21

Hand-written (never autogenerate). One new table only.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0069_pos_packages"
down_revision: str | None = "0068_compass"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "pos_packages",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("symbol", sa.String(8), nullable=False, server_default="SPX"),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("pkg_key", sa.String(96), nullable=False),
        sa.Column("structure", sa.String(12), nullable=False),
        sa.Column("cp", sa.String(1), nullable=False),
        sa.Column("dear_root", sa.String(8), nullable=False),
        sa.Column("dear_expiry", sa.Date(), nullable=False),
        sa.Column("dear_strike", sa.Float(), nullable=False),
        sa.Column("cheap_root", sa.String(8), nullable=False),
        sa.Column("cheap_expiry", sa.Date(), nullable=False),
        sa.Column("cheap_strike", sa.Float(), nullable=False),
        sa.Column("first_ts", sa.DateTime(), nullable=False),
        sa.Column("last_ts", sa.DateTime(), nullable=False),
        sa.Column("lots", sa.Integer(), nullable=False),
        sa.Column("fills", sa.Integer(), nullable=False),
        sa.Column("net", sa.Float()),
        sa.Column("side", sa.Integer(), nullable=False),
        sa.Column("vote", sa.Float(), nullable=False),
        sa.Column("action", sa.String(12), nullable=False),
        sa.Column("oi_score", sa.Float()),
        sa.Column("roll_key", sa.String(96)),
        sa.Column("source", sa.String(16), nullable=False, server_default="convex_tape"),
        sa.Column("ingested_at", sa.DateTime()),
        sa.UniqueConstraint("symbol", "trade_date", "pkg_key", name="uq_pos_packages"),
    )
    op.create_index("ix_pos_packages_trade_date", "pos_packages", ["trade_date"])
    op.create_index("ix_pos_packages_dear_expiry", "pos_packages", ["dear_expiry"])


def downgrade() -> None:
    op.drop_index("ix_pos_packages_dear_expiry", table_name="pos_packages")
    op.drop_index("ix_pos_packages_trade_date", table_name="pos_packages")
    op.drop_table("pos_packages")
