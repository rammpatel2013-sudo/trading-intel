"""greeks_chain: add source column + unique key for idempotent snapshots

Revision ID: 0004
Revises: 0003
Create Date: 2026-05-22

The per-strike ``greeks_chain`` table (created in 0001) had no natural key, so
the daily chain-snapshot collector could not upsert idempotently (CLAUDE.md
rule 5). This adds a ``source`` column (matching every other snapshot table) and
a unique constraint on (symbol, ts, source, expiry, strike, cp) so re-running a
slot is a no-op via ``INSERT ... ON CONFLICT DO NOTHING``.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_UQ = "uq_greeks_chain"
_UQ_COLS = ["symbol", "ts", "source", "expiry", "strike", "cp"]


def upgrade() -> None:
    op.add_column(
        "greeks_chain",
        sa.Column("source", sa.String(length=32), nullable=False, server_default="convex"),
    )
    op.create_unique_constraint(_UQ, "greeks_chain", _UQ_COLS)


def downgrade() -> None:
    op.drop_constraint(_UQ, "greeks_chain", type_="unique")
    op.drop_column("greeks_chain", "source")
