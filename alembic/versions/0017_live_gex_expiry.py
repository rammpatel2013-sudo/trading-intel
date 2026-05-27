"""live_gex: add per-expiry ``expiry`` column + expiry in the natural key

Revision ID: 0017
Revises: 0016
Create Date: 2026-05-26

Adds ``expiry`` (option expiration date) to ``live_gex`` and widens the unique
key to ``(symbol, ts, strike, cp, expiry)`` so per-strike rows are kept *per
expiration* (the per-expiry decomposition + true-0DTE scope, see ADR-002),
instead of being collapsed across expiries.

``live_gex`` is ephemeral (pruned EOD), so rather than fight a cross-dialect
UNIQUE-constraint swap we drop and recreate the table. Both upgrade and
downgrade rebuild it; no data migration (next collector slot repopulates).
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "live_gex"
_UQ = "uq_live_gex"
_IX = "ix_live_gex_symbol_ts"

def _base_cols() -> list[sa.Column]:
    """Fresh Column objects each call (SQLAlchemy Columns can't be reused)."""
    return [
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("symbol", sa.String(length=16), nullable=False),
        sa.Column("ts", sa.DateTime(), nullable=False),
        sa.Column("strike", sa.Float(), nullable=False),
        sa.Column("cp", sa.String(length=1), nullable=False),
        sa.Column("spot", sa.Float(), nullable=True),
        sa.Column("delta", sa.Float(), nullable=True),
        sa.Column("gamma", sa.Float(), nullable=True),
        sa.Column("iv", sa.Float(), nullable=True),
        sa.Column("gxoi", sa.Float(), nullable=True),
        sa.Column("dxoi", sa.Float(), nullable=True),
        sa.Column("oi", sa.Float(), nullable=True),
        sa.Column("vanna", sa.Float(), nullable=True),
        sa.Column("charm", sa.Float(), nullable=True),
        sa.Column("source", sa.String(length=32), nullable=False, server_default="convex"),
    ]


def _recreate(*, with_expiry: bool) -> None:
    op.drop_index(_IX, table_name=_TABLE)
    op.drop_table(_TABLE)
    cols = _base_cols()
    if with_expiry:
        cols.insert(5, sa.Column("expiry", sa.Date(), nullable=True))
        uq = sa.UniqueConstraint("symbol", "ts", "strike", "cp", "expiry", name=_UQ)
    else:
        uq = sa.UniqueConstraint("symbol", "ts", "strike", "cp", name=_UQ)
    op.create_table(_TABLE, *cols, uq)
    op.create_index(_IX, _TABLE, ["symbol", "ts"])


def upgrade() -> None:
    _recreate(with_expiry=True)


def downgrade() -> None:
    _recreate(with_expiry=False)
