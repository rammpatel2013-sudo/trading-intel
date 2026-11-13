"""Pure assembly + read for the constant-maturity vol-surface-changes board.

Takes banked ``vol_surface_cm`` rows (today + a prior compare date) and produces
the structured view the report renders: the delta×rung IV grid, the weekly
CHANGE grid, the ATM term structure + forward-vol curve, the front-rung skew,
and — the point of the board — the auto READ.

The read separates PRICE from the DEMAND for vol, at a fixed forward horizon.
Because the rungs are constant-maturity, a rising IV at a (rung, delta) is a real
re-mark, not delta drift. Pairing that with spot direction gives conviction:

    price ↑ + call-wing vol BID   → rally confirmed (bullish; stronger out the curve)
    price ↑ + call-wing vol OFFERED→ rally unconfirmed (flow/overwriting — fade/caution)
    price ↓ + put-wing vol BID    → real fear / risk-off
    price ↓ + put-wing vol FLAT   → complacent slide (the quiet-unwind vacuum)

Front rungs (≤21d) moving = near-term/event; back rungs (≥30d) = regime.

2026-11-13 — VOL MOVE SPLIT (Citi "Jargon-Busting Guide", 2008). A fixed-DELTA grid moves even
when nothing is re-priced: after a down day the 25Δ put is a lower strike, which already had a
higher IV on yesterday's curve. On our SPX history (8/11–11/12, 60 clean day pairs) 64–73% of the
ATM change on down days was that slide, and the 25Δ put moved ~4x more at fixed delta than at
fixed strike. So each rung's ATM change is split into
    slide = yesterday's curve read at today's ATM strike − yesterday's ATM   (moving along the skew)
    lift  = today's ATM − that                                             (the curve itself moved)
and the READ uses the wing change at a FIXED STRIKE (= the lift there). Rungs whose 50Δ call and
put IVs disagree by more than _BAD_RUNG_PTS (a broken forward — 10 of 87 days had one) are skipped.

Descriptor / research only (FlashAlpha rule 4). Pure stdlib — no pandas / no DB.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from statistics import NormalDist
from typing import Any

DELTAS = (5.0, 7.5, 10.0, 15.0, 20.0, 25.0, 30.0, 35.0, 40.0, 45.0, 47.5, 50.0)
_WING = 25.0  # representative wing delta for the read
_FRONT_MAX = 21  # rungs ≤ this = "front" (event); > this = "back" (regime)
_BAD_RUNG_PTS = 3.0  # 50Δ call vs put IV gap (vol pts) above which a rung is treated as broken
_SPLIT_THR = 0.25  # vol pts: smaller slide/lift counts as "no move"
_ND = NormalDist()


def _by_key(rows: list[dict[str, Any]]) -> dict[tuple, float]:
    """{(dte, delta, side): iv} from banked rows (iv in decimal)."""
    out: dict[tuple, float] = {}
    for r in rows or []:
        iv = r.get("iv")
        if iv is None:
            continue
        out[(int(r["dte"]), round(float(r["delta"]), 2), str(r["side"]))] = float(iv)
    return out


def _atm(idx: dict[tuple, float], dte: int) -> float | None:
    """ATM IV (vol pts) at a rung = mean of the 50Δ call/put points."""
    c = idx.get((dte, 50.0, "call"))
    p = idx.get((dte, 50.0, "put"))
    vals = [v for v in (c, p) if v is not None]
    return (sum(vals) / len(vals)) * 100.0 if vals else None


def forward_vol(rungs: list[int], atm_pts: dict[int, float | None]) -> dict[tuple, float]:
    """Between-rung forward vol (vol pts): fwd² = (σ2²·T2 − σ1²·T1)/(T2−T1)."""
    out: dict[tuple, float] = {}
    for a, b in zip(rungs, rungs[1:]):
        s1, s2 = atm_pts.get(a), atm_pts.get(b)
        if s1 is None or s2 is None or b <= a:
            continue
        v = (s2 * s2 * b - s1 * s1 * a) / (b - a)
        if v > 0:
            out[(a, b)] = math.sqrt(v)
    return out


def rung_ok(idx: dict[tuple, float], dte: int, tol: float = _BAD_RUNG_PTS) -> bool:
    """False when the 50Δ call and put IV disagree by more than ``tol`` vol pts (bad forward)."""
    c = idx.get((dte, 50.0, "call"))
    p = idx.get((dte, 50.0, "put"))
    if c is None or p is None:
        return True
    return abs(c - p) * 100.0 <= tol


def strike_smile(idx: dict[tuple, float], dte: int, spot: float) -> list[tuple[float, float]]:
    """The rung's delta grid mapped to strikes (Black-Scholes, r = 0): sorted [(strike, iv)]."""
    t = max(dte, 1) / 365.0
    out = []
    for (r, d, side), iv in idx.items():
        if r != dte or iv is None or iv <= 0 or not 0 < d < 100:
            continue
        x = _ND.inv_cdf(d / 100.0)
        sgn = -1.0 if side == "call" else 1.0
        out.append((spot * math.exp(sgn * x * iv * math.sqrt(t) + iv * iv * t / 2.0), iv))
    out.sort()
    return out


