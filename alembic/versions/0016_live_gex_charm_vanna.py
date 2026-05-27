"""live_gex: add oi / vanna / charm for the charm-vanna live heatmap

Revision ID: 0016
Revises: 0015
Create Date: 2026-05-26

Adds ``oi``, ``vanna`` and ``charm`` (raw greeks + open interest) to ``live_gex``
so the live gamma/charm/vanna map can compute per-strike charm/vanna exposure
(``charm * oi`` / ``vanna * oi``) alongside the Convex-precomputed ``gxoi`` /
``dxoi``. Nullable, additive — no data migration. Reversible.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "live_gex"
_COLS = ("oi", "vanna", "charm")


def upgrade() -> None:
    for col in _COLS:
        op.add_column(_TABLE, sa.Column(col, sa.Float(), nullable=True))


def downgrade() -> None:
    for col in reversed(_COLS):
        op.drop_column(_TABLE, col)
