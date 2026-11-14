"""Vol board tells (2026-11-14, from the Jared H harvest) - pure, no I/O.

Three plain-English reads for the cockpit VIX tab > Vol board (display only):

1. "Calm drop"       SPX closes down 1%+ but VIX rose at least 1 point LESS than the day's drop
                     explains (VIX overshoot model residual <= -1): big players were already hedged.
2. "Rally + fear up" SPX closes up 1%+ and VIX still closes higher: the rally is on shaky legs.
3. VIX futures basis 30-day constant-maturity VIX future (front two monthly futures, interpolated
                     to 30 calendar days) minus spot VIX. <= 0 = fear priced now (cheap to enter a
                     fade), 0-2 = normal, > 2 = crowded and expensive.

The model for (1) is ``vix_overshoot.compute`` (refit daily on the prior 126 sessions, no
look-ahead). The evidence blocks are FROZEN from the 2026-11-14 tests on Cboe history (VIX / SPX
closes 1990-2026; VX settlement files 2013-05 to 2026-11) - see the project doc
claude/jaredhstocks-methodology-20261114.md. Descriptors only (rule 4): never writes ``signals``.
"""

from __future__ import annotations

from datetime import date

from trading_intel.market.macro_rails import is_vix_monthly
from trading_intel.vol import vix_overshoot

CALM_SPX = -1.0  # SPX close-to-close, % (100 * ln)
CALM_RES = -1.0  # VIX overshoot residual, vol points
RALLY_SPX = 1.0
ACTIVE = 4  # a tell stays lit for the session it printed + 4 more (the 5-day read)
CM_DAYS = 30  # constant maturity of the basis, calendar days
CHEAP_MAX = 0.0  # basis <= 0  -> cheap
NORMAL_MAX = 2.0  # 0 < basis <= 2 -> normal; above -> crowded

# 5-session SPX return after the tell, from the close. "base" = every session in the same span.
EVIDENCE = {
    "calm": {
        "span": "1990-2026",
        "n": 197,
        "avg": 0.81,
        "up": 59,
        "base_avg": 0.19,
        "base_up": 58,
        "recent": {"span": "2018-11 to 2026-11", "n": 60, "avg": 1.48, "up": 65},
    },
    "rally": {
        "span": "1990-2026",
        "n": 83,
        "avg": 0.06,
        "up": 48,
        "base_avg": 0.19,
        "base_up": 58,
        "recent": {"span": "2018-11 to 2026-11", "n": 24, "avg": -0.59, "up": 38},
    },
}
# Basis buckets, 2013-05-20 to 2026-11-13 (3,395 sessions). short_vx = short the 2nd-month VX
# future for 10 sessions (points); big_loss = share of those that lost 3+ points; jump = VIX up 5+
# points at some close in the next 20 sessions; spx20 / vix20 = 20-session SPX % / VIX points.
BASIS_EV = {
    "cheap": {
        "n": 596,
        "short_vx": 0.95,
        "win": 72,
        "big_loss": 9.5,
        "jump": 27,
        "spx20": 2.46,
        "vix20": -4.5,
    },
    "normal": {
        "n": 1776,
        "short_vx": 0.19,
        "win": 62,
        "big_loss": 6.7,
        "jump": 31,
        "spx20": 0.83,
        "vix20": 0.59,
    },
    "crowded": {
        "n": 1023,
        "short_vx": 0.37,
        "win": 70,
        "big_loss": 4.8,
        "jump": 39,
        "spx20": 0.46,
        "vix20": 1.66,
    },
    "span": "2013-05 to 2026-11, Cboe VX settlements",
}

TEXT = {
    "calm": {
        "name": "Calm drop",
        "what": "SPX fell 1%+ but VIX rose less than the drop explains: big players were already "
        "hedged, no panic.",
        "lean": "Next 5 days leaned UP",
        "use": "Favour put-side credit spreads (selling under the market) over buying puts.",
    },
    "rally": {
        "name": "Rally + fear up",
        "what": "SPX rose 1%+ and VIX still closed higher: the rally is on shaky legs.",
        "lean": "Next 5 days: the normal upward drift went missing",
        "use": "Don't chase the rally; favour call-side credit spreads (selling above the market) "
        "over buying calls.",
    },
}
BASIS_TEXT = {
    "cheap": (
        "Cheap to enter",
        "Fear is priced NOW (futures at or under spot VIX) - cheap to " "enter the fade.",
    ),
    "normal": ("Normal", "Futures a little over spot VIX - the usual slope, nothing to lean on."),
    "crowded": (
        "Crowded & expensive",
        "Many are already short VIX: protection costs extra and a " "shock hits hard.",
    ),
}


