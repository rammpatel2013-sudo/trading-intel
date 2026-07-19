"""vix_expirations: standard monthly VIX expiration calendar table

Revision ID: 0026
Revises: 0025
Create Date: 2026-06-14

Creates ``vix_expirations`` — one row per standard (monthly) VIX settlement
date. Deterministic Cboe calendar (Wednesday 30 days before the following
month's third-Friday SPX expiration, rolled back on holidays), so the table is
populated by ``scheduler/jobs/vix_expirations.py`` from ``vol.vix_calendar``
with no vendor call. Reversible (CLAUDE.md rule 3).

Columns:
- ``expiration``        — PK; the VIX settlement date (Wed, or Tue if rolled).
- ``spx_ref_expiry``    — the paired SPX third Friday this expiry is 30d before.
- ``holiday_adjusted``  — True when the row rolled off the normal Wednesday.
- ``updated_at``        — date the row was last (re)computed.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0026"
down_revision: str | None = "0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "vix_expirations"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("expiration", sa.Date(), primary_key=True, nullable=False),
        sa.Column("spx_ref_expiry", sa.Date(), nullable=False),
        sa.Column("holiday_adjusted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("updated_at", sa.Date(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table(_TABLE)
