"""surface_reports: nightly interpretive surface + flow report per ticker

Revision ID: 0012
Revises: 0011
Create Date: 2026-05-24

One row per (symbol, as_of) holding the markdown 3-part surface + flow report
written nightly by the surface-report job (surface metrics from the latest
oi_chain_eod snapshot + stored option flow + KB grounding, via the LLM). Shown on
the Vol Lab page. Descriptive regime read-through only (FlashAlpha rule 4).
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "surface_reports"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("symbol", sa.String(length=16), nullable=False),
        sa.Column("as_of", sa.Date(), nullable=False),
        sa.Column("report_md", sa.Text(), nullable=False),
        sa.Column("flow_source", sa.String(length=32), nullable=True),
        sa.Column("model", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("symbol", "as_of", name="uq_surface_reports"),
    )
    op.create_index("ix_surface_reports_symbol", _TABLE, ["symbol"])


def downgrade() -> None:
    op.drop_index("ix_surface_reports_symbol", table_name=_TABLE)
    op.drop_table(_TABLE)
