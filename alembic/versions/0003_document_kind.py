"""documents.kind: methodology vs company-research discriminator

Revision ID: 0003
Revises: 0002
Create Date: 2026-05-21

Splits the knowledge base into two kinds (per design decision):
- methodology: "knowledge for the LLM" — frameworks the system applies to live
  data to find/interpret trades (vol-surface, GEX mechanics, flow rules).
- research:    "knowledge about companies/themes" — symbol/theme material for
  deep research, watchlists, and Q&A (Type-2, RAG-backed).

Existing rows backfill to 'methodology' (everything ingested so far is
methodology material).
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "documents",
        sa.Column("kind", sa.String(length=16), server_default="methodology", nullable=False),
    )
    op.create_check_constraint(
        "ck_documents_kind", "documents", "kind IN ('methodology', 'research')"
    )
    op.create_index("ix_documents_kind", "documents", ["kind"])


def downgrade() -> None:
    op.drop_index("ix_documents_kind", table_name="documents")
    op.drop_constraint("ck_documents_kind", "documents", type_="check")
    op.drop_column("documents", "kind")
