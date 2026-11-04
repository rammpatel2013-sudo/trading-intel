"""tas_prints.quote_loc + spread_leg, tas_daily_flow.signable_pct + pct_buy_all (Phase 2, 2026-11-04).

Hand-written (never autogenerate). Additive, nullable columns only: old rows stay NULL
and every reader treats NULL as "no quote recorded" (the pre-2026-11-04 behaviour).

Revision ID: 0052_tas_quote_loc
Revises: 0051_pos_ledger
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0052_tas_quote_loc"
down_revision = "0051_pos_ledger"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tas_prints", sa.Column("quote_loc", sa.String(8), nullable=True))
    op.add_column("tas_prints", sa.Column("spread_leg", sa.Boolean(), nullable=True))
    op.add_column("tas_daily_flow", sa.Column("signable_pct", sa.Float(), nullable=True))
    op.add_column("tas_daily_flow", sa.Column("pct_buy_all", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column("tas_daily_flow", "pct_buy_all")
    op.drop_column("tas_daily_flow", "signable_pct")
    op.drop_column("tas_prints", "spread_leg")
    op.drop_column("tas_prints", "quote_loc")
