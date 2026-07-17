"""index_skew_daily: add Cboe VIXEQ / DSPX dispersion columns

Revision ID: 0027
Revises: 0026
Create Date: 2026-06-14

Extends ``index_skew_daily`` with the Cboe constituent-volatility / dispersion
family (single-stock leg of the dispersion trade):

- ``vixeq``             — Cboe S&P 500 Constituent Volatility Index (^VIXEQ).
- ``vixeq_pctile_252d`` — trailing-252d percentile of ``vixeq``.
- ``dspx``              — Cboe S&P 500 Dispersion Index (^DSPX); DSPX^2 = VIXEQ^2 - VIX^2.
- ``dspx_pctile_252d``  — trailing-252d percentile of ``dspx``.
- ``vixeq_vix_spread``  — VIXEQ - VIX (wide = high dispersion / low correlation).

All nullable + additive — no data migration. Reversible (CLAUDE.md rule 3).
The EOD ``index_skew`` job fills these forward; ``backfill_index_skew`` can
hydrate history from Yahoo (VIXEQ from ~Nov 2024, DSPX from ~Sep 2023).
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0027"
down_revision: str | None = "0026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "index_skew_daily"

_NEW_COLS = (
    "vixeq",
    "vixeq_pctile_252d",
    "dspx",
    "dspx_pctile_252d",
    "vixeq_vix_spread",
)


def upgrade() -> None:
    for col in _NEW_COLS:
        op.add_column(_TABLE, sa.Column(col, sa.Float(), nullable=True))


def downgrade() -> None:
    for col in reversed(_NEW_COLS):
        op.drop_column(_TABLE, col)
