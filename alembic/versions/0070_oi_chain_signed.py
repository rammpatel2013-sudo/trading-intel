"""oi_chain_eod: signed day volume / premium columns for the SPX full-chain step (2026-11-21)

Revision ID: 0070_oi_chain_signed
Revises: 0069_pos_packages
Create Date: 2026-11-21

Hand-written (never autogenerate). Four nullable columns, no backfill, no index.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0070_oi_chain_signed"
down_revision: str | None = "0069_pos_packages"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_COLS = ("vol_buy", "vol_sell", "val_buy", "val_sell")


def upgrade() -> None:
    for c in _COLS:
        op.add_column("oi_chain_eod", sa.Column(c, sa.Float(), nullable=True))


def downgrade() -> None:
    for c in reversed(_COLS):
        op.drop_column("oi_chain_eod", c)
