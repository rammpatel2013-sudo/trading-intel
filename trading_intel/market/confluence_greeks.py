"""Confluence layer (a): greek peaks from the per-strike chain — pure maths, no DB.

Black-Scholes with r = q = 0 and calendar time (``dte / 365``), each contract priced
at its OWN stored IV (sticky strike), the same conventions as the map maths. The
naive dealer sign is used throughout: calls +, puts − (an inferred book, not cleared
inventory).

Three kinds of level per expiry bucket (default buckets: <=7d and 8-30d):

* **vomma peak** — the strike carrying the most open-interest-weighted vomma
  (dVega/dVol) at today's spot: where the book's vega is most sensitive to a vol move.
* **speed extremes** — the spots (on a +-6% ladder) where the dealer gamma curve
  bends hardest. speed = −(Γ/S)·(d1/(σ√T) + 1) (note the "+1").
* **gamma zero** — where the naive dealer dollar-gamma curve crosses zero on the
  same ladder.

Descriptors only (CLAUDE.md rule 4) — they say where the book is shaped, never
which way price goes.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

_SQRT_2PI = math.sqrt(2.0 * math.pi)
_MIN_T = 1.0 / 365.0 / 6.5  # ~one trading hour, floors same-day contracts
DEFAULT_BUCKETS: tuple[tuple[str, int, int], ...] = (("<=7d", 0, 7), ("8-30d", 8, 30))
ZERO_BUCKETS: tuple[tuple[str, int, int], ...] = (("0-45d", 0, 45),)


@dataclass(frozen=True, slots=True)
class GreekRow:
    """One contract the greek layer needs (a subset of a chain row)."""

    strike: float
    cp: str  # 'C' or 'P'
    dte: int
    iv: float | None
    oi: float | None


def _pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / _SQRT_2PI


def _years(dte: int) -> float:
    return max(float(dte), 0.0) / 365.0 if dte > 0 else _MIN_T


def d1_d2(spot: float, strike: float, sigma: float, t: float) -> tuple[float, float]:
    """Black-Scholes d1, d2 with r = q = 0."""
    st = sigma * math.sqrt(t)
    d1 = (math.log(spot / strike) + 0.5 * sigma * sigma * t) / st
    return d1, d1 - st


def bs_gamma(spot: float, strike: float, sigma: float, t: float) -> float:
    """Γ = φ(d1) / (S σ √T) — identical for calls and puts."""
    d1, _ = d1_d2(spot, strike, sigma, t)
    return _pdf(d1) / (spot * sigma * math.sqrt(t))


def bs_vega(spot: float, strike: float, sigma: float, t: float) -> float:
    """Vega per 1.00 of vol (multiply by 0.01 for per vol point)."""
    d1, _ = d1_d2(spot, strike, sigma, t)
    return spot * _pdf(d1) * math.sqrt(t)


def bs_vomma(spot: float, strike: float, sigma: float, t: float) -> float:
    """Vomma = vega · d1 · d2 / σ (dVega/dσ)."""
    d1, d2 = d1_d2(spot, strike, sigma, t)
    return bs_vega(spot, strike, sigma, t) * d1 * d2 / sigma


def bs_speed(spot: float, strike: float, sigma: float, t: float) -> float:
    """Speed = dΓ/dS = −(Γ/S)·(d1/(σ√T) + 1)."""
    d1, _ = d1_d2(spot, strike, sigma, t)
    g = bs_gamma(spot, strike, sigma, t)
    return -(g / spot) * (d1 / (sigma * math.sqrt(t)) + 1.0)


def bs_delta(spot: float, strike: float, sigma: float, t: float, cp: str) -> float:
    """Call delta N(d1); put delta N(d1) − 1."""
    d1, _ = d1_d2(spot, strike, sigma, t)
    nd1 = 0.5 * (1.0 + math.erf(d1 / math.sqrt(2.0)))
    return nd1 if cp.upper().startswith("C") else nd1 - 1.0


def _valid(r: GreekRow) -> bool:
    return bool(r.iv and r.iv > 0 and r.oi and r.oi > 0 and r.strike > 0)


def _sign(cp: str) -> float:
    return 1.0 if cp.upper().startswith("C") else -1.0


def spot_ladder(spot: float, span: float = 0.06, n: int = 121) -> list[float]:
    """Evenly spaced spots across spot·(1 ± span)."""
    if n < 2:
        return [spot]
    lo, hi = spot * (1.0 - span), spot * (1.0 + span)
    return [lo + (hi - lo) * i / (n - 1) for i in range(n)]


def _arrays(rows: Sequence[GreekRow]) -> tuple[np.ndarray, ...]:
    good = [r for r in rows if _valid(r)]
    k = np.array([r.strike for r in good], dtype=float)
    sig = np.array([float(r.iv) for r in good], dtype=float)
    t = np.array([_years(r.dte) for r in good], dtype=float)
    w = np.array([_sign(r.cp) * float(r.oi) for r in good], dtype=float)
    return k, sig, t, w


def _ladder_greeks(
    rows: Sequence[GreekRow], ladder: Sequence[float]
) -> tuple[np.ndarray, np.ndarray]:
    """(signed Σ OI·Γ, signed Σ OI·speed) at each ladder spot — vectorised."""
    k, sig, t, w = _arrays(rows)
    s = np.asarray(ladder, dtype=float)[:, None]
    if k.size == 0:
        z = np.zeros(len(ladder))
        return z, z
    st = sig * np.sqrt(t)
    d1 = (np.log(s / k) + 0.5 * sig * sig * t) / st
    gam = np.exp(-0.5 * d1 * d1) / _SQRT_2PI / (s * st)
    speed = -(gam / s) * (d1 / st + 1.0)
    return (gam * w).sum(axis=1), (speed * w).sum(axis=1)


def dealer_gamma_curve(rows: Sequence[GreekRow], ladder: Sequence[float]) -> list[float]:
    """Naive dealer dollar-gamma per 1% move at each ladder spot (calls +, puts −)."""
    g, _ = _ladder_greeks(rows, ladder)
    s = np.asarray(ladder, dtype=float)
    return [float(x) for x in g * s * s * 0.01]


def dealer_speed_curve(rows: Sequence[GreekRow], ladder: Sequence[float]) -> list[float]:
    """Naive dealer speed (Σ sign·OI·speed) at each ladder spot."""
    _, sp = _ladder_greeks(rows, ladder)
    return [float(x) for x in sp]


def zero_crossings(xs: Sequence[float], ys: Sequence[float]) -> list[float]:
    """Linear-interpolated x where ys changes sign (exact zeros included once)."""
    out: list[float] = []
    for i in range(1, len(xs)):
        y0, y1 = ys[i - 1], ys[i]
        if y0 == 0.0 and (not out or out[-1] != xs[i - 1]):
            out.append(xs[i - 1])
        elif y0 * y1 < 0:
            out.append(xs[i - 1] + (xs[i] - xs[i - 1]) * (-y0) / (y1 - y0))
    if ys and ys[-1] == 0.0:
        out.append(xs[-1])
    return out


def vomma_by_strike(rows: Sequence[GreekRow], spot: float) -> dict[float, float]:
    """Σ OI·vomma per strike at today's spot (unsigned: size of the vol-of-vol exposure)."""
    agg: dict[float, float] = {}
    for r in rows:
        if not _valid(r):
            continue
        v = float(r.oi) * bs_vomma(spot, r.strike, float(r.iv), _years(r.dte))
        agg[r.strike] = agg.get(r.strike, 0.0) + v
    return agg