def _interp(smile: list[tuple[float, float]], k: float) -> float | None:
    for (k0, v0), (k1, v1) in zip(smile, smile[1:]):
        if k0 <= k <= k1:
            return v0 if k1 == k0 else v0 + (v1 - v0) * (k - k0) / (k1 - k0)
    return None


def split_regime(slide: float, lift: float, thr: float = _SPLIT_THR) -> str:
    """Citi's three regimes (+ relief), in plain words."""
    if abs(slide) < thr and abs(lift) < thr:
        return "flat"
    if abs(lift) < thr or abs(lift) < 0.5 * abs(slide):
        return "along the curve"  # sticky strike: normal, tends to snap back
    if slide * lift > 0:
        return "nervous" if lift > 0 else "relief"  # super-sticky strike
    if abs(slide + lift) < thr:
        return "curve moved with price"  # sticky delta
    return "curve re-marked"


def vol_split(
    idx_prior: dict[tuple, float], idx_now: dict[tuple, float], spot_prior: float | None,
    spot_now: float | None, dte: int,
) -> dict[str, Any] | None:
    """ATM change at one rung split into slide (along the prior curve) + lift (curve moved), vol pts."""
    if not spot_prior or not spot_now:
        return None
    a0, a1 = _atm(idx_prior, dte), _atm(idx_now, dte)
    if a0 is None or a1 is None:
        return None
    t = max(dte, 1) / 365.0
    k_new = spot_now * math.exp((a0 / 100.0) ** 2 * t / 2.0)  # today's 50Δ strike, prior vol
    y = _interp(strike_smile(idx_prior, dte, spot_prior), k_new)
    if y is None:
        return None
    y *= 100.0
    slide, lift = y - a0, a1 - y
    return {"total": a1 - a0, "slide": slide, "lift": lift, "regime": split_regime(slide, lift)}


def fixed_strike_wing_change(
    idx_prior: dict[tuple, float], idx_now: dict[tuple, float], spot_prior: float | None,
    spot_now: float | None, dte: int, side: str, wing: float = _WING,
) -> float | None:
    """IV change (vol pts) at the strike that was the prior session's ``wing``-delta option."""
    p = idx_prior.get((dte, wing, side))
    if p is None or not spot_prior or not spot_now:
        return None
    t = max(dte, 1) / 365.0
    x = _ND.inv_cdf(wing / 100.0)
    sgn = -1.0 if side == "call" else 1.0
    k = spot_prior * math.exp(sgn * x * p * math.sqrt(t) + p * p * t / 2.0)
    v = _interp(strike_smile(idx_now, dte, spot_now), k)
    return None if v is None else (v - p) * 100.0


