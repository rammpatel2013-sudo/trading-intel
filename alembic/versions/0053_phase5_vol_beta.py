"""Phase 5 tables: vix_signal_snapshots, straddle_snapshots, beta_reset_daily (2026-11-04).

Hand-written (never autogenerate). New tables only — nothing existing is touched.

Revision ID: 0053_phase5_vol_beta
Revises: 0052_tas_quote_loc
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0053_phase5_vol_beta"
down_revision = "0052_tas_quote_loc"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "vix_signal_snapshots",
        sa.Column("date", sa.Date(), primary_key=True),
        sa.Column("vix_actual", sa.Float()),
        sa.Column("vix_prev", sa.Float()),
        sa.Column("spx_ret_pct", sa.Float()),
        sa.Column("alpha", sa.Float()),
        sa.Column("beta", sa.Float()),
        sa.Column("vix_model", sa.Float()),
        sa.Column("residual", sa.Float()),
        sa.Column("flag", sa.Boolean()),
        sa.Column("n_fit", sa.Integer()),
        sa.Column("created_at", sa.DateTime()),
    )
    op.create_table(
        "straddle_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("symbol", sa.String(16), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("spot", sa.Float()),
        sa.Column("straddle", sa.Float()),
        sa.Column("pct_of_spot", sa.Float()),
        sa.Column("iv", sa.Float()),
        sa.Column("on_straddle", sa.Float()),
        sa.Column("source", sa.String(16)),
        sa.Column("created_at", sa.DateTime()),
        sa.UniqueConstraint("symbol", "date", name="uq_straddle_snapshots"),
    )
    op.create_table(
        "beta_reset_daily",
        sa.Column("date", sa.Date(), primary_key=True),
        sa.Column("spx", sa.Float()),
        sa.Column("splv", sa.Float()),
        sa.Column("sphb", sa.Float()),
        sa.Column("cor3m", sa.Float()),
        sa.Column("cor10d", sa.Float()),
        sa.Column("cor90d", sa.Float()),
        sa.Column("cor_skew_ratio", sa.Float()),
        sa.Column("splv_off_high", sa.Float()),
        sa.Column("spx_wk", sa.Float()),
        sa.Column("abs_break", sa.Boolean()),
        sa.Column("rel_break", sa.Boolean()),
        sa.Column("splv_beta20", sa.Float()),
        sa.Column("beta_hi", sa.Boolean()),
        sa.Column("cor_jump", sa.Boolean()),
        sa.Column("corr_break", sa.Boolean()),
        sa.Column("low_beta_state", sa.String(12)),
        sa.Column("episode_start", sa.Date()),
        sa.Column("beta_frozen", sa.Float()),
        sa.Column("sphb_resid", sa.Float()),
        sa.Column("high_beta_state", sa.String(12)),
        sa.Column("label", sa.String(48)),
    )


def downgrade() -> None:
    op.drop_table("beta_reset_daily")
    op.drop_table("straddle_snapshots")
    op.drop_table("vix_signal_snapshots")
