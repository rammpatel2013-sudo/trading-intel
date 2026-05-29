"""vix_options_chain: EOD snapshot of the VIX options chain (per-strike rows)

Revision ID: 0021
Revises: 0020
Create Date: 2026-05-28

Adds the ``vix_options_chain`` table: one row per (ts, expiration, strike,
opt_kind) holding the EOD VIX-options chain pulled via
``OptionsDataSource.vix_chain``. The dashboard reads this to render the
call-wing IV / OI distribution view, and the EOD ``index_skew`` job uses it to
compute the day's ``vix_call_skew_25d`` and ``vix_call_oi_share`` columns on
``index_skew_daily`` (and hence the composite tail-hedging score).

Reversible. Pruning is left for a follow-up migration once we know how heavy
the chain is in production; for now treat as un-pruned alongside its siblings.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0021"
down_revision: str | None = "0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "vix_options_chain"
_UQ = "uq_vix_options_chain"
_IX = "ix_vix_options_chain_ts"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("ts", sa.Date(), nullable=False),
        sa.Column("expiration", sa.Date(), nullable=False),
        sa.Column("strike", sa.Float(), nullable=False),
        sa.Column("opt_kind", sa.String(length=4), nullable=False),  # call / put
        sa.Column("delta", sa.Float(), nullable=True),
        sa.Column("iv", sa.Float(), nullable=True),
        sa.Column("oi", sa.Float(), nullable=True),
        sa.Column("oi_change", sa.Float(), nullable=True),
        sa.Column("volume", sa.Float(), nullable=True),
        sa.UniqueConstraint(
            "ts", "expiration", "strike", "opt_kind", name=_UQ,
        ),
    )
    op.create_index(_IX, _TABLE, ["ts"])


def downgrade() -> None:
    op.drop_index(_IX, table_name=_TABLE)
    op.drop_table(_TABLE)
