"""index_skew_daily: add Cboe implied-correlation / dispersion columns

Revision ID: 0025
Revises: 0024
Create Date: 2026-06-14

Extends ``index_skew_daily`` with the Cboe S&P 500 implied-correlation family:

- ``cor1m``               — Cboe 1-month implied correlation (^COR1M) close.
- ``cor1m_pctile_252d``   — trailing-252d percentile of ``cor1m``.
- ``cor3m``               — Cboe 3-month implied correlation (^COR3M) close.
- ``cor3m_pctile_252d``   — trailing-252d percentile of ``cor3m``.

High correlation = index-vol-led / "everything moves together" regime; low =
dispersion. The 1m-vs-3m slope mirrors a VIX term-structure read for
correlation. All nullable + additive — no data migration. Reversible
(CLAUDE.md rule 3). The EOD ``index_skew`` job fills these forward; the
``backfill_index_skew`` script can hydrate history from Yahoo.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0025"
down_revision: str | None = "0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "index_skew_daily"

_NEW_COLS = (
    "cor1m",
    "cor1m_pctile_252d",
    "cor3m",
    "cor3m_pctile_252d",
)


def upgrade() -> None:
    for col in _NEW_COLS:
        op.add_column(_TABLE, sa.Column(col, sa.Float(), nullable=True))


def downgrade() -> None:
    for col in reversed(_NEW_COLS):
        op.drop_column(_TABLE, col)
