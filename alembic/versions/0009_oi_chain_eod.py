"""oi_chain_eod: end-of-day wide (~180d) per-strike chain for OI/flow change study

Revision ID: 0009
Revises: 0008
Create Date: 2026-05-23

Adds the ``oi_chain_eod`` table: one row per (symbol, ts[day], expiry, strike,
cp) holding open interest, the vendor's day-over-day OI change (Convex
``oi_ch``), traded volume, signed greek-OI exposures (gxoi/dxoi/vxoi) and the
raw greeks/iv. Feeds the day-over-day positioning analytics (volume vs OI vs
ΔOI vs ΔGEX). Unique on the natural key for idempotent upserts (CLAUDE.md rule
5). Regime-descriptor data only (FlashAlpha rule 4).
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "oi_chain_eod"
_UQ = "uq_oi_chain_eod"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("symbol", sa.String(length=16), nullable=False),
        sa.Column("ts", sa.DateTime(), nullable=False),
        sa.Column("expiry", sa.Date(), nullable=False),
        sa.Column("strike", sa.Float(), nullable=False),
        sa.Column("cp", sa.String(length=1), nullable=False),
        sa.Column("dte", sa.Integer(), nullable=True),
        sa.Column("oi", sa.Integer(), nullable=True),
        sa.Column("oi_change", sa.Integer(), nullable=True),
        sa.Column("volume", sa.Integer(), nullable=True),
        sa.Column("delta", sa.Float(), nullable=True),
        sa.Column("gamma", sa.Float(), nullable=True),
        sa.Column("iv", sa.Float(), nullable=True),
        sa.Column("gxoi", sa.Float(), nullable=True),
        sa.Column("dxoi", sa.Float(), nullable=True),
        sa.Column("vxoi", sa.Float(), nullable=True),
        sa.Column("source", sa.String(length=32), nullable=False, server_default="convex"),
        sa.UniqueConstraint("symbol", "ts", "source", "expiry", "strike", "cp", name=_UQ),
    )
    op.create_index("ix_oi_chain_eod_symbol_ts", _TABLE, ["symbol", "ts"])


def downgrade() -> None:
    op.drop_index("ix_oi_chain_eod_symbol_ts", table_name=_TABLE)
    op.drop_table(_TABLE)
