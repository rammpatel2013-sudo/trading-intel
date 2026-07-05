"""tas_daily_contract: add spot + avg_delta for moneyness/delta reads

Revision ID: 0030
Revises: 0029
Create Date: 2026-06-25

Adds two columns to ``tas_daily_contract`` so the repeat-contract drill-down can
show moneyness and delta at trade time (durable, surviving the raw-print prune):

  - ``spot``       that session's average underlying price for the contract
  - ``avg_delta``  that session's average option delta for the contract

Both are sourced straight from ``tas_prints`` (which already stores per-print spot
and delta) by the ``tas_daily_rollup`` job. Descriptive only (FlashAlpha rule 4).
Reversible (rule 3): ``downgrade`` drops the two columns.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0030"
down_revision: str | None = "0029"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "tas_daily_contract"


def upgrade() -> None:
    op.add_column(_TABLE, sa.Column("spot", sa.Float(), nullable=True))
    op.add_column(_TABLE, sa.Column("avg_delta", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column(_TABLE, "avg_delta")
    op.drop_column(_TABLE, "spot")
