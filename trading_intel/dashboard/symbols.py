"""Shared symbol-selectors for dashboard pages.

One place to decide "what should the Symbol selectbox offer?" so the gamma /
price-cone / forward-field pages all draw from the same population. Currently:

- ``gamma_page_symbols(session, settings)`` — symbols with stored ``live_gex``
  data first (those are the ones the gamma-profile / forward-field engines can
  actually work with intraday), then the configured intraday set, then the full
  watchlist as a last resort. SPX / SPY / QQQ are floated to the front of any
  result list — matches the GEX Surface page convention.

Side-effect-free; pure projection over the session + settings.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from trading_intel.config import Settings
from trading_intel.dashboard.live_gex_data import live_gex_symbols

_PREFERRED = ("SPX", "SPY", "QQQ")


def _ordered(symbols: list[str]) -> list[str]:
    """Float SPX / SPY / QQQ to the front, preserve the order of the rest."""
    seen: set[str] = set()
    out: list[str] = []
    for s in _PREFERRED:
        if s in symbols and s not in seen:
            out.append(s)
            seen.add(s)
    for s in symbols:
        if s not in seen:
            out.append(s)
            seen.add(s)
    return out


def gamma_page_symbols(session: Session, settings: Settings) -> list[str]:
    """Selectbox population for gamma / price-cone / forward-field pages.

    Priority order:
    1. Symbols with stored ``live_gex`` rows (the intraday-tier data is the
       precondition for the gamma-profile + forward-cone engines).
    2. ``settings.intraday_symbols`` if (1) returns nothing.
    3. ``settings.watchlist_symbols`` as a final fallback.

    Always reordered so SPX / SPY / QQQ come first if present.
    """
    live = live_gex_symbols(session)
    if live:
        return _ordered(list(live))
    intraday = list(settings.intraday_symbols)
    if intraday:
        return _ordered(intraday)
    return _ordered(list(settings.watchlist_symbols))
