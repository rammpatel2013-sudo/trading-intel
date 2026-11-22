"""0DTE Sell Gate (2026-11-22) - Imran Lakha / Options Insight "4 stop signs before you sell 0DTE premium".

Every input is the PRIOR session's close, so the gate is known before the open:
  1. Dealers short gamma   - SPX net GEX < 0 or spot below the gamma flip (greeks_snapshots, last row before the day)
  2. Front vol rising      - 7d at-the-money vol up vs 1, 3 AND 5 sessions earlier (vol_surface_cm, 7d rung, 50 delta).
                             v1 PROXY: constant-delta, not true fixed-strike vol (that needs the chain bank).
  3. VIX above 25          - vix_data.vix
  4. Skew >= 75th pct      - index_skew_daily.spx_rr_pctile_252d (25 delta put - call, 30d)

Count 0 = sell as normal, 1-2 = half size / wider wings, 3-4 = no premium selling (debit only).
Descriptor only (rule 4): it tags days; it never changes the frozen seven-test parameters.
"""
from __future__ import annotations

from datetime import date, datetime, time

from sqlalchemy import select
from sqlalchemy.orm import Session

from trading_intel.memory.models import GreeksSnapshot, IndexSkewDaily, VixData, VolSurfaceCM

VIX_LINE = 25.0
SKEW_PCT_LINE = 0.75
SIGN_NAMES = ("short_gamma", "front_vol_rising", "vix_above_25", "skew_high")
PLAIN = {
    "short_gamma": "Dealers short gamma - their hedging adds to the move both ways",
    "front_vol_rising": "Front-week vol rising on the 1, 3 and 5 day view (constant-delta proxy)",
    "vix_above_25": "VIX above 25",
    "skew_high": "Put skew at or above its 75th percentile (1 year)",
}


def bucket(count: int | None) -> str:
    if count is None:
        return "unknown"
    return "sell" if count == 0 else ("caution" if count <= 2 else "stop")


BUCKET_PLAIN = {
    "sell": "0 signs - sell premium as normal",
    "caution": "1-2 signs - half size, wider wings",
    "stop": "3-4 signs - no premium selling (debit trades only)",
    "unknown": "not enough data",
}


def count_signs(signs: dict[str, bool | None]) -> int | None:
    """Number of signs ON. None when fewer than 3 of the 4 could be read (too little data to trust)."""
    known = [v for v in signs.values() if v is not None]
    if len(known) < 3:
        return None
    return sum(1 for v in known if v)


def front_vol_rising(series: list[float]) -> bool | None:
    """``series`` oldest -> newest, newest = prior close. ON when the newest is above the values 1, 3 and 5 back."""
    if len(series) < 6:
        return None
    now = series[-1]
    return all(now > series[-1 - k] for k in (1, 3, 5))


def _gamma_sign(s: Session, day: date) -> bool | None:
    row = s.execute(
        select(GreeksSnapshot.gex_total, GreeksSnapshot.spot, GreeksSnapshot.gex_flip)
        .where(
            GreeksSnapshot.symbol == "SPX",
            GreeksSnapshot.source == "convex",  # the EOD book (not the hourly cvforge_bank rows)
            GreeksSnapshot.ts < datetime.combine(day, time(0)),
        )
        .order_by(GreeksSnapshot.ts.desc())
        .limit(1)
    ).first()
    if row is None:
        return None
    gex, spot, flip = row
    if gex is None and (spot is None or flip is None):
        return None
    return bool((gex is not None and gex < 0) or (spot is not None and flip is not None and spot < flip))


def _front_vol_series(s: Session, day: date) -> list[float]:
    rows = s.execute(
        select(VolSurfaceCM.ts, VolSurfaceCM.iv).where(
            VolSurfaceCM.symbol == "SPX", VolSurfaceCM.dte == 7, VolSurfaceCM.delta == 50.0, VolSurfaceCM.ts < day
        )
    ).all()
    by_day: dict[date, list[float]] = {}
    for ts, iv in rows:
        if iv is not None:
            by_day.setdefault(ts, []).append(float(iv))
    days = sorted(by_day)[-6:]
    return [sum(by_day[d]) / len(by_day[d]) for d in days]


def _vix(s: Session, day: date) -> float | None:
    v = s.execute(
        select(VixData.vix).where(VixData.date < day, VixData.vix.is_not(None)).order_by(VixData.date.desc()).limit(1)
    ).scalar()
    return float(v) if v is not None else None


def _skew_pct(s: Session, day: date) -> float | None:
    v = s.execute(
        select(IndexSkewDaily.spx_rr_pctile_252d)
        .where(IndexSkewDaily.date < day, IndexSkewDaily.spx_rr_pctile_252d.is_not(None))
        .order_by(IndexSkewDaily.date.desc())
        .limit(1)
    ).scalar()
    return float(v) if v is not None else None


def gate_for_session(s: Session, day: date) -> dict:
    """The gate for trading session ``day``, from the closes before it."""
    fv = _front_vol_series(s, day)
    vix = _vix(s, day)
    sk = _skew_pct(s, day)
    signs = {
        "short_gamma": _gamma_sign(s, day),
        "front_vol_rising": front_vol_rising(fv),
        "vix_above_25": None if vix is None else vix > VIX_LINE,
        "skew_high": None if sk is None else sk >= SKEW_PCT_LINE,
    }
    n = count_signs(signs)
    return {
        "day": day.isoformat(),
        "signs": signs,
        "count": n,
        "bucket": bucket(n),
        "values": {"vix": vix, "skew_pctile": sk, "front_vol_now": fv[-1] if fv else None},
    }
