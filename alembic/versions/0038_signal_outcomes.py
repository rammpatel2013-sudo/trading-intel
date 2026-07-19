"""signal outcomes: EM-break re-entry backtest ledger (P6 bank-forward)

Revision ID: 0038
Revises: 0037
Create Date: 2026-07-19

Adds ``signal_outcomes`` — the realized win/loss/open + R-multiple ledger the
``em_break_validation`` job banks by walking ``quotes_daily`` forward from each
``EM_BREAK_REENTRY`` signal (``docs/em_break_backtest.md``). One row per signal
(unique on ``signal_id``) so the weekly re-evaluation upserts idempotently (rule 5);
an OPEN trade is refreshed until it closes.

Reversible (rule 3): ``downgrade`` drops the table.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0038"
down_revision: str | None = "0037"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TBL = "signal_outcomes"
_UQ = "uq_signal_outcomes_signal"
_IX = "ix_signal_outcomes_symbol"


def upgrade() -> None:
    op.create_table(
        _TBL,
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("signal_id", sa.Integer(), sa.ForeignKey("signals.id"), nullable=False),
        sa.Column("symbol", sa.String(length=16), nullable=False),
        sa.Column("signal_type", sa.String(length=64), nullable=False),
        sa.Column("entry_date", sa.Date(), nullable=False),
        sa.Column("entry", sa.Float(), nullable=True),
        sa.Column("target", sa.Float(), nullable=True),
        sa.Column("stop", sa.Float(), nullable=True),
        sa.Column("result", sa.String(length=8), nullable=True),
        sa.Column("exit_date", sa.Date(), nullable=True),
        sa.Column("exit_price", sa.Float(), nullable=True),
        sa.Column("days_held", sa.Integer(), nullable=True),
        sa.Column("r_multiple", sa.Float(), nullable=True),
        sa.Column("conviction", sa.Float(), nullable=True),
        sa.Column("max_days", sa.Integer(), nullable=True),
        sa.Column("evaluated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("signal_id", name=_UQ),
    )
    op.create_index(_IX, _TBL, ["symbol"])


def downgrade() -> None:
    op.drop_index(_IX, table_name=_TBL)
    op.drop_table(_TBL)