@dataclass
class SurfaceView:
    rungs: list[int] = field(default_factory=list)
    deltas: list[float] = field(default_factory=list)
    near_expiry: dict[int, Any] = field(default_factory=dict)  # rung -> expiry label
    iv_now: dict[tuple, float] = field(default_factory=dict)  # (dte,delta,side) -> vol pts
    iv_chg: dict[tuple, float] = field(default_factory=dict)  # weekly change, vol pts
    atm_now: dict[int, float | None] = field(default_factory=dict)
    atm_prior: dict[int, float | None] = field(default_factory=dict)
    fwd_now: dict[tuple, float] = field(default_factory=dict)
    spot_now: float | None = None
    spot_prior: float | None = None
    ts_now: str | None = None
    ts_prior: str | None = None
    read_label: str = ""
    read_text: str = ""
    split: dict[int, dict[str, Any]] = field(default_factory=dict)  # rung -> vol_split()
    wing_fixed_strike: dict[tuple, float] = field(default_factory=dict)  # (dte, side) -> vol pts
    bad_rungs: list[int] = field(default_factory=list)
    read_basis: str = "fixed-delta"


def _wing_change(iv_chg: dict[tuple, float], side: str, rungs: list[int], *, front: bool | None = None) -> float | None:
    """Mean ΔIV (vol pts) at the ~25Δ wing for a side, optionally front/back only."""
    vals = []
    for dte in rungs:
        if front is True and dte > _FRONT_MAX:
            continue
        if front is False and dte <= _FRONT_MAX:
            continue
        v = iv_chg.get((dte, _WING, side))
        if v is not None:
            vals.append(v)
    return sum(vals) / len(vals) if vals else None


def classify_read(
    spot_chg_pct: float | None,
    call_chg: float | None,
    put_chg: float | None,
    call_chg_back: float | None = None,
    *,
    bid: float = 0.10,
    skew_thr: float = 0.75,
) -> tuple[str, str]:
    """Read from spot direction × where the wing vol moved × the SKEW SHIFT (vol pts).

    Beyond the absolute wing change, the relative put-vs-call move matters: in a
    vol crush the whole surface falls, so calls are rarely bid outright even on a
    genuine risk-on rally — but puts come off far harder, collapsing the skew
    (hedges dumped). ``skew_shift = call_chg - put_chg``: >0 = skew collapsing
    (risk-on), <0 = steepening (protection bid). ``bid`` = the "outright bid"
    threshold; ``skew_thr`` = the "meaningful skew shift" threshold (vol pts).
    """
    if spot_chg_pct is None or (call_chg is None and put_chg is None):
        return ("no-read", "Not enough history yet to read the change — banks forward.")
    up = spot_chg_pct >= 0
    skew_shift = (call_chg - put_chg) if (call_chg is not None and put_chg is not None) else None
    where = "back rungs (regime)" if (call_chg_back is not None and call_chg_back >= bid) else "front rungs (event)"
    c = call_chg if call_chg is not None else 0.0
    p = put_chg if put_chg is not None else 0.0

    if up:
        if call_chg is not None and call_chg >= bid:
            return (
                "rally-confirmed",
                f"Spot +{spot_chg_pct:.1f}% and upside (call) vol is getting BID "
                f"(+{call_chg:.2f}pt, {where}) — demand is confirming the move. Bullish; "
                "strongest when the bid is out the curve.",
            )
        if skew_shift is not None and skew_shift >= skew_thr:
            return (
                "risk-on",
                f"Spot +{spot_chg_pct:.1f}% with calls flat/offered ({c:+.2f}pt) BUT skew "
                f"collapsing — puts came off far harder ({p:+.2f}pt, Δskew {skew_shift:+.2f}pt). "
                "Hedges being dumped / vol crushing into the rally — risk-on, protection sold "
                "rather than a demand-led breakout. Constructive while skew keeps flattening; "
                "would turn suspect if calls never pick up the bid.",
            )
        return (
            "rally-unconfirmed",
            f"Spot +{spot_chg_pct:.1f}% but upside vol flat/offered ({c:+.2f}pt) and skew isn't "
            f"collapsing (Δskew {(skew_shift or 0):+.2f}pt) — the rally isn't being paid for "
            "(flow / short-cover / call overwriting). Low conviction; prone to stall or get sold.",
        )

    # spot down
    if (put_chg is not None and put_chg >= bid) or (skew_shift is not None and skew_shift <= -skew_thr):
        return (
            "fear",
            f"Spot {spot_chg_pct:.1f}% and downside vol bid / skew steepening "
            f"(puts {p:+.2f}pt vs calls {c:+.2f}pt, Δskew {(skew_shift or 0):+.2f}pt) — genuine "
            "protection demand / risk-off. Real fear, especially if the back rungs are bid.",
        )
    return (
        "quiet-slide",
        f"Spot {spot_chg_pct:.1f}% but vol is asleep (puts {p:+.2f}pt, skew flat) — a complacent "
        "slide: support leaving with nobody bidding protection. The quiet-unwind vacuum; watch "
        "for acceleration.",
    )


