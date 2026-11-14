"""Lakha (@options_insight) vol read - pure checks (2026-11-13 overnight; spec claude/lakha-read-plan.md).

One function per check -> {name, value, score (+1/0/-1 or None = unscored), verdict, line}.
Eight scored gates: spot-vs-vol, base, skew gate, IV percentile, carry, VIX-ATM gap, term,
long-gamma proxy. Bands: >= +3 Constructive, -2..+2 Mixed, <= -3 Defensive.
Plus the fear-release signal (VIX9D/VIX > 1.00 for >= 3 days then back <= 1.00 = release),
with strength = low dealer gamma during the fear (SqueezeMetrics GEX <= 30th pct) + SPX >= 3%
off its 60-day high. Descriptive only (rule 4).
"""
from __future__ import annotations

from collections.abc import Sequence


def pct_rank(hist: Sequence[float], x: float) -> float | None:
    h = [v for v in hist if v is not None]
    return 100.0 * sum(v <= x for v in h) / len(h) if h else None


def _c(name, value, score, verdict, line):
    return {"name": name, "value": value, "score": score, "verdict": verdict, "line": line}


def spot_vs_vol(spot_chg: list[float], iv_chg: list[float]) -> dict:
    """Last 2 days: drops with IV up = real selling (-1); drops with IV down = sellers tiring (+1)."""
    pairs = list(zip(spot_chg[-2:], iv_chg[-2:]))
    downs = [(s, v) for s, v in pairs if s < 0]
    if not downs:
        sc, vd = (1, "rally, vol calm") if all(v <= 0 for _, v in pairs) else (0, "rally with vol bid")
    elif all(v > 0 for _, v in downs):
        sc, vd = -1, "real selling (vol up on drops)"
    elif all(v <= 0 for _, v in downs):
        sc, vd = 1, "sellers running out (vol down on drops)"
    else:
        sc, vd = 0, "mixed"
    return _c("Spot vs vol", f"{spot_chg[-1]:+.2f}% / IV {iv_chg[-1]:+.1f} pts", sc, vd,
              "When price drops, does option vol rise or fall? Rising = fear being bought, the selling is real. "
              "Falling = sellers are running out.")


def divergence(spot_chg: float, iv_chg: float, rr_chg: float, new_high: bool) -> dict:
    if new_high and iv_chg < 0 and rr_chg > 0:
        lab = "Stall"
    elif spot_chg > 0 and iv_chg > 0:
        lab = "Breakout"
    elif spot_chg < 0 and iv_chg <= 0 and rr_chg <= 0:
        lab = "Exhaustion"
    else:
        lab = "-"
    return _c("Divergence label", lab, None, lab,
              "One word for price and vol together. Stall = rally tiring. Breakout = real upside chase. "
              "Exhaustion = selling is done. Base = a bottom is forming.")


def base_signal(leg1_iv_per_pct: float | None, leg2_iv_per_pct: float | None) -> dict:
    if leg1_iv_per_pct is None or leg2_iv_per_pct is None:
        return _c("Base signal", "-", -1, "no second leg yet - no base", _BASE)
    ok = leg2_iv_per_pct < leg1_iv_per_pct
    return _c("Base signal", f"{leg1_iv_per_pct:.2f} -> {leg2_iv_per_pct:.2f} IV pts per 1%", 1 if ok else -1,
              "base forming" if ok else "not done", _BASE)


_BASE = ("On the second push down, if vol jumps less per 1% drop than the first time, fear is fading and "
         "a bottom is forming. The same or a bigger jump means it's not done.")


def skew_gate(rr_pct: float | None) -> dict:
    if rr_pct is None:
        return _c("Skew gate", "-", 0, "no history", "")
    sc = -1 if rr_pct >= 75 else (1 if rr_pct <= 25 else 0)
    vd = "NO THETA (puts very expensive)" if rr_pct >= 75 else ("street long skew" if rr_pct <= 5 else "normal")
    return _c("Skew gate", f"RR {rr_pct:.0f}th pct", sc, vd,
              "Skew = how much more puts cost than calls. Top 25% vs history = selling options is dangerous; "
              "people are paying up for protection for a reason.")