def _row(r: vix_overshoot.OvershootRow) -> dict:
    return {
        "d": r.date.isoformat(),
        "spx": round(r.spx_ret_pct, 2),
        "vix": round(r.vix_actual, 2),
        "vix_chg": round(r.vix_actual - r.vix_prev, 2),
        "model_chg": round(r.vix_model - r.vix_prev, 2),
        "res": round(r.residual, 2),
    }


def is_calm(r: vix_overshoot.OvershootRow) -> bool:
    return r.spx_ret_pct <= CALM_SPX and r.residual <= CALM_RES


def is_rally(r: vix_overshoot.OvershootRow) -> bool:
    return r.spx_ret_pct >= RALLY_SPX and r.vix_actual > r.vix_prev


def setups(
    vix: dict[date, float], spx: dict[date, float], *, provisional: date | None = None
) -> dict:
    """Both day tells from VIX / SPX closes. ``provisional`` = the date of a live (not yet closed)
    session included in the inputs; the result says so when the newest row is that session."""
    rows = vix_overshoot.compute(vix, spx)
    if not rows:
        return {"found": False, "reason": "under 61 sessions of VIX + SPX history"}
    n = len(rows)
    out: dict = {
        "found": True,
        "asof": rows[-1].date.isoformat(),
        "today": _row(rows[-1]),
        "provisional": provisional is not None and rows[-1].date == provisional,
        "beta": round(rows[-1].beta, 2),
        "n_fit": rows[-1].n_fit,
    }
    for key, test in (("calm", is_calm), ("rally", is_rally)):
        hit = next((i for i in range(n - 1, -1, -1) if test(rows[i])), None)
        last = None
        if hit is not None:
            ago = n - 1 - hit
            last = {
                **_row(rows[hit]),
                "ago": ago,
                "active": ago <= ACTIVE,
                "count_1y": sum(1 for r in rows[-252:] if test(r)),
            }
        out[key] = {**TEXT[key], "last": last, "ev": EVIDENCE[key]}
    return out


def basis_state(b: float) -> str:
    return "cheap" if b <= CHEAP_MAX else "normal" if b <= NORMAL_MAX else "crowded"


def vx_basis(exps: list[dict], asof: date, vix: float) -> dict:
    """exps = [{"e": "YYYY-MM-DD", "F": forward}] (vixmap_*.json, VIX option forwards). The front
    contract is the first MONTHLY expiry after ``asof`` (expiring today = gone). 30-day weight
    w = (30 - t1) / (t2 - t1) clamped to 0..1, as in the 2013-2026 test."""
    if not vix or vix <= 0:
        return {"found": False, "reason": "no spot VIX"}
    mon = []
    for x in exps or []:
        try:
            e = date.fromisoformat(str(x["e"])[:10])
            f = float(x["F"])
        except (KeyError, TypeError, ValueError):
            continue
        if e > asof and f > 0 and is_vix_monthly(e):
            mon.append((e, f))
    mon.sort()
    if len(mon) < 2:
        return {"found": False, "reason": f"only {len(mon)} monthly VIX expiries with a forward"}
    (e1, f1), (e2, f2) = mon[0], mon[1]
    t1, t2 = (e1 - asof).days, (e2 - asof).days
    w = min(1.0, max(0.0, (CM_DAYS - t1) / (t2 - t1))) if t2 > t1 else 0.0
    vx30 = f1 + w * (f2 - f1)
    b = vx30 - vix
    st = basis_state(b)
    label, text = BASIS_TEXT[st]
    return {
        "found": True,
        "asof": asof.isoformat(),
        "vix": round(vix, 2),
        "vx30": round(vx30, 2),
        "basis": round(b, 2),
        "state": st,
        "label": label,
        "text": text,
        "legs": [
            {"e": e1.isoformat(), "F": round(f1, 2), "t": t1},
            {"e": e2.isoformat(), "F": round(f2, 2), "t": t2},
        ],
        "w2": round(w, 3),
        "ev": BASIS_EV,
    }