def local_peaks(
    items: dict[float, float], *, top: int = 3, min_rel: float = 0.35
) -> list[tuple[float, float]]:
    """Up to ``top`` local maxima of a strike->value map, each >= min_rel of the max."""
    if not items:
        return []
    ks = sorted(items)
    vals = [items[k] for k in ks]
    vmax = max(vals)
    if vmax <= 0:
        return []
    peaks = []
    for i, k in enumerate(ks):
        left = vals[i - 1] if i > 0 else -math.inf
        right = vals[i + 1] if i + 1 < len(ks) else -math.inf
        if vals[i] >= left and vals[i] >= right and vals[i] >= min_rel * vmax:
            peaks.append((k, vals[i]))
    peaks.sort(key=lambda p: -p[1])
    return peaks[:top]


def vol_shock_peaks(rows: Sequence[GreekRow], spot: float) -> list[tuple[str, float, float]]:
    """Per-contract vomma peak in each wing: the OTM put (strike < spot) and OTM call
    (strike > spot) whose SINGLE-contract vomma is largest at today's spot, own IV and time.

    Ignores open interest: it says where one option's vega reacts most to a vol shock,
    not where anyone is positioned. Returns ``[(wing, strike, vomma)]``.
    """
    out = []
    for wing, cp, keep in (("put", "P", lambda k: k < spot), ("call", "C", lambda k: k > spot)):
        best: tuple[float, float] | None = None
        for r in rows:
            if not (r.iv and r.iv > 0 and r.strike > 0) or not r.cp.upper().startswith(cp):
                continue
            if not keep(r.strike):
                continue
            v = bs_vomma(spot, r.strike, float(r.iv), _years(r.dte))
            if best is None or v > best[1]:
                best = (r.strike, v)
        if best is not None and best[1] > 0:
            out.append((wing, best[0], best[1]))
    return out


