"""flow_snapshots: aggregate options-flow per symbol/snapshot

Revision ID: 0007
Revises: 0006
Create Date: 2026-05-22

Adds the ``flow_snapshots`` table for the options-flow collector: call/put
premium notional, put/call tilt, net premium, largest prints (JSON) and notable
multi-leg packages (JSON). Unique on (symbol, ts, source) for idempotent
upserts (CLAUDE.md rule 5). Regime descriptor data only (FlashAlpha rule 4).
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "flow_snapshots"
_UQ = "uq_flow_snapshots"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("symbol", sa.String(length=16), nullable=False),
        sa.Column("ts", sa.DateTime(), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False, server_default="convex"),
        sa.Column("call_notional", sa.Float(), nullable=True),
        sa.Column("put_notional", sa.Float(), nullable=True),
        sa.Column("net_premium", sa.Float(), nullable=True),
        sa.Column("put_call_ratio", sa.Float(), nullable=True),
        sa.Column("tilt", sa.String(length=32), nullable=True),
        sa.Column("n_prints", sa.Integer(), nullable=True),
        sa.Column("top_prints", sa.JSON(), nullable=True),
        sa.Column("packages", sa.JSON(), nullable=True),
        sa.UniqueConstraint("symbol", "ts", "source", name=_UQ),
    )
    op.create_index("ix_flow_snapshots_symbol_ts", _TABLE, ["symbol", "ts"])


def downgrade() -> None:
    op.drop_index("ix_flow_snapshots_symbol_ts", table_name=_TABLE)
    op.drop_table(_TABLE)
