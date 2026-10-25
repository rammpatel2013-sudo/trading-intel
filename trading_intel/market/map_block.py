"""The daily-brief MAP block: regime, two switches, close odds, cones, hedge pressure.

Everything here is derived from the 08:35 ``index_walls_am`` per-strike chain
already banked in ``oi_chain_eod`` (strike / cp / dte / oi / delta / gamma / iv).
No vendor calls, no new Convex requests.

What it answers, in Mithil's words (build plan R2, 2026-10-24):

* **Regime + two switches** — spot against the whole-book flip AND the same-day
  flip. The day's range often lives between them (@GammaGirlLab).
* **Walls, both views** — the 0–7 DTE book (stable overnight) shown next to the
  same-day book (stale before 09:30 because most 0DTE OI is opened intraday).
* **Three ways it closes** — risk-neutral P(close below the floor / between /
  above the cap). Probability-zone idea is TTWD's, already live in the cockpit;
  this is the same chain read the same way, so the two should agree. Cross-check
  against the cockpit on a live day before trusting either to the point.
* **Likely ranges** — the 25Δ / 15Δ / 10Δ strikes ≈ the 50 / 70 / 80% bands.
* **Hedge pressure** — dollars of dealer hedging forced by a ±1% move, the
  drop/rally ratio, and the same move with IV shifted (a selloff lifts IV, which
  changes the number materially).
* **Fragility** — walls and flip recomputed at IV ±1 vol point. If they move
  more than the threshold the levels are soft and shouldn't be leaned on.

Sign convention matches the house standard: dealers long calls (+), short puts
(−). That is an INFERRED book from naive OI, not cleared positioning — the
caller prints the caveat. Descriptor only (FlashAlpha rule 4): this module
emits numbers and labels, never a signal.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Iterable, Sequence

import numpy as np

from trading_intel.greeks.black_scholes import bs_gamma

# A chain row as read from oi_chain_eod.
# (strike, cp, dte, oi, delta, gamma, iv, gxoi, gammas_buy, gammas_sell)
# The last two are the vendor's aggressor-split gamma flow for the session and
# are NULL for every row banked before 2026-10-26.
ChainRow = tuple[
    float, str, int | None, int | None, float | None, float | None, float | None,
    float | None, float | None, float | None,
]

_MULT = 100.0  # index option multiplier
_MIN_T = 1.0 / (365.0 * 24.0)
_NEAR_DTE = 7  # "the week book" — the stable overnight view


@dataclass(frozen=True)
class Book:
    """A cleaned, vectorised slice of the chain for one DTE window."""

    strike: np.ndarray
    sign: np.ndarray  # +1 call, -1 put
    oi: np.ndarray
    iv: np.ndarray  # decimal (0.14 = 14 vol)
    t: np.ndarray  # years to expiry
    gxoi: np.ndarray  # vendor gamma x OI, frozen at the snapshot's spot
    g_buy: np.ndarray  # vendor gamma x volume BOUGHT by customers (session)
    g_sell: np.ndarray  # ... and SOLD
    label: str = ""
    n: int = field(default=0)

    @property
    def empty(self) -> bool:
        return self.strike.size == 0


def build_book(rows: Iterable[ChainRow], *, dte_max: int | None, label: str = "") -> Book:
    """Vectorise the chain rows for one DTE window, dropping unusable contracts.

    A row is usable when it has a strike, an OI, and a positive IV — gamma is
    always recomputed from Black-Scholes here so that the IV-shift and the
    spot-shift use one consistent model (the stored vendor gamma is a point
    value at the vendor's own spot and can't be shocked).
    """
    ks: list[float] = []
    sg: list[float] = []
    oi_: list[float] = []
    iv_: list[float] = []
    t_: list[float] = []
    gx_: list[float] = []
    gb_: list[float] = []
    gs_: list[float] = []
    for row in rows:
        strike, cp, dte, oi, _delta, _gamma, iv = row[:7]
        gxoi = row[7] if len(row) > 7 else None
        gbuy = row[8] if len(row) > 8 else None
        gsell = row[9] if len(row) > 9 else None
        if strike is None or oi is None or iv is None:
            continue
        try:
            k = float(strike)
            v = float(iv)
            o = float(oi)
        except (TypeError, ValueError):
            continue
        if k <= 0 or v <= 0 or o <= 0:
            continue
        d = int(dte) if dte is not None else 0
        if dte_max is not None and d > dte_max:
            continue
        ks.append(k)
        sg.append(1.0 if str(cp).upper().startswith("C") else -1.0)
        oi_.append(o)
        iv_.append(v)
        t_.append(max(d / 365.0, _MIN_T))
        for src, dest in ((gxoi, gx_), (gbuy, gb_), (gsell, gs_)):
            try:
                dest.append(abs(float(src)) if src is not None else float("nan"))
            except (TypeError, ValueError):
                dest.append(float("nan"))
    return Book(
        strike=np.asarray(ks, dtype=float),
        sign=np.asarray(sg, dtype=float),
        oi=np.asarray(oi_, dtype=float),
        iv=np.asarray(iv_, dtype=float),
        t=np.asarray(t_, dtype=float),
        gxoi=np.asarray(gx_, dtype=float),
        g_buy=np.asarray(gb_, dtype=float),
        g_sell=np.asarray(gs_, dtype=float),
        label=label,
        n=len(ks),
    )


def dealer_gamma_dollars(book: Book, spot: float, *, iv_shift: float = 0.0) -> float:
    """Net dealer $-gamma per 1% move at ``spot`` (calls +, puts −).

    ``iv_shift`` is in VOL POINTS (1.0 = +1 vol), applied to every contract —
    the parallel shift used by the fragility test and the IV-bumped hedge row.
    """
    if book.empty or spot <= 0:
        return 0.0
    sigma = np.maximum(book.iv + iv_shift / 100.0, 1e-4)
    g = bs_gamma(spot, book.strike, sigma, book.t)
    return float(np.sum(book.sign * g * book.oi * _MULT * spot**2 * 0.01))


def gamma_flip(book: Book, spot: float, *, iv_shift: float = 0.0, span: float = 0.06,
               n_points: int = 121) -> float | None:
    """Spot where net dealer $-gamma crosses zero, nearest ``spot``.

    Sticky-strike: each contract keeps its own IV as the hypothetical spot moves.
    Returns ``None`` when the profile never crosses inside ``±span``.
    """
    if book.empty or spot <= 0:
        return None
    grid = np.linspace(spot * (1 - span), spot * (1 + span), n_points)
    vals = np.array([dealer_gamma_dollars(book, float(s), iv_shift=iv_shift) for s in grid])
    best: float | None = None
    for i in range(1, len(grid)):
        a, b = vals[i - 1], vals[i]
        if (a < 0 <= b) or (a > 0 >= b):
            x = float(grid[i - 1] + (grid[i] - grid[i - 1]) * (0 - a) / ((b - a) or 1e-12))
            if best is None or abs(x - spot) < abs(best - spot):
                best = x
    return best


def walls(book: Book, spot: float, *, iv_shift: float = 0.0) -> tuple[float | None, float | None]:
    """(put_wall, call_wall) = the heaviest gamma x OI strike each side of spot.

    Gamma is RECOMPUTED here at the caller's ``spot`` from each contract's own
    stored IV, rather than read from the banked ``gxoi``.

    That is the whole point. ``gxoi`` is gamma x open interest where the gamma
    was frozen at the vendor's spot at snapshot time — 08:35 or the EOD pull.
    Open interest does not change overnight, but the gamma WEIGHTS do: on a
    40-point gap the stored ranking weights every strike as though price never
    moved, and can name the wrong strike. Same definition, correct anchor.

    ``iv_shift`` (in vol points) is used only by the how-solid check.
    """
    if book.empty or spot <= 0:
        return (None, None)
    sigma = np.maximum(book.iv + iv_shift / 100.0, 1e-4)
    g = bs_gamma(spot, book.strike, sigma, book.t)
    return _walls_from_weight(book, spot, np.abs(g * book.oi))


def walls_vendor(book: Book, spot: float) -> tuple[float | None, float | None]:
    """Walls from the STORED ``gxoi`` — the stale-anchor version.

    Kept only so the two can be compared on a gap day; never printed.
    """
    if book.empty or spot <= 0:
        return (None, None)
    return _walls_from_weight(book, spot, np.nan_to_num(book.gxoi, nan=0.0))


def _walls_from_weight(
    book: Book, spot: float, weight: np.ndarray
) -> tuple[float | None, float | None]:
    """Heaviest ``weight`` strike per side, summed per (strike, call?) pair."""
    agg: dict[tuple[float, bool], float] = {}
    for k, sg, w in zip(book.strike, book.sign, weight):
        if not np.isfinite(w):
            continue
        key = (float(k), bool(sg > 0))
        agg[key] = agg.get(key, 0.0) + float(w)
    calls = {k: w for (k, c), w in agg.items() if c and k >= spot and w > 0}
    puts = {k: w for (k, c), w in agg.items() if not c and k <= spot and w > 0}
    cw = max(calls, key=lambda k: calls[k]) if calls else None
    pw = max(puts, key=lambda k: puts[k]) if puts else None
    return (pw, cw)


def heaviest_strike(book: Book, spot: float) -> float | None:
    """The single biggest gamma x OI pile either side — the "magnet"."""
    if book.empty or spot <= 0:
        return None
    sigma = np.maximum(book.iv, 1e-4)
    w = np.abs(bs_gamma(spot, book.strike, sigma, book.t) * book.oi)
    agg: dict[float, float] = {}
    for k, x in zip(book.strike, w):
        if np.isfinite(x):
            agg[float(k)] = agg.get(float(k), 0.0) + float(x)
    return max(agg, key=lambda k: agg[k]) if agg else None


# ── signed flow: who actually traded ───────────────────────────────────


def signed_flow(book: Book, *, strikes: Sequence[float] | None = None) -> dict[str, Any]:
    """Net customer-initiated gamma for the session: bought minus sold.

    The dealer takes the other side, so a NEGATIVE net (customers net sellers)
    leaves dealers holding more and tends to steady price; a positive net
    leaves them shorter and tends to loosen it.

    Unlike the walls this has a REAL side — it is the vendor's own aggressor
    classification, not our calls-plus/puts-minus assumption. It is one
    session's activity, not a standing position.

    Returns ``{"found": False}`` whenever the columns are null, which is every
    session banked before the 2026-10-25 collector widening.
    """
    if book.empty:
        return {"found": False}
    ok = np.isfinite(book.g_buy) & np.isfinite(book.g_sell)
    if not ok.any():
        return {"found": False}

    buy = float(np.sum(book.g_buy[ok]))
    sell = float(np.sum(book.g_sell[ok]))
    gross = buy + sell
    net = buy - sell

    per_strike: dict[float, float] = {}
    for k, b, s_, good in zip(book.strike, book.g_buy, book.g_sell, ok):
        if good:
            per_strike[float(k)] = per_strike.get(float(k), 0.0) + (float(b) - float(s_))

    at: list[dict[str, Any]] = []
    for k in strikes or ():
        if k is None:
            continue
        v = per_strike.get(float(k))
        if v is not None:
            at.append({"strike": float(k), "net": v, "customers": "sold" if v < 0 else "bought"})

    ranked = sorted(per_strike.items(), key=lambda kv: abs(kv[1]), reverse=True)[:3]
    return {
        "found": True,
        "buy": buy,
        "sell": sell,
        "net": net,
        "tilt": (net / gross) if gross else 0.0,
        "customers": "sold" if net < 0 else "bought",
        "steadying": net < 0,
        "at_levels": at,
        "busiest": [
            {"strike": k, "net": v, "customers": "sold" if v < 0 else "bought"}
            for k, v in ranked
        ],
        "n_strikes": len(per_strike),
    }


def atm_iv(book: Book, spot: float) -> float | None:
    """IV of the strike closest to spot in the front expiry of the window."""
    if book.empty:
        return None
    t_front = float(np.min(book.t))
    mask = book.t <= t_front + 1e-9
    if not mask.any():
        return None
    k = book.strike[mask]
    v = book.iv[mask]
    return float(v[int(np.argmin(np.abs(k - spot)))])


def delta_cones(rows: Sequence[ChainRow], spot: float, *, expiry_dte: int = 0,
                deltas: Sequence[float] = (0.25, 0.15, 0.10)) -> list[dict[str, Any]]:
    """Strikes at |Δ| = 25 / 15 / 10 on each wing ≈ the 50 / 70 / 80% bands.

    Reads the vendor delta straight off the chain (no model), interpolating
    between the two bracketing strikes on each side.
    """
    calls = sorted(
        (float(r[0]), abs(float(r[4])))
        for r in rows
        if r[4] is not None and str(r[1]).upper().startswith("C")
        and (r[2] is None or int(r[2]) == expiry_dte) and float(r[0]) > spot
    )
    puts = sorted(
        (float(r[0]), abs(float(r[4])))
        for r in rows
        if r[4] is not None and str(r[1]).upper().startswith("P")
        and (r[2] is None or int(r[2]) == expiry_dte) and float(r[0]) < spot
    )
    out: list[dict[str, Any]] = []
    for d in deltas:
        out.append(
            {
                "delta": d,
                "band_pct": round((1 - 2 * d) * 100),
                "upper": _interp_strike(calls, d),
                "lower": _interp_strike(puts, d),
            }
        )
    return out


def _interp_strike(pts: list[tuple[float, float]], target: float) -> float | None:
    """Strike where |delta| == target, linear between the bracketing strikes."""
    if len(pts) < 2:
        return None
    for (k0, d0), (k1, d1) in zip(pts, pts[1:]):
        lo, hi = (d0, d1) if d0 <= d1 else (d1, d0)
        if lo <= target <= hi and d0 != d1:
            return float(k0 + (target - d0) / (d1 - d0) * (k1 - k0))
    return None


def hedge_pressure(book: Book, spot: float, *, move_pct: float = 1.0,
                   iv_up: float = 2.0, iv_down: float = 2.0) -> dict[str, float | None]:
    """Dollars of dealer hedging forced by a ±``move_pct``% move.

    The plain rows hold IV fixed. The shifted rows add the IV move that usually
    travels with the spot move (a drop lifts IV, a rally crushes it), which is
    where the asymmetry @GammaGirlLab prints comes from.

    Positive ``sell_on_drop`` = dealers must SELL into the drop (short gamma).
    """
    if book.empty or spot <= 0:
        return {}
    f = move_pct / 100.0
    g0 = dealer_gamma_dollars(book, spot)
    down = dealer_gamma_dollars(book, spot * (1 - f))
    up = dealer_gamma_dollars(book, spot * (1 + f))
    down_iv = dealer_gamma_dollars(book, spot * (1 - f), iv_shift=iv_up)
    up_iv = dealer_gamma_dollars(book, spot * (1 + f), iv_shift=-iv_down)
    sell_on_drop = -(g0 + down) / 2.0 * move_pct
    buy_on_rally = (g0 + up) / 2.0 * move_pct
    sell_on_drop_iv = -(g0 + down_iv) / 2.0 * move_pct
    buy_on_rally_iv = (g0 + up_iv) / 2.0 * move_pct
    ratio = (
        abs(sell_on_drop) / abs(buy_on_rally)
        if buy_on_rally and abs(buy_on_rally) > 1e-6
        else None
    )
    return {
        "net_gamma_dollars": g0,
        "sell_on_drop": sell_on_drop,
        "buy_on_rally": buy_on_rally,
        "ratio": ratio,
        "sell_on_drop_iv": sell_on_drop_iv,
        "buy_on_rally_iv": buy_on_rally_iv,
        "move_pct": move_pct,
        "iv_up": iv_up,
        "iv_down": iv_down,
    }
