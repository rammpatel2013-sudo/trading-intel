"""schema_drift.py - written 2026-11-19 (DB review round 1, D2). REPORT ONLY - changes nothing.

Compares the live Postgres schema with the ORM models (trading_intel/memory/models*.py)
using Alembic's compare_metadata - the same diff `--autogenerate` would make, but it is only
printed, never written
(project rule: never autogenerate a migration). Use it after each migration ship to catch
drift such as the 2026-11-19 find: sector_corr_snapshots.source defaults to "fmp" in the
ORM but "yfinance" in the DB.

Run (laptop, read-only login):
    python scripts/schema_drift.py            # uses DATABASE_URL_RO from .env, else DATABASE_URL
    python scripts/schema_drift.py --strict   # exit 1 when anything differs (for checks)
Ignored on purpose: the pgvector embedding column and index on chunks (added by raw SQL in 0001),
alembic_version, and server-default text differences (Postgres rewrites them).
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

ROOT = Path(os.environ.get("TI_ROOT") or Path(__file__).resolve().parents[1])
sys.path.insert(0, str(ROOT))

_IGNORE_TABLES = {"alembic_version", "quotes_daily", "desk_bars", "quotes_daily_old", "desk_bars_old"}   # views since 0065 (+ kept old tables)
_IGNORE_COLS = {("chunks", "embedding")}
_IGNORE_INDEX = {"ix_chunks_embedding"}


def _url() -> str:
    env = {}
    p = ROOT / ".env"
    if p.exists():
        for line in p.read_text(encoding="utf-8", errors="ignore").splitlines():
            m = re.match(r"\s*([A-Z_]+)\s*=\s*(.*)", line)
            if m:
                env[m.group(1)] = m.group(2).strip().strip('"').strip("'")
    url = (
        os.environ.get("DATABASE_URL_RO")
        or env.get("DATABASE_URL_RO")
        or os.environ.get("DATABASE_URL")
        or env.get("DATABASE_URL")
    )
    if not url:
        sys.exit("no DATABASE_URL_RO / DATABASE_URL")
    return url


def _engine_url(raw: str) -> object:
    """psycopg 3 driver; the RO password has a special character, so split on the LAST '@'."""
    from sqlalchemy.engine import URL

    rest = re.sub(r"^[a-z0-9+]+://", "", raw)
    cred, _, host = rest.rpartition("@")
    user, _, pw = cred.partition(":")
    hp, _, db = host.partition("/")
    h, _, port = hp.partition(":")
    return URL.create(
        "postgresql+psycopg",
        username=user,
        password=pw,
        host=h,
        port=int(port) if port else None,
        database=db.split("?")[0],
    )


def _keep(diff: tuple | list, indexes: bool = False) -> bool:
    kind = diff[0] if isinstance(diff, tuple) else diff[0][0]
    if kind == "remove_index" and not indexes:
        return False  # DB-only index from a migration, not mirrored in the model: harmless
    if kind == "modify_type" and not isinstance(diff, tuple):
        old, new = diff[0][-2], diff[0][-1]
        if type(old).__name__ == "BIGINT" and type(new).__name__ == "Integer":
            return False  # BIGINT in DB vs Integer in model: wider in DB, harmless
    if kind in ("add_table", "remove_table"):
        return diff[1].name not in _IGNORE_TABLES
    if kind in ("add_column", "remove_column"):
        return (diff[2], diff[3].name) not in _IGNORE_COLS
    if kind in ("add_index", "remove_index"):
        return diff[1].name not in _IGNORE_INDEX
    if isinstance(diff, list) and diff and diff[0][0] == "modify_default":
        return False
    return True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--strict", action="store_true")
    ap.add_argument("--indexes", action="store_true", help="also list DB-only indexes")
    a = ap.parse_args()
    from alembic.autogenerate import compare_metadata
    from alembic.runtime.migration import MigrationContext
    from sqlalchemy import create_engine

    import trading_intel.memory.models_desk  # register tables
    import trading_intel.memory.models_equity  # noqa: F401
    import trading_intel.memory.models_flow  # noqa: F401
    import trading_intel.memory.models_close_flow  # noqa: F401
    from trading_intel.memory.models import Base

    eng = create_engine(_engine_url(_url()), pool_pre_ping=True)
    with eng.connect() as conn:
        opts = {"compare_type": True, "compare_server_default": False}
        ctx = MigrationContext.configure(conn, opts=opts)
        diffs = [d for d in compare_metadata(ctx, Base.metadata) if _keep(d, a.indexes)]
    if not diffs:
        print("SCHEMA DRIFT: none - the live database matches the ORM models")
        return 0
    print(f"SCHEMA DRIFT: {len(diffs)} difference(s) live DB vs ORM (report only):")
    for d in diffs:
        print("  -", d if isinstance(d, tuple) else d[0])
    return 1 if a.strict else 0


if __name__ == "__main__":
    sys.exit(main())
