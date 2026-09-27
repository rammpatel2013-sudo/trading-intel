"""Skew & vanna read — pure assembly (numbers-in → numbers-out).

Implements the VolSignals skew/vanna-inversion framework on our data:

* **Normalized skew** = (25Δ put IV − 25Δ call IV) / ATM IV per constant-maturity
  rung (``vol_surface_cm``). 1M SPX lives ~0.20–0.50, typically ~0.30. Plus the
  wings separately: put skew = 25Δp − ATM, call skew = 25Δc − ATM (vol points).
* **Dealer vanna by spot**, now vs an earlier book (``get_profile`` with
  ``as_of`` + a pinned ``spot_ref`` so both curves share one grid). Our sign
  convention is calls+/puts− (inferred, not cleared inventory): trust the SHAPE and
  the zero-crossing, not the level.
* **Reads**: skew crush / skew bid on the move, and vanna inversion (dealer vanna
  at spot flips sign between the two books, or spot has crossed the vanna
  zero-line).

Descriptor only (CLAUDE.md rule 4).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any

SKEW_CRUSH = -0.03  # Δ normalized skew over the window that counts as "crushed"
SKEW_BID = 0.03


@dataclass(frozen=True, slots=True)
class SkewPoint:
    d: date
    spot: float | None
    atm: float | None
    put25: float | None
    call25: float | None

    @property
    def norm_skew(self) -> float | None:
        if None in (self.atm, self.put25, self.call25) or not self.atm:
            return None
        return (self.put25 - self.call25) / self.atm

    @property
    def put_skew(self) -> float | None:  # vol points
        return None if None in (self.put25, self.atm) else (self.put25 - self.atm) * 100

    @property
    def call_skew(self) -> float | None:
        return None if None in (self.call25, self.atm) else (self.call25 - self.atm) * 100


def skew_series(rows: Iterable[dict[str, Any]], *, dte: int = 30,
                sessions: set[date] | None = None) -> list[SkewPoint]:
    """``vol_surface_cm`` rows → one SkewPoint per day for ``dte`` (oldest→newest).

    ATM = mean of the 50Δ call and put IV. ``sessions`` (optional) drops non-trading
    days the collector may have banked on a weekend.
    """
    by_day: dict[date, dict[str, Any]] = {}
    for r in rows:
        if int(r["dte"]) != dte:
            continue
        d = r["ts"]
        if sessions is not None and d not in sessions:
            continue
        slot = by_day.setdefault(d, {"spot": r.get("spot"), "atm": []})
        delta, side, iv = float(r["delta"]), str(r["side"]).lower(), r.get("iv")
        if iv is None:
            continue
        if delta == 50.0:
            slot["atm"].append(float(iv))
        elif delta == 25.0:
            slot["put25" if side.startswith("p") else "call25"] = float(iv)
    out = []
    for d in sorted(by_day):
        s = by_day[d]
        atm = sum(s["atm"]) / len(s["atm"]) if s["atm"] else None
        out.append(SkewPoint(d, s.get("spot"), atm, s.get("put25"), s.get("call25")))
    return out


def _interp_zero(xs: Sequence[float], ys: Sequence[float], near: float) -> float | None:
    """Zero-crossing of ys(xs) closest to ``near`` (linear), or None."""
    best = None
    for i in range(1, len(xs)):
        y0, y1 = ys[i - 1], ys[i]
        if y0 is None or y1 is None or (y0 > 0) == (y1 > 0) or y0 == y1:
            continue
        x = xs[i - 1] + (xs[i] - xs[i - 1]) * (0 - y0) / (y1 - y0)
        if best is None or abs(x - near) < abs(best - near):
            best = x
    return best


def _at(xs: Sequence[float], ys: Sequence[float], x: float) -> float | None:
    for i in range(1, len(xs)):
        if xs[i - 1] <= x <= xs[i] and ys[i - 1] is not None and ys[i] is not None:
            t = 0 if xs[i] == xs[i - 1] else (x - xs[i - 1]) / (xs[i] - xs[i - 1])
            return ys[i - 1] + t * (ys[i] - ys[i - 1])
    return None


@dataclass(slots=True)
class VannaRead:
    grid: list[float]
    now: list[float]
    before: list[float] | None
    spot: float
    as_of_now: str | None
    as_of_before: str | None
    at_spot_now: float | None = None
    at_spot_before: float | None = None
    zero_now: float | None = None
    zero_before: float | None = None
    inverted: bool = False
    notes: list[str] = field(default_factory=list)


def vanna_read(now: dict[str, Any], before: dict[str, Any] | None) -> VannaRead | None:
    """Two ``get_profile`` payloads (same ``spot_ref``) → the vanna comparison."""
    if not now or not now.get("found"):
        return None
    p = now["profiles"]
    grid = [float(x) for x in p["spot_ref"]]
    spot = float(now["spot"])
    cur = [float(v) for v in p["vanna"]["all"]]
    prev = None
    if before and before.get("found"):
        pb = before["profiles"]
        if len(pb["spot_ref"]) == len(grid):
            prev = [float(v) for v in pb["vanna"]["all"]]
    vr = VannaRead(grid, cur, prev, spot, now.get("as_of"),
                   before.get("as_of") if before else None)
    vr.at_spot_now = _at(grid, cur, spot)
    vr.zero_now = _interp_zero(grid, cur, spot)
    if prev:
        vr.at_spot_before = _at(grid, prev, spot)
        vr.zero_before = _interp_zero(grid, prev, spot)
    a, b = vr.at_spot_now, vr.at_spot_before
    if a is not None and b is not None and (a > 0) != (b > 0):
        vr.inverted = True
        vr.notes.append("dealer vanna at spot FLIPPED sign vs the earlier book")
    if vr.zero_now is not None and abs(vr.zero_now / spot - 1) < 0.01:
        vr.notes.append(f"spot within 1% of the vanna zero-line ({vr.zero_now:,.0f})")
    return vr


def skew_read(series: Sequence[SkewPoint], *, window: int = 5) -> dict[str, Any]:
    """Latest normalized skew vs ``window`` sessions ago + the regime label."""
    pts = [p for p in series if p.norm_skew is not None]
    if not pts:
        return {"label": "no-data"}
    cur = pts[-1]
    prior = pts[-1 - window] if len(pts) > window else pts[0]
    d_skew = cur.norm_skew - prior.norm_skew
    d_spot = (cur.spot / prior.spot - 1) if cur.spot and prior.spot else None
    if d_skew <= SKEW_CRUSH:
        label = "skew-crushed-on-rally" if (d_spot or 0) > 0 else "skew-crushed"
    elif d_skew >= SKEW_BID:
        label = "skew-bid-on-selloff" if (d_spot or 0) < 0 else "skew-bid-into-rally"
    else:
        label = "skew-steady"
    vals = [p.norm_skew for p in pts]
    rank = sum(v <= cur.norm_skew for v in vals) / len(vals)
    return {"label": label, "norm_skew": cur.norm_skew, "prior": prior.norm_skew,
            "d_skew": d_skew, "d_spot": d_spot, "pctile": rank, "n": len(pts),
            "as_of": cur.d, "prior_as_of": prior.d}
