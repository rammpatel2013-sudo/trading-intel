"""OpEx-morning gamma split: NOW (AM-settled + PM book) vs POST-SET (PM 0DTE only).

Alma (2026-08-21 OpEx post): on the monthly, the AM-settled SPX legs settle on the
opening prints (SET). Dealers' index hedge against them is redundant once they
settle, so the open meets responsive supply; after the SET only the PM (SPXW)
0DTE book is left and charm drives the afternoon. Two gamma-by-spot curves show
the book before and after that moment, with the named features she reads off
the curve: trapdoor / cushion / pocket / pin / wall / accel.

Data: ``oi_chain_eod`` rows for today's expiry from the 08:35 pull. AM-settled
rows carry ``source='convex_am_set'`` (tagged from the contract root since
2026-10-22); everything else is the PM book. Descriptor only (rule 4).
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any

import pandas as pd
import structlog
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from trading_intel.expiry_clock import monthly_opex
from trading_intel.greeks.exposure_profile import greek_profiles
from trading_intel.memory.models import GreeksSnapshot, OiChainEod
from trading_intel.timeutils import same_day_years

log = structlog.get_logger(__name__)

AM_SOURCE = "convex_am_set"


def is_monthly_opex(today: date) -> bool:
    return monthly_opex(today.year, today.month) == today


def _frame(rows: list, book_day: date) -> pd.DataFrame:
    d0_end = datetime.combine(book_day, time()) + timedelta(seconds=same_day_years(book_day) * 365.0 * 24 * 3600)
    return pd.DataFrame([{
        "strike": r.strike,
        "opt_kind": "call" if str(r.cp).upper().startswith("C") else "put",
        "iv": r.iv, "oi": r.oi, "expiration": d0_end,
    } for r in rows])


def features(grid: list[float], vals: list[float], spot: float) -> list[dict[str, Any]]:
    """Name the curve's features relative to spot (Alma's vocabulary).

    trapdoor = zero crossing below spot (gamma turns negative on the way down);
    cushion = positive peak below; pocket = negative trough just under spot;
    pin = positive peak within 0.3 % of spot; wall = largest positive peak above;
    accel = negative trough above (acceleration zone).
    """
    out: list[dict[str, Any]] = []
    n = len(grid)
    if n < 5 or not vals:
        return out
    peaks = [i for i in range(1, n - 1) if vals[i] > vals[i - 1] and vals[i] >= vals[i + 1] and vals[i] > 0]
    troughs = [i for i in range(1, n - 1) if vals[i] < vals[i - 1] and vals[i] <= vals[i + 1] and vals[i] < 0]
    zeros = [i for i in range(1, n) if (vals[i - 1] < 0 <= vals[i]) or (vals[i - 1] > 0 >= vals[i])]
    below = lambda i: grid[i] < spot  # noqa: E731
    near = [i for i in peaks if abs(grid[i] - spot) / spot <= 0.003]
    if near:
        i = max(near, key=lambda j: vals[j]); out.append({"name": "pin", "level": grid[i], "value": vals[i]})
    pb = [i for i in peaks if below(i) and i not in near]
    cushion_i = max(pb, key=lambda j: vals[j]) if pb else None
    if cushion_i is not None:
        out.append({"name": "cushion", "level": grid[cushion_i], "value": vals[cushion_i]})
    # the trapdoor is where gamma turns negative UNDER the cushion (the pocket's
    # edge just below spot is not it) — nearest zero below the cushion, else below spot
    zb = [i for i in zeros if (grid[i] < grid[cushion_i] if cushion_i is not None else below(i))]
    if zb:
        i = max(zb); out.append({"name": "trapdoor", "level": grid[i], "value": 0.0})
    tb = [i for i in troughs if below(i) and (spot - grid[i]) / spot <= 0.01]
    if tb:
        i = min(tb, key=lambda j: vals[j]); out.append({"name": "pocket", "level": grid[i], "value": vals[i]})
    pa = [i for i in peaks if not below(i) and i not in near]
    if pa:
        i = max(pa, key=lambda j: vals[j]); out.append({"name": "wall", "level": grid[i], "value": vals[i]})
    ta = [i for i in troughs if not below(i)]
    if ta:
        i = min(ta, key=lambda j: vals[j]); out.append({"name": "accel", "level": grid[i], "value": vals[i]})
    out.sort(key=lambda f: f["level"])
    return out


def build_opex_split(session: Session, symbol: str = "SPX", *, today: date | None = None,
                     span: float = 0.03, n_points: int = 61) -> dict[str, Any] | None:
    """Both curves + features for a monthly-OpEx morning; None on any other day or without AM rows."""
    today = today or date.today()
    if not is_monthly_opex(today):
        return None
    sym = symbol.upper()
    ts = session.execute(select(func.max(OiChainEod.ts)).where(OiChainEod.symbol == sym)).scalar_one_or_none()
    if ts is None or ts.date() != today:
        return None
    rows = session.execute(
        select(OiChainEod.strike, OiChainEod.cp, OiChainEod.iv, OiChainEod.oi, OiChainEod.source)
        .where(OiChainEod.symbol == sym, OiChainEod.ts == ts, OiChainEod.expiry == today, OiChainEod.iv.isnot(None))
    ).all()
    am = [r for r in rows if r.source == AM_SOURCE]
    pm = [r for r in rows if r.source != AM_SOURCE]
    if not am or not pm:
        log.info("opex_split.no_split", am=len(am), pm=len(pm))
        return None
    spot = session.execute(
        select(GreeksSnapshot.spot).where(GreeksSnapshot.symbol == sym, GreeksSnapshot.spot.isnot(None))
        .order_by(GreeksSnapshot.ts.desc()).limit(1)
    ).scalar_one_or_none()
    try:
        from trading_intel.synthesis.zero_dte import chain_spot
        cs = chain_spot(session, sym, ts=ts)
        if cs:
            spot = cs["spot"]
    except Exception:  # noqa: BLE001
        pass
    if not spot:
        return None
    now_p = greek_profiles(_frame(rows, today), float(spot), span=span, n_points=n_points, ref=today, greeks=("gamma",))
    post_p = greek_profiles(_frame(pm, today), float(spot), span=span, n_points=n_points, ref=today, greeks=("gamma",))
    if not now_p or not post_p:
        return None
    grid = [float(x) for x in now_p["spot_ref"]]
    now_v = [float(v) for v in now_p["gamma"]["all"]]
    post_v = [float(v) for v in post_p["gamma"]["all"]]
    am_oi = sum(int(r.oi or 0) for r in am)
    pm_oi = sum(int(r.oi or 0) for r in pm)
    return {
        "symbol": sym, "date": today.isoformat(), "spot": float(spot),
        "grid": grid, "now": now_v, "post": post_v,
        "now_flip": now_p["gamma"].get("flip"), "post_flip": post_p["gamma"].get("flip"),
        "now_features": features(grid, now_v, float(spot)),
        "post_features": features(grid, post_v, float(spot)),
        "am_contracts": len(am), "pm_contracts": len(pm), "am_oi": am_oi, "pm_oi": pm_oi,
        "read": (
            "Monthly OpEx: the AM-settled legs settle on the opening prints, so their dealer hedge is redundant "
            "at the SET — the open meets responsive supply. After the SET only the PM 0DTE book remains "
            "(the cyan curve): its features are the afternoon's structure, and charm drives dealer buying by the hour."
        ),
    }
