"""focus_names: Mithil's up-to-4 focus names a trading day (2026-11-20)

Revision ID: 0067_focus_names
Revises: 0066_chain_bank
Create Date: 2026-11-20

Hand-written (never autogenerate). New table only; nothing else changes.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0067_focus_names"
down_revision: str | None = "0066_chain_bank"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "focus_names",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("slot", sa.Integer(), nullable=False),
        sa.Column("symbol", sa.String(16), nullable=False),
        sa.Column("source", sa.String(16), nullable=False, server_default="cockpit"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("day", "symbol", name="uq_focus_names_day_symbol"),
        sa.UniqueConstraint("day", "slot", name="uq_focus_names_day_slot"),
        sa.CheckConstraint("slot between 1 and 4", name="ck_focus_names_slot"),
    )
    op.create_index("ix_focus_names_day", "focus_names", ["day"])


def downgrade() -> None:
    op.drop_index("ix_focus_names_day", table_name="focus_names")
    op.drop_table("focus_names")
