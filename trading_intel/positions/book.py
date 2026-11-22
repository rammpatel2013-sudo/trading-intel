"""OI-anchored customer position book (2026-11-22) - pure math, no DB.

Idea (Mithil 11/21: "tape + change in OI"): every night, for every contract,
  * SIZE comes from the OI change the session made (exact - OCC counts it),
  * SIDE comes from what customers did that session: single-leg tape (aggressor side, weighted by where the print
    sat in the quote), package legs (pos_packages: side decided once per spread), and - when the tape is thin -
    the strike's IV change vs its expiry's ATM IV change (buying lifts the strike's IV, writing drops it).
The position is re-anchored to OI every day (|position| <= OI), so a missed tape day can never make it drift; it only
lowers ``confidence`` (= share of OI whose side we measured). OI that was already open the first time we saw a
contract is kept as ``seed_oi`` and is NEVER signed (Mithil 11/06: don't seed the ledger with OI).

Sign: ``cust_pos`` > 0 = customers net long -> dealers short that line -> dealer gamma negative there.
"""

from __future__ import annotations

from dataclasses import dataclass

QUOTE_WEIGHT = {
    "at_ask": 1.0,
    "at_bid": 1.0,
    "outside": 0.8,
    "in_hi": 0.7,
    "in_lo": 0.7,
    "mid": 0.2,
}
IV_DEAD = 0.001  # 0.10 vol point dead zone (same as oi-flow-direction rule)
IV_LEAN = 0.5  # IV-only side is half as sure as the tape
IV_WEIGHT = 0.5  # and counts half toward confidence
SHRINK = 25.0  # tape lean = net / (gross + SHRINK): 30 lots can't decide a 20k line


@dataclass
class Line:
    pos: float = 0.0  # customers net long (+) / short (-)
    known: float = 0.0  # contracts whose side was measured
    oi: float = 0.0  # OI after the last processed session
    seed_oi: float = 0.0  # OI open when first seen (unsigned)


def tape_lean(buy: float, sell: float) -> float:
    g = buy + sell
    return 0.0 if g <= 0 else (buy - sell) / (g + SHRINK)


def iv_lean(d_iv_strike: float | None, d_iv_atm: float | None) -> float:
    if d_iv_strike is None or d_iv_atm is None:
        return 0.0
    x = d_iv_strike - d_iv_atm
    return 0.0 if abs(x) < IV_DEAD else (IV_LEAN if x > 0 else -IV_LEAN)


def session_lean(buy: float, sell: float, d_oi: float, ivl: float) -> tuple[float, float]:
    """(lean -1..1, confidence weight 0..1) for one session. The tape's reliability grows with how many contracts it
    saw relative to the OI change (a sample of ~5% of the change already counts half) - NOT the plain coverage
    ratio, which made a 10%-sampled tape (Oct 2026) look 10x weaker than it is. IV fills the rest.
    """
    g = buy + sell
    w_tape = 0.0 if g <= 0 else g / (g + 0.05 * abs(d_oi) + 50.0)
    lean = w_tape * tape_lean(buy, sell) + (1.0 - w_tape) * ivl
    weight = w_tape + (1.0 - w_tape) * (IV_WEIGHT if ivl else 0.0)
    return max(-1.0, min(1.0, lean)), weight


def step(
    line: Line | None, oi_before: float | None, oi_after: float, lean: float, weight: float
) -> Line:
    """Apply one session. ``line`` None = first time we see the contract (its open OI becomes seed_oi)."""
    if line is None:
        before = float(oi_before or 0.0)
        line = Line(pos=0.0, known=0.0, oi=before, seed_oi=before)
    before = line.oi if oi_before is None else float(oi_before)
    after = max(0.0, float(oi_after))
    d = after - before
    pos, known, seed = line.pos, line.known, line.seed_oi
    if d < 0 and before > 0:
        scale = after / before
        neutral = pos * scale  # closes spread evenly over holders
        signed = pos + lean * abs(d)  # customers selling = closing longs, buying = closing shorts
        a = abs(lean)
        pos = (1 - a) * neutral + a * signed
        known *= scale
        seed *= scale
    elif d > 0:
        pos += lean * d
        known += abs(lean) * weight * d
    pos = max(-after, min(after, pos))
    known = min(known, after)
    seed = min(seed, max(0.0, after - known))
    return Line(pos=pos, known=known, oi=after, seed_oi=seed)


def confidence(line: Line) -> float:
    return 0.0 if line.oi <= 0 else round(min(1.0, line.known / line.oi), 3)


def gamma_usd_1pct(contracts: float, gamma: float | None, spot: float | None) -> float | None:
    """$ gamma per 1% move for ``contracts`` held (x100 multiplier). Dealer view: pass -cust_pos."""
    if gamma is None or spot is None:
        return None
    return contracts * gamma * 100.0 * spot * spot * 0.01


def short_gamma_shares(rows: list[dict]) -> dict[tuple, float]:
    """Each dealer-short line's share (0..1) of all dealer short gamma; key = row["key"]."""
    neg = [
        (r["key"], r["mm_gamma_usd_1pct"]) for r in rows if (r.get("mm_gamma_usd_1pct") or 0) < 0
    ]
    tot = sum(v for _, v in neg)
    return {} if tot >= 0 else {k: v / tot for k, v in neg}