def build_view(rows_now: list[dict[str, Any]], rows_prior: list[dict[str, Any]] | None = None) -> SurfaceView:
    """Assemble the full board view from banked rows (today + prior compare date)."""
    idx_now = _by_key(rows_now)
    idx_prior = _by_key(rows_prior or [])
    rungs = sorted({int(r["dte"]) for r in (rows_now or [])})
    deltas = sorted({round(float(r["delta"]), 2) for r in (rows_now or [])}) or list(DELTAS)

    v = SurfaceView(rungs=rungs, deltas=deltas)
    v.spot_now = next((r.get("spot") for r in (rows_now or []) if r.get("spot") is not None), None)
    v.spot_prior = next((r.get("spot") for r in (rows_prior or []) if r.get("spot") is not None), None)
    v.ts_now = str((rows_now or [{}])[0].get("ts")) if rows_now else None
    v.ts_prior = str((rows_prior or [{}])[0].get("ts")) if rows_prior else None
    for dte in rungs:
        v.near_expiry[dte] = next(
            (r.get("near_expiry") for r in rows_now if int(r["dte"]) == dte and r.get("near_expiry")),
            None,
        )
        v.atm_now[dte] = _atm(idx_now, dte)
        v.atm_prior[dte] = _atm(idx_prior, dte)
    v.fwd_now = forward_vol(rungs, v.atm_now)

    for (dte, d, side), iv in idx_now.items():
        v.iv_now[(dte, d, side)] = iv * 100.0
        p = idx_prior.get((dte, d, side))
        if p is not None:
            v.iv_chg[(dte, d, side)] = round((iv - p) * 100.0, 3)

    spot_chg = ((v.spot_now / v.spot_prior - 1.0) * 100.0) if (v.spot_now and v.spot_prior) else None
    good = [r for r in rungs if rung_ok(idx_now, r) and (not idx_prior or rung_ok(idx_prior, r))]
    v.bad_rungs = [r for r in rungs if r not in good]
    if idx_prior:
        for r in good:
            sp = vol_split(idx_prior, idx_now, v.spot_prior, v.spot_now, r)
            if sp is not None:
                v.split[r] = sp
            for side in ("call", "put"):
                w = fixed_strike_wing_change(idx_prior, idx_now, v.spot_prior, v.spot_now, r, side)
                if w is not None:
                    v.wing_fixed_strike[(r, side)] = w

    def fs_mean(side: str, front: bool | None = None) -> float | None:
        vals = [w for (r, s), w in v.wing_fixed_strike.items() if s == side
                and not (front is True and r > _FRONT_MAX) and not (front is False and r <= _FRONT_MAX)]
        return sum(vals) / len(vals) if vals else None

    if v.wing_fixed_strike:
        v.read_basis = "fixed-strike"
        call_chg, put_chg, call_back = fs_mean("call"), fs_mean("put"), fs_mean("call", front=False)
    else:
        call_chg = _wing_change(v.iv_chg, "call", good)
        put_chg = _wing_change(v.iv_chg, "put", good)
        call_back = _wing_change(v.iv_chg, "call", good, front=False)
    v.read_label, v.read_text = classify_read(spot_chg, call_chg, put_chg, call_back)
    key = min(v.split, key=lambda r: abs(r - 30)) if v.split else None
    if key is not None and v.read_label != "no-read":
        sp = v.split[key]
        v.read_text += (
            f" Split ({key}d ATM {sp['total']:+.2f}pt): {sp['slide']:+.2f} from sliding along the skew"
            f" (normal), {sp['lift']:+.2f} from the curve itself moving — {sp['regime']}."
        )
    if v.bad_rungs:
        v.read_text += f" Skipped broken data: {', '.join(f'{r}d' for r in v.bad_rungs)} (call/put ATM vol disagree)."
    return v
