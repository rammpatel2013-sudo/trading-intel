"""delta_flow: intraday cumulative traded delta-notional (call/put, all/next expiry)

Revision ID: 0014
Revises: 0013
Create Date: 2026-05-26

Adds the ``delta_flow`` table: one row per (symbol, 5-min ts) holding the running
dollar-delta of the day's option flow — call vs put, summed over ALL expiries and
over the NEXT (nearest) expiry — plus spot and the next-expiry date. Feeds the
delta-notional flow chart (price overlaid with cumulative call/put delta). Unique
on the natural key for idempotent 5-min upserts (CLAUDE.md rule 5).

Regime-descriptor data only (FlashAlpha rule 4).
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "delta_flow"
_UQ = "uq_delta_flow"
_IX = "ix_delta_flow_symbol_ts"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("symbol", sa.String(length=16), nullable=False),
        sa.Column("ts", sa.DateTime(), nullable=False),
        sa.Column("spot", sa.Float(), nullable=True),
        sa.Column("next_expiry", sa.Date(), nullable=True),
        sa.Column("call_notional_all", sa.Float(), nullable=True),
        sa.Column("put_notional_all", sa.Float(), nullable=True),
        sa.Column("call_notional_next", sa.Float(), nullable=True),
        sa.Column("put_notional_next", sa.Float(), nullable=True),
        sa.Column("source", sa.String(length=32), nullable=False, server_default="convex"),
        sa.UniqueConstraint("symbol", "ts", "source", name=_UQ),
    )
    op.create_index(_IX, _TABLE, ["symbol", "ts"])


def downgrade() -> None:
    op.drop_index(_IX, table_name=_TABLE)
    op.drop_table(_TABLE)