def greek_levels(
    rows: Sequence[GreekRow],
    spot: float,
    *,
    buckets: Sequence[tuple[str, int, int]] = DEFAULT_BUCKETS,
    zero_buckets: Sequence[tuple[str, int, int]] = ZERO_BUCKETS,
    span: float = 0.06,
    n_points: int = 121,
) -> list[dict]:
    """Layer (a) candidates: vomma peaks, speed extremes, gamma zero per expiry bucket.

    ``zero_buckets`` add a gamma zero for wider books (default the whole 0-45d book).
    Also emits per-wing ``vol_shock_peak`` levels flagged ``info: True`` (per-contract vomma,
    no OI) — shown, never counted toward a zone.
    Returns plain dicts ``{level, label, strength, kind, bucket, value}``. Strength is
    1.0 for each bucket's primary level (gamma zero, speed max/min, top vomma peak) and
    the relative size for secondary vomma peaks.
    """
    out: list[dict] = []
    ladder = spot_ladder(spot, span, n_points)
    for name, lo, hi in buckets:
        sub = [r for r in rows if lo <= r.dte <= hi and _valid(r)]
        if not sub:
            continue
        for wing, k, v in vol_shock_peaks(sub, spot):
            # INFO ONLY: no OI in it, so it never counts toward a zone's layers
            out.append({"level": k, "kind": "vol_shock_peak", "bucket": name, "info": True,
                        "label": f"vol-shock peak (info) {name} {wing} wing",
                        "strength": 0.0, "value": v})
        peaks = local_peaks(vomma_by_strike(sub, spot))
        if peaks:
            top = peaks[0][1]
            for k, v in peaks:
                out.append({"level": k, "kind": "vomma_peak", "bucket": name,
                            "label": f"vomma peak {name}", "strength": round(v / top, 3),
                            "value": v})
        g = dealer_gamma_curve(sub, ladder)
        for z in zero_crossings(ladder, g):
            out.append({"level": round(z, 1), "kind": "gamma_zero", "bucket": name,
                        "label": f"gamma zero {name}", "strength": 1.0, "value": 0.0})
        sp = dealer_speed_curve(sub, ladder)
        if any(sp):
            imax = max(range(len(sp)), key=lambda i: sp[i])
            imin = min(range(len(sp)), key=lambda i: sp[i])
            for i, tag in ((imax, "speed max"), (imin, "speed min")):
                # an extreme pinned at the ladder edge is the edge, not a bend
                if 0 < i < len(ladder) - 1:
                    out.append({"level": round(ladder[i], 1), "kind": "speed_extreme",
                                "bucket": name, "label": f"{tag} {name}", "strength": 1.0,
                                "value": sp[i]})
    for name, lo, hi in zero_buckets:
        sub = [r for r in rows if lo <= r.dte <= hi and _valid(r)]
        if sub:
            for z in zero_crossings(ladder, dealer_gamma_curve(sub, ladder)):
                out.append({"level": round(z, 1), "kind": "gamma_zero", "bucket": name,
                            "label": f"gamma zero {name}", "strength": 1.0, "value": 0.0})
    return out


def strike_for_delta(
    spot: float, target: float, sigma: float, t: float, cp: str
) -> float | None:
    """Strike whose BS delta equals ``target`` (call 0..1, put −1..0); None if invalid."""
    if sigma <= 0 or t <= 0:
        return None
    if cp.upper().startswith("C"):
        nd1 = target
    else:
        nd1 = target + 1.0
    if not 0.0 < nd1 < 1.0:
        return None
    d1 = _norm_ppf(nd1)
    st = sigma * math.sqrt(t)
    return spot * math.exp(-(d1 * st) + 0.5 * st * st)


def _norm_ppf(p: float) -> float:
    """Inverse standard normal CDF (Acklam's rational approximation, |err| < 1.2e-9)."""
    a = (-3.969683028665376e01, 2.209460984245205e02, -2.759285104469687e02,
         1.383577518672690e02, -3.066479806614716e01, 2.506628277459239e00)
    b = (-5.447609879822406e01, 1.615858368580409e02, -1.556989798598866e02,
         6.680131188771972e01, -1.328068155288572e01)
    c = (-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e00,
         -2.549732539343734e00, 4.374664141464968e00, 2.938163982698783e00)
    d = (7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e00,
         3.754408661907416e00)
    lo, hi = 0.02425, 1 - 0.02425
    if p < lo:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
            ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
    if p > hi:
        q = math.sqrt(-2 * math.log(1 - p))
        return -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
            ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
    q = p - 0.5
    r = q * q
    return (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q / \
        (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1)
