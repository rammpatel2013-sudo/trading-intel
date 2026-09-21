"""Pure analytics for the vol board (spot / skew30 / fixed-strike vol, realized
skew, constant-maturity term structure).

No I/O: every function takes plain rows or arrays so it is unit-testable and the
fetch layer stays in ``scripts/vol_board_report.py``. Regime DESCRIPTORS only —
nothing here writes to ``signals`` (FlashAlpha rule 4).

Definitions (validated against the reference board):
- ``implied_skew``  = d(IV)/d(moneyness), vol-points per 1% of strike/spot, from
  TODAY's smile near the money.
- ``realized_skew`` = d(ATM IV)/d(spot return), vol-points per 1% spot move,
  regressed over a trailing window.
- ``lambda``        = realized_skew / implied_skew. >1 means ATM vol re-marks
  HARDER than the static smile slope implies (the smile is not sticky-strike).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

TENORS: tuple[int, ...] = (30, 60, 90, 180, 360)


def cm_interp(dte: np.ndarray, vals: np.ndarray, target: float) -> float | None:
    """IV at ``target`` DTE, linear in total variance. Re-exported from the
    iv_tenor job's construction so the board and the collector agree."""
    from trading_intel.scheduler.jobs.iv_tenor_snapshots import cm_interp as _ci

    return _ci(dte, vals, target)


def atm_iv_by_expiry(rows: list[dict], spot: float) -> dict[str, tuple[int, float]]:
    """``{expiration: (dte, atm_iv)}`` — ATM taken as the mean of the call and put
    whose |delta| is nearest 0.50, falling back to the strike nearest spot."""
    by_exp: dict[str, dict[str, list]] = {}
    for r in rows:
        iv = r.get("implied_volatility")
        exp = r.get("expiration_date")
        if iv is None or exp is None or not math.isfinite(float(iv)) or float(iv) <= 0:
            continue
        side = "calls" if str(r.get("contract_type", "")).lower().startswith("c") else "puts"
        by_exp.setdefault(str(exp), {"calls": [], "puts": []})[side].append(r)

    out: dict[str, tuple[int, float]] = {}
    for exp, sides in by_exp.items():
        ivs = []
        for side in ("calls", "puts"):
            lst = sides[side]
            if not lst:
                continue
            with_delta = [c for c in lst if c.get("delta") is not None]
            if with_delta:
                pick = min(with_delta, key=lambda c: abs(abs(float(c["delta"])) - 0.5))
                if abs(abs(float(pick["delta"])) - 0.5) > 0.15:
                    pick = min(lst, key=lambda c: abs(float(c["strike_price"]) - spot))
            else:
                pick = min(lst, key=lambda c: abs(float(c["strike_price"]) - spot))
            ivs.append(float(pick["implied_volatility"]))
        if ivs:
            out[exp] = (0, float(np.mean(ivs)))
    return out


def cm_curve(atm_by_dte: dict[int, float], tenors: tuple[int, ...] = TENORS) -> dict[int, float | None]:
    """Constant-maturity ATM IV at each tenor. ``None`` where the tenor is not
    bracketed by two real expiries — never extrapolated."""
    if not atm_by_dte:
        return {t: None for t in tenors}
    d = np.array(sorted(atm_by_dte), dtype=float)
    v = np.array([atm_by_dte[int(x)] for x in d], dtype=float)
    return {t: cm_interp(d, v, float(t)) for t in tenors}


def implied_skew(rows: list[dict], spot: float, *, band: float = 0.10) -> tuple[float | None, float | None]:
    """Smile slope near the money: regress IV (vol pts) on strike/spot (%).

    Returns ``(slope_per_1pct, r2)``. Negative = downside strikes carry higher
    IV (the normal equity/ETF shape).
    """
    xs, ys = [], []
    for r in rows:
        iv, k = r.get("implied_volatility"), r.get("strike_price")
        if iv is None or k is None:
            continue
        iv, k = float(iv), float(k)
        if not (math.isfinite(iv) and iv > 0 and k > 0):
            continue
        m = (k - spot) / spot
        if abs(m) <= band:
            xs.append(m * 100.0)
            ys.append(iv * 100.0)
    if len(xs) < 5:
        return None, None
    return _ols(np.array(xs), np.array(ys))


