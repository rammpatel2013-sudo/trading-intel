"""squeezemetrics_daily (SqueezeMetrics DIX + GEX, free public CSV)

Revision ID: f2a9c4d7e1b5
Revises: 0050_tas_financing
Create Date: 2026-10-25

Hand-written: creates ONLY the squeezemetrics_daily table (date PK, spx_price,
dix, gex). Additive; no drops/alters. Feeds the fear-release "low-gamma" label
(percentile of THEIR gex, never its sign) — trading-intel build plan R1.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'f2a9c4d7e1b5'
down_revision: Union[str, None] = '0050_tas_financing'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'squeezemetrics_daily',
        sa.Column('date', sa.Date(), primary_key=True),
        sa.Column('spx_price', sa.Float(), nullable=True),
        sa.Column('dix', sa.Float(), nullable=True),
        sa.Column('gex', sa.Float(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table('squeezemetrics_daily')
