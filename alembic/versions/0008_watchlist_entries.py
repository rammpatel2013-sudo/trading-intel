"""watchlist_entries: research-driven dynamic watchlist

Revision ID: 0008
Revises: 0007
Create Date: 2026-05-22

Adds ``watchlist_entries`` for the research-ingest pipeline: tickers surfaced
from uploaded company research with an LLM rationale, sentiment and themes. One
row per (symbol, source document); ``symbol`` is not a FK (a researched name may
not be in ``tickers`` yet). Descriptive context only (FlashAlpha rule 4).
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "watchlist_entries"
_UQ = "uq_watchlist_entries"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("symbol", sa.String(length=16), nullable=False),
        sa.Column("source_doc_id", sa.Integer(), sa.ForeignKey("documents.id"), nullable=True),
        sa.Column("rationale", sa.Text(), nullable=True),
        sa.Column("sentiment", sa.Float(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("themes", sa.JSON(), nullable=True),
        sa.Column("added_at", sa.DateTime(), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=True),
        sa.UniqueConstraint("symbol", "source_doc_id", name=_UQ),
    )
    op.create_index("ix_watchlist_entries_symbol", _TABLE, ["symbol"])


def downgrade() -> None:
    op.drop_index("ix_watchlist_entries_symbol", table_name=_TABLE)
    op.drop_table(_TABLE)
