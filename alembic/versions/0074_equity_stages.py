"""equity_stages + equity_convex_picks: stage per index member per day; daily convexity lists (2026-11-22)

Revision ID: 0074_equity_stages
Revises: 0073_index_cor_delta
Create Date: 2026-11-22

Hand-written (never autogenerate). One derived owner table, rebuildable from equity_bars, written by
scheduler/jobs/stage_breadth.py (first run back-fills every day since 2023; later runs redo the last 10 sessions).
Read by the stage breadth cards on the Risk page (via stagetab.json) and by MCP / ad-hoc queries.
equity_stages also carries the weekly (30-week Weinstein) stage and Mansfield relative strength per day.
equity_convex_picks = the daily convexity lists (scheduler/jobs/convexity.py: cheap upside / cheap downside /
rich premium / avoid), stored so each list can be scored later (paper track). Not applied anywhere before
2026-11-30, so both tables live in this one revision.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0074_equity_stages"
down_revision: str | None = "0073_index_cor_delta"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "equity_stages",
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("symbol", sa.String(16), nullable=False),
        sa.Column("code", sa.String(2), nullable=False),
        sa.Column("stage", sa.Integer(), nullable=False),
        sa.Column("xatr", sa.Float(), nullable=True),
        sa.Column("wstage", sa.Integer(), nullable=True),
        sa.Column("mrs", sa.Float(), nullable=True),
        sa.PrimaryKeyConstraint("date", "symbol", name="pk_equity_stages"),
    )
    op.create_index("ix_equity_stages_symbol_date", "equity_stages", ["symbol", "date"])
    op.create_table(
        "equity_convex_picks",
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("symbol", sa.String(16), nullable=False),
        sa.Column("bucket", sa.String(12), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=True),
        sa.Column("score", sa.Float(), nullable=True),
        sa.Column("spot", sa.Float(), nullable=True),
        sa.Column("iv30", sa.Float(), nullable=True),
        sa.Column("iv_rank", sa.Float(), nullable=True),
        sa.Column("hv10", sa.Float(), nullable=True),
        sa.Column("carry", sa.Float(), nullable=True),
        sa.Column("ts_30_90", sa.Float(), nullable=True),
        sa.Column("skew25", sa.Float(), nullable=True),
        sa.Column("skew_rank", sa.Float(), nullable=True),
        sa.Column("code", sa.String(2), nullable=True),
        sa.Column("wstage", sa.Integer(), nullable=True),
        sa.Column("mrs", sa.Float(), nullable=True),
        sa.Column("move20", sa.Float(), nullable=True),
        sa.Column("computed_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("date", "symbol", name="pk_equity_convex_picks"),
    )
    op.create_index("ix_equity_convex_picks_bucket_date", "equity_convex_picks", ["bucket", "date"])


def downgrade() -> None:
    op.drop_index("ix_equity_convex_picks_bucket_date", table_name="equity_convex_picks")
    op.drop_table("equity_convex_picks")
    op.drop_index("ix_equity_stages_symbol_date", table_name="equity_stages")
    op.drop_table("equity_stages")
