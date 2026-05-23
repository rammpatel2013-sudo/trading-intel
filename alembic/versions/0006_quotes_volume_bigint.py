"""quotes_daily.volume: widen Integer -> BigInteger

Revision ID: 0006
Revises: 0005
Create Date: 2026-05-22

Underlying/aggregate daily volume can exceed the 32-bit Integer range
(2,147,483,647) — index proxies like ^GSPC report market-wide volume in the
billions, which overflowed the column (psycopg DataError). Widen to BigInteger.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "quotes_daily",
        "volume",
        existing_type=sa.Integer(),
        type_=sa.BigInteger(),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "quotes_daily",
        "volume",
        existing_type=sa.BigInteger(),
        type_=sa.Integer(),
        existing_nullable=False,
    )
