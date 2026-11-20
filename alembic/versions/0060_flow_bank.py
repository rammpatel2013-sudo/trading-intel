"""flow bank: Convex und + flowchart, optionsdata flow categories, 1-minute ETF bars (2026-11-19)

Revision ID: 0060_flow_bank
Revises: 0059_db_foundation_a1
Create Date: 2026-11-19

Hand-written (never autogenerate). Creates ONLY the 4 tables in
``trading_intel/memory/models_flow.py``. Additive; touches no existing table.
Field lists are copied here on purpose (a migration must not change when the model does).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0060_flow_bank"
down_revision: str | None = "0059_db_foundation_a1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UND_FIELDS = (
    "price change day_volume option_volume call_volume put_volume put_call_ratio volatility "
    "front_volatility back_volatility value volm value_buy value_sell value_call_buy "
    "value_call_sell value_put_buy value_put_sell volm_buy volm_sell volm_call_buy volm_call_sell "
    "volm_put_buy volm_put_sell deltas_buy deltas_sell deltas_call_buy deltas_call_sell "
    "deltas_put_buy deltas_put_sell gammas_buy gammas_sell vegas_buy vegas_sell value_und volm_und "
    "value_bs volm_bs flowratio vflowratio value_call_ratio value_put_ratio volm_call_ratio "
    "volm_put_ratio gxoi dxoi vxoi vannaxoi charmxoi gxvolm dxvolm vannaxvolm charmxvolm".split()
)
FLOWCHART_COLS = (
    "flownet vflownet prop1 prop2 prop3 prop4 value_call_bs value_put_bs volm_call_bs "
    "volm_put_bs call_gxoi put_gxoi call_dxoi put_dxoi call_vxoi put_vxoi call_txoi "
    "put_txoi".split()
)


def upgrade() -> None:
    op.create_table(
        "flow_und_daily",
        sa.Column("date", sa.Date(), primary_key=True),
        sa.Column("symbol", sa.String(16), primary_key=True),
        sa.Column("captured_at", sa.DateTime()),
        *[sa.Column(f, sa.Float()) for f in UND_FIELDS],
    )
    op.create_index("ix_flow_und_daily_symbol", "flow_und_daily", ["symbol"])

    op.create_table(
        "flowchart_15s",
        sa.Column("symbol", sa.String(16), primary_key=True),
        sa.Column("ts", sa.DateTime(), primary_key=True),
        sa.Column("trade_date", sa.Date()),
        *[sa.Column(f, sa.Float()) for f in FLOWCHART_COLS],
    )
    op.create_index("ix_flowchart_15s_trade_date", "flowchart_15s", ["trade_date"])

    dialect = op.get_bind().dialect.name
    uq_kw = {"postgresql_nulls_not_distinct": True} if dialect == "postgresql" else {}
    op.create_table(
        "od_flow_daily",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("symbol", sa.String(16), nullable=False),
        sa.Column("expiration", sa.Date()),
        sa.Column("right", sa.String(4), nullable=False),
        sa.Column("category", sa.String(12), nullable=False),
        sa.Column("side", sa.String(10), nullable=False),
        sa.Column("premium_usd", sa.Float()),
        sa.Column("volume", sa.Float()),
        sa.Column("count", sa.Float()),
        sa.UniqueConstraint(
            "date", "symbol", "expiration", "right", "category", "side",
            name="uq_od_flow_daily", **uq_kw,
        ),
    )
    op.create_index("ix_od_flow_daily_date", "od_flow_daily", ["date"])

    op.create_table(
        "bars_1m",
        sa.Column("symbol", sa.String(16), primary_key=True),
        sa.Column("ts", sa.DateTime(), primary_key=True),
        sa.Column("source", sa.String(16), primary_key=True),
        sa.Column("open", sa.Float()),
        sa.Column("high", sa.Float()),
        sa.Column("low", sa.Float()),
        sa.Column("close", sa.Float()),
        sa.Column("volume", sa.Float()),
    )


def downgrade() -> None:
    op.drop_table("bars_1m")
    op.drop_index("ix_od_flow_daily_date", table_name="od_flow_daily")
    op.drop_table("od_flow_daily")
    op.drop_index("ix_flowchart_15s_trade_date", table_name="flowchart_15s")
    op.drop_table("flowchart_15s")
    op.drop_index("ix_flow_und_daily_symbol", table_name="flow_und_daily")
    op.drop_table("flow_und_daily")
