"""ETF board: etf_board_daily + symbol_map tags / trades_24h (2026-11-19)

Revision ID: 0064_etf_board
Revises: 0063_symbol_contract_map
Create Date: 2026-11-19

Hand-written (never autogenerate). Additive only.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0064_etf_board"
down_revision: str | None = "0063_symbol_contract_map"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

FLOAT_COLS = (
    "last prev_close chg_1d chg_5d open high low vwap chg_open volume relvol buy_usd sell_usd bsr strength "
    "building price_vol stretch mo1 mo2 corr10 corr20 call_vol put_vol pc_ratio iv iv_front iv_back iv_diff "
    "iv_chg_1d iv_chg_5d"
).split()


def upgrade() -> None:
    op.add_column("symbol_map", sa.Column("tags", sa.String(64), nullable=True))
    op.add_column("symbol_map", sa.Column("trades_24h", sa.Boolean(), nullable=True))
    op.create_table(
        "etf_board_daily",
        sa.Column("date", sa.Date(), primary_key=True),
        sa.Column("symbol", sa.String(16), primary_key=True),
        sa.Column("mo3", sa.Integer()),
        sa.Column("n_minutes", sa.Integer()),
        sa.Column("trades_24h", sa.Boolean()),
        sa.Column("computed_at", sa.DateTime()),
        *[sa.Column(c, sa.Float()) for c in FLOAT_COLS],
    )


def downgrade() -> None:
    op.drop_table("etf_board_daily")
    op.drop_column("symbol_map", "trades_24h")
    op.drop_column("symbol_map", "tags")
