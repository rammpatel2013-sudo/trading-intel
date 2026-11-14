"""tas_crowd_minute (SPX/SPXW tape summed per minute x contract x whale/crowd bucket)

Revision ID: 0054_tas_crowd_minute
Revises: 0053_phase5_vol_beta
Create Date: 2026-11-13

Hand-written (never autogenerate). Creates ONLY the tas_crowd_minute table + its unique
key and a trade_date index. Additive; no drops/alters. Backlog #97 (whales vs crowd).
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0054_tas_crowd_minute'
down_revision: Union[str, None] = '0053_phase5_vol_beta'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'tas_crowd_minute',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('trade_date', sa.Date(), nullable=False),
        sa.Column('minute', sa.DateTime(), nullable=False),
        sa.Column('root', sa.String(8), nullable=False),
        sa.Column('expiry', sa.Date(), nullable=False),
        sa.Column('strike', sa.Float(), nullable=False),
        sa.Column('cp', sa.String(1), nullable=False),
        sa.Column('bucket', sa.String(6), nullable=False),
        sa.Column('n_prints', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('buy_contracts', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('sell_contracts', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('other_contracts', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('buy_premium', sa.Float(), nullable=False, server_default='0'),
        sa.Column('sell_premium', sa.Float(), nullable=False, server_default='0'),
        sa.Column('signed_delta', sa.Float(), nullable=False, server_default='0'),
        sa.Column('spot', sa.Float(), nullable=True),
        sa.Column('last_ts', sa.DateTime(), nullable=True),
        sa.UniqueConstraint('trade_date', 'minute', 'root', 'expiry', 'strike', 'cp', 'bucket',
                            name='uq_tas_crowd_minute'),
    )
    op.create_index('ix_tas_crowd_minute_trade_date', 'tas_crowd_minute', ['trade_date'])


def downgrade() -> None:
    op.drop_index('ix_tas_crowd_minute_trade_date', table_name='tas_crowd_minute')
    op.drop_table('tas_crowd_minute')
