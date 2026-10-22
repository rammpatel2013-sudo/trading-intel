"""tas_daily_contract.financing_notional — the tape's own is_financing tag survives the roll-up

Hand-written additive migration (repo rule: NEVER `alembic revision --autogenerate`).

Revision ID: 0050_tas_financing
Revises: 0049_oi_chain_daily
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0050_tas_financing"
down_revision = "0049_oi_chain_daily"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tas_daily_contract",
        sa.Column("financing_notional", sa.Float(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("tas_daily_contract", "financing_notional")
