"""breadth_snapshots extra counts (up/down volume, Stockbee 4% / 25% counts, % > 40-day, setup supply)

Revision ID: 0055_breadth_extras
Revises: 0054_tas_crowd_minute
Create Date: 2026-11-13

Hand-written (never autogenerate). ADD COLUMN only, all nullable; no drops. Brief item 5.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0055_breadth_extras'
down_revision: Union[str, None] = '0054_tas_crowd_minute'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_COLS = [('up_volume', sa.BigInteger()), ('down_volume', sa.BigInteger()), ('up4', sa.Integer()),
         ('down4', sa.Integer()), ('up25q', sa.Integer()), ('down25q', sa.Integer()),
         ('pct_above_40', sa.Integer()), ('setups_base', sa.Integer()), ('setups_pullback', sa.Integer())]


def upgrade() -> None:
    for name, typ in _COLS:
        op.add_column('breadth_snapshots', sa.Column(name, typ, nullable=True))


def downgrade() -> None:
    for name, _ in reversed(_COLS):
        op.drop_column('breadth_snapshots', name)
