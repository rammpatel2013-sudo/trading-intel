"""Watchlist coverage check: which active names are actually collecting on the NAS.

Compares the effective watchlist (static WATCHLIST + active research entries) against
the latest per-symbol dates in the core collector tables, so you can see what's FRESH,
what's STALE, and what's MISSING (likely non-optionable junk to prune). Run it a day
after adding names — freshly-added names show MISSING until the next EOD collector run.

Run from the repo root:
    .venv\\Scripts\\python check_watchlist_coverage.py
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from sqlalchemy import func, select

from trading_intel.config import get_settings
from trading_intel.memory.db import make_session_factory
from trading_intel.memory.models import OiChainEod, QuoteDaily
from trading_intel.watchlist import effective_symbols

settings = get_settings()
factory = make_session_factory(settings)

with factory() as session:
    universe = sorted(set(effective_symbols(session, settings)))
    chain_latest = dict(
        session.execute(
            select(OiChainEod.symbol, func.max(OiChainEod.ts)).group_by(OiChainEod.symbol)
        ).all()
    )
    quote_latest = dict(
        session.execute(
            select(QuoteDaily.symbol, func.max(QuoteDaily.date)).group_by(QuoteDaily.symbol)
        ).all()
    )

fresh_cut = date.today() - timedelta(days=5)  # within ~a trading week


def _as_date(x: object) -> date:
    """oi_chain_eod.ts is a datetime; quotes_daily.date is a date -> normalize."""
    return x.date() if isinstance(x, datetime) else x  # type: ignore[return-value]


def bucket(latest: object | None) -> str:
    if latest is None:
        return "missing"
    return "fresh" if _as_date(latest) >= fresh_cut else "stale"


missing, stale, fresh = [], [], []
q_missing = []
for s in universe:
    cl = chain_latest.get(s)
    b = bucket(cl)
    (fresh if b == "fresh" else stale if b == "stale" else missing).append((s, cl))
    if quote_latest.get(s) is None:
        q_missing.append(s)

n = len(universe)
print(f"Effective watchlist: {n} names")
print(
    f"  options chain (oi_chain_eod):  FRESH {len(fresh)}  ·  STALE {len(stale)}  ·  MISSING {len(missing)}"
)
print(f"  daily quotes (quotes_daily):   MISSING {len(q_missing)}")

if missing:
    print("\nNO options-chain data (non-optionable / not yet collected -> prune candidates):")
    print("  " + ", ".join(s for s, _ in missing))
if stale:
    print("\nSTALE options-chain (>5 days old -> collection may be gapping):")
    print("  " + ", ".join(f"{s}({_as_date(d)})" for s, d in stale))

print("\nPrune dead names with:  .venv\\Scripts\\python scripts\\prune_dead_watchlist.py")
