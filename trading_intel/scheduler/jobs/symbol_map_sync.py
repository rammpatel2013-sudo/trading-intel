"""symbol_map_sync - created 2026-11-19 (DB wave A2 step 6). Keep ``symbol_map`` in step with what we store.

Symbols: every symbol in equity_bars + the watchlist (tickers) + desk_names + equity_profiles + the index list.
Asset type: index (clients.symbols.INDEXES) | etf (desk_names / equity_profiles is_etf) | stock. Vendor spellings
from clients.symbols.vendor_symbol. od_iid is filled by contracts_sync and never overwritten here. Idempotent upsert.
Run: python -m trading_intel.scheduler.jobs.symbol_map_sync   (DSM: chained after contracts_sync, 17:20 Mon-Fri)
"""

from __future__ import annotations

import sys

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from trading_intel.clients.symbols import INDEXES, asset_type_of, map_row, normalize_symbol
from trading_intel.config import get_settings
from trading_intel.equity.store import upsert
from trading_intel.memory.models import Ticker
from trading_intel.memory.models_desk import DeskName
from trading_intel.memory.models_equity import EquityBar, EquityProfile
from trading_intel.memory.models_ref import SymbolMap
from trading_intel.timeutils import eastern_now

log = structlog.get_logger()


def collect(session: Session) -> dict[str, dict]:
    """{symbol: {"is_etf": bool | None, "name": str | None}} from every table that names symbols."""
    out: dict[str, dict] = {s: {"is_etf": False, "name": None} for s in INDEXES}

    def add(sym: str | None, is_etf: bool | None = None, name: str | None = None) -> None:
        s = normalize_symbol(sym)
        if not s or len(s) > 16:
            return
        cur = out.setdefault(s, {"is_etf": None, "name": None})
        if is_etf is not None and cur["is_etf"] is None:
            cur["is_etf"] = is_etf
        if name and not cur["name"]:
            cur["name"] = name[:160]

    for (s,) in session.execute(select(EquityBar.symbol).distinct()):
        add(s)
    for (s,) in session.execute(select(Ticker.symbol)):
        add(s)
    for s, e, n in session.execute(select(DeskName.symbol, DeskName.is_etf, DeskName.name)):
        add(s, e, n)
    for p in session.execute(select(EquityProfile)).scalars():
        add(p.symbol, p.is_etf, p.name)
    return out


def rows_for(found: dict[str, dict]) -> list[dict]:
    now = eastern_now().replace(microsecond=0)
    rows = []
    for s, info in sorted(found.items()):
        r = map_row(s, asset_type_of(s, info.get("is_etf")))
        r["name"] = info.get("name")
        r["ingested_at"] = now
        rows.append(r)
    return rows


def run(session: Session) -> int:
    rows = rows_for(collect(session))
    n = upsert(session, SymbolMap, rows, ["symbol"], chunk=1000)
    log.info("symbol_map_sync.done", rows=n)
    try:
        from trading_intel.scheduler import track

        track.report(rows_attempted=len(rows), rows_written=n)
    except Exception:  # noqa: S110 - bookkeeping only
        pass
    return n


def main(argv: list[str] | None = None) -> int:
    from trading_intel.memory.db import make_session_factory

    with make_session_factory(get_settings())() as session:
        print(run(session))
    return 0


if __name__ == "__main__":
    sys.exit(main())
