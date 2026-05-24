"""vix_data: persist CBOE term structure (VIX9D/VIX3M/VIX6M) + variance risk premium

Revision ID: 0010
Revises: 0009
Create Date: 2026-05-24

``vix_snapshot`` already fetches the CBOE index term structure via ``CboeClient``
but discarded every tenor except VVIX. This persists VIX9D/VIX3M/VIX6M so the
term-structure curve (contango/backwardation) can be trended over time, and adds
``vrp`` — the variance risk premium (VIX minus SPX 20-day realized vol, in vol
points), the mechanical-vs-fear baseline from the VIX-decomposition playbooks.
All columns nullable; descriptive data only (FlashAlpha rule 4).
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "vix_data"
_COLS = ("vix9d", "vix3m", "vix6m", "vrp")


def upgrade() -> None:
    for col in _COLS:
        op.add_column(_TABLE, sa.Column(col, sa.Float(), nullable=True))


def downgrade() -> None:
    for col in reversed(_COLS):
        op.drop_column(_TABLE, col)
