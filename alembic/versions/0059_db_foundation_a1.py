"""DB foundation wave A1 (2026-11-19): pipeline_runs, NULL-safe unique keys, two dead tables, tape backfill

Revision ID: 0059_db_foundation_a1
Revises: 0058_desk_brief
Create Date: 2026-11-19

Hand-written (never autogenerate). Project doc ``claude/db-schema-contract-20261114.md`` fix steps 3-5 +
DB review round 1 (``claude/db-review-round1-20261119.md``) items D5 / D18. Checked read-only on the live DB
2026-11-19 before writing: 0 rows in flow_buckets / alerts_sent; 0 duplicate or NULL-key rows in the three
unique keys made NULL-safe here; 0 duplicates in chunks / equity_members; 427 tas_prints rows with no strike.

1. ``pipeline_runs`` (one row per scheduled job run: start / finish / status / rows / error) - replaces the
   ``scheduled_jobs_state`` table CLAUDE.md rule 5 names but that never existed.
2. ``uq_tas_prints``, ``uq_tas_daily_contract``, ``uq_watchlist_entries`` re-created ``NULLS NOT DISTINCT``
   (PostgreSQL treats NULLs as all different, so a NULL in a key column let duplicates past the check).
3. New unique keys: ``chunks`` (document_id, chunk_idx), ``equity_members`` (symbol, index_code, start_date).
   NOT added (decided 2026-11-19): ``signals`` - several NORSEMAN_EVENT rows on one day are distinct events,
   and vol_regime re-states a day when its overlays change, so (symbol, ts, signal_type) is not its key;
   ``theme_observations`` - one document can mention the same theme + symbol in several chunks.
4. Drop the never-used ``flow_buckets`` and ``alerts_sent`` (downgrade re-creates them as in 0001).
5. Backfill root / expiry / cp / strike on tas_prints rows the old decoder could not read (adjusted
   contracts with a digit in the root: BABA2, TSLL1, WDC1, ETHA1, TSLQ1). Downgrade leaves them filled.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0059_db_foundation_a1"
down_revision: Union[str, None] = "0058_desk_brief"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_NULL_SAFE = (
    ("tas_prints", "uq_tas_prints", ("ts", "symbol", "price", "size", "source")),
    ("tas_daily_contract", "uq_tas_daily_contract", ("trade_date", "root", "expiry", "strike", "cp")),
    ("watchlist_entries", "uq_watchlist_entries", ("symbol", "source_doc_id")),
)
_NEW_UQ = (
    ("chunks", "uq_chunks_doc_idx", ("document_id", "chunk_idx")),
    ("equity_members", "uq_equity_members_interval", ("symbol", "index_code", "start_date")),
)


def _pg() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    op.create_table(
        "pipeline_runs",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), primary_key=True, autoincrement=True),
        sa.Column("job", sa.String(64), nullable=False),
        sa.Column("run_date", sa.Date(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("rows_attempted", sa.Integer(), nullable=True),
        sa.Column("rows_written", sa.Integer(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("host", sa.String(64), nullable=True),
    )
    op.create_index("ix_pipeline_runs_job_date", "pipeline_runs", ["job", "run_date"])

    if _pg():
        for table, name, cols in _NULL_SAFE:
            op.execute(f'ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {name}')
            op.execute(f'ALTER TABLE {table} ADD CONSTRAINT {name} UNIQUE NULLS NOT DISTINCT ({", ".join(cols)})')
        for table, name, cols in _NEW_UQ:
            op.execute(f'ALTER TABLE {table} ADD CONSTRAINT {name} UNIQUE NULLS NOT DISTINCT ({", ".join(cols)})')
        # adjusted roots (digit in the root): the decoder before 2026-11-19 took letters only -> no strike stored
        op.execute(
            r"""
            UPDATE tas_prints t SET root = m.r[1], expiry = to_date(m.r[2], 'YYMMDD'), cp = m.r[3],
                   strike = m.r[4]::double precision
            FROM (SELECT id, regexp_match(symbol, '^\.?([A-Z][A-Z0-9]*?)(\d{6})([CP])(\d+(?:\.\d+)?)$') AS r
                  FROM tas_prints WHERE strike IS NULL) m
            WHERE t.id = m.id AND m.r IS NOT NULL
            """
        )
    else:
        for table, name, cols in _NEW_UQ:
            with op.batch_alter_table(table) as b:
                b.create_unique_constraint(name, list(cols))

    op.drop_index("ix_flow_buckets_symbol_ts", table_name="flow_buckets")
    op.drop_table("flow_buckets")
    op.drop_table("alerts_sent")


def downgrade() -> None:
    op.create_table(
        "alerts_sent",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("signal_id", sa.BigInteger, sa.ForeignKey("signals.id"), nullable=False),
        sa.Column("channel", sa.String(32), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("response_code", sa.Integer),
    )
    op.create_table(
        "flow_buckets",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("symbol", sa.String(16), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("bucket_min", sa.Integer, nullable=False),
        sa.Column("volm", sa.Float),
        sa.Column("value", sa.Float),
        sa.Column("volmbs", sa.Float),
        sa.Column("valuebs", sa.Float),
        sa.Column("flowratio", sa.Float),
    )
    op.create_index("ix_flow_buckets_symbol_ts", "flow_buckets", ["symbol", "ts"])
    if _pg():
        for table, name, _cols in _NEW_UQ:
            op.execute(f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {name}")
        for table, name, cols in _NULL_SAFE:
            op.execute(f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {name}")
            op.execute(f'ALTER TABLE {table} ADD CONSTRAINT {name} UNIQUE ({", ".join(cols)})')
    else:
        for table, name, _cols in _NEW_UQ:
            with op.batch_alter_table(table) as b:
                b.drop_constraint(name, type_="unique")
    op.drop_index("ix_pipeline_runs_job_date", table_name="pipeline_runs")
    op.drop_table("pipeline_runs")
