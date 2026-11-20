"""Compass (Macro view): auction_daily + compass_daily, both derived (2026-11-20)

Revision ID: 0068_compass
Revises: 0067_focus_names
Create Date: 2026-11-20

Hand-written (never autogenerate). Two new tables only.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0068_compass"
down_revision: str | None = "0067_focus_names"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

AUCTION_FLOATS = "open high low close vwap vwap_sd poc vah val ib_high ib_low".split()
COMPASS_FLOATS = (
    "close ret1 ret5 ret20 sigma_move hv10 hv30 iv30 iv_rank carry ts_30_90 skew25 skew_rank bb_pctb "
    "dark_ratio dark_z prem_usd prem_x20 call_share gex gex_flip call_wall put_wall"
).split()


def upgrade() -> None:
    op.create_table(
        "auction_daily",
        sa.Column("symbol", sa.String(16), primary_key=True),
        sa.Column("date", sa.Date(), primary_key=True),
        sa.Column("day_type", sa.String(12)),
        sa.Column("open_vs_value", sa.String(8)),
        sa.Column("close_vs_value", sa.String(8)),
        sa.Column("poor_high", sa.Boolean()),
        sa.Column("poor_low", sa.Boolean()),
        sa.Column("n_minutes", sa.Integer()),
        sa.Column("source", sa.String(16), nullable=False, server_default="bars_1m"),
        sa.Column("computed_at", sa.DateTime()),
        *[sa.Column(c, sa.Float()) for c in AUCTION_FLOATS],
    )
    op.create_table(
        "compass_daily",
        sa.Column("symbol", sa.String(16), primary_key=True),
        sa.Column("date", sa.Date(), primary_key=True),
        sa.Column("grp", sa.String(16)),
        sa.Column("iv_src", sa.String(16)),
        sa.Column("flags", sa.String(400)),
        sa.Column("computed_at", sa.DateTime()),
        *[sa.Column(c, sa.Float()) for c in COMPASS_FLOATS],
    )
    op.create_index("ix_auction_daily_date", "auction_daily", ["date"])
    op.create_index("ix_compass_daily_date", "compass_daily", ["date"])


def downgrade() -> None:
    op.drop_index("ix_compass_daily_date", table_name="compass_daily")
    op.drop_index("ix_auction_daily_date", table_name="auction_daily")
    op.drop_table("compass_daily")
    op.drop_table("auction_daily")
