"""iv_tenor_snapshots: constant-maturity forward IV for index ETFs

Revision ID: 0028
Revises: 0027
Create Date: 2026-06-23

Adds ``iv_tenor_snapshots`` — the index-ETF complement to ``skew_snapshots``.
SPY / QQQ / SPX are excluded from the per-strike persisters (``oi_chain_eod`` /
``chain_snapshot``) via ``CHAIN_EXCLUDE_ROOTS``, so the delta-surface pipeline
that feeds ``skew_snapshots`` has no stored chain for them. The EOD
``iv_tenor_snapshots`` job pulls a live chain, builds the surface in memory, and
writes one small aggregate row per (symbol, day, constant-maturity tenor):
ATM IV plus the 15Δ / 25Δ call and put wings, interpolated to a fixed 30 / 90
DTE in total-variance space.

One row per (symbol, ts, tenor_dte); the natural key carries a unique
constraint for the job's idempotent ``ON CONFLICT … DO UPDATE`` upsert
(CLAUDE.md rule 5). Reversible (rule 3): ``downgrade`` drops the table.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0028"
down_revision: str | None = "0027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "iv_tenor_snapshots"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "symbol",
            sa.String(length=16),
            sa.ForeignKey("tickers.symbol"),
            nullable=False,
        ),
        sa.Column("ts", sa.Date(), nullable=False),
        sa.Column("tenor_dte", sa.Integer(), nullable=False),
        sa.Column("iv_atm", sa.Float(), nullable=True),
        sa.Column("iv_call_15d", sa.Float(), nullable=True),
        sa.Column("iv_put_15d", sa.Float(), nullable=True),
        sa.Column("iv_call_25d", sa.Float(), nullable=True),
        sa.Column("iv_put_25d", sa.Float(), nullable=True),
        sa.Column("spot", sa.Float(), nullable=True),
        sa.Column("n_expiries", sa.Integer(), nullable=True),
        sa.UniqueConstraint("symbol", "ts", "tenor_dte", name="uq_iv_tenor_snapshots"),
    )
    op.create_index(
        "ix_iv_tenor_snapshots_symbol_ts", _TABLE, ["symbol", "ts"]
    )


def downgrade() -> None:
    op.drop_index("ix_iv_tenor_snapshots_symbol_ts", table_name=_TABLE)
    op.drop_table(_TABLE)
