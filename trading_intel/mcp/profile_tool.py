"""MCP reader: per-strike dealer gamma/charm/vanna BY-SPOT profile (get_profile).

Reads the latest ``oi_chain_eod`` snapshot for a symbol (the table
``index_walls_am`` fills for SPX/SPY despite CHAIN_EXCLUDE_ROOTS, and the EOD
``oi_chain_eod`` job fills for single names), shapes it into the chain the BS
engine wants, and returns the spot-ladder $gamma/$charm/$vanna profiles per
expiry + aggregate + flip via ``greeks.exposure_profile.greek_profiles``.

Spot comes from the aggregate ``GreeksSnapshot`` (same source ``get_walls`` uses
via ``latest_snapshot``). READ-ONLY, no Convex calls, descriptor only (rule 4).

Wire in server.py:
    from trading_intel.mcp import profile_tool as pt
    @mcp.tool()
    def get_profile(symbol: str, span: float = 0.05, n_points: int = 141) -> dict[str, Any]:
        with session_scope() as session:
            return pt.get_profile(session, symbol, span=span, n_points=n_points)
"""
from __future__ import annotations

from typing import Any

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from trading_intel.greeks.exposure_profile import greek_profiles
from trading_intel.memory.models import GreeksSnapshot, OiChainEod


def _latest_oi_ts(session: Session, sym: str):
    return session.execute(
        select(OiChainEod.ts).where(OiChainEod.symbol == sym).order_by(OiChainEod.ts.desc())
    ).scalars().first()


def _latest_spot(session: Session, sym: str) -> float | None:
    row = session.execute(
        select(GreeksSnapshot.spot)
        .where(GreeksSnapshot.symbol == sym)
        .order_by(GreeksSnapshot.ts.desc())
    ).first()
    return float(row[0]) if row and row[0] is not None else None


def get_profile(
    session: Session,
    symbol: str,
    *,
    span: float = 0.05,
    n_points: int = 141,
    dte_max: int = 400,
) -> dict[str, Any]:
    """Spot-ladder gamma/charm/vanna profiles for ``symbol`` from the latest EOD chain.

    Returns ``{symbol, as_of, spot, found, profiles}`` where ``profiles`` is
    ``{spot_ref: [...], gamma/charm/vanna: {all, by_expiry, flip}}`` — JSON-safe.
    Consumers build the 0DTE-shaded view by summing the near expiries in
    ``by_expiry``.
    """
    sym = symbol.upper()
    ts = _latest_oi_ts(session, sym)
    spot = _latest_spot(session, sym)
    if ts is None or spot is None:
        return {"symbol": sym, "found": False, "reason": "no oi_chain_eod snapshot or spot"}

    rows = session.execute(
        select(
            OiChainEod.strike, OiChainEod.cp, OiChainEod.iv, OiChainEod.oi, OiChainEod.expiry
        ).where(
            OiChainEod.symbol == sym,
            OiChainEod.ts == ts,
            OiChainEod.dte >= 0,
            OiChainEod.dte <= dte_max,
            OiChainEod.iv.isnot(None),
        )
    ).all()
    if not rows:
        return {"symbol": sym, "as_of": ts.date().isoformat(), "found": False}

    chain = pd.DataFrame(
        [
            {
                "strike": r.strike,
                "opt_kind": "call" if str(r.cp).upper().startswith("C") else "put",
                "iv": r.iv,
                "oi": r.oi,
                "expiration": r.expiry,
            }
            for r in rows
        ]
    )
    profiles = greek_profiles(chain, float(spot), span=span, n_points=n_points)
    return {
        "symbol": sym,
        "as_of": ts.date().isoformat(),
        "spot": float(spot),
        "found": bool(profiles),
        "n_strikes": int(len(chain)),
        "profiles": profiles,
    }
