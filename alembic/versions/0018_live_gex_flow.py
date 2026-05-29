"""live_gex: add volm_buy / volm_sell (signed flow) for OI+flow positioning

Revision ID: 0018
Revises: 0017
Create Date: 2026-05-27

Adds ``volm_buy`` and ``volm_sell`` (today's buy-/sell-side volume per strike,
from ConvexValue ``flowsum``) to ``live_gex`` so the gamma/charm exposures and
the forward field can use an effective position ``oi + (volm_buy - volm_sell)``
rather than resting OI alone. Nullable, additive — no data migration. Reversible.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "live_gex"
_COLS = ("volm_buy", "volm_sell")


def upgrade() -> None:
    for col in _COLS:
        op.add_column(_TABLE, sa.Column(col, sa.Float(), nullable=True))


def downgrade() -> None:
    for col in reversed(_COLS):
        op.drop_column(_TABLE, col)
