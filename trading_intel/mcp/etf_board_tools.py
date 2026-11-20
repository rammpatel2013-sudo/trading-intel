"""MCP tool for the ETF board (2026-11-19). Reads the NAS DB only - no vendor calls.

* ``get_etf_board(sort, n, day)`` - Mithil's "Alt ETF Selects" board (48 ETFs): 1D / 5D, Buying | Selling $,
  BSR, Strength, Building, Corr vs QQQ, Stretch, Mo1-3, RelVol, P/C, IVs. Page: http://192.168.1.211:8899/etf_board.html
"""

from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from trading_intel.memory.models_etf import FLOAT_COLS, EtfBoardDaily

SORTABLE = (
    "bsr",
    "chg_1d",
    "chg_5d",
    "strength",
    "building",
    "corr20",
    "stretch",
    "relvol",
    "pc_ratio",
    "iv",
)


def etf_board(s: Session, sort: str = "bsr", n: int = 48, day: str = "") -> dict[str, Any]:
    d = date.fromisoformat(day) if day else s.execute(select(func.max(EtfBoardDaily.date))).scalar()
    if d is None:
        return {"error": "no ETF board rows yet (job etf_board, DSM 16:45 Mon-Fri)"}
    if sort.lstrip("-") not in SORTABLE:
        return {"error": f"sort must be one of {SORTABLE} (prefix - for ascending)"}
    rows = s.execute(select(EtfBoardDaily).where(EtfBoardDaily.date == d)).scalars().all()
    key, asc = sort.lstrip("-"), sort.startswith("-")
    have = [r for r in rows if getattr(r, key) is not None]
    have.sort(key=lambda r: getattr(r, key), reverse=not asc)
    out = []
    for r in (have + [r for r in rows if getattr(r, key) is None])[: max(1, n)]:
        o = {"symbol": r.symbol, "mo3": r.mo3, "n_minutes": r.n_minutes, "trades_24h": r.trades_24h}
        for c in FLOAT_COLS:
            v = getattr(r, c)
            o[c] = None if v is None else round(v, 4)
        out.append(o)
    return {
        "date": str(d),
        "sort": sort,
        "page": "http://192.168.1.211:8899/etf_board.html",
        "rows": out,
        "units": "chg_* fractions; mo1/mo2/price_vol % points; buy/sell $; iv decimal (Convex und); "
        "BSR = buy/sell if buy>=sell else -(sell/buy)",
    }


def register(mcp: Any, settings: Any, session_factory: Any) -> None:  # noqa: ANN401
    @mcp.tool()
    def get_etf_board(sort: str = "bsr", n: int = 48, day: str = "") -> dict[str, Any]:
        """Mithil's ETF board ("Alt ETF Selects", 48 ETFs) for a day (default latest): per ETF 1D / 5D change,
        Buying | Selling $ from 1-minute bars, BSR, Strength, Building, correlation vs QQQ (10 / 20 sessions),
        Stretch, Mo1-3, RelVol, option P/C ratio and IVs. ``sort`` one of bsr, chg_1d, chg_5d, strength,
        building, corr20, stretch, relvol, pc_ratio, iv (prefix - = ascending). Descriptor only, DB only.
        """
        with session_factory() as s:
            return etf_board(s, sort, n, day)
