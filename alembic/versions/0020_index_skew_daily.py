"""index_skew_daily: daily index-level skew snapshot (Cboe SKEW + SDEX + ours)

Revision ID: 0020
Revises: 0019
Create Date: 2026-05-28

Adds the ``index_skew_daily`` table: one row per trading day holding the Cboe
SKEW close (BKM third-moment estimator over OTM SPX), the Nations SkewDex
(``SDEX``) close, our self-computed SPX 25Δ RR + percentiles, mirrored VVIX,
and the VIX-options-derived tail-hedging composite. Per ADR-003 §2.3 and §3.4.

Reversible (CLAUDE.md rule 3); un-pruned (the trailing distribution is the
percentile baseline, same convention as ``vol_richness``).
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0020"
down_revision: str | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "index_skew_daily"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("date", sa.Date(), primary_key=True),
        sa.Column("cboe_skew", sa.Float(), nullable=True),
        sa.Column("sdex", sa.Float(), nullable=True),
        sa.Column("spx_rr_25d_30d", sa.Float(), nullable=True),
        sa.Column("spx_rr_pctile_252d", sa.Float(), nullable=True),
        sa.Column("sdex_pctile_252d", sa.Float(), nullable=True),
        sa.Column("vvix", sa.Float(), nullable=True),
        sa.Column("vix_call_skew_25d", sa.Float(), nullable=True),
        sa.Column("vix_call_oi_share", sa.Float(), nullable=True),
        sa.Column("vix_tail_hedging_score", sa.Float(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table(_TABLE)
