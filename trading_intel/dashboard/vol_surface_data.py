"""Readers + shaping for the banked vol surface (``surface_snapshots``).

Feeds ``pages/20_Vol_Surface.py`` and ``scripts/vol_surface_report.py`` — the vol-surface
changes board. Pure DB reads + pandas pivots. Everything is keyed by STRIKE: day-over-day
changes and the footprint align on (expiry_date, strike), so a specific listed contract is
compared like-for-like day after day (fixed strike = the receipt). A delta view is derivable
from the stored ``delta`` column when a moneyness axis is wanted.
"""

from __future__ import annotations

from datetime import date

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from trading_intel.memory.models import GreeksSnapshot, SurfaceSnapshot


def available_symbols(session: Session) -> list[str]:
    """Symbols that have any banked surface (for the page selector)."""
    rows = (
        session.execute(
            select(SurfaceSnapshot.symbol).distinct().order_by(SurfaceSnapshot.symbol)
        )
        .scalars()
        .all()
    )
    return list(rows)


def two_latest_dates(session: Session, symbol: str) -> list[date]:
    """The two most recent snapshot dates for ``symbol`` (newest first)."""
    rows = (
        session.execute(
            select(SurfaceSnapshot.ts)
            .where(SurfaceSnapshot.symbol == symbol)
            .distinct()
            .order_by(SurfaceSnapshot.ts.desc())
            .limit(2)
        )
        .scalars()
        .all()
    )
    return list(rows)


def load_surface(session: Session, symbol: str, ts: date) -> pd.DataFrame:
    """Long-format surface for one (symbol, date): expiry_date, dte, strike, iv, delta, spot."""
    rows = session.execute(
        select(
            SurfaceSnapshot.expiry_date,
            SurfaceSnapshot.dte,
            SurfaceSnapshot.strike,
            SurfaceSnapshot.iv,
            SurfaceSnapshot.delta,
            SurfaceSnapshot.spot,
        )
        .where(
            SurfaceSnapshot.symbol == symbol,
            SurfaceSnapshot.ts == ts,
            SurfaceSnapshot.iv.is_not(None),
        )
        .order_by(SurfaceSnapshot.dte, SurfaceSnapshot.strike)
    ).all()
    return pd.DataFrame(
        rows, columns=["expiry_date", "dte", "strike", "iv", "delta", "spot"]
    )


def surface_pivot(df: pd.DataFrame) -> pd.DataFrame:
    """IV matrix in vol %, index = strike (high->low), columns = dte (asc)."""
    if df is None or df.empty:
        return pd.DataFrame()
    piv = df.pivot_table(index="strike", columns="dte", values="iv", aggfunc="first")
    return (piv.sort_index(ascending=False) * 100.0).round(2)


def changes_pivot(df_today: pd.DataFrame, df_prior: pd.DataFrame) -> pd.DataFrame:
    """Day-over-day IV change (vol points), aligned on (expiry_date, STRIKE).

    True fixed-strike re-mark: each listed contract is matched to its own prior-day row by
    (expiry_date, strike), so the change is the actual move in that contract's vol, not a
    delta bucket sliding along the skew. Columns use today's DTE for the expiry.
    """
    if df_today is None or df_today.empty or df_prior is None or df_prior.empty:
        return pd.DataFrame()
    merged = df_today.merge(
        df_prior[["expiry_date", "strike", "iv"]],
        on=["expiry_date", "strike"],
        suffixes=("", "_prior"),
    )
    merged["chg"] = (merged["iv"] - merged["iv_prior"]) * 100.0
    piv = merged.pivot_table(index="strike", columns="dte", values="chg", aggfunc="first")
    return piv.sort_index(ascending=False).round(2)


def _front_expiry(session: Session, symbol: str, ts: date, target_dte: int) -> date | None:
    """The expiry_date nearest ``target_dte`` on the ``ts`` snapshot (a fixed contract to track)."""
    rows = session.execute(
        select(SurfaceSnapshot.expiry_date, SurfaceSnapshot.dte)
        .where(SurfaceSnapshot.symbol == symbol, SurfaceSnapshot.ts == ts)
        .distinct()
    ).all()
    if not rows:
        return None
    return min(rows, key=lambda r: abs(int(r[1]) - target_dte))[0]


