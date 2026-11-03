"""Signed-position ledger tables: pos_days + pos_ledger.

Running ledger of signed trades (buy/sell) over each contract's life (0-11 DTE),
using the raw collector's volm_buy/sell, gammas_buy/sell, deltas_buy/sell,
value_buy/sell fields.  Stage-2 transform - no new API calls.

See: claude/signed-position-ledger-plan-20261028.md

Revision ID: 0051_pos_ledger
Revises: c7e1b94a2f60
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0051_pos_ledger"
down_revision = "c7e1b94a2f60"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- pos_days: one row per (trade_date, symbol, expiry, strike, right) ---
    op.create_table(
        "pos_days",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("symbol", sa.String(16), nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("expiry", sa.Date(), nullable=False),
        sa.Column("strike", sa.Float(), nullable=False),
        sa.Column("right", sa.String(1), nullable=False),
        sa.Column("dte_at_date", sa.Integer(), nullable=True),
        # Signed flow (customer side)
        sa.Column("vb", sa.Float(), nullable=True),
        sa.Column("vs", sa.Float(), nullable=True),
        sa.Column("vol", sa.Float(), nullable=True),
        sa.Column("gb", sa.Float(), nullable=True),
        sa.Column("gs", sa.Float(), nullable=True),
        sa.Column("db", sa.Float(), nullable=True),
        sa.Column("ds", sa.Float(), nullable=True),
        sa.Column("val_b", sa.Float(), nullable=True),
        sa.Column("val_s", sa.Float(), nullable=True),
        sa.Column("oi_am", sa.Integer(), nullable=True),
        sa.Column("snap_ts", sa.DateTime(), nullable=True),
        sa.Column("n_snaps", sa.Integer(), nullable=True),
        sa.UniqueConstraint(
            "symbol", "trade_date", "expiry", "strike", "right",
            name="uq_pos_days",
        ),
    )
    op.create_index(
        "ix_pos_days_symbol_date",
        "pos_days",
        ["symbol", "trade_date"],
    )

    # --- pos_ledger: running state as of each morning ---
    op.create_table(
        "pos_ledger",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("symbol", sa.String(16), nullable=False),
        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.Column("expiry", sa.Date(), nullable=False),
        sa.Column("strike", sa.Float(), nullable=False),
        sa.Column("right", sa.String(1), nullable=False),
        sa.Column("dte_at_date", sa.Integer(), nullable=True),
        # Variant A: pure flow accumulation
        sa.Column("pos_raw", sa.Float(), nullable=True),
        # Variant B: OI-reconciled
        sa.Column("pos_oi", sa.Float(), nullable=True),
        # Coverage
        sa.Column("coverage_days", sa.Integer(), nullable=True),
        sa.Column("days_since_listing", sa.Integer(), nullable=True),
        sa.Column("seed_gap", sa.Boolean(), nullable=True),
        # Flags
        sa.Column("low_confidence", sa.Boolean(), nullable=True),
        # Spread annotation
        sa.Column("gate_ratio", sa.Float(), nullable=True),
        sa.Column("spread_label", sa.String(32), nullable=True),
        # OI for reconciliation
        sa.Column("oi_prior", sa.Integer(), nullable=True),
        sa.Column("oi_current", sa.Integer(), nullable=True),
        sa.UniqueConstraint(
            "symbol", "as_of_date", "expiry", "strike", "right",
            name="uq_pos_ledger",
        ),
    )
    op.create_index(
        "ix_pos_ledger_symbol_date",
        "pos_ledger",
        ["symbol", "as_of_date"],
    )
    op.create_index(
        "ix_pos_ledger_expiry",
        "pos_ledger",
        ["expiry"],
    )


def downgrade() -> None:
    op.drop_table("pos_ledger")
    op.drop_table("pos_days")
