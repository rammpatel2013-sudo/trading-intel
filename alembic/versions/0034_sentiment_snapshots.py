"""sentiment_snapshots: institutional 13F + analyst ratings/targets

Revision ID: 0034
Revises: 0033
Create Date: 2026-07-17

Adds ``sentiment_snapshots`` — one row per (symbol, ts) banking the latest 13F
institutional-ownership snapshot + analyst price-target/rating consensus pulled from
CVForge FMP (ADR-005, no new vendor), plus two pure derivations (implied upside to the
average target, Buy-share of the panel). Written by ``scheduler/jobs/sentiment.py``.

Unique on (symbol, ts) for the idempotent ``ON CONFLICT ... DO UPDATE`` upsert
(CLAUDE.md rule 5). Reversible (rule 3): ``downgrade`` drops the table.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0034"
down_revision: str | None = "0033"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "sentiment_snapshots"
_UQ = "uq_sentiment_snapshots"
_IX = "ix_sentiment_snapshots_symbol_ts"

_FLOAT = (
    "inst_pct",
    "inst_holders",
    "inst_shares",
    "inst_net_share_change",
    "inst_new_positions",
    "inst_closed_positions",
    "inst_put_call",
    "pt_avg",
    "pt_high",
    "pt_low",
    "rating_buy",
    "rating_hold",
    "rating_sell",
    "num_analysts",
    "price",
    "pt_upside_pct",
    "buy_share",
)


def upgrade() -> None:
    cols = [
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("symbol", sa.String(length=16), sa.ForeignKey("tickers.symbol"), nullable=False),
        sa.Column("ts", sa.Date(), nullable=False),
    ]
    cols += [sa.Column(name, sa.Float(), nullable=True) for name in _FLOAT]
    cols.append(sa.Column("rating_consensus", sa.String(length=16), nullable=True))
    cols.append(sa.Column("source", sa.String(length=32), nullable=True))
    cols.append(sa.UniqueConstraint("symbol", "ts", name=_UQ))
    op.create_table(_TABLE, *cols)
    op.create_index(_IX, _TABLE, ["symbol", "ts"])


def downgrade() -> None:
    op.drop_index(_IX, table_name=_TABLE)
    op.drop_table(_TABLE)