def _fixed_strikes(session: Session, symbol: str, ts: date, exp: date) -> dict | None:
    """On ``ts``/``exp``, pick the call ~25Δ, put ~25Δ, and ATM listed strikes to track.

    Returns absolute strikes so the footprint follows the SAME contracts day-over-day.
    """
    rows = session.execute(
        select(SurfaceSnapshot.strike, SurfaceSnapshot.delta, SurfaceSnapshot.spot).where(
            SurfaceSnapshot.symbol == symbol,
            SurfaceSnapshot.ts == ts,
            SurfaceSnapshot.expiry_date == exp,
            SurfaceSnapshot.iv.is_not(None),
        )
    ).all()
    rows = [r for r in rows if r[1] is not None]
    if not rows:
        return None
    calls = [r for r in rows if r[1] > 0]
    puts = [r for r in rows if r[1] < 0]
    spot = next((r[2] for r in rows if r[2] is not None), None)

    call_k = min(calls, key=lambda r: abs(r[1] - 0.25))[0] if calls else None
    put_k = min(puts, key=lambda r: abs(r[1] + 0.25))[0] if puts else None
    if spot is not None:
        atm_k = min(rows, key=lambda r: abs(r[0] - spot))[0]
    else:
        atm_k = min(rows, key=lambda r: abs(abs(r[1]) - 0.5))[0]
    return {"call": call_k, "put": put_k, "atm": atm_k, "spot": spot}


def load_footprint_panel(
    session: Session, symbol: str, *, days: int = 6, target_dte: int = 7
) -> dict | None:
    """Multi-day FIXED-STRIKE wing IV on the FORWARD (front-week) expiry — the vol footprint.

    Locks the front-week expiry (nearest ``target_dte``, ~1 week) and the specific call/put/ATM
    listed strikes chosen on the latest snapshot, then tracks those exact strikes across the
    last ``days`` snapshot dates. This is the desk read: the same contract offered/bid day after
    day, not a delta bucket smeared by spot drift. Series oldest->newest.
    """
    tss = (
        session.execute(
            select(SurfaceSnapshot.ts)
            .where(SurfaceSnapshot.symbol == symbol)
            .distinct()
            .order_by(SurfaceSnapshot.ts.desc())
            .limit(days)
        )
        .scalars()
        .all()
    )
    if not tss:
        return None
    tss = sorted(tss)  # oldest -> newest
    exp = _front_expiry(session, symbol, tss[-1], target_dte)
    if exp is None:
        return None
    ks = _fixed_strikes(session, symbol, tss[-1], exp)
    if ks is None:
        return None
    want = [k for k in (ks["call"], ks["put"], ks["atm"]) if k is not None]
    if not want:
        return None
    rows = session.execute(
        select(SurfaceSnapshot.ts, SurfaceSnapshot.strike, SurfaceSnapshot.iv).where(
            SurfaceSnapshot.symbol == symbol,
            SurfaceSnapshot.expiry_date == exp,
            SurfaceSnapshot.ts.in_(tss),
            SurfaceSnapshot.strike.in_(want),
        )
    ).all()
    by_ts: dict = {}
    for t, k, iv in rows:
        by_ts.setdefault(t, {})[float(k)] = iv
    out_ts = [t for t in tss if t in by_ts]
    if not out_ts:
        return None

    def _series(k: float | None) -> list:
        return [by_ts[t].get(float(k)) if k is not None else None for t in out_ts]

    return {
        "expiry_date": exp,
        "call_strike": ks["call"],
        "put_strike": ks["put"],
        "atm_strike": ks["atm"],
        "spot": ks["spot"],
        "ts": [str(t) for t in out_ts],
        "call": _series(ks["call"]),
        "put": _series(ks["put"]),
        "atm": _series(ks["atm"]),
    }


def latest_net_gex(session: Session, symbol: str) -> float | None:
    """Latest aggregate net GEX (``GreeksSnapshot.gex_total``) for the symbol, or None."""
    snap = (
        session.execute(
            select(GreeksSnapshot)
            .where(GreeksSnapshot.symbol == symbol)
            .order_by(GreeksSnapshot.ts.desc())
            .limit(1)
        )
        .scalar_one_or_none()
    )
    if snap is None or snap.gex_total is None:
        return None
    return float(snap.gex_total)
