"""surface_snapshots: delta-moneyness x expiry IV surface for index ETFs

Revision ID: 0035
Revises: 0034
Create Date: 2026-07-17

Original surface board schema — one row per (symbol, ts, expiry_date, moneyness) where
moneyness is a delta axis (50=ATM). Superseded by 0036, which converts the table to a
fixed-STRIKE schema. This migration is left intact (already applied on existing DBs);
per CLAUDE.md rule 3 the strike change is a NEW migration (0036), not an edit here.

Reversible (rule 3): ``downgrade`` drops the table.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0035"
down_revision: str | None = "0034"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "surface_snapshots"
_UQ = "uq_surface_snapshots"
_IX = "ix_surface_snapshots_symbol_ts"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("symbol", sa.String(length=16), sa.ForeignKey("tickers.symbol"), nullable=False),
        sa.Column("ts", sa.Date(), nullable=False),
        sa.Column("expiry_date", sa.Date(), nullable=False),
        sa.Column("dte", sa.Integer(), nullable=False),
        sa.Column("moneyness", sa.Float(), nullable=False),
        sa.Column("iv", sa.Float(), nullable=True),
        sa.Column("spot", sa.Float(), nullable=True),
        sa.UniqueConstraint("symbol", "ts", "expiry_date", "moneyness", name=_UQ),
    )
    op.create_index(_IX, _TABLE, ["symbol", "ts"])


def downgrade() -> None:
    op.drop_index(_IX, table_name=_TABLE)
    op.drop_table(_TABLE)
