"""estimate_snapshots: weekly analyst EPS/revenue estimates (for revision trend)

Revision ID: 0040
Revises: 0039
Create Date: 2026-07-28

Adds ``estimate_snapshots`` — one row per (symbol, ts) banking the nearest
upcoming fiscal period's analyst EPS/revenue estimate from CVForge FMP (ADR-005,
no new vendor). Banked forward so the earnings-alignment screen can read the
*revision* (this week vs a prior week) — the top-quality of its three signals.
Written by ``scheduler/jobs/estimate_snapshots.py``.

Unique on (symbol, ts) for the idempotent ``ON CONFLICT ... DO UPDATE`` upsert
(CLAUDE.md rule 5). Reversible (rule 3): ``downgrade`` drops the table.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0040"
down_revision: str | None = "0039"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "estimate_snapshots"
_UQ = "uq_estimate_snapshots"
_IX = "ix_estimate_snapshots_symbol_ts"

_FLOAT = ("eps_avg", "eps_high", "eps_low", "eps_num", "revenue_avg")


def upgrade() -> None:
    cols = [
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("symbol", sa.String(length=16), sa.ForeignKey("tickers.symbol"), nullable=False),
        sa.Column("ts", sa.Date(), nullable=False),
        sa.Column("period_date", sa.Date(), nullable=True),
    ]
    cols += [sa.Column(name, sa.Float(), nullable=True) for name in _FLOAT]
    cols.append(sa.Column("source", sa.String(length=32), nullable=True))
    cols.append(sa.UniqueConstraint("symbol", "ts", name=_UQ))
    op.create_table(_TABLE, *cols)
    op.create_index(_IX, _TABLE, ["symbol", "ts"])


def downgrade() -> None:
    op.drop_index(_IX, table_name=_TABLE)
    op.drop_table(_TABLE)
