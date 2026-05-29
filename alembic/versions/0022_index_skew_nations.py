"""index_skew_daily: add Nations VOLI/TDEX + CallDex/PutDex/RiskDex proxies

Revision ID: 0022
Revises: 0021
Create Date: 2026-05-28

Extends ``index_skew_daily`` with the rest of the Nations Indexes vol family:

- ``voli``                — Nations VolDex Large-Cap close (Yahoo ``^VOLI``).
                            ATM-only IV gauge; cleaner than VIX which is a strip.
- ``tdex``                — Nations TailDex close (Yahoo ``^TDEX``).
                            Deep-OTM put cost; isolates tail-hedge demand.
- ``calldex_proxy``       — IV @ 15Δ call, 30d, vol points. Stand-in for the
                            Nations CallDex® (subscription-only) — same regime
                            information, computed from SPX/SPY chain.
- ``putdex_proxy``        — IV @ 15Δ put, 30d, vol points. PutDex proxy.
- ``riskdex_proxy``       — ``putdex_proxy / calldex_proxy`` — relative cost of
                            downside vs upside protection, RiskDex proxy.

Each gets a trailing-252d percentile companion column so the regime classifier
in ``strategies/vol_regime.py`` can z-rank without re-scanning history.

Reversible (CLAUDE.md rule 3). No data backfill — columns start NULL and fill
forward as the EOD ``index_skew`` job runs.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0022"
down_revision: str | None = "0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "index_skew_daily"

_NEW_COLS = (
    "voli",
    "voli_pctile_252d",
    "tdex",
    "tdex_pctile_252d",
    "calldex_proxy",
    "calldex_proxy_pctile_252d",
    "putdex_proxy",
    "putdex_proxy_pctile_252d",
    "riskdex_proxy",
    "riskdex_proxy_pctile_252d",
)


def upgrade() -> None:
    for col in _NEW_COLS:
        op.add_column(_TABLE, sa.Column(col, sa.Float(), nullable=True))


def downgrade() -> None:
    for col in reversed(_NEW_COLS):
        op.drop_column(_TABLE, col)