def realized_skew(spot_ret_pct: np.ndarray, d_atm_iv_pts: np.ndarray) -> tuple[float | None, float | None]:
    """Regress the daily CHANGE in ATM IV (vol pts) on the daily spot return (%).

    Returns ``(slope_per_1pct, r2)``. Negative = IV marks up when spot falls.
    """
    x = np.asarray(spot_ret_pct, dtype=float)
    y = np.asarray(d_atm_iv_pts, dtype=float)
    m = np.isfinite(x) & np.isfinite(y)
    if m.sum() < 5:
        return None, None
    return _ols(x[m], y[m])


def _ols(x: np.ndarray, y: np.ndarray) -> tuple[float, float]:
    slope, intercept = np.polyfit(x, y, 1)
    pred = slope * x + intercept
    ss_res = float(np.sum((y - pred) ** 2))
    ss_tot = float(np.sum((y - np.mean(y)) ** 2))
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0
    return float(slope), float(r2)


def skew_lambda(realized: float | None, implied: float | None) -> float | None:
    """``realized / implied`` — how much harder ATM vol re-marks than the static
    smile slope implies. ``None`` when either leg is missing or implied ~ 0."""
    if realized is None or implied is None or abs(implied) < 1e-9:
        return None
    return float(realized / implied)


def quartiles(vals: np.ndarray) -> dict[str, float] | None:
    """Min/Q1/median/Q3/max for a tenor's trailing window (the box plot)."""
    v = np.asarray([x for x in np.asarray(vals, dtype=float) if math.isfinite(x)])
    if v.size < 8:
        return None
    return {
        "lo": float(np.min(v)), "q1": float(np.percentile(v, 25)),
        "med": float(np.percentile(v, 50)), "q3": float(np.percentile(v, 75)),
        "hi": float(np.max(v)), "n": int(v.size),
    }


@dataclass(frozen=True)
class BoardRead:
    headline: str
    narrative: str


MIN_R2 = 0.15  # below this the realized-skew slope is noise, not a relationship


def classify(
    lam: float | None, cm_now: dict[int, float | None], q: dict[int, dict | None],
    *, r2: float | None = None,
) -> BoardRead:
    """Descriptive read: where ATM vol sits in its own 1Y range + what lambda says."""
    front = cm_now.get(30)
    qq = q.get(30)
    where = ""
    if front is not None and qq:
        if front <= qq["q1"]:
            where = "front vol is in the BOTTOM quartile of its trailing range"
        elif front >= qq["q3"]:
            where = "front vol is in the TOP quartile of its trailing range"
        else:
            where = "front vol sits mid-range"
    if lam is None:
        return BoardRead("NO READ", "Not enough history to fit the realized-vs-implied skew relationship.")
    if r2 is not None and r2 < MIN_R2:
        # A slope from an unfit regression is a number, not a finding. Report the
        # range position (which needs no fit) and say plainly that lambda is not
        # measurable rather than dressing noise as a sticky-strike call.
        base = (
            f"Spot moves explain almost none of the day-to-day ATM vol change (R2 {r2:.2f}), so the "
            "realized skew slope is not measurable here and lambda is not reported."
        )
        return BoardRead("NO RELIABLE SKEW FIT", (base + " " + where.capitalize() + ".") if where else base)
    if lam > 1.5:
        head = "VOL RE-MARKS HARDER THAN THE SMILE"
        why = (
            f"Realized skew is {lam:.1f}x the smile slope — when spot moves, ATM vol re-prices far "
            "more than a sticky-strike surface would. Delta-hedgedpositions carry more vol risk "
            "than the smile advertises."
        )
    elif lam < 0.5:
        head = "SMILE OVERSTATES THE VOL RESPONSE"
        why = (
            f"Realized skew is only {lam:.1f}x the smile slope — ATM vol barely moves when spot does. "
            "The surface is behaving sticky-strike; the skew is priced richer than it trades."
        )
    else:
        head = "SKEW TRADING NEAR ITS IMPLIED SLOPE"
        why = f"Realized skew is {lam:.1f}x the smile slope — vol is re-marking roughly as the smile implies."
    return BoardRead(head, (why + " " + where.capitalize() + ".") if where else why)
