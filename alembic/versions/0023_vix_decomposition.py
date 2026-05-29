"""index_skew_daily: add VIX-decomposition descriptors (term + β + richness)

Revision ID: 0023
Revises: 0022
Create Date: 2026-05-29

Extends ``index_skew_daily`` with the seven descriptors the unified vol-regime
classifier needs (``strategies/vol_regime.py``):

- ``vix9d``                 — Cboe VIX 9-day index close.
- ``vix3m``                 — Cboe VIX 3-month index close.
- ``vix6m``                 — Cboe VIX 6-month index close.
- ``vix_voli_spread``       — ``VIX - VOLI`` (wing premium contribution).
- ``vix_term_9d_30d``       — ``VIX9D - VIX`` (negative = backwardation = stress).
- ``vix_term_3m_30d``       — ``VIX3M - VIX`` (negative = transient stress expected).
- ``vix_spx_beta_60d``      — OLS β of %ΔVIX on %ΔSPX over 60d.
- ``vvix_vix_ratio``        — ``VVIX / VIX``.
- ``vix_options_richness``  — ``VVIX / (|β| × VIX)`` — VIX-options-expensive metric.

All nullable + additive — no data migration. Reversible (CLAUDE.md rule 3).
The EOD ``index_skew`` job fills these forward from today's run.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0023"
down_revision: str | None = "0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "index_skew_daily"

_NEW_COLS = (
    "vix9d",
    "vix3m",
    "vix6m",
    "vix_voli_spread",
    "vix_term_9d_30d",
    "vix_term_3m_30d",
    "vix_spx_beta_60d",
    "vvix_vix_ratio",
    "vix_options_richness",
)


def upgrade() -> None:
    for col in _NEW_COLS:
        op.add_column(_TABLE, sa.Column(col, sa.Float(), nullable=True))


def downgrade() -> None:
    for col in reversed(_NEW_COLS):
        op.drop_column(_TABLE, col)
