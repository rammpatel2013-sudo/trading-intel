"""desk brief (nextSignals Equity Desk Brief rebuilt on our data)

Revision ID: 0058_desk_brief
Revises: 0057_equity_foundation
Create Date: 2026-11-14

Hand-written (never autogenerate). Creates ONLY the 7 tables in
``trading_intel/memory/models_desk.py``. Additive; touches no existing table.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0058_desk_brief"
down_revision: Union[str, None] = "0057_equity_foundation"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

F = sa.Float
TABLES = (
    "desk_brief_daily",
    "desk_picks",
    "desk_names",
    "desk_bars",
    "desk_gamma_daily",
    "desk_und_daily",
    "finra_short_volume",
)


def upgrade() -> None:
    op.create_table(
        "finra_short_volume",
        sa.Column("date", sa.Date(), primary_key=True),
        sa.Column("symbol", sa.String(16), primary_key=True),
        sa.Column("short_volume", F()),
        sa.Column("short_exempt", F()),
        sa.Column("total_volume", F()),
    )
    op.create_index("ix_finra_short_volume_symbol", "finra_short_volume", ["symbol"])
    op.create_table(
        "desk_und_daily",
        sa.Column("date", sa.Date(), primary_key=True),
        sa.Column("symbol", sa.String(16), primary_key=True),
        *[
            sa.Column(c, F())
            for c in (
                "price",
                "change",
                "option_volume",
                "call_volume",
                "put_volume",
                "call_buy",
                "call_sell",
                "put_buy",
                "put_sell",
                "gxoi",
                "iv",
            )
        ],
        sa.Column("captured_at", sa.DateTime()),
    )
    op.create_table(
        "desk_gamma_daily",
        sa.Column("date", sa.Date(), primary_key=True),
        sa.Column("symbol", sa.String(16), primary_key=True),
        sa.Column("spot", F()),
        sa.Column("st_gex", F()),
        sa.Column("total_gex", F()),
        sa.Column("st_days", sa.Integer()),
    )
    op.create_table(
        "desk_bars",
        sa.Column("symbol", sa.String(16), primary_key=True),
        sa.Column("date", sa.Date(), primary_key=True),
        sa.Column("open", F()),
        sa.Column("high", F()),
        sa.Column("low", F()),
        sa.Column("close", F(), nullable=False),
        sa.Column("volume", sa.BigInteger()),
    )
    op.create_index("ix_desk_bars_date", "desk_bars", ["date"])
    op.create_table(
        "desk_names",
        sa.Column("symbol", sa.String(16), primary_key=True),
        sa.Column("name", sa.String(160)),
        sa.Column("sector", sa.String(48)),
        sa.Column("industry", sa.String(96)),
        sa.Column("is_etf", sa.Boolean()),
        sa.Column("avg_volume", F()),
        sa.Column("price", F()),
        sa.Column("market_cap", F()),
        sa.Column("beta", F()),
        sa.Column("updated", sa.Date()),
    )
    op.create_table(
        "desk_picks",
        sa.Column("date", sa.Date(), primary_key=True),
        sa.Column("source", sa.String(8), primary_key=True),
        sa.Column("symbol", sa.String(16), primary_key=True),
        sa.Column("role", sa.String(8), primary_key=True),
        sa.Column("info", sa.JSON(), nullable=True),
    )
    op.create_table(
        "desk_brief_daily",
        sa.Column("date", sa.Date(), primary_key=True),
        sa.Column("n_screen", sa.Integer()),
        sa.Column("top_pick", sa.String(16)),
        sa.Column("payload", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime()),
    )


def downgrade() -> None:
    op.drop_index("ix_desk_bars_date", table_name="desk_bars")
    op.drop_index("ix_finra_short_volume_symbol", table_name="finra_short_volume")
    for t in TABLES:
        op.drop_table(t)
