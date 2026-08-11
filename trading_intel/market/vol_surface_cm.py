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

Descriptor / research only (FlashAlpha rule 4). Pure stdlib — no pandas / no DB.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

DELTAS = (5.0, 7.5, 10.0, 15.0, 20.0, 25.0, 30.0, 35.0, 40.0, 45.0, 47.5, 50.0)
_WING = 25.0  # representative wing delta for the read
_FRONT_MAX = 21  # rungs ≤ this = "front" (event); > this = "back" (regime)


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
) -> tuple[str, str]:
    """Quadrant read from spot direction × where the wing vol moved (vol pts)."""
    if spot_chg_pct is None or (call_chg is None and put_chg is None):
        return ("no-read", "Not enough history yet to read the change — banks forward.")
    up = spot_chg_pct >= 0
    bid = 0.10  # vol-pt threshold for "meaningfully bid"
    where = "back rungs (regime)" if (call_chg_back is not None and call_chg_back >= bid) else "front rungs (event)"
    if up:
        if call_chg is not None and call_chg >= bid:
            return (
                "rally-confirmed",
                f"Spot +{spot_chg_pct:.1f}% and upside (call) vol is getting BID "
                f"(+{call_chg:.2f}pt, {where}) — demand is confirming the move. Bullish; "
                "strongest when the bid is out the curve.",
            )
        return (
            "rally-unconfirmed",
            f"Spot +{spot_chg_pct:.1f}% but upside vol is flat/offered "
            f"({(call_chg or 0):+.2f}pt) — the rally isn't being paid for (flow / short-cover / "
            "call overwriting). Low conviction; prone to stall or get sold.",
        )
    if put_chg is not None and put_chg >= bid:
        return (
            "fear",
            f"Spot {spot_chg_pct:.1f}% and downside (put) vol is getting BID "
            f"(+{put_chg:.2f}pt) — genuine protection demand / risk-off. Real fear, "
            "especially if the back rungs are bid.",
        )
    return (
        "quiet-slide",
        f"Spot {spot_chg_pct:.1f}% but vol is asleep (downside {(put_chg or 0):+.2f}pt) — "
        "a complacent slide: support leaving with nobody bidding protection. The quiet-unwind "
        "vacuum; watch for acceleration.",
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
    call_chg = _wing_change(v.iv_chg, "call", rungs)
    put_chg = _wing_change(v.iv_chg, "put", rungs)
    call_back = _wing_change(v.iv_chg, "call", rungs, front=False)
    v.read_label, v.read_text = classify_read(spot_chg, call_chg, put_chg, call_back)
    return v
