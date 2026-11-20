"""tape sessions: tas_prints.session + tas_session_flow (2026-11-19, wave A1b)

Revision ID: 0062_tape_sessions
Revises: 0061_letf_close_flow
Create Date: 2026-11-19

Hand-written (never autogenerate). Additive: one nullable column on tas_prints (no backfill - older
rows were RTH-only captures) and one new roll-up table.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0062_tape_sessions"
down_revision: Union[str, None] = "0061_letf_close_flow"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("tas_prints", sa.Column("session", sa.String(5), nullable=True))
    op.create_table(
        "tas_session_flow",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("session", sa.String(5), nullable=False),
        sa.Column("root", sa.String(16), nullable=False),
        sa.Column("prints", sa.Integer()),
        sa.Column("contracts", sa.Float()),
        sa.Column("total_notional", sa.Float()),
        sa.Column("call_notional", sa.Float()),
        sa.Column("put_notional", sa.Float()),
        sa.Column("buy_notional", sa.Float()),
        sa.Column("sell_notional", sa.Float()),
        sa.Column("net_dollar_delta", sa.Float()),
        sa.Column("first_ts", sa.DateTime()),
        sa.Column("last_ts", sa.DateTime()),
        sa.UniqueConstraint("trade_date", "session", "root", name="uq_tas_session_flow"),
    )
    op.create_index("ix_tas_session_flow_trade_date", "tas_session_flow", ["trade_date"])


def downgrade() -> None:
    op.drop_index("ix_tas_session_flow_trade_date", table_name="tas_session_flow")
    op.drop_table("tas_session_flow")
    op.drop_column("tas_prints", "session")
