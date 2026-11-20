"""Chain bank into the owner tables: greeks_chain + root / spot / prices, greeks_snapshots + vol columns (2026-11-20)

Revision ID: 0066_chain_bank
Revises: 0065_bars_views
Create Date: 2026-11-20

Hand-written (never autogenerate). Additive, except the greeks_chain unique key gains ``root``
(SPX AM and SPXW PM share expiry + strike on monthly expiries; review item D6). The new unique index is
built CONCURRENTLY (greeks_chain is ~13 GB; readers are never blocked), then it replaces the old key.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0066_chain_bank"
down_revision: str | None = "0065_bars_views"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SNAP_FLOATS = (
    "iv30 iv90 ts_30_90 skew25_30 put25_30 call25_30 call_oi put_oi call_premium_usd put_premium_usd"
).split()
NEW_KEY = "uq_greeks_chain_root"
OLD_KEY = "uq_greeks_chain"
KEY_COLS = "symbol, ts, source, root, expiry, strike, cp"


def upgrade() -> None:
    op.add_column("greeks_chain", sa.Column("root", sa.String(8), nullable=False, server_default=""))
    for c in ("spot", "px_vwap", "px_close"):
        op.add_column("greeks_chain", sa.Column(c, sa.Float(), nullable=True))
    op.add_column("greeks_chain", sa.Column("ingested_at", sa.DateTime(), nullable=True))
    for c in SNAP_FLOATS:
        op.add_column("greeks_snapshots", sa.Column(c, sa.Float(), nullable=True))
    op.add_column("greeks_snapshots", sa.Column("n_contracts", sa.Integer(), nullable=True))
    op.add_column("greeks_snapshots", sa.Column("ingested_at", sa.DateTime(), nullable=True))

    bind = op.get_bind()
    if bind.dialect.name != "postgresql":  # SQLite test databases: plain swap
        with op.batch_alter_table("greeks_chain") as b:
            b.drop_constraint(OLD_KEY, type_="unique")
            b.create_unique_constraint(NEW_KEY, KEY_COLS.split(", "))
        return
    with op.get_context().autocommit_block():
        # a failed earlier attempt leaves an INVALID index behind - drop it first
        bad = op.get_bind().execute(sa.text(
            "select 1 from pg_class c join pg_index i on i.indexrelid = c.oid "
            "where c.relname = :n and not i.indisvalid"), {"n": NEW_KEY}).first()
        if bad:
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {NEW_KEY}")
        op.execute(f"CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS {NEW_KEY} ON greeks_chain ({KEY_COLS})")
    op.execute(f"ALTER TABLE greeks_chain ADD CONSTRAINT {NEW_KEY} UNIQUE USING INDEX {NEW_KEY}")
    op.execute(f"ALTER TABLE greeks_chain DROP CONSTRAINT IF EXISTS {OLD_KEY}")


def downgrade() -> None:
    # bank rows (and any second root on one expiry/strike) cannot live under the old key
    op.execute("DELETE FROM greeks_chain WHERE source = 'cvforge_bank'")
    op.execute("DELETE FROM greeks_snapshots WHERE source = 'cvforge_bank'")
    op.execute(f"ALTER TABLE greeks_chain DROP CONSTRAINT IF EXISTS {NEW_KEY}")
    op.execute(f"ALTER TABLE greeks_chain ADD CONSTRAINT {OLD_KEY} UNIQUE (symbol, ts, source, expiry, strike, cp)")
    for c in ("ingested_at", "px_close", "px_vwap", "spot", "root"):
        op.drop_column("greeks_chain", c)
    for c in ("ingested_at", "n_contracts", *SNAP_FLOATS):
        op.drop_column("greeks_snapshots", c)
