"""filing holdings: 13F holdings snapshots for the investor-letters pipeline

Revision ID: 0039
Revises: 0038
Create Date: 2026-07-19

Adds ``filing_holdings`` — per (CIK, report period, CUSIP) 13F snapshot rows the
``filings_fetch`` job banks from SEC EDGAR so a fund's holdings can be diffed
quarter-over-quarter (``docs/investor_letters_pipeline.md``). Unique on
(cik, period, cusip) for idempotent upserts (rule 5). Reversible (rule 3).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0039"
down_revision: str | None = "0038"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TBL = "filing_holdings"
_UQ = "uq_filing_holdings"
_IX = "ix_filing_holdings_cik_period"


def upgrade() -> None:
    op.create_table(
        _TBL,
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("cik", sa.String(length=10), nullable=False),
        sa.Column("fund", sa.String(length=64), nullable=True),
        sa.Column("period", sa.String(length=10), nullable=False),
        sa.Column("cusip", sa.String(length=12), nullable=False),
        sa.Column("issuer", sa.String(length=128), nullable=True),
        sa.Column("ticker", sa.String(length=16), nullable=True),
        sa.Column("value_usd", sa.Float(), nullable=True),
        sa.Column("shares", sa.Float(), nullable=True),
        sa.Column("fetched_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("cik", "period", "cusip", name=_UQ),
    )
    op.create_index(_IX, _TBL, ["cik", "period"])


def downgrade() -> None:
    op.drop_index(_IX, table_name=_TBL)
    op.drop_table(_TBL)
