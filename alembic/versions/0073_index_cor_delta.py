"""index_skew_daily: cor10d + cor90d (Cboe 3-month delta implied correlation) (2026-11-22)

Revision ID: 0073_index_cor_delta
Revises: 0072_pos_book
Create Date: 2026-11-22

Hand-written (never autogenerate). Two nullable Float columns on the existing owner table
index_skew_daily, filled nightly by scheduler/jobs/index_skew.py and back-filled from the Cboe
history CSVs by scripts/backfill_index_cor_cboe.py. Read by scripts/fuse_tab.py (Correlation Fuse).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0073_index_cor_delta"
down_revision: str | None = "0072_pos_book"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("index_skew_daily", sa.Column("cor10d", sa.Float(), nullable=True))
    op.add_column("index_skew_daily", sa.Column("cor90d", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column("index_skew_daily", "cor90d")
    op.drop_column("index_skew_daily", "cor10d")
