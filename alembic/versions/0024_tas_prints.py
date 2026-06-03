"""tas_prints: market-wide option time-and-sales capture (Phase 3)

Revision ID: 0024
Revises: 0023
Create Date: 2026-06-02

Adds the ``tas_prints`` table: one decoded row per large option print captured
from the live, market-wide ConvexValue tape by the NAS ``tas_capture_job``.
Holds the raw contract ``symbol`` plus decoded ``root``/``expiry``/``strike``/
``cp``, the inferred ``side``, ``price``/``size``/``notional`` and the per-print
greeks/spot. Idempotent on the natural print key (ts, symbol, price, size,
source). Indexed by ``trade_date`` (for the EOD roll-up + retention prune) and
by ``root`` (per-name lookups). Descriptive flow only — rule 4.

Reversible. Raw prints are pruned by ``prune_tas_prints`` (default 30 days); the
small per-day summary lives in a later migration alongside its job.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0024"
down_revision: str | None = "0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "tas_prints"
_UQ = "uq_tas_prints"
_IX_DATE = "ix_tas_prints_trade_date"
_IX_ROOT = "ix_tas_prints_root"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("captured_at", sa.DateTime(), nullable=False),
        sa.Column("ts", sa.DateTime(), nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("symbol", sa.String(length=32), nullable=False),
        sa.Column("root", sa.String(length=16), nullable=True),
        sa.Column("expiry", sa.Date(), nullable=True),
        sa.Column("strike", sa.Float(), nullable=True),
        sa.Column("cp", sa.String(length=1), nullable=True),
        sa.Column("side", sa.String(length=8), nullable=True),
        sa.Column("price", sa.Float(), nullable=True),
        sa.Column("size", sa.Integer(), nullable=True),
        sa.Column("notional", sa.Float(), nullable=True),
        sa.Column("spot", sa.Float(), nullable=True),
        sa.Column("delta", sa.Float(), nullable=True),
        sa.Column("gamma", sa.Float(), nullable=True),
        sa.Column("vega", sa.Float(), nullable=True),
        sa.Column("theta", sa.Float(), nullable=True),
        sa.Column("iv", sa.Float(), nullable=True),
        sa.Column("source", sa.String(length=32), nullable=False, server_default="convex"),
        sa.UniqueConstraint("ts", "symbol", "price", "size", "source", name=_UQ),
    )
    op.create_index(_IX_DATE, _TABLE, ["trade_date"])
    op.create_index(_IX_ROOT, _TABLE, ["root"])


def downgrade() -> None:
    op.drop_index(_IX_ROOT, table_name=_TABLE)
    op.drop_index(_IX_DATE, table_name=_TABLE)
    op.drop_table(_TABLE)
