"""oi_chain_eod: closing quotes + aggressor-split greek flow.

Six nullable columns on the EOD per-strike chain, all filled by widening the
Convex chain pull we already make (same single request — params are a list
inside one POST, so this adds no API calls).

Why: ``gxoi`` is gamma x OPEN INTEREST, and open interest has no side. The
dealer sign we apply on top is an assumption, and on SPX it forces the book
negative 33 sessions out of 36. ``gammas_buy``/``gammas_sell`` are the vendor's
own aggressor-side split, so they carry a real side. ``bid_price``/``ask_price``
give a true mid for the Breeden-Litzenberger density.

Purely additive: all nullable, no backfill, nothing reads them yet.

Revision ID: c7e1b94a2f60
Revises: b3d8e5a1c074
Create Date: 2026-10-25
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "c7e1b94a2f60"
down_revision = "b3d8e5a1c074"
branch_labels = None
depends_on = None

_COLS = (
    "bid_price",
    "ask_price",
    "gammas_buy",
    "gammas_sell",
    "deltas_buy",
    "deltas_sell",
)


def upgrade() -> None:
    for col in _COLS:
        op.add_column("oi_chain_eod", sa.Column(col, sa.Float(), nullable=True))


def downgrade() -> None:
    for col in reversed(_COLS):
        op.drop_column("oi_chain_eod", col)
