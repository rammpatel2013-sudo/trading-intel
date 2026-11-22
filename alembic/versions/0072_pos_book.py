"""pos_book_daily + tape_coverage_daily + flow_players + flow_player_sightings (2026-11-22)

Revision ID: 0072_pos_book
Revises: 0071_decay_line_marks
Create Date: 2026-11-22

Hand-written (never autogenerate). Four new tables, all DERIVED (rebuildable from oi_chain_eod + tas_prints +
pos_packages), except flow_players.name / status which Mithil edits (manual names for repeat traders).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0072_pos_book"
down_revision: str | None = "0071_decay_line_marks"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "pos_book_daily",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("symbol", sa.String(8), nullable=False),
        sa.Column("root", sa.String(8), nullable=False),
        sa.Column("expiry", sa.Date(), nullable=False),
        sa.Column("strike", sa.Float(), nullable=False),
        sa.Column("cp", sa.String(1), nullable=False),
        sa.Column("oi", sa.Float()),
        sa.Column("d_oi", sa.Float()),
        sa.Column("tape_buy", sa.Float()),
        sa.Column("tape_sell", sa.Float()),
        sa.Column("pkg_buy", sa.Float()),
        sa.Column("pkg_sell", sa.Float()),
        sa.Column("iv_lean", sa.Float()),
        sa.Column("lean", sa.Float()),
        sa.Column("cust_pos", sa.Float()),
        sa.Column("known", sa.Float()),
        sa.Column("seed_oi", sa.Float()),
        sa.Column("confidence", sa.Float()),
        sa.Column("gamma", sa.Float()),
        sa.Column("spot", sa.Float()),
        sa.Column("mm_gamma_usd_1pct", sa.Float()),
        sa.Column("naive_mm_gamma_usd_1pct", sa.Float()),
        sa.Column("player_slug", sa.String(64)),
        sa.Column("ingested_at", sa.DateTime()),
        sa.UniqueConstraint(
            "date", "symbol", "root", "expiry", "strike", "cp", name="uq_pos_book_daily"
        ),
    )
    op.create_index("ix_pos_book_daily_date", "pos_book_daily", ["date"])
    op.create_table(
        "tape_coverage_daily",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("root", sa.String(8), nullable=False),
        sa.Column("prints", sa.Integer()),
        sa.Column("contracts", sa.Float()),
        sa.Column("chain_volume", sa.Float()),
        sa.Column("coverage_pct", sa.Float()),
        sa.Column("vs_median_pct", sa.Float()),
        sa.Column("first_et", sa.String(5)),
        sa.Column("last_et", sa.String(5)),
        sa.Column("empty_rth_slots", sa.String(200)),
        sa.Column("status", sa.String(8)),
        sa.Column("ingested_at", sa.DateTime()),
        sa.UniqueConstraint("date", "root", name="uq_tape_coverage_daily"),
    )
    op.create_table(
        "flow_players",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("slug", sa.String(64), nullable=False),
        sa.Column("name", sa.String(80)),
        sa.Column("description", sa.String(400)),
        sa.Column("status", sa.String(12)),
        sa.Column("first_seen", sa.Date()),
        sa.Column("last_seen", sa.Date()),
        sa.Column("n_sightings", sa.Integer()),
        sa.Column("ingested_at", sa.DateTime()),
        sa.UniqueConstraint("slug", name="uq_flow_players"),
    )
    op.create_table(
        "flow_player_sightings",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("player_slug", sa.String(64), nullable=False),
        sa.Column("ref", sa.String(120), nullable=False),
        sa.Column("structure", sa.String(12)),
        sa.Column("cp", sa.String(1)),
        sa.Column("expiry", sa.Date()),
        sa.Column("strike", sa.Float()),
        sa.Column("strike2", sa.Float()),
        sa.Column("dte", sa.Integer()),
        sa.Column("otm_pct", sa.Float()),
        sa.Column("lots", sa.Float()),
        sa.Column("side", sa.Integer()),
        sa.Column("action", sa.String(12)),
        sa.Column("premium_usd", sa.Float()),
        sa.Column("clips", sa.Integer()),
        sa.Column("fingerprint", sa.String(80)),
        sa.Column("ingested_at", sa.DateTime()),
        sa.UniqueConstraint("date", "player_slug", "ref", name="uq_flow_player_sightings"),
    )
    op.create_index("ix_flow_player_sightings_date", "flow_player_sightings", ["date"])


def downgrade() -> None:
    op.drop_index("ix_flow_player_sightings_date", table_name="flow_player_sightings")
    op.drop_table("flow_player_sightings")
    op.drop_table("flow_players")
    op.drop_table("tape_coverage_daily")
    op.drop_index("ix_pos_book_daily_date", table_name="pos_book_daily")
    op.drop_table("pos_book_daily")
