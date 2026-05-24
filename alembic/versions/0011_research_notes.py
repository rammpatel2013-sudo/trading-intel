"""research_notes: per-ticker narrative research notes (PDF + 10-K + FMP + regime)

Revision ID: 0011
Revises: 0010
Create Date: 2026-05-24

One row per (symbol, as_of) holding the markdown research note written nightly by
the research-note job (uploaded PDF + SEC 10-K + FMP fundamentals/news + the live
options-vol regime, via the LLM). Shown on the Research Watchlist page.
Descriptive research read-through only (FlashAlpha rule 4).
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "research_notes"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("symbol", sa.String(length=16), nullable=False),
        sa.Column("as_of", sa.Date(), nullable=False),
        sa.Column("note_md", sa.Text(), nullable=False),
        sa.Column("sources", sa.String(length=128), nullable=True),
        sa.Column("model", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("symbol", "as_of", name="uq_research_notes"),
    )
    op.create_index("ix_research_notes_symbol", _TABLE, ["symbol"])


def downgrade() -> None:
    op.drop_index("ix_research_notes_symbol", table_name=_TABLE)
    op.drop_table(_TABLE)
