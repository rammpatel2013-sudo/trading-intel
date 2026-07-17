"""surface_snapshots -> fixed-STRIKE schema (drop moneyness, add strike + delta)

Revision ID: 0036
Revises: 0035
Create Date: 2026-07-17

The 0035 surface board keyed the table on delta-moneyness; the fixed-strike rebuild keys it
on the listed STRIKE (+ stored ``delta``) so day-over-day changes and the vol footprint
track the SAME contract (fixed strike = the receipt; fixed delta gets smeared as spot slides
along the skew). Snapshot rows are disposable (re-collected EOD by
``scheduler/jobs/surface_snapshots.py``), so this drops + recreates the table rather than
back-filling a strike for moneyness rows that never had one.

Unique on (symbol, ts, expiry_date, strike) for the idempotent upsert (rule 5).
Reversible (rule 3): ``downgrade`` restores the 0035 moneyness schema.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0036"
down_revision: str | None = "0035"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "surface_snapshots"
_UQ = "uq_surface_snapshots"
_IX = "ix_surface_snapshots_symbol_ts"


def _create_strike_table() -> None:
    op.create_table(
        _TABLE,
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("symbol", sa.String(length=16), sa.ForeignKey("tickers.symbol"), nullable=False),
        sa.Column("ts", sa.Date(), nullable=False),
        sa.Column("expiry_date", sa.Date(), nullable=False),
        sa.Column("dte", sa.Integer(), nullable=False),
        sa.Column("strike", sa.Float(), nullable=False),
        sa.Column("iv", sa.Float(), nullable=True),
        sa.Column("delta", sa.Float(), nullable=True),
        sa.Column("spot", sa.Float(), nullable=True),
        sa.UniqueConstraint("symbol", "ts", "expiry_date", "strike", name=_UQ),
    )
    op.create_index(_IX, _TABLE, ["symbol", "ts"])


def _create_moneyness_table() -> None:
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


def upgrade() -> None:
    op.drop_table(_TABLE)  # drops its index + UQ + FK; stale moneyness rows are disposable
    _create_strike_table()


def downgrade() -> None:
    op.drop_table(_TABLE)
    _create_moneyness_table()
