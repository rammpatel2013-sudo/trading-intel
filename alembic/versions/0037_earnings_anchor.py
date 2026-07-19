"""earnings anchor: pre_earnings_straddle baseline table

Revision ID: 0037
Revises: 0036
Create Date: 2026-07-18

Wires the earnings-date anchor for the EM-break / gamma-burn-off system
(``docs/em-break-system-plan.md``). ``earnings_events`` already exists AND already
carries a ``(symbol, date)`` unique constraint (``uq_earnings_events``) from
migration 0001, so this migration only adds the new baseline table:

  * ``pre_earnings_straddle`` — the options-implied expected move captured just
    before each print; the baseline the EM-break detector measures the realized
    gap against. Unique on ``(symbol, earnings_date)`` for the idempotent
    per-event upsert (rule 5).

(The model's ``EarningsEvent.__table_args__`` was updated to DECLARE the existing
0001 constraint — fixing prior model↔DB drift — so no DDL change is needed for it
here.)

Reversible (rule 3): ``downgrade`` drops the new table.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0037"
down_revision: str | None = "0036"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_PRE = "pre_earnings_straddle"
_PRE_UQ = "uq_pre_earnings_straddle"
_PRE_IX = "ix_pre_earnings_straddle_symbol"


def upgrade() -> None:
    op.create_table(
        _PRE,
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("symbol", sa.String(length=16), nullable=False),
        sa.Column("earnings_date", sa.Date(), nullable=False),
        sa.Column("snap_ts", sa.DateTime(), nullable=False),
        sa.Column("dte", sa.Integer(), nullable=True),
        sa.Column("straddle", sa.Float(), nullable=True),
        sa.Column("em_pct", sa.Float(), nullable=True),
        sa.Column("atm_iv", sa.Float(), nullable=True),
        sa.Column("spot", sa.Float(), nullable=True),
        sa.Column("source", sa.String(length=32), nullable=False, server_default="convex"),
        sa.UniqueConstraint("symbol", "earnings_date", name=_PRE_UQ),
    )
    op.create_index(_PRE_IX, _PRE, ["symbol"])


def downgrade() -> None:
    op.drop_index(_PRE_IX, table_name=_PRE)
    op.drop_table(_PRE)