def iv_percentile(p: float | None) -> dict:
    if p is None:
        return _c("IV percentile", "-", 0, "no history", "")
    sc = -1 if p >= 80 else (1 if p <= 30 else 0)
    return _c("IV percentile", f"{p:.0f}th", sc, "expensive" if p >= 80 else ("cheap" if p <= 30 else "middle"),
              f"Where today's option price sits vs the last year: {p:.0f}th = pricier than {p:.0f}% of days.")


def carry(iv: float, rv10: float) -> dict:
    c = (iv - rv10) / iv if iv else 0.0
    sc = 1 if c >= 0.15 else (-1 if c < 0 else 0)
    return _c("Carry", f"{c:+.0%} (IV {iv:.1f} vs 10d RV {rv10:.1f})", sc,
              "options rich vs movement" if c > 0 else "options cheap vs movement",
              "What options charge vs how much the market actually moves. Positive = overpriced: an edge for "
              "sellers, a cost for buyers.")


def vix_gap(vix: float, atm: float) -> dict:
    g = vix - atm
    sc = -1 if g > 5 else (0 if g >= 3 else 1)
    return _c("VIX vs 1M ATM", f"{g:.1f} pts", sc, "tail demand" if g > 5 else "normal",
              "VIX includes far-out crash puts; ATM vol doesn't. Above 5 pts = people paying extra for crash cover.")


def term(v9: float, v: float, v3m: float) -> dict:
    inv = v9 > v
    sc = -1 if inv else (1 if v < v3m else 0)
    return _c("Term structure", f"9D {v9:.1f} / VIX {v:.1f} / 3M {v3m:.1f}", sc,
              "inverted front - panic now" if inv else "normal",
              "Normal = near-term vol below longer-term. Near-term above = panic now; short-dated options too dear.")


def long_gamma_proxy(spot: float, ma10: float, gap_pct: float) -> dict:
    on = spot > ma10 and abs(gap_pct) < 0.5
    return _c("Long-gamma proxy", f"SPX {spot:,.0f} vs 10d {ma10:,.0f}", 1 if on else -1,
              "cushion on" if on else "cushion off",
              "Above its 10-day average with small gaps, dealers usually cushion moves. Below it the cushion "
              "is gone and moves can run.")


def breakeven(iv: float, spot_chg: float, iv_chg: float) -> dict:
    be = iv / 16.0
    flow = abs(spot_chg) < be and abs(iv_chg) >= 1.0
    return _c("Daily breakeven", f"{be:.2f}% a day", None, "vol moved on a small day = flow" if flow else "-",
              "Options imply about IV/16 % a day. Vol jumping on a smaller day = someone's order, not the market.")


def verdict(checks: Sequence[dict]) -> dict:
    scored = [c["score"] for c in checks if c["score"] is not None]
    s = sum(scored)
    band = "Constructive" if s >= 3 else ("Defensive" if s <= -3 else "Mixed")
    text = {"Constructive": "Dips buyable; premium selling OK if the skew gate passes.",
            "Mixed": "Hands-off: be selective, small size.",
            "Defensive": "No premium selling; wait for a base before buying."}[band]
    return {"score": s, "of": len(scored), "band": band, "text": text}


def fear_release(ratio: Sequence[float], *, gex_pct_in_fear: float | None = None,
                 spx_off_high_pct: float | None = None) -> dict:
    """VIX9D/VIX: >1.00 for >= 3 days then back <= 1.00 = release. Early warning at 0.97."""
    state, run = "none", 0
    for i, r in enumerate(ratio):
        if r > 1.0:
            run += 1
        else:
            if run >= 3:
                state = "release" if i == len(ratio) - 1 else "released earlier"
            run = 0
    if run >= 3:
        state = "in fear" if ratio[-1] > 1.0 else state
    elif ratio and 0.97 <= ratio[-1] <= 1.0 and state == "none":
        state = "early warning"
    strength = int(gex_pct_in_fear is not None and gex_pct_in_fear <= 30) + \
        int(spx_off_high_pct is not None and spx_off_high_pct >= 3)
    hist = {0: "+1.20%", 1: "+1.74%", 2: "+3.39%"}[strength]
    return {"state": state, "ratio": ratio[-1] if ratio else None, "strength": strength,
            "line": (f"Fear release: {state}. Strength {strength}/2 (past 20-day SPX average after a release "
                     f"with this strength: {hist}).")}
