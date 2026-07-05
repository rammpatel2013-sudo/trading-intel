"""tas_daily_rollup: durable per-name + per-contract daily flow aggregates

Revision ID: 0029
Revises: 0028
Create Date: 2026-06-25

Adds the two DURABLE roll-up tables the EOD ``tas_daily_rollup`` job writes from
``tas_prints``. Raw prints are pruned at ``TAS_RETENTION_DAYS`` (~30d); these
aggregates are kept long-term so the accumulation/distribution scorecard can look
back months.

  - ``tas_daily_flow``     one row per (trade_date, root): buy/sell/call/put
                           premium, signed net $delta, dominant side.
  - ``tas_daily_contract`` one row per (trade_date, root, expiry, strike, cp):
                           the repeat-contract grain (which strikes are accumulated).

Descriptive flow only — neither table feeds the ``signals`` table (FlashAlpha
rule 4). Each natural key carries a UniqueConstraint for the job's idempotent
``ON CONFLICT … DO UPDATE`` upsert (rule 5). Reversible (rule 3): ``downgrade``
drops both tables.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0029"
down_revision: str | None = "0028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_FLOW = "tas_daily_flow"
_CONTRACT = "tas_daily_contract"
_UQ_FLOW = "uq_tas_daily_flow"
_UQ_CONTRACT = "uq_tas_daily_contract"
_IX_FLOW_DATE = "ix_tas_daily_flow_trade_date"
_IX_FLOW_ROOT = "ix_tas_daily_flow_root"
_IX_CONTRACT_DATE = "ix_tas_daily_contract_trade_date"
_IX_CONTRACT_ROOT = "ix_tas_daily_contract_root"


def upgrade() -> None:
    op.create_table(
        _FLOW,
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("root", sa.String(length=16), nullable=False),
        sa.Column("prints", sa.Integer(), nullable=True),
        sa.Column("total_notional", sa.Float(), nullable=True),
        sa.Column("call_notional", sa.Float(), nullable=True),
        sa.Column("put_notional", sa.Float(), nullable=True),
        sa.Column("buy_notional", sa.Float(), nullable=True),
        sa.Column("sell_notional", sa.Float(), nullable=True),
        sa.Column("net_dollar_delta", sa.Float(), nullable=True),
        sa.Column("gross_dollar_delta", sa.Float(), nullable=True),
        sa.Column("net_premium_call_put", sa.Float(), nullable=True),
        sa.Column("pct_buy", sa.Float(), nullable=True),
        sa.Column("dominant_side", sa.String(length=8), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("trade_date", "root", name=_UQ_FLOW),
    )
    op.create_index(_IX_FLOW_DATE, _FLOW, ["trade_date"])
    op.create_index(_IX_FLOW_ROOT, _FLOW, ["root"])

    op.create_table(
        _CONTRACT,
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("root", sa.String(length=16), nullable=False),
        sa.Column("expiry", sa.Date(), nullable=True),
        sa.Column("strike", sa.Float(), nullable=True),
        sa.Column("cp", sa.String(length=1), nullable=True),
        sa.Column("n_prints", sa.Integer(), nullable=True),
        sa.Column("total_notional", sa.Float(), nullable=True),
        sa.Column("total_size", sa.Integer(), nullable=True),
        sa.Column("avg_price", sa.Float(), nullable=True),
        sa.Column("buy_prints", sa.Integer(), nullable=True),
        sa.Column("sell_prints", sa.Integer(), nullable=True),
        sa.Column("buy_notional", sa.Float(), nullable=True),
        sa.Column("sell_notional", sa.Float(), nullable=True),
        sa.Column("net_dollar_delta", sa.Float(), nullable=True),
        sa.Column("dominant_side", sa.String(length=8), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("trade_date", "root", "expiry", "strike", "cp", name=_UQ_CONTRACT),
    )
    op.create_index(_IX_CONTRACT_DATE, _CONTRACT, ["trade_date"])
    op.create_index(_IX_CONTRACT_ROOT, _CONTRACT, ["root"])


def downgrade() -> None:
    op.drop_index(_IX_CONTRACT_ROOT, table_name=_CONTRACT)
    op.drop_index(_IX_CONTRACT_DATE, table_name=_CONTRACT)
    op.drop_table(_CONTRACT)
    op.drop_index(_IX_FLOW_ROOT, table_name=_FLOW)
    op.drop_index(_IX_FLOW_DATE, table_name=_FLOW)
    op.drop_table(_FLOW)
