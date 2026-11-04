"""VIX overshoot read (Phase 5 V1, 2026-11-04) — pure, no I/O.

Question it answers: did VIX jump MORE than the day's SPX move explains?

Model, refit every day on the PRIOR ``window`` sessions only (no look-ahead):
    dVIX_t = alpha + beta * r_t          r_t = 100 * ln(SPX_t / SPX_{t-1})
    vix_model_t = VIX_{t-1} + alpha + beta * r_t
    residual_t  = VIX_t - vix_model_t    (vol points)
A day is FLAGGED when residual > ``flag_pts`` (default +2 vol points): fear was
bid beyond what the tape did. ``recovery`` then measures what happened after the
flagged days (SPX forward return, VIX give-back) so the flag carries its own
track record. Descriptor only (rule 4) — never writes ``signals``.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

WINDOW = 126  # ~6 months of sessions
MIN_FIT = 60  # don't fit on fewer than this
FLAG_PTS = 2.0
HORIZONS = (5, 10, 20)


@dataclass(frozen=True, slots=True)
class OvershootRow:
    date: date
    vix_actual: float
    vix_prev: float
    spx_ret_pct: float
    alpha: float
    beta: float
    vix_model: float
    residual: float
    flag: bool
    n_fit: int


def _ols(xs: Sequence[float], ys: Sequence[float]) -> tuple[float, float]:
    """(alpha, beta) of y = a + b x. Degenerate x variance -> (mean y, 0)."""
    n = len(xs)
    mx = sum(xs) / n
    my = sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx <= 1e-12:
        return my, 0.0
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    b = sxy / sxx
    return my - b * mx, b


def align(vix: dict[date, float], spx: dict[date, float]) -> list[tuple[date, float, float]]:
    """Dates present in both series, ascending: (date, vix, spx). Drops non-positive SPX."""
    out = []
    for d in sorted(set(vix) & set(spx)):
        v, s = vix[d], spx[d]
        if v is None or s is None or s <= 0 or not math.isfinite(v) or not math.isfinite(s):
            continue
        out.append((d, float(v), float(s)))
    return out


def compute(
    vix: dict[date, float],
    spx: dict[date, float],
    *,
    window: int = WINDOW,
    min_fit: int = MIN_FIT,
    flag_pts: float = FLAG_PTS,
) -> list[OvershootRow]:
    """One row per session that has ``min_fit`` prior (dVIX, r) pairs."""
    s = align(vix, spx)
    dv: list[float] = []
    rr: list[float] = []
    rows: list[OvershootRow] = []
    for i in range(1, len(s)):
        d, v, p = s[i]
        _, v0, p0 = s[i - 1]
        r = 100.0 * math.log(p / p0)
        if len(dv) >= min_fit:
            xs, ys = rr[-window:], dv[-window:]
            a, b = _ols(xs, ys)
            model = v0 + a + b * r
            res = v - model
            rows.append(OvershootRow(d, v, v0, r, a, b, model, res, res > flag_pts, len(xs)))
        dv.append(v - v0)
        rr.append(r)
    return rows


def recovery(
    rows: Sequence[OvershootRow],
    spx: dict[date, float],
    vix: dict[date, float],
    *,
    horizons: Sequence[int] = HORIZONS,
) -> dict:
    """Track record of the flag: after flagged days vs all days.

    For each horizon h: mean SPX % return over the next h sessions, % of times it
    was up, and mean VIX change (negative = fear gave back). Uses the aligned
    session calendar so h counts trading days.
    """
    cal = [d for d, _, _ in align(vix, spx)]
    idx = {d: i for i, d in enumerate(cal)}

    def fwd(d: date, h: int) -> tuple[float, float] | None:
        i = idx.get(d)
        if i is None or i + h >= len(cal):
            return None
        d2 = cal[i + h]
        return 100.0 * (spx[d2] / spx[d] - 1.0), vix[d2] - vix[d]

    def agg(ds: list[date]) -> dict:
        out = {}
        for h in horizons:
            got = [x for x in (fwd(d, h) for d in ds) if x is not None]
            if not got:
                out[str(h)] = {"n": 0, "spx_mean": None, "spx_up": None, "vix_mean": None}
                continue
            out[str(h)] = {
                "n": len(got),
                "spx_mean": round(sum(g[0] for g in got) / len(got), 3),
                "spx_up": round(sum(1 for g in got if g[0] > 0) / len(got), 3),
                "vix_mean": round(sum(g[1] for g in got) / len(got), 3),
            }
        return out

    flagged = [r.date for r in rows if r.flag]
    return {"flagged_n": len(flagged), "flagged": agg(flagged), "all": agg([r.date for r in rows])}


def plain_read(row: OvershootRow | None) -> str:
    """One plain sentence for the cockpit / brief."""
    if row is None:
        return "Not enough VIX + SPX history yet to fit the model."
    move = "fell" if row.spx_ret_pct < 0 else "rose"
    exp = row.vix_model - row.vix_prev
    got = row.vix_actual - row.vix_prev
    base = (
        f"SPX {move} {abs(row.spx_ret_pct):.2f}%. That move normally takes VIX "
        f"{exp:+.2f} pts; it actually moved {got:+.2f} pts (residual {row.residual:+.2f})."
    )
    if row.flag:
        return base + " FLAG: fear bid beyond the tape — this has tended to mean-revert."
    if row.residual < -2.0:
        return base + " VIX is lagging the tape — fear is being sold, not bought."
    return base + " VIX is in line with the tape."
