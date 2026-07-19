"""Post-earnings expected-move-break RE-ENTRY scanner (signal-eligible, experimental).

The convergence point of the pattern (``docs/learning/em-break-gamma-burnoff-digest.md``
and ``docs/playbooks/em_break_reentry.md``): once a name has broken its earnings
expected move AND the front-expiry gamma that amplified the flush has burned off,
the next convex opportunity is often a DEFINED-RISK UPSIDE structure toward the
next call-strike concentration — positioned for stabilization / mean-reversion.

``evaluate_reentry`` is the pure, unit-tested gate: it takes the descriptor
features (EM-break, gamma burn-off, vol-reset, dealer lean, wall structure,
overwriter re-supply, systematic tailwind) and returns eligibility + a 0-100
conviction + the candidate structure (call wall = target, put wall = stop
reference). ``emit_signals`` assembles those features from the banked tables and
writes ``EM_BREAK_REENTRY`` rows.

This module is allowed to write to ``signals`` (CLAUDE.md — only ``strategies/``
may). Every row is flagged ``experimental=True`` in its payload until the P6
backtest validates it (same discipline as the skew generators). It combines
validated descriptors + the probability-style composite — it does NOT alert on a
raw GEX flip (FlashAlpha rule 4).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from trading_intel.memory.models import Signal
from trading_intel.timeutils import eastern_now

log = structlog.get_logger(__name__)

SIGNAL_TYPE = "EM_BREAK_REENTRY"

#: Conviction weights (points; sum to 100). Mirrors the swing scorer's transparent
#: weighted-composite style (``swing/scoring.py``).
W_VOL_RESET = 25.0  # post-earnings IV crush underway (straddle decaying + VRP normalizing)
W_PHASE = 15.0  # gamma phase gone linear (amplifier spent)
W_DEALER = 15.0  # dealer flipped to long gamma (dampening)
W_STRUCT = 25.0  # spot near/through put wall + a call wall to aim at
W_SUPPLY = 10.0  # overwriters rebuilding the call wall (the target forming)
W_TAILWIND = 10.0  # index systematic buying (RV roll-off) as a market-wide bid

#: Still-over-realizing = the flush may not be done; don't catch the knife.
OVER_REALIZE_PENALTY = 20.0

#: Minimum conviction (after prerequisites) to be eligible.
ELIGIBLE_CUTOFF = 55.0

#: Spot within this fraction at/below the put wall counts as "at support".
PUT_WALL_PROXIMITY = 0.02


@dataclass(frozen=True, slots=True)
class ReentryEval:
    """Result of the re-entry gate for one name."""

    eligible: bool
    conviction: float
    prerequisites_met: bool
    target: float | None  # call wall (upside magnet)
    stop_ref: float | None  # put wall (support / stop reference)
    phase: str
    rationale: list[str] = field(default_factory=list)
    experimental: bool = True


def _f(features: Mapping, key: str) -> float | None:
    val = features.get(key)
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def evaluate_reentry(features: Mapping) -> ReentryEval:
    """Score a post-earnings re-entry candidate from its descriptor features.

    Recognised feature keys (all optional except the two prerequisites; missing
    keys simply do not contribute):
        em_broke (bool, REQUIRED)        earnings gap beyond the expected move
        gamma_burned_off (bool, REQUIRED) front-expiry gamma expired / bled out
        over_realizing (bool)            move still extending beyond EM (penalty)
        phase (str)                      "linear"/"transition"/"mechanical"
        straddle_label (str)             "decaying"/"flat"/"repricing_up"
        vrp_normalizing (bool)           IV crush handing back to realized
        dealer_gamma_sign (float)        >0 long/dampening, <0 short/amplifying
        spot, put_wall, call_wall (float)
        overwriter_rebuilding (bool)     supply-led call writing rebuilding the wall
        systematic_buying_usd (float)    index vol-control tailwind (>0 = bid)

    The two prerequisites (``em_broke`` and ``gamma_burned_off``) gate eligibility;
    without both the candidate is never eligible regardless of score.
    """
    em_broke = bool(features.get("em_broke"))
    burned_off = bool(features.get("gamma_burned_off"))
    prereq = em_broke and burned_off

    rationale: list[str] = []
    score = 0.0

    # Vol reset (25): straddle decaying (15) + VRP normalizing (10).
    if features.get("straddle_label") == "decaying":
        score += 15.0
        rationale.append("straddle decaying (vol reset underway)")
    elif features.get("straddle_label") == "repricing_up":
        rationale.append("straddle repricing UP — vol not reset yet")
    if bool(features.get("vrp_normalizing")):
        score += 10.0
        rationale.append("VRP normalizing")

    # Phase (15): linear full, transition half.
    phase = str(features.get("phase") or "unknown")
    if phase == "linear":
        score += W_PHASE
        rationale.append("gamma phase linear (amplifier spent)")
    elif phase == "transition":
        score += W_PHASE / 2
        rationale.append("gamma phase transitioning")

    # Dealer lean (15): long gamma = dampening.
    dealer = _f(features, "dealer_gamma_sign")
    if dealer is not None and dealer > 0:
        score += W_DEALER
        rationale.append("dealer long gamma (dampening)")

    # Structure (25): spot at/through put wall (15) + a call wall to target (10).
    spot = _f(features, "spot")
    put_wall = _f(features, "put_wall")
    call_wall = _f(features, "call_wall")
    if spot is not None and put_wall is not None and spot <= put_wall * (1 + PUT_WALL_PROXIMITY):
        score += 15.0
        rationale.append("spot at/through put wall (support)")
    if call_wall is not None and (spot is None or call_wall > spot):
        score += 10.0
        rationale.append("call wall above as upside target")

    # Overwriter re-supply (10).
    if bool(features.get("overwriter_rebuilding")):
        score += W_SUPPLY
        rationale.append("overwriters rebuilding the call wall")

    # Index tailwind (10).
    sysbuy = _f(features, "systematic_buying_usd")
    if sysbuy is not None and sysbuy > 0:
        score += W_TAILWIND
        rationale.append("systematic (vol-control) buying tailwind")

    # Still over-realizing -> the flush may not be done; penalize.
    if bool(features.get("over_realizing")):
        score -= OVER_REALIZE_PENALTY
        rationale.append("still over-realizing — early, knife risk")

    conviction = max(0.0, min(100.0, score))
    eligible = bool(prereq and conviction >= ELIGIBLE_CUTOFF)
    if not prereq:
        missing = []
        if not em_broke:
            missing.append("no EM break")
        if not burned_off:
            missing.append("front gamma not burned off")
        rationale.append("prerequisites unmet: " + ", ".join(missing))

    return ReentryEval(
        eligible=eligible,
        conviction=conviction,
        prerequisites_met=prereq,
        target=call_wall,
        stop_ref=put_wall,
        phase=phase,
        rationale=rationale,
    )


def _payload(symbol: str, ev: ReentryEval, features: Mapping) -> dict:
    return {
        "symbol": symbol,
        "conviction": ev.conviction,
        "target_call_wall": ev.target,
        "stop_ref_put_wall": ev.stop_ref,
        "phase": ev.phase,
        "rationale": ev.rationale,
        "break_ratio": features.get("break_ratio"),
        "sigma": features.get("sigma"),
        "direction": features.get("direction"),
        "experimental": True,
    }


def emit_signals(
    session: Session,
    features_by_symbol: Mapping[str, Mapping],
    *,
    as_of: date | None = None,
) -> list[Signal]:
    """Evaluate pre-assembled features per symbol and write eligible re-entry signals.

    ``features_by_symbol`` maps symbol -> feature dict (see ``evaluate_reentry``);
    it is built by the scheduled job from the banked tables so this function stays
    pure-ish and testable with a fake session. Idempotent on (day, symbol, type):
    a same-day row for the symbol is not duplicated.
    """
    as_of = as_of or eastern_now().date()
    ts = datetime.combine(as_of, datetime.min.time())
    inserts: list[Signal] = []

    for symbol, feats in features_by_symbol.items():
        ev = evaluate_reentry(feats)
        if not ev.eligible:
            continue
        existing = session.execute(
            select(Signal.id)
            .where(
                Signal.signal_type == SIGNAL_TYPE,
                Signal.symbol == symbol,
                Signal.ts == ts,
            )
            .limit(1)
        ).scalar_one_or_none()
        if existing is not None:
            log.info("em_break_reentry.skip_duplicate", symbol=symbol, as_of=as_of.isoformat())
            continue
        sig = Signal(
            ts=ts,
            symbol=symbol,
            signal_type=SIGNAL_TYPE,
            payload=_payload(symbol, ev, feats),
            confidence=ev.conviction / 100.0,
        )
        session.add(sig)
        inserts.append(sig)

    if inserts:
        session.flush()
    log.info("em_break_reentry.emitted", as_of=as_of.isoformat(), n=len(inserts))
    return inserts
