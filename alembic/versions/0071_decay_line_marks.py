"""decay_line_marks: background score of the expiry-week decay line (N / W / L) per session x mark (2026-11-21)

Revision ID: 0071_decay_line_marks
Revises: 0070_oi_chain_signed
Create Date: 2026-11-21

Hand-written (never autogenerate). One new table only.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0071_decay_line_marks"
down_revision: str | None = "0070_oi_chain_signed"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "decay_line_marks",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("mark", sa.String(5), nullable=False),
        sa.Column("variant", sa.String(1), nullable=False),
        sa.Column("spot", sa.Float()),
        sa.Column("close", sa.Float()),
        sa.Column("es_at_spot", sa.Float()),
        sa.Column("flip_below", sa.Float()),
        sa.Column("flip_above", sa.Float()),
        sa.Column("flips", sa.String(200)),
        sa.Column("move", sa.Float()),
        sa.Column("hit", sa.Boolean()),
        sa.Column("ingested_at", sa.DateTime()),
        sa.UniqueConstraint("trade_date", "mark", "variant", name="uq_decay_line_marks"),
    )
    op.create_index("ix_decay_line_marks_trade_date", "decay_line_marks", ["trade_date"])


def downgrade() -> None:
    op.drop_index("ix_decay_line_marks_trade_date", table_name="decay_line_marks")
    op.drop_table("decay_line_marks")
