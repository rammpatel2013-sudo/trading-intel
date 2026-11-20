"""symbol_map + contracts, daily bars into equity_bars (2026-11-19, DB wave A2 steps 6 + 7a)

Revision ID: 0063_symbol_contract_map
Revises: 0062_tape_sessions
Create Date: 2026-11-19

Hand-written (never autogenerate).
- symbol_map: one symbol master (our spelling + vendor spellings), filled by job symbol_map_sync.
- contracts: one option master keyed by the OCC symbol + vendor IDs, filled by job contracts_sync.
- Step 7a (rule 1, equity_bars owns daily bars): copy quotes_daily and desk_bars rows that equity_bars does not
  have yet (checked 11/19: 117,489 quotes_daily and 83 desk_bars rows already there; 2 differ by > 1%, equity_bars
  kept). From now on both writers also write equity_bars. The two tables stay (views later, step 7b).
Downgrade drops the two new tables; the copied bars stay in equity_bars (data only, no reader depends on absence).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0063_symbol_contract_map"
down_revision: str | None = "0062_tape_sessions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "symbol_map",
        sa.Column("symbol", sa.String(16), primary_key=True),
        sa.Column("asset_type", sa.String(8), nullable=False),
        sa.Column("name", sa.String(160)),
        sa.Column("convex", sa.String(24)),
        sa.Column("cvforge", sa.String(24)),
        sa.Column("od", sa.String(24)),
        sa.Column("od_iid", sa.BigInteger()),
        sa.Column("fmp", sa.String(24)),
        sa.Column("finra", sa.String(24)),
        sa.Column("yahoo", sa.String(24)),
        sa.Column("ingested_at", sa.DateTime()),
    )
    op.create_table(
        "contracts",
        sa.Column("occ", sa.String(21), primary_key=True),
        sa.Column("underlying", sa.String(16), nullable=False),
        sa.Column("root", sa.String(6), nullable=False),
        sa.Column("expiry", sa.Date(), nullable=False),
        sa.Column("cp", sa.String(1), nullable=False),
        sa.Column("strike", sa.Float(), nullable=False),
        sa.Column("settlement", sa.String(2)),
        sa.Column("exercise", sa.String(10)),
        sa.Column("multiplier", sa.Float()),
        sa.Column("od_iid", sa.BigInteger()),
        sa.Column("polygon", sa.String(32)),
        sa.Column("dxfeed", sa.String(32)),
        sa.Column("first_seen", sa.Date()),
        sa.Column("last_seen", sa.Date()),
        sa.Column("ingested_at", sa.DateTime()),
    )
    op.create_index("ix_contracts_und_exp", "contracts", ["underlying", "expiry"])
    op.create_index("ix_contracts_od_iid", "contracts", ["od_iid"], unique=True)
    for src in ("quotes_daily", "desk_bars"):
        op.execute(
            f"""INSERT INTO equity_bars (symbol, date, open, high, low, close, volume)
                SELECT symbol, date, open, high, low, close, volume FROM {src}
                WHERE close IS NOT NULL
                ON CONFLICT (symbol, date) DO NOTHING"""
        )


def downgrade() -> None:
    op.drop_index("ix_contracts_od_iid", table_name="contracts")
    op.drop_index("ix_contracts_und_exp", table_name="contracts")
    op.drop_table("contracts")
    op.drop_table("symbol_map")
