"""finra_dix_daily (own DIX from FINRA short volume)

Revision ID: 0056_finra_dix_daily
Revises: 0055_breadth_extras
Create Date: 2026-11-13

Hand-written (never autogenerate). Creates ONLY finra_dix_daily. Additive. Phase 6 / ADR-008.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0056_finra_dix_daily'
down_revision: Union[str, None] = '0055_breadth_extras'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'finra_dix_daily',
        sa.Column('date', sa.Date(), primary_key=True),
        sa.Column('dix', sa.Float(), nullable=True),
        sa.Column('short_dollars', sa.Float(), nullable=True),
        sa.Column('total_dollars', sa.Float(), nullable=True),
        sa.Column('n_symbols', sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table('finra_dix_daily')
