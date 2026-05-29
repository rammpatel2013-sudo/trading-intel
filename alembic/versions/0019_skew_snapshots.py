"""skew_snapshots: daily per-name volatility skew descriptors + percentiles

Revision ID: 0019
Revises: 0018
Create Date: 2026-05-28

Adds the ``skew_snapshots`` table: one row per (symbol, trading-day, horizon_dte)
holding the 10Δ/25Δ risk reversals and butterflies, their trailing-window
percentiles (63d ~ 3-month, 252d ~ 1-year), the front-vs-back skew slope, the
name's VIX beta (60d OLS), the abnormal RR change (the residual of Δrr after the
VIX-beta-implied component is removed), the shift-vs-slide label decomposing the
day's surface move, and the descriptive label. Unique on the natural key for
idempotent EOD upserts (CLAUDE.md rule 5).

**UN-PRUNED** by design: this table is the long skew percentile baseline the
standardization reads back (``oi_chain_eod`` retains only 90d, so it cannot serve
that role). Per ADR-003 (revision 2), skew is signal-eligible — but only
``strategies/skew.py`` writes to the ``signals`` table; this descriptor table
stays under ``vol/``.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0019"
down_revision: str | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "skew_snapshots"
_UQ = "uq_skew_snapshots"
_IX = "ix_skew_snapshots_symbol_ts"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "symbol",
            sa.String(length=16),
            sa.ForeignKey("tickers.symbol"),
            nullable=False,
        ),
        sa.Column("ts", sa.Date(), nullable=False),
        sa.Column("horizon_dte", sa.Integer(), nullable=False),
        # Surface points (vol points unless noted).
        sa.Column("atm_iv", sa.Float(), nullable=True),
        sa.Column("rr_10d", sa.Float(), nullable=True),   # iv_put_10Δ - iv_call_10Δ
        sa.Column("rr_25d", sa.Float(), nullable=True),   # iv_put_25Δ - iv_call_25Δ
        sa.Column("bf_10d", sa.Float(), nullable=True),   # avg(wing) - atm
        sa.Column("bf_25d", sa.Float(), nullable=True),
        # Standardization to the name's own trailing distribution.
        sa.Column("rr_25d_pctile_63d", sa.Float(), nullable=True),
        sa.Column("rr_25d_pctile_252d", sa.Float(), nullable=True),
        sa.Column("bf_25d_pctile_252d", sa.Float(), nullable=True),
        # Term-structure of skew + index-relative read.
        sa.Column("front_back_rr_slope", sa.Float(), nullable=True),
        sa.Column("vix_beta_60d", sa.Float(), nullable=True),
        sa.Column("rr_25d_abnormal", sa.Float(), nullable=True),
        # Move decomposition (per the shift-vs-slide playbook).
        sa.Column("shift_slide_label", sa.String(length=16), nullable=True),
        sa.Column("label", sa.String(length=64), nullable=True),
        sa.UniqueConstraint("symbol", "ts", "horizon_dte", name=_UQ),
    )
    op.create_index(_IX, _TABLE, ["symbol", "ts"])


def downgrade() -> None:
    op.drop_index(_IX, table_name=_TABLE)
    op.drop_table(_TABLE)
