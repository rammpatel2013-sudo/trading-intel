"""docs/db_dictionary.md must list every ORM table, and nothing else (2026-11-19, DB wave A1)."""

import re
from pathlib import Path

import trading_intel.memory.models_desk  # noqa: F401 - register tables
import trading_intel.memory.models_equity  # noqa: F401
import trading_intel.memory.models_flow  # noqa: F401
import trading_intel.memory.models_ref  # noqa: F401
import trading_intel.memory.models_close_flow  # noqa: F401
from trading_intel.memory.models import Base

DOC = Path(__file__).resolve().parents[1] / "docs" / "db_dictionary.md"


def _doc_tables() -> set[str]:
    return set(re.findall(r"^\| `([a-z0-9_]+)` \|", DOC.read_text(encoding="utf-8"), re.M))


def test_every_orm_table_has_a_dictionary_row() -> None:
    missing = set(Base.metadata.tables) - _doc_tables()
    assert not missing, f"add rows to docs/db_dictionary.md for: {sorted(missing)}"


def test_dictionary_has_no_stale_rows() -> None:
    stale = _doc_tables() - set(Base.metadata.tables)
    assert not stale, f"docs/db_dictionary.md lists tables the ORM no longer has: {sorted(stale)}"
