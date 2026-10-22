"""tas_daily_contract.legged_notional — multi-leg notional survives the roll-up

Hand-written additive migration (repo rule: NEVER `alembic revision --autogenerate`
here, it produces destructive diffs against the drifted models).

Revision ID: 0048_tas_legged
Revises: 0046
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0048_tas_legged"
down_revision = "0046"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tas_daily_contract",
        sa.Column("legged_notional", sa.Float(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("tas_daily_contract", "legged_notional")
