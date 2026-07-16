"""letf_shares_snapshots: daily LETF shares-outstanding snapshots (issuance flow)

Revision ID: 0032
Revises: 0031
Create Date: 2026-07-15

Adds ``letf_shares_snapshots`` — one row per (symbol, trading day) banking a
leveraged/inverse ETF's shares outstanding. FMP's stable tier serves only the
current figure, so the EOD job (``scheduler/jobs/letf_flows.py``) snapshots it
daily and banks the series forward; Δshares, net issuance $ (= Δshares × price),
issuer buckets, and the k(k-1)·assets·return forced-rebalance estimate are all
computed downstream. ``nav`` carries the fund close so the $-flow descriptor is
self-contained.

Unique on (symbol, ts) for the idempotent ``ON CONFLICT … DO UPDATE`` upsert
(CLAUDE.md rule 5). Reversible (rule 3): ``downgrade`` drops the table.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0032"
down_revision: str | None = "0031"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "letf_shares_snapshots"
_UQ = "uq_letf_shares_snapshots"
_IX = "ix_letf_shares_snapshots_symbol_ts"


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
        sa.Column("shares_outstanding", sa.BigInteger(), nullable=False),
        sa.Column("float_shares", sa.BigInteger(), nullable=True),
        sa.Column("nav", sa.Float(), nullable=True),
        sa.Column("vendor_date", sa.Date(), nullable=True),
        sa.Column("source", sa.String(length=32), nullable=True),
        sa.UniqueConstraint("symbol", "ts", name=_UQ),
    )
    op.create_index(_IX, _TABLE, ["symbol", "ts"])


def downgrade() -> None:
    op.drop_index(_IX, table_name=_TABLE)
    op.drop_table(_TABLE)
