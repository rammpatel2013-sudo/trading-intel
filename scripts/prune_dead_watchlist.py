"""Find (and optionally deactivate) research-watchlist symbols that never collect.

The LLM watchlist extractor (``synthesis/watchlist_extract.py``) only checks that a
candidate matches a ticker-shaped regex — it does NOT verify the symbol is a real,
optionable US-listed equity. So company *names* get mis-mapped to junk tickers
(``Osaka Titanium`` -> ``OSAKA``, ``HSBC Asset Management Turkey`` -> ``AUMTU``,
mutual funds ``TAVFX`` / ``TVFVX``, foreign lines ``UKPN`` / ``ABN`` ...). These land
as ``active`` ``watchlist_entries`` rows, collectors try to pull Convex options data
for them every cycle, get nothing, and they show up as blank rows in the regime table.

``scripts/add_watchlist.py --deactivate`` only touches MANUAL entries
(``source_doc_id IS NULL``), so it can't clear research-sourced junk. This script can.

A symbol is "dead" when it has NO ``greeks_snapshots``, NO ``live_gex`` and NO
``quotes_daily`` row within ``--stale-days`` (default 5 trading-ish days). That's the
same signal you see as a null regime row — it isn't collecting, full stop.

Dry-run by default (reports only). Add ``--deactivate`` to set ``active=False`` on
every ``watchlist_entries`` row (manual + research) for each dead symbol. Reversible
(re-activate via ``add_watchlist.py``) and idempotent (already-inactive rows are
skipped). ``watchlist.effective_symbols`` drops them on the collectors' next run — no
image rebuild needed.

Usage (repo root, venv active):
    python scripts/prune_dead_watchlist.py                 # report dead symbols
    python scripts/prune_dead_watchlist.py --stale-days 7  # widen the freshness window
    python scripts/prune_dead_watchlist.py --deactivate    # apply
    python scripts/prune_dead_watchlist.py --keep AKTS,IMXT --deactivate   # spare some
"""

from __future__ import annotations

import argparse
from datetime import timedelta

import structlog
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from trading_intel.config import get_settings
from trading_intel.memory.db import make_session_factory
from trading_intel.memory.models import (
    GreeksSnapshot,
    LiveGex,
    QuoteDaily,
    WatchlistEntry,
)
from trading_intel.timeutils import eastern_now

log = structlog.get_logger(__name__)


def _active_symbols(session: Session) -> list[str]:
    """Distinct symbols with at least one active research-watchlist entry."""
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
    return list(rows)


def _symbols_with_recent_data(
    session: Session, *, stale_days: int, include_quotes: bool = False
) -> set[str]:
    """Symbols that have collected OPTIONS data (greeks / live_gex) recently.

    The watchlist regime table is options-derived, so a symbol is only "alive" if
    it has a greeks_snapshots or live_gex row — a bare stock quote (quotes_daily)
    does NOT count, because a real microcap with no options chain still gets a
    daily price yet shows a blank regime row. Pass ``include_quotes=True`` to also
    treat a recent stock quote as alive (looser).
    """
    now = eastern_now()
    ts_cut = now - timedelta(days=stale_days)
    date_cut = (now - timedelta(days=stale_days)).date()
    stmts = [
        select(GreeksSnapshot.symbol).where(GreeksSnapshot.ts >= ts_cut).distinct(),
        select(LiveGex.symbol).where(LiveGex.ts >= ts_cut).distinct(),
    ]
    if include_quotes:
        stmts.append(select(QuoteDaily.symbol).where(QuoteDaily.date >= date_cut).distinct())
    alive: set[str] = set()
    for stmt in stmts:
        alive.update(session.execute(stmt).scalars().all())
    return alive


def find_dead(session: Session, *, stale_days: int, include_quotes: bool = False) -> list[str]:
    """Active watchlist symbols with no collected OPTIONS data in the freshness window."""
    active = _active_symbols(session)
    alive = _symbols_with_recent_data(session, stale_days=stale_days, include_quotes=include_quotes)
    return [s for s in active if s not in alive]


def _entry_summary(session: Session, symbol: str) -> tuple[int, int]:
    """(#active entries, #research-sourced) for a symbol — for the report."""
    total = session.execute(
        select(func.count())
        .select_from(WatchlistEntry)
        .where(WatchlistEntry.symbol == symbol, WatchlistEntry.active.is_(True))
    ).scalar_one()
    research = session.execute(
        select(func.count())
        .select_from(WatchlistEntry)
        .where(
            WatchlistEntry.symbol == symbol,
            WatchlistEntry.active.is_(True),
            WatchlistEntry.source_doc_id.is_not(None),
        )
    ).scalar_one()
    return int(total), int(research)


def deactivate(session: Session, symbols: list[str]) -> int:
    """Set active=False on every entry (manual + research) for ``symbols``."""
    if not symbols:
        return 0
    rows = list(
        session.execute(
            select(WatchlistEntry).where(
                WatchlistEntry.symbol.in_(symbols),
                WatchlistEntry.active.is_(True),
            )
        ).scalars()
    )
    for r in rows:
        r.active = False
    session.commit()
    return len(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description="Prune non-collecting watchlist symbols.")
    parser.add_argument(
        "--stale-days",
        type=int,
        default=5,
        help="a symbol is dead if no greeks/live_gex/quotes row in this many days (default 5)",
    )
    parser.add_argument(
        "--deactivate",
        action="store_true",
        help="apply: deactivate dead symbols (default is dry-run / report only)",
    )
    parser.add_argument(
        "--keep",
        default="",
        help="comma list of symbols to spare even if they look dead",
    )
    parser.add_argument(
        "--include-quotes",
        action="store_true",
        help="also treat a recent stock quote as alive (looser; default options-only)",
    )
    args = parser.parse_args()

    keep = {s.strip().upper() for s in args.keep.split(",") if s.strip()}

    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ]
    )

    session_factory = make_session_factory(get_settings())
    with session_factory() as session:
        dead = [
            s
            for s in find_dead(
                session, stale_days=args.stale_days, include_quotes=args.include_quotes
            )
            if s not in keep
        ]

        kind = "options/price" if args.include_quotes else "options"
        if not dead:
            print(f"No dead symbols ({kind}, {args.stale_days}d window). Watchlist is clean.")
            return

        print(f"Dead symbols — no {kind} data in {args.stale_days}d ({len(dead)}):\n")
        print(f"  {'symbol':<10}{'active entries':>15}{'research-sourced':>18}")
        for sym in dead:
            total, research = _entry_summary(session, sym)
            print(f"  {sym:<10}{total:>15}{research:>18}")

        if not args.deactivate:
            print("\nDry-run. Re-run with --deactivate to deactivate these.")
            return

        n = deactivate(session, dead)
        log.info("prune_dead_watchlist.done", symbols=dead, rows_deactivated=n)
        print(f"\nDeactivated {n} entr{'y' if n == 1 else 'ies'} across {len(dead)} symbol(s).")
        print("Collectors and dashboards drop them on their next run.")


if __name__ == "__main__":
    main()
