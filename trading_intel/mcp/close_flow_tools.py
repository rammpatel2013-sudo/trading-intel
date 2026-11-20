"""MCP tool for the leveraged-ETF close flow (2026-11-19). Reads the NAS DB only - no vendor calls.

* ``get_close_flow`` - latest (or a day's) forced-rebalance estimate per index (SPX in ES, NDX in NQ),
  the flip level, strength, per-fund rows and the path through the afternoon.

Wire in server.py:
    from trading_intel.mcp import close_flow_tools
    close_flow_tools.register(mcp, settings, session_factory)
"""

from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from trading_intel.memory.models_close_flow import LetfCloseFlow


def close_flow(s: Session, day: str = "", with_funds: bool = True) -> dict[str, Any]:
    d = date.fromisoformat(day) if day else s.execute(select(func.max(LetfCloseFlow.date))).scalar()
    if d is None:
        return {"error": "no close-flow rows yet (letf_close_live runs the last hour before the close)"}
    rows = s.execute(
        select(LetfCloseFlow).where(LetfCloseFlow.date == d).order_by(LetfCloseFlow.ts)
    ).scalars().all()
    out: dict[str, Any] = {"date": str(d), "page": "http://192.168.1.211:8899/close_flow.html", "groups": {}}
    for r in rows:
        g = out["groups"].setdefault(r.grp, {"path": []})
        g["path"].append([r.ts.strftime("%H:%M"), round((r.rebalance_usd or 0) / 1e6, 1)])
        g["latest"] = {
            "ts": r.ts.isoformat(timespec="minutes"), "direction": r.direction, "strength": r.strength,
            "rebalance_usd_m": round((r.rebalance_usd or 0) / 1e6, 1),
            "futures": None if r.futures is None else round(r.futures),
            "future": "ES" if r.grp == "SPX" else "NQ", "index_last": r.index_last,
            "flip_level": r.index_prev, "index_ret_pct": None if r.index_ret is None else round(r.index_ret * 100, 3),
            "per_1pct_usd_m": None if r.per_1pct_usd is None else round(r.per_1pct_usd / 1e6),
            "n_missing": r.n_missing, "n_index_fallback": r.n_index_fallback,
        }
        if with_funds:
            g["funds"] = r.funds
    return out


def register(mcp: Any, settings: Any, session_factory: Any) -> None:  # noqa: ANN401
    @mcp.tool()
    def get_close_flow(day: str = "", with_funds: bool = True) -> dict[str, Any]:
        """Leveraged / inverse ETF forced rebalance into the close: per index (SPX -> ES contracts, NDX -> NQ)
        the $ to trade (+ buy / - sell), direction, strength (LIGHT / MODERATE / HEAVY / EXTREME), flip level
        (= prior close), $ per 1% move, the per-fund rows and the path every 2 min in the last hour.
        ``day`` YYYY-MM-DD (default latest). Estimate from FMP 1-minute bars; descriptor only."""
        with session_factory() as s:
            return close_flow(s, day, with_funds)
