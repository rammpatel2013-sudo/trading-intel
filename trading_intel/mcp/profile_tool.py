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

from datetime import date, datetime, time, timedelta
from typing import Any

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from trading_intel.greeks.exposure_profile import greek_profiles
from trading_intel.memory.models import GreeksSnapshot, OiChainEod
from trading_intel.timeutils import eastern_now, live_expiry_floor, same_day_years


def _latest_oi_ts(session: Session, sym: str, as_of: date | None = None):
    q = select(OiChainEod.ts).where(OiChainEod.symbol == sym, OiChainEod.source.like("convex%"))
    if as_of is not None:
        q = q.where(OiChainEod.ts < datetime.combine(as_of + timedelta(days=1), time.min))
    return session.execute(q.order_by(OiChainEod.ts.desc())).scalars().first()


def _latest_spot(session: Session, sym: str, as_of: date | None = None) -> float | None:
    q = select(GreeksSnapshot.spot).where(GreeksSnapshot.symbol == sym, GreeksSnapshot.source == "convex")
    if as_of is not None:
        q = q.where(GreeksSnapshot.ts < datetime.combine(as_of + timedelta(days=1), time.min))
    row = session.execute(q.order_by(GreeksSnapshot.ts.desc())).first()
    return float(row[0]) if row and row[0] is not None else None


def _book_chain(
    session: Session, sym: str, ts: datetime, floor: date, dte_max: int = 400
) -> pd.DataFrame:
    """One ``oi_chain_eod`` book shaped for the BS engine, expiries ``>= floor``."""
    rows = session.execute(
        select(
            OiChainEod.strike, OiChainEod.cp, OiChainEod.iv, OiChainEod.oi, OiChainEod.expiry
        ).where(
            OiChainEod.symbol == sym,
            OiChainEod.source.like("convex%"),
            OiChainEod.ts == ts,
            OiChainEod.expiry >= floor,
            OiChainEod.dte <= dte_max,
            OiChainEod.iv.isnot(None),
        )
    ).all()
    # The same-day expiry expires at 16:00, not at midnight: give it the real time
    # left so its gamma isn't priced at the 1-hour floor.
    book_day = ts.date()
    d0_end = datetime.combine(book_day, time()) + timedelta(
        seconds=same_day_years(book_day) * 365.0 * 24 * 3600
    )
    return pd.DataFrame(
        [
            {
                "strike": r.strike,
                "opt_kind": "call" if str(r.cp).upper().startswith("C") else "put",
                "iv": r.iv,
                "oi": r.oi,
                "expiration": d0_end if r.expiry == book_day else r.expiry,
            }
            for r in rows
        ]
    )


def expired_rolloff(
    session: Session, symbol: str = "SPX", *, today: date | None = None
) -> dict[str, Any] | None:
    """How much of the last closed book's gamma AT SPOT sat in its own same-day expiry.

    Reads the newest book stamped BEFORE ``today`` (yesterday's EOD book), prices it
    once with and once without its same-day expiry, and returns the share that
    settled at that close. The brief prints it as one line so the drop from last
    night's numbers to this morning's map is explained, not a surprise. $bn per 1%
    move (``get_profile`` units). None when there is no prior book or no such expiry.
    """
    today = today or eastern_now().date()
    sym = symbol.upper()
    ts = session.execute(
        select(OiChainEod.ts)
        .where(OiChainEod.symbol == sym,
               OiChainEod.source.like("convex%"),
               OiChainEod.ts < datetime.combine(today, time.min))
        .order_by(OiChainEod.ts.desc())
    ).scalars().first()
    if ts is None:
        return None
    book_day = ts.date()
    spot = _latest_spot(session, sym, book_day)
    if spot is None:
        return None
    chain = _book_chain(session, sym, ts, book_day)
    if chain.empty:
        return None
    prof = greek_profiles(chain, float(spot), span=0.001, n_points=3, ref=book_day,
                          greeks=("gamma",))
    g = (prof or {}).get("gamma") or {}
    total = (g.get("all") or [None, None])[1]
    expired = ((g.get("by_expiry") or {}).get(book_day.isoformat()) or [None, None])[1]
    if total is None or expired is None:
        return None
    return {
        "symbol": sym,
        "book_day": book_day.isoformat(),
        "spot": float(spot),
        "gamma_total": float(total),
        "gamma_expired": float(expired),
        "gamma_after": float(total) - float(expired),
        "share": (float(expired) / float(total)) if total else None,
        "units": "$bn per 1% move",
    }


def get_profile(
    session: Session,
    symbol: str,
    *,
    span: float = 0.05,
    n_points: int = 141,
    dte_max: int = 400,
    as_of: date | None = None,
    spot_ref: float | None = None,
    greeks: tuple[str, ...] = ("gamma", "charm", "vanna"),
) -> dict[str, Any]:
    """Spot-ladder gamma/charm/vanna profiles for ``symbol`` from the latest EOD chain.

    Returns ``{symbol, as_of, spot, found, profiles}`` where ``profiles`` is
    ``{spot_ref: [...], gamma/charm/vanna: {all, by_expiry, flip}}`` — JSON-safe.
    Consumers build the 0DTE-shaded view by summing the near expiries in
    ``by_expiry``.

    ``as_of`` reads the newest chain ON/BEFORE that day (before/after books);
    ``spot_ref`` pins the spot ladder so two dates overlay on one grid.
    """
    sym = symbol.upper()
    ts = _latest_oi_ts(session, sym, as_of)
    spot = spot_ref if spot_ref is not None else _latest_spot(session, sym, as_of)
    if ts is None or spot is None:
        return {"symbol": sym, "found": False, "reason": "no oi_chain_eod snapshot or spot"}

    # Settled expiries are out: the book's own same-day expiry drops once its
    # session has closed, for any later read (2026-10-29 fix).
    chain = _book_chain(session, sym, ts, live_expiry_floor(ts.date()), dte_max)
    if chain.empty:
        return {"symbol": sym, "as_of": ts.date().isoformat(), "found": False}
    # Time-to-expiry from the BOOK's own date: an as_of book priced with today's
    # clock under-states t (and over-states gamma/charm) for the earlier snapshot.
    profiles = greek_profiles(chain, float(spot), span=span, n_points=n_points,
                              ref=ts.date(), greeks=greeks)
    return {
        "symbol": sym,
        "as_of": ts.date().isoformat(),
        "spot": float(spot),
        "found": bool(profiles),
        "n_strikes": int(len(chain)),
        "profiles": profiles,
    }
