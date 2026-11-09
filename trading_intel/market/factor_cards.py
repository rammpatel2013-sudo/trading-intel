"""Factor cards with honest base rates (created 2026-11-09).

Six style / macro factors, each read as a 50-session relative return of an ETF pair
(or a rolling beta for rates), z-scored against its own rolling history:

    beta     SPHB vs SPLV        high beta minus low beta
    vol      SPY  vs USMV        market minus min-vol  (the volatility factor:
                                 "vol factor down" = low-vol stocks beating the market)
    size     IWM  vs SPY         small minus large
    quality  QUAL vs SPY         quality / profitability minus market
    oil      XLE  vs SPY         energy minus market (oil beta)
    rates    60-session rolling beta of SPY daily returns to TLT daily returns
             (positive = stocks and long bonds rise together; TLT is the long-duration
             proxy, IEF would give the same sign with a smaller move)

Maths (all on daily closes, dividend-adjusted when the price client returns it):
    rel50  = 100 * [ln(A_t/A_{t-50}) - ln(B_t/B_{t-50})]            (% points)
    z      = (rel50 - mean_756) / std_756   rolling 3 years, min 252 sessions, past only
    zones  = +/-2 sigma ("sell zone" above, "buy zone" below); "nearing" = |z| >= 1.5
    record = every past ENTRY into the same zone (z crosses the line from inside), counted
             at most once per 20 sessions so outcomes do not overlap; outcome = the pair's
             next-20-session relative return (rates: the next-20-session change in beta).
             Compared with the same outcome over ALL days ("any 20 days").
    grade  = "weak record" when n < 30 or the hit-rate edge vs any-20-days is < 5 points.

Descriptor only (rule 4): positioning read, not a trade signal. The zone names are the
reference card's wording; the base rate says how little (or much) they meant here.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

DISCLAIMER = "positioning read, not a trade signal"
REL_N = 50
FWD_N = 20
Z_WIN = 756
Z_MINP = 252
ZONE = 2.0
NEAR = 1.5
MIN_N = 30
MIN_EDGE = 5.0
LOOKBACK_2W = 10
SPARK = 260


@dataclass(frozen=True)
class Factor:
    key: str
    name: str
    long: str
    short: str
    kind: str = "rel"  # "rel" relative return | "beta" rolling beta of long on short
    about: str = ""


FACTORS: tuple[Factor, ...] = (
    Factor(
        "beta", "Beta", "SPHB", "SPLV", about="High-beta stocks minus low-beta stocks, 50 sessions."
    ),
    Factor(
        "vol",
        "Volatility",
        "SPY",
        "USMV",
        about=(
            "Market minus minimum-volatility stocks, 50 sessions. "
            "Down = low-vol stocks beating the market."
        ),
    ),
    Factor("size", "Size", "IWM", "SPY", about="Small caps minus the S&P 500, 50 sessions."),
    Factor(
        "quality",
        "Profitability / quality",
        "QUAL",
        "SPY",
        about="Quality (high-profitability) stocks minus the S&P 500, 50 sessions.",
    ),
    Factor("oil", "Oil beta", "XLE", "SPY", about="Energy stocks minus the S&P 500, 50 sessions."),
    Factor(
        "rates",
        "Rates beta",
        "SPY",
        "TLT",
        kind="beta",
        about=(
            "60-session beta of the S&P 500 to long Treasuries. "
            "Positive = stocks and bonds move together."
        ),
    ),
)
SYMBOLS = sorted({s for f in FACTORS for s in (f.long, f.short)})


def rel_return(a: pd.Series, b: pd.Series, n: int = REL_N) -> pd.Series:
    la, lb = np.log(a), np.log(b)
    return 100.0 * ((la - la.shift(n)) - (lb - lb.shift(n)))


def rolling_beta(y: pd.Series, x: pd.Series, n: int = 60) -> pd.Series:
    ry, rx = np.log(y).diff(), np.log(x).diff()
    cov = ry.rolling(n, min_periods=n).cov(rx)
    var = rx.rolling(n, min_periods=n).var()
    return cov / var


def zscore(x: pd.Series, win: int = Z_WIN, minp: int = Z_MINP) -> pd.Series:
    m = x.rolling(win, min_periods=minp).mean()
    s = x.rolling(win, min_periods=minp).std()
    return (x - m) / s.replace(0.0, np.nan)


def zone_of(z: float | None) -> str:
    if z is None or not np.isfinite(z):
        return "none"
    if z <= -ZONE:
        return "buy"
    if z >= ZONE:
        return "sell"
    if abs(z) >= NEAR:
        return "nearing_buy" if z < 0 else "nearing_sell"
    return "none"


def entries(z: pd.Series, side: str, *, gap: int = FWD_N) -> list[int]:
    """Positions where ``z`` ENTERS the zone (crosses -2 downward / +2 upward), de-overlapped."""
    v = z.to_numpy(dtype=float)
    out: list[int] = []
    last = -(10**9)
    for i in range(1, len(v)):
        a, b = v[i - 1], v[i]
        if not (np.isfinite(a) and np.isfinite(b)):
            continue
        hit = (a > -ZONE >= b) if side == "buy" else (a < ZONE <= b)
        if hit and i - last >= gap:
            out.append(i)
            last = i
    return out


def base_rate(level: pd.Series, z: pd.Series, side: str, *, fwd: int = FWD_N) -> dict:
    """Hit rate and mean of the next-``fwd`` change after each zone entry vs any day."""
    fut = level.shift(-fwd) - level
    idx = [i for i in entries(z, side) if np.isfinite(fut.iloc[i])]
    ev = fut.iloc[idx]
    allv = fut.dropna()
    n = len(ev)
    up = float((ev > 0).mean() * 100) if n else None
    up_all = float((allv > 0).mean() * 100) if len(allv) else None
    edge = None if up is None or up_all is None else up - up_all
    weak = n < MIN_N or edge is None or abs(edge) < MIN_EDGE
    return {
        "side": side,
        "n": n,
        "pct_up": None if up is None else round(up, 1),
        "avg": None if not n else round(float(ev.mean()), 2),
        "pct_up_all": None if up_all is None else round(up_all, 1),
        "avg_all": None if not len(allv) else round(float(allv.mean()), 2),
        "n_all": len(allv),
        "edge_pts": None if edge is None else round(edge, 1),
        "grade": "weak record" if weak else "stronger record",
        "dates": [z.index[i].date().isoformat() for i in idx][-12:],
    }


def _sentence(f: Factor, br: dict, unit: str) -> str:
    zn = "buy zone (z at or below -2)" if br["side"] == "buy" else "sell zone (z at or above +2)"
    if not br["n"]:
        return (
            f"{f.name}: z has never entered the {zn} in the history we hold, so there is no record."
        )
    return (
        f"Each of the {br['n']} times {f.name} entered the {zn}, the next 20 sessions were up "
        f"{br['pct_up']:.0f}% of the time, average {br['avg']:+.2f}{unit}, "
        f"vs {br['pct_up_all']:.0f}% "
        f"and {br['avg_all']:+.2f}{unit} for any 20 sessions: {br['grade']}."
    )


def card(f: Factor, prices: pd.DataFrame) -> dict:
    """One factor card from a close-price frame (columns = symbols, DatetimeIndex)."""
    if f.long not in prices or f.short not in prices:
        return {"key": f.key, "name": f.name, "found": False, "reason": "missing prices"}
    px = prices[[f.long, f.short]].dropna()
    if len(px) < Z_MINP + REL_N + 5:
        return {
            "key": f.key,
            "name": f.name,
            "found": False,
            "reason": "history too short",
            "since": px.index.min().date().isoformat() if len(px) else None,
            "n_days": len(px),
        }
    if f.kind == "beta":
        value = rolling_beta(px[f.long], px[f.short], 60)
        level, unit, vlabel = value, "", "60-session beta"
    else:
        value = rel_return(px[f.long], px[f.short], REL_N)
        level = 100.0 * (np.log(px[f.long]) - np.log(px[f.short]))  # cumulative relative, % pts
        unit, vlabel = " pts", "50-session relative return, %"
    z = zscore(value)
    zz = z.dropna()
    if zz.empty:
        return {"key": f.key, "name": f.name, "found": False, "reason": "no z-score yet"}
    last = zz.index[-1]
    zn = float(zz.iloc[-1])
    z2w = float(zz.iloc[-1 - LOOKBACK_2W]) if len(zz) > LOOKBACK_2W else None
    zone = zone_of(zn)
    side = "buy" if zn < 0 else "sell"
    br = base_rate(level, z, side)
    sp = zz.iloc[-SPARK:]
    since = px.index.min().date()
    years = round((last - px.index.min()).days / 365.25, 1)
    badge = {
        "buy": "Buy zone",
        "sell": "Sell zone",
        "nearing_buy": "Nearing buy zone",
        "nearing_sell": "Nearing sell zone",
    }.get(zone, "No signal")
    return {
        "key": f.key,
        "name": f.name,
        "pair": f"{f.long} vs {f.short}",
        "kind": f.kind,
        "about": f.about,
        "found": True,
        "asof": last.date().isoformat(),
        "value": round(float(value.loc[last]), 3),
        "value_label": vlabel,
        "z": round(zn, 2),
        "z_2w": None if z2w is None else round(z2w, 2),
        "zone": zone,
        "badge": badge,
        "record": br,
        "sentence": _sentence(f, br, unit),
        "since": since.isoformat(),
        "years": years,
        "z_since": zz.index[0].date().isoformat(),
        "short_history": years < 15,
        "spark": [round(float(x), 3) for x in sp.to_numpy()],
        "spark_dates": [d.date().isoformat() for d in sp.index],
        "window": {"rel": REL_N, "z": Z_WIN, "z_minp": Z_MINP, "fwd": FWD_N, "zone": ZONE},
    }


def build_cards(prices: pd.DataFrame) -> dict:
    cards = [card(f, prices) for f in FACTORS]
    ok = [c for c in cards if c.get("found")]
    return {
        "asof": max((c["asof"] for c in ok), default=None),
        "cards": cards,
        "method": {
            "rel": f"{REL_N}-session log relative return, % points",
            "z": (
                f"z-score vs its own rolling {Z_WIN}-session (3-year) history, "
                f"min {Z_MINP}, past data only"
            ),
            "zones": f"+/-{ZONE:g} sigma; nearing = |z| >= {NEAR:g}",
            "record": (
                f"every past entry into the same zone (at most one per {FWD_N} sessions); "
                "outcome = "
                f"next {FWD_N}-session relative return, vs any {FWD_N} sessions"
            ),
            "grade": (
                f"weak record when n < {MIN_N} or the edge vs any-20-days is "
                f"< {MIN_EDGE:g} points"
            ),
            "rates": "60-session beta of SPY daily log returns on TLT daily log returns",
        },
        "note": DISCLAIMER,
    }
