"""fundamentals_snapshots: weekly factor inputs + cross-sectional scores

Revision ID: 0033
Revises: 0032
Create Date: 2026-07-16

Adds ``fundamentals_snapshots`` — one row per (symbol, week) banking the raw
fundamental/momentum inputs pulled from CVForge FMP (ADR-005, no new vendor) plus
the universe-relative factor z-scores (Value/Quality/Growth/Momentum/Risk) and
the weighted composite computed by ``trading_intel.factors``. Written by
``scheduler/jobs/factor_scores.py``.

Unique on (symbol, ts) for the idempotent weekly ``ON CONFLICT ... DO UPDATE``
upsert (CLAUDE.md rule 5). Reversible (rule 3): ``downgrade`` drops the table.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0033"
down_revision: str | None = "0032"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "fundamentals_snapshots"
_UQ = "uq_fundamentals_snapshots"
_IX = "ix_fundamentals_snapshots_symbol_ts"

_RAW = (
    "pe",
    "pb",
    "ps",
    "ev_ebitda",
    "roe",
    "roic",
    "gross_margin",
    "net_margin",
    "fcf_margin",
    "debt_to_equity",
    "current_ratio",
    "revenue_growth",
    "eps_growth",
    "beta",
    "ret_3m",
    "ret_12m",
)
_SCORES = (
    "value_score",
    "quality_score",
    "growth_score",
    "momentum_score",
    "risk_score",
    "composite_score",
)


def upgrade() -> None:
    cols = [
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("symbol", sa.String(length=16), sa.ForeignKey("tickers.symbol"), nullable=False),
        sa.Column("ts", sa.Date(), nullable=False),
    ]
    cols += [sa.Column(name, sa.Float(), nullable=True) for name in (*_RAW, *_SCORES)]
    cols.append(sa.Column("source", sa.String(length=32), nullable=True))
    cols.append(sa.UniqueConstraint("symbol", "ts", name=_UQ))
    op.create_table(_TABLE, *cols)
    op.create_index(_IX, _TABLE, ["symbol", "ts"])


def downgrade() -> None:
    op.drop_index(_IX, table_name=_TABLE)
    op.drop_table(_TABLE)
