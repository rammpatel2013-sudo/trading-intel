"""MCP tools: gamma / vega by tenor, tenor history, factor cards (created 2026-11-09).

READ-ONLY. Each tool returns the precomputed JSON the nightly ``tenor_gamma_report``
job writes (``scripts/cvxmap/data/tenor.json`` + ``reports/tenor/*``) when it matches
the request, and computes on the fly from the DB otherwise. Zero vendor calls except
``get_factor_cards`` when no precomputed cards exist (it then reads closes through the
existing price clients). Descriptor only (rule 4): positioning read, not a trade signal.

Registered from ``server.build_server`` via ``register`` (one line there).
"""

# ruff: noqa: ANN401  (FastMCP + settings are duck-typed, as in phase5_tools)


from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from trading_intel.greeks.tenor_buckets import DISCLAIMER
from trading_intel.market import tenor_data as td

TENOR_JSON = td.DATA_DIR / "tenor.json"
FACTOR_JSON = td.HIST_DIR / "factor_cards.json"


def _read(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _day(d: str | None) -> date | None:
    return date.fromisoformat(d) if d else None


def _pre(symbol: str, day: date | None, path: Path | None = None) -> dict[str, Any] | None:
    obj = _read(path or TENOR_JSON)
    if not obj or not obj.get("found") or obj.get("symbol") != symbol.upper():
        return None
    if day is not None and obj.get("asof") != day.isoformat():
        return None
    return obj


def gamma_by_tenor(
    session: Session | None,
    symbol: str = "SPX",
    day: date | None = None,
    *,
    path: Path | None = None,
) -> dict[str, Any]:
    pre = _pre(symbol, day, path)
    if pre is not None:
        return {
            "symbol": pre["symbol"],
            "as_of": pre["asof"],
            "spot": pre["spot"],
            "found": True,
            "source": "precomputed",
            "gamma": pre["gamma"],
            "change_1w": pre.get("change_1w"),
            "note": DISCLAIMER,
        }
    if session is None:
        return {"symbol": symbol.upper(), "found": False, "reason": "no precomputed file and no DB"}
    obj = td.compute_day(session, symbol, day)
    if not obj.get("found"):
        return obj
    return {
        "symbol": obj["symbol"],
        "as_of": obj["as_of"],
        "spot": obj["spot"],
        "found": True,
        "source": "computed",
        "gamma": obj["gamma"],
        "note": DISCLAIMER,
    }


def vega_by_tenor(
    session: Session | None,
    symbol: str = "SPX",
    day: date | None = None,
    *,
    path: Path | None = None,
) -> dict[str, Any]:
    pre = _pre(symbol, day, path)
    if pre is not None and pre.get("vega"):
        return {
            "symbol": pre["symbol"],
            "as_of": pre["asof"],
            "spot": pre["spot"],
            "found": True,
            "source": "precomputed",
            "vega": pre["vega"],
            "note": DISCLAIMER,
        }
    if session is None:
        return {"symbol": symbol.upper(), "found": False, "reason": "no precomputed file and no DB"}
    obj = td.compute_day(session, symbol, day)
    if not obj.get("found"):
        return obj
    return {
        "symbol": obj["symbol"],
        "as_of": obj["as_of"],
        "spot": obj["spot"],
        "found": True,
        "source": "computed",
        "vega": obj["vega"],
        "note": DISCLAIMER,
    }


def tenor_history(
    session: Session | None, symbol: str = "SPX", days: int = 120, *, root: Path | None = None
) -> dict[str, Any]:
    rows = td.read_history(symbol, root)
    src = "history file"
    if not rows and session is not None:
        rows = td.backfill(session, symbol, root, write=False)  # read-only: nothing persisted
        src = "computed from oi_chain_eod (no history file yet)"
    from trading_intel.timeutils import is_trading_session

    # 2026-11-13 data check D5: drop rows filed on market holidays (5/25, 9/07)
    rows = [r for r in rows if _session_day(r.get("date"), is_trading_session)]
    rows = rows[-max(1, days) :]
    return {
        "symbol": symbol.upper(),
        "found": bool(rows),
        "source": src,
        "n": len(rows),
        "since": rows[0]["date"] if rows else None,
        "rows": rows,
        "change_1w": td.change_vs(rows, 5),
        "units": {"gamma": "$bn per 1% move", "vega": "$mn per vol point"},
        "note": DISCLAIMER,
        "vs_gamma_history": ("this re-prices the whole book across spot; get_gamma_history sums "
                             "gamma strike by strike - a long/short disagreement between them "
                             "means price is near the flip"),
    }


def _session_day(d: Any, is_session: Any) -> bool:
    from datetime import date as _date

    try:
        return bool(is_session(_date.fromisoformat(str(d)[:10])))
    except ValueError:
        return True


def factor_cards(settings: Any = None, *, path: Path | None = None) -> dict[str, Any]:
    pre = _read(path or FACTOR_JSON)
    if pre and pre.get("cards"):
        return {**pre, "found": True, "source": "precomputed"}
    from trading_intel.market.factor_cards import SYMBOLS, build_cards
    from trading_intel.market.factor_prices import load_closes

    px, src = load_closes(SYMBOLS, settings=settings)
    if px.empty:
        return {"found": False, "reason": "no prices", "note": DISCLAIMER}
    return {
        **build_cards(px),
        "found": True,
        "source": "computed",
        "price_sources": sorted(set(src.values())),
    }


def register(mcp: Any, settings: Any, session_factory: sessionmaker[Session]) -> None:
    @mcp.tool()
    def get_gamma_by_tenor(symbol: str = "SPX", date: str | None = None) -> dict[str, Any]:
        """Dealer gamma by tenor across spot (BofA "Exhibit 7" style), $bn per 1% move.

        Naive book (calls +, puts -), Black-Scholes, per-strike IV, spot ladder +/-12% in
        25-pt steps. Buckets by business days: 1 day, 2 days-1 week, 1 week-1 month,
        > 1 month, + total; each with curve, value at spot, zero-cross, share.
        ``date`` (YYYY-MM-DD) = the book on/before that day. Positioning read, not a trade signal.
        """
        with session_factory() as s:
            return gamma_by_tenor(s, symbol, _day(date))

    @mcp.tool()
    def get_vega_by_tenor(symbol: str = "SPX", date: str | None = None) -> dict[str, Any]:
        """Dealer vega by tenor and expiry, $mn per vol point, under three sign conventions.

        naive = dealers long calls / short puts; short_all = customers own every option;
        flipped = naive x -1. Shows how much the sign assumption matters. Not a trade signal.
        """
        with session_factory() as s:
            return vega_by_tenor(s, symbol, _day(date))

    @mcp.tool()
    def get_tenor_history(symbol: str = "SPX", days: int = 120) -> dict[str, Any]:
        """Daily history of gamma-at-spot per tenor bucket, vega per bucket, total and zero-cross.

        One row per stored book day (history starts with the oldest ``oi_chain_eod`` book
        still kept, ~2026-07-28). Positioning read, not a trade signal.
        """
        with session_factory() as s:
            return tenor_history(s, symbol, days)

    @mcp.tool()
    def get_factor_cards() -> dict[str, Any]:
        """Factor cards: beta, volatility, size, quality, oil beta, rates beta.

        50-session relative return z-scored vs its own 3-year history, +/-2 sigma zones,
        z two weeks ago, and the honest base rate of every past zone entry vs any 20
        sessions ("weak record" when n < 30 or the edge < 5 pts). Not a trade signal.
        """
        return factor_cards(settings)
