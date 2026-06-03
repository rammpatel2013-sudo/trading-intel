"""Add, reactivate, deactivate or list research-watchlist tickers.

The MCP tools are read-only by design (FlashAlpha rule 4 + the data-source
isolation spirit), so mutating the watchlist lives here in a small CLI rather
than as a tool. This inserts a manual ``watchlist_entries`` row (``source_doc_id``
NULL); ``watchlist.effective_symbols`` unions active entries with the static
``.env`` ``WATCHLIST``, so collectors and dashboards pick the name up on their
next run - no image rebuild needed.

Usage (from the repo root, with the venv active):
    python scripts/add_watchlist.py NVDA --rationale "manual add"
    python scripts/add_watchlist.py NVDA --deactivate
    python scripts/add_watchlist.py --list
"""
from __future__ import annotations

import argparse

from sqlalchemy import select

from trading_intel.config import get_settings
from trading_intel.memory.db import make_session_factory
from trading_intel.memory.models import WatchlistEntry


def _manual_entry(session, symbol: str):  # noqa: ANN001, ANN202
    """The manual (source_doc_id NULL) entry for ``symbol``, or None."""
    return (
        session.execute(
            select(WatchlistEntry).where(
                WatchlistEntry.symbol == symbol,
                WatchlistEntry.source_doc_id.is_(None),
            )
        )
        .scalars()
        .first()
    )


def add(session, symbol: str, *, rationale: str | None,  # noqa: ANN001
        sentiment: float | None, confidence: float | None) -> str:
    sym = symbol.strip().upper()
    existing = _manual_entry(session, sym)
    if existing is not None:
        existing.active = True
        if rationale:
            existing.rationale = rationale
        session.commit()
        return f"{sym}: manual entry already existed -> reactivated"
    session.add(
        WatchlistEntry(
            symbol=sym, source_doc_id=None, rationale=rationale or "manual add",
            sentiment=sentiment, confidence=confidence, themes=None, active=True,
        )
    )
    session.commit()
    return f"{sym}: added to research watchlist (active)"


def deactivate(session, symbol: str) -> str:  # noqa: ANN001
    sym = symbol.strip().upper()
    existing = _manual_entry(session, sym)
    if existing is None:
        return f"{sym}: no manual entry to deactivate"
    existing.active = False
    session.commit()
    return f"{sym}: deactivated"


def list_active(session) -> str:  # noqa: ANN001
    rows = (
        session.execute(
            select(WatchlistEntry.symbol)
            .where(WatchlistEntry.active.is_(True))
            .distinct()
            .order_by(WatchlistEntry.symbol)
        )
        .scalars()
        .all()
    )
    return "active research watchlist: " + (", ".join(rows) if rows else "(none)")


def main() -> None:
    parser = argparse.ArgumentParser(description="Manage the research watchlist.")
    parser.add_argument("symbol", nargs="?", help="ticker, e.g. NVDA")
    parser.add_argument("--rationale", default=None, help="one-line note")
    parser.add_argument("--sentiment", type=float, default=None, help="-1 bearish .. 1 bullish")
    parser.add_argument("--confidence", type=float, default=None, help="0..1")
    parser.add_argument("--deactivate", action="store_true", help="deactivate instead of add")
    parser.add_argument("--list", action="store_true", help="list active entries and exit")
    args = parser.parse_args()

    session_factory = make_session_factory(get_settings())
    with session_factory() as session:
        if args.list:
            print(list_active(session))
            return
        if not args.symbol:
            parser.error("a symbol is required unless --list is given")
        if args.deactivate:
            print(deactivate(session, args.symbol))
        else:
            print(
                add(
                    session, args.symbol, rationale=args.rationale,
                    sentiment=args.sentiment, confidence=args.confidence,
                )
            )


if __name__ == "__main__":
    main()
