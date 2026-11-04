"""Vol-tab panel maths (Phase 5, 2026-11-04) — pure, no I/O.

Feeds ``scheduler/jobs/vol_tab.py`` which writes ``voltab.json`` for the
cockpit's Vol EOD tab. Every function here takes plain rows and returns plain
dicts so it unit-tests without a database. Descriptors only (rule 4).

Panels:
  V2  term_vrp_panel   VIX3M, VIX, RV21 on one chart + the two spreads and their
                       1-year percentiles (term slope = VIX3M-VIX, premium = VIX-RV21)
  V3  straddle_panel   0DTE straddle as % of spot + 20-day average + Fed-day dots
  V5  color_from_chain dealer net gamma tomorrow vs today at the SAME spot/IV/OI —
                       the pure time effect (Color = dGamma/dTime), split into
                       "existing contracts" and "expiring contracts rolling off"
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from datetime import date

PCT_WINDOW = 252
AVG_WINDOW = 20


def pctile(hist: Sequence[float], x: float) -> float | None:
    """Share of ``hist`` at or below ``x`` (0..1). None on empty history."""
    vals = [h for h in hist if h is not None and math.isfinite(h)]
    if not vals:
        return None
    return round(sum(1 for h in vals if h <= x) / len(vals), 3)


def _r(x: float | None, k: int = 2) -> float | None:
    return None if x is None or not math.isfinite(x) else round(x, k)


# ---------------------------------------------------------------- V2
def term_vrp_panel(rows: Iterable[tuple[date, float | None, float | None, float | None]]) -> dict:
    """rows: (date, vix, vix3m, vrp) where vrp = VIX - SPX 20d realized vol (pts).

    RV21 is recovered as VIX - VRP (the vix_snapshot job's own definition), so the
    three lines are self-consistent on every row.
    """
    pts = []
    for d, vix, v3m, vrp in sorted(rows, key=lambda t: t[0]):
        if vix is None:
            continue
        rv = (vix - vrp) if vrp is not None else None
        pts.append(
            {
                "d": d.isoformat(),
                "vix": _r(vix),
                "vix3m": _r(v3m),
                "rv21": _r(rv),
                "term": _r(v3m - vix) if v3m is not None else None,
                "prem": _r(vix - rv) if rv is not None else None,
            }
        )
    cur = pts[-1] if pts else None
    out: dict = {"points": pts, "now": cur}
    if cur:
        hist = pts[-PCT_WINDOW:]
        for k in ("term", "prem"):
            out[k + "_pct"] = (
                pctile([p[k] for p in hist if p[k] is not None], cur[k])
                if cur[k] is not None
                else None
            )
        out["read"] = _term_read(cur, out.get("term_pct"), out.get("prem_pct"))
    return out


def _term_read(cur: dict, tp: float | None, pp: float | None) -> str:
    bits = []
    if cur.get("term") is not None:
        shape = (
            "upward (calm, contango)" if cur["term"] > 0 else "inverted (stress, near-term fear)"
        )
        bits.append(
            f"VIX3M−VIX = {cur['term']:+.2f}: the curve slopes {shape}"
            + (f", {tp * 100:.0f}th percentile of the last year." if tp is not None else ".")
        )
    if cur.get("prem") is not None:
        rich = (
            "options are pricing MORE movement than SPX has delivered"
            if cur["prem"] > 0
            else "options are pricing LESS movement than SPX has delivered (vol is cheap vs realized)"
        )
        bits.append(
            f"VIX−RV21 = {cur['prem']:+.2f}: {rich}"
            + (f", {pp * 100:.0f}th percentile." if pp is not None else ".")
        )
    return " ".join(bits)


# ---------------------------------------------------------------- V3
def straddle_panel(rows: Iterable[tuple[date, float | None]], event_days: set[date]) -> dict:
    """rows: (date, straddle % of spot). Adds a trailing 20-day mean and Fed-day marks."""
    pts = []
    buf: list[float] = []
    for d, pct in sorted(rows, key=lambda t: t[0]):
        if pct is None or not math.isfinite(pct):
            continue
        buf.append(pct)
        avg = sum(buf[-AVG_WINDOW:]) / min(len(buf), AVG_WINDOW)
        pts.append(
            {"d": d.isoformat(), "pct": _r(pct, 3), "avg20": _r(avg, 3), "fed": d in event_days}
        )
    out: dict = {"points": pts, "now": pts[-1] if pts else None}
    if pts:
        c = pts[-1]
        ratio = c["pct"] / c["avg20"] if c["avg20"] else None
        out["ratio"] = _r(ratio, 2)
        if ratio is None:
            out["read"] = ""
        else:
            word = "above" if ratio > 1.1 else "below" if ratio < 0.9 else "near"
            out["read"] = (
                f"Today's 0DTE straddle priced a ±{c['pct']:.2f}% day, {word} its 20-day average "
                f"of ±{c['avg20']:.2f}% ({ratio:.2f}×). Fed days are dotted — the straddle usually "
                f"lifts into them."
            )
    return out


# ---------------------------------------------------------------- V5
def _bs_gamma(s: float, k: float, t: float, iv: float) -> float:
    if s <= 0 or k <= 0 or t <= 0 or iv <= 0:
        return 0.0
    st = iv * math.sqrt(t)
    d1 = (math.log(s / k) + 0.5 * iv * iv * t) / st
    return math.exp(-0.5 * d1 * d1) / (math.sqrt(2 * math.pi) * s * st)


def color_from_chain(
    chain: Iterable[tuple[date, float, str, float | None, float | None]],
    spot: float,
    as_of: date,
    *,
    step_days: int = 1,
) -> dict:
    """Dealer net gamma ($ per 1% move) today vs ``step_days`` later, nothing else changed.

    chain rows: (expiry, strike, 'C'|'P', open_interest, iv). Sign convention = the
    repo's net_gxoi (calls − puts). T is measured close-to-close in calendar days
    (expiry day counts as 0 at its 16:00 close). Contracts with T <= step roll off:
    their whole gamma leaves the book.
    """
    mult = 100.0 * spot * spot * 0.01
    g_now = g_next_live = roll_off = 0.0
    n = 0
    for exp, k, cp, oi, iv in chain:
        if not oi or not iv or iv <= 0:
            continue
        days = (exp - as_of).days
        if days < 0:
            continue
        sign = 1.0 if cp == "C" else -1.0
        t_now = max(days, 0.25) / 365.0  # same-day leftovers priced at ~6h
        g0 = sign * oi * mult * _bs_gamma(spot, k, t_now, iv)
        g_now += g0
        n += 1
        if days <= step_days:
            roll_off -= g0
        else:
            g_next_live += sign * oi * mult * _bs_gamma(spot, k, (days - step_days) / 365.0, iv)
    live_now = g_now + roll_off  # gamma of contracts that survive the step (roll_off is negative)
    color_live = g_next_live - live_now
    total = color_live + roll_off
    out = {
        "as_of": as_of.isoformat(),
        "spot": _r(spot),
        "n_contracts": n,
        "gamma_now": _r(g_now / 1e9, 4),  # $bn per 1%
        "gamma_next": _r((g_now + total) / 1e9, 4),
        "color_live": _r(color_live / 1e9, 4),
        "roll_off": _r(roll_off / 1e9, 4),
        "color_total": _r(total / 1e9, 4),
    }
    out["read"] = _color_read(out)
    return out


def _color_read(c: dict) -> str:
    if c["gamma_now"] is None:
        return ""
    sign = "long" if c["gamma_now"] > 0 else "short"
    return (
        f"Dealers are net {sign} ${abs(c['gamma_now']):.2f}bn of gamma per 1% move. "
        f"With spot and IV unchanged, one day of time alone takes that from "
        f"{c['gamma_now']:+.2f} to {c['gamma_next']:+.2f} $bn: {c['roll_off']:+.2f} from contracts "
        f"expiring and rolling off, {c['color_live']:+.2f} from the rest getting closer to expiry."
    )


def gex_change_series(rows: Iterable[tuple[date, float | None]], n: int = 60) -> list[dict]:
    """oi_chain_daily net_gxoi -> day-over-day change (total: OI + spot + time), last n.

    Raw units, as the reports store them (the cockpit formats them compactly).
    """
    s = [(d, g) for d, g in sorted(rows, key=lambda t: t[0]) if g is not None]
    out = []
    for i in range(1, len(s)):
        out.append({"d": s[i][0].isoformat(), "gex": s[i][1], "chg": s[i][1] - s[i - 1][1]})
    return out[-n:]
