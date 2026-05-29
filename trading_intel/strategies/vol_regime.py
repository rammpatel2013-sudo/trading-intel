"""Unified vol-regime classifier — five-dimension decomposition + composite VSI.

Reads today's ``index_skew_daily`` row, decomposes the vol picture into five
orthogonal dimensions, scores each on a 252d trailing z, combines them into a
0-100 Vol Stress Index (VSI), and applies a 7-state classifier on the z-score
matrix. Emits one ``INDEX_VOL_REGIME`` signal per day with the full
decomposition in the payload — the dashboard reads it for the per-dimension
cards.

Dimensions:

1. **LEVEL**       — ``VOLI`` z-score (clean ATM IV).
   Side-read: ``vix_voli_spread`` z (wing-vs-ATM contribution to VIX).
2. **SKEW**        — ``SDEX`` z-score.
3. **TAIL**        — ``TDEX`` z-score, double-confirmed by
                     ``vix_tail_hedging_score`` z (≥0.5 lifts the dimension).
4. **TERM**        — ``vix_term_9d_30d`` z (negative = backwardation; we sign-
                     flip so positive z always means *more stressful*).
5. **VOL-OF-VOL**  — ``vix_options_richness`` z (overlay: ``z ≥ 1.5`` tags
                     ``VIX_OPTIONS_RICH`` regardless of primary state).

Composite Vol Stress Index:

    VSI = clip(50 + 10 * mean(z_LEVEL, z_SKEW, z_TAIL, z_TERM, z_VVOL), 0, 100)

States (first match wins, priority order):

1. ``CRASH_HEDGING``     — z_LEVEL ≥ 1 ∧ z_TAIL ≥ 1.5 ∧ z_TERM ≥ 1
2. ``TERM_STRESS_FLIP``  — vix_term_9d_30d raw > 0 (VIX9D > VIX backwardation)
3. ``VOL_CRUSH_SETUP``   — z_LEVEL ≥ 1.5 ∧ 5d Δz(RiskDex) ≤ -0.25
4. ``STEALTH_STRESS``    — z_TAIL ≥ 0.75 ∧ z_SKEW ≥ 0.5 ∧ z_LEVEL < 0.5
5. ``COMPLACENT``        — z_LEVEL ≤ -0.5 ∧ z_TAIL ≤ -0.5 ∧ z_SKEW ≤ 0
6. ``MIXED``             — none of above

Plus an **overlay tag** ``VIX_OPTIONS_RICH`` when z_VVOL ≥ 1.5, regardless of
primary state. The overlay travels in the payload as ``overlays: [...]``.

Strategies-only writes to ``signals`` (CLAUDE.md rule 4). All payloads carry
``experimental: True`` until the Phase-5 backtest validates the cutoffs.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from typing import Any

import numpy as np
import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.sql import ColumnElement

from trading_intel.memory.models import IndexSkewDaily, Signal
from trading_intel.timeutils import eastern_now

log = structlog.get_logger(__name__)

# ── Z-scoring contract ─────────────────────────────────────────────────

Z_WINDOW = 252
MIN_HISTORY = 40

# ── State-classifier thresholds ────────────────────────────────────────

CRASH_Z_LEVEL_MIN = 1.0
CRASH_Z_TAIL_MIN = 1.5
CRASH_Z_TERM_MIN = 1.0

STEALTH_Z_TAIL_MIN = 0.75
STEALTH_Z_SKEW_MIN = 0.5
STEALTH_Z_LEVEL_MAX = 0.5

CRUSH_Z_LEVEL_MIN = 1.5
CRUSH_RISKDEX_5D_DELTA_MAX = -0.25
RISKDEX_MOMENTUM_LOOKBACK = 5

COMPLACENT_Z_LEVEL_MAX = -0.5
COMPLACENT_Z_TAIL_MAX = -0.5
COMPLACENT_Z_SKEW_MAX = 0.0

# Term-stress: backwardation raw threshold AND z confirmation.
TERM_STRESS_RAW_MIN = 0.0          # VIX9D > VIX
TERM_STRESS_Z_MIN = 2.0

# Overlay tag.
VVOL_RICH_Z_MIN = 1.5

REGIME_INDEX_SYMBOL = "INDEX"
SIGNAL_TYPE_STATE = "INDEX_VOL_REGIME"
SIGNAL_TYPE_TRANSITION = "VOL_REGIME_TRANSITION"


# ── Latest row + history reader ────────────────────────────────────────


@dataclass(frozen=True)
class _LatestRow:
    date: date
    # LEVEL
    voli: float | None
    vix_voli_spread: float | None
    # SKEW
    sdex: float | None
    # TAIL
    tdex: float | None
    vix_tail_hedging_score: float | None
    # TERM
    vix_term_9d_30d: float | None
    # VOL-OF-VOL
    vix_options_richness: float | None
    vvix_vix_ratio: float | None
    # Side
    riskdex_proxy: float | None


def _latest_row(session: Session, *, as_of: date) -> _LatestRow | None:
    row = session.execute(
        select(
            IndexSkewDaily.date,
            IndexSkewDaily.voli,
            IndexSkewDaily.vix_voli_spread,
            IndexSkewDaily.sdex,
            IndexSkewDaily.tdex,
            IndexSkewDaily.vix_tail_hedging_score,
            IndexSkewDaily.vix_term_9d_30d,
            IndexSkewDaily.vix_options_richness,
            IndexSkewDaily.vvix_vix_ratio,
            IndexSkewDaily.riskdex_proxy,
        )
        .where(IndexSkewDaily.date <= as_of)
        .order_by(IndexSkewDaily.date.desc())
        .limit(1)
    ).first()
    if row is None:
        return None
    return _LatestRow(*row)


def _history(
    session: Session,
    column: ColumnElement[float],
    *,
    before: date,
    limit: int = Z_WINDOW,
) -> list[float]:
    rows = session.execute(
        select(IndexSkewDaily.date, column)
        .where(column.is_not(None), IndexSkewDaily.date < before)
        .order_by(IndexSkewDaily.date.desc())
        .limit(limit)
    ).all()
    if not rows:
        return []
    rows = sorted(rows, key=lambda r: r[0])
    return [float(r[1]) for r in rows]


def _z(history: list[float], current: float | None) -> float | None:
    if current is None or not np.isfinite(current):
        return None
    if len(history) < MIN_HISTORY:
        return None
    arr = np.asarray(history, dtype=float)
    sd = float(arr.std(ddof=1))
    if not np.isfinite(sd) or sd <= 0:
        return None
    return (current - float(arr.mean())) / sd


def _percentile(history: list[float], current: float | None) -> float | None:
    """Fraction of trailing history ≤ ``current`` (0..1). ``None`` if cold."""
    if current is None or not np.isfinite(current):
        return None
    if len(history) < MIN_HISTORY:
        return None
    arr = np.asarray(history, dtype=float)
    return float((arr <= current).mean())


def _riskdex_5d_z_delta(session: Session, *, as_of: date) -> float | None:
    hist = _history(
        session,
        IndexSkewDaily.riskdex_proxy,
        before=as_of,
        limit=Z_WINDOW + RISKDEX_MOMENTUM_LOOKBACK + 5,
    )
    if len(hist) < MIN_HISTORY + RISKDEX_MOMENTUM_LOOKBACK:
        return None
    arr = np.asarray(hist, dtype=float)
    sd = float(arr.std(ddof=1))
    if not np.isfinite(sd) or sd <= 0:
        return None
    return (arr[-1] - arr[-1 - RISKDEX_MOMENTUM_LOOKBACK]) / sd


# ── Dimension card ─────────────────────────────────────────────────────


@dataclass
class DimensionCard:
    """One row of the dashboard's decomposition strip.

    Stored verbatim in the signal payload so consumers (the page, the AM
    report, Discord) don't have to recompute or re-interpret z-scores.
    """

    name: str               # canonical id, e.g. "LEVEL"
    label: str              # display label, e.g. "Level (ATM IV)"
    metric_name: str        # what we're showing, e.g. "VOLI"
    metric_value: float | None
    z_score: float | None
    percentile: float | None  # 0..1
    severity: str           # "calm" | "low" | "normal" | "elevated" | "extreme"
    description: str        # one-line plain-English description


def _severity(z: float | None) -> str:
    if z is None:
        return "unknown"
    if z >= 2.0:
        return "extreme"
    if z >= 1.0:
        return "elevated"
    if z <= -1.0:
        return "calm"
    if z <= -0.5:
        return "low"
    return "normal"


def _describe(dim: str, z: float | None, value: float | None, side: str | None = None) -> str:
    """One-line, plain-English read of a single dimension.

    The dashboard renders this verbatim under each card. Keep terse and
    actionable — no academic hedging.
    """
    if z is None:
        return "Insufficient history to score."

    val = "—" if value is None else f"{value:.2f}"
    sev = _severity(z)

    if dim == "LEVEL":
        if sev == "extreme":
            return f"ATM IV ({val}) in top 5% of the trailing year — acute level stress."
        if sev == "elevated":
            return f"ATM IV ({val}) elevated; vol-of-vol matters here, not just direction."
        if sev == "calm":
            return f"ATM IV ({val}) at multi-year lows — sell-premium friendly."
        if sev == "low":
            return f"ATM IV ({val}) below average — premium-selling regime."
        return f"ATM IV ({val}) near normal."
    if dim == "SKEW":
        if sev == "extreme":
            return f"Put skew at top 5% — wings priced for downside; pre-correction setup risk."
        if sev == "elevated":
            return f"Put skew bid — defensive positioning building."
        if sev == "calm":
            return f"Skew unusually flat — call-bias regime; right-tail demand."
        if sev == "low":
            return f"Skew softer than usual — calls keeping up with puts."
        return "Skew within normal band."
    if dim == "TAIL":
        if sev == "extreme":
            return "Deep-OTM put cost in extreme territory — full-on tail-hedging bid."
        if sev == "elevated":
            return f"Tail demand elevated; check {side or 'VIX call wing'} for confirmation."
        if sev == "calm":
            return "Tail bid washed out — no one paying up for crash protection."
        if sev == "low":
            return "Tail premium below average — clean of recent hedging unwind."
        return "Tail in normal range."
    if dim == "TERM":
        # We sign-flip TERM z so positive z = more stress (raw spread positive
        # = VIX9D > VIX = backwardation, the stressful case).
        if sev == "extreme":
            return "Term backwardated and inverted — front-month panic; expect 1-3 sessions to resolve."
        if sev == "elevated":
            return "Term flattening / inverting — short-dated VIX bid; stress front-loading."
        if sev == "calm":
            return "Steep contango — calm regime; vol carry positive."
        if sev == "low":
            return "Contango normalizing — no front-month stress."
        return "Term structure in normal contango."
    if dim == "VVOL":
        if sev == "extreme":
            return "VIX options very expensive vs realized β — fade VIX call wings."
        if sev == "elevated":
            return "VIX options pricing more than realized β justifies — overlay: VIX-options rich."
        if sev == "calm":
            return "VIX options cheap relative to β — protection on sale (rare)."
        if sev == "low":
            return "VVIX/VIX modestly below realized — slight discount."
        return "VVIX/VIX matches realized — fair."
    return ""


def _build_cards(
    latest: _LatestRow,
    z_voli: float | None,
    z_voli_spread: float | None,
    z_sdex: float | None,
    z_tdex: float | None,
    z_vth: float | None,
    z_term_signed: float | None,
    z_vvol: float | None,
    p_voli: float | None,
    p_sdex: float | None,
    p_tdex: float | None,
    p_term_signed: float | None,
    p_vvol: float | None,
) -> list[DimensionCard]:
    """Assemble the 5 dimension cards consumed by the dashboard."""
    # TAIL composite z = max(TDEX z, VTH z) — either bid lifts the dimension.
    z_tail = z_tdex
    if z_vth is not None:
        z_tail = max(z_tdex, z_vth) if z_tdex is not None else z_vth

    return [
        DimensionCard(
            name="LEVEL",
            label="Level (ATM IV)",
            metric_name="VOLI",
            metric_value=latest.voli,
            z_score=z_voli,
            percentile=p_voli,
            severity=_severity(z_voli),
            description=_describe("LEVEL", z_voli, latest.voli),
        ),
        DimensionCard(
            name="SKEW",
            label="Skew (put-vs-ATM)",
            metric_name="SDEX",
            metric_value=latest.sdex,
            z_score=z_sdex,
            percentile=p_sdex,
            severity=_severity(z_sdex),
            description=_describe("SKEW", z_sdex, latest.sdex),
        ),
        DimensionCard(
            name="TAIL",
            label="Tail (deep-OTM)",
            metric_name="TDEX (+ VIX-tail)",
            metric_value=latest.tdex,
            z_score=z_tail,
            percentile=p_tdex,
            severity=_severity(z_tail),
            description=_describe("TAIL", z_tail, latest.tdex,
                                  side=f"VIX-tail z={z_vth:+.1f}" if z_vth is not None else None),
        ),
        DimensionCard(
            name="TERM",
            label="Term (VIX9D-VIX)",
            metric_name="VIX9D-VIX",
            metric_value=latest.vix_term_9d_30d,
            z_score=z_term_signed,
            percentile=p_term_signed,
            severity=_severity(z_term_signed),
            description=_describe("TERM", z_term_signed, latest.vix_term_9d_30d),
        ),
        DimensionCard(
            name="VVOL",
            label="Vol-of-vol (VIX options)",
            metric_name="VVIX / (|β|·VIX)",
            metric_value=latest.vix_options_richness,
            z_score=z_vvol,
            percentile=p_vvol,
            severity=_severity(z_vvol),
            description=_describe("VVOL", z_vvol, latest.vix_options_richness),
        ),
    ]


# ── VSI + classifier ───────────────────────────────────────────────────


def _vsi(cards: list[DimensionCard]) -> float | None:
    zs = [c.z_score for c in cards if c.z_score is not None]
    if not zs:
        return None
    return float(max(0.0, min(100.0, 50.0 + 10.0 * (sum(zs) / len(zs)))))


def classify_state(
    *,
    z_level: float | None,
    z_skew: float | None,
    z_tail: float | None,
    z_term: float | None,
    term_raw: float | None,
    riskdex_5d_z_delta: float | None,
) -> tuple[str, str]:
    """7-state vol-regime classifier. Returns ``(state, rationale)``."""
    if z_level is None or z_tail is None or z_skew is None:
        return ("MIXED", "Insufficient history to score one or more dimensions.")

    # 1. CRASH_HEDGING — strongest, check first.
    if (
        z_level >= CRASH_Z_LEVEL_MIN
        and z_tail >= CRASH_Z_TAIL_MIN
        and z_term is not None
        and z_term >= CRASH_Z_TERM_MIN
    ):
        return (
            "CRASH_HEDGING",
            f"z(LEVEL)={z_level:+.2f}≥{CRASH_Z_LEVEL_MIN}, "
            f"z(TAIL)={z_tail:+.2f}≥{CRASH_Z_TAIL_MIN}, "
            f"z(TERM)={z_term:+.2f}≥{CRASH_Z_TERM_MIN}",
        )

    # 2. TERM_STRESS_FLIP — backwardation is the rare, severe single-axis flag.
    if (
        term_raw is not None
        and term_raw > TERM_STRESS_RAW_MIN
        and z_term is not None
        and z_term >= TERM_STRESS_Z_MIN
    ):
        return (
            "TERM_STRESS_FLIP",
            f"VIX9D-VIX={term_raw:+.2f} (backwardation), z={z_term:+.2f}≥{TERM_STRESS_Z_MIN}",
        )

    # 3. VOL_CRUSH_SETUP — high level + RiskDex rolling over.
    if (
        z_level >= CRUSH_Z_LEVEL_MIN
        and riskdex_5d_z_delta is not None
        and riskdex_5d_z_delta <= CRUSH_RISKDEX_5D_DELTA_MAX
    ):
        return (
            "VOL_CRUSH_SETUP",
            f"z(LEVEL)={z_level:+.2f}≥{CRUSH_Z_LEVEL_MIN}, "
            f"5d Δz(RiskDex)={riskdex_5d_z_delta:+.2f}≤{CRUSH_RISKDEX_5D_DELTA_MAX}",
        )

    # 4. STEALTH_STRESS — tails + skew bid while ATM is calm.
    if (
        z_tail >= STEALTH_Z_TAIL_MIN
        and z_skew >= STEALTH_Z_SKEW_MIN
        and z_level < STEALTH_Z_LEVEL_MAX
    ):
        return (
            "STEALTH_STRESS",
            f"z(TAIL)={z_tail:+.2f}≥{STEALTH_Z_TAIL_MIN}, "
            f"z(SKEW)={z_skew:+.2f}≥{STEALTH_Z_SKEW_MIN}, "
            f"z(LEVEL)={z_level:+.2f}<{STEALTH_Z_LEVEL_MAX}",
        )

    # 5. COMPLACENT
    if (
        z_level <= COMPLACENT_Z_LEVEL_MAX
        and z_tail <= COMPLACENT_Z_TAIL_MAX
        and z_skew <= COMPLACENT_Z_SKEW_MAX
    ):
        return (
            "COMPLACENT",
            f"z(LEVEL)={z_level:+.2f}≤{COMPLACENT_Z_LEVEL_MAX}, "
            f"z(TAIL)={z_tail:+.2f}≤{COMPLACENT_Z_TAIL_MAX}, "
            f"z(SKEW)={z_skew:+.2f}≤{COMPLACENT_Z_SKEW_MAX}",
        )

    return (
        "MIXED",
        f"z(LEVEL)={z_level:+.2f}, z(TAIL)={z_tail:+.2f}, z(SKEW)={z_skew:+.2f} — no rule matched.",
    )


# Back-compat alias — earlier strategy code called this ``classify_regime``.
def classify_regime(
    *,
    z_voli: float | None,
    z_tdex: float | None,
    z_sdex: float | None,
    z_riskdex: float | None = None,        # accepted, not used by new classifier
    riskdex_5d_z_delta: float | None = None,
    z_term: float | None = None,
    term_raw: float | None = None,
    z_tail_extra: float | None = None,     # vix_tail_hedging_score z
) -> tuple[str, str]:
    """Legacy entrypoint preserved for old callers / probes.

    Maps the old single-z inputs onto the new 7-state classifier:
    - ``z_voli``  → ``z_level``
    - ``z_tdex``  → ``z_tail`` (uplifted by ``z_tail_extra`` if provided)
    - ``z_sdex``  → ``z_skew``
    """
    z_tail = z_tdex
    if z_tail_extra is not None and z_tdex is not None:
        z_tail = max(z_tdex, z_tail_extra)
    elif z_tail_extra is not None:
        z_tail = z_tail_extra
    return classify_state(
        z_level=z_voli, z_skew=z_sdex, z_tail=z_tail,
        z_term=z_term, term_raw=term_raw,
        riskdex_5d_z_delta=riskdex_5d_z_delta,
    )


# ── Read assembly ──────────────────────────────────────────────────────


@dataclass(frozen=True)
class RegimeRead:
    label: str
    rationale: str
    vsi: float | None
    cards: list[DimensionCard]
    overlays: list[str]
    riskdex_5d_z_delta: float | None
    confidence: float | None


def read_regime(session: Session, *, as_of: date | None = None) -> RegimeRead | None:
    as_of = as_of or eastern_now().date()
    latest = _latest_row(session, as_of=as_of)
    if latest is None:
        return None

    # Per-dimension trailing-window history reads.
    voli_hist = _history(session, IndexSkewDaily.voli, before=as_of)
    voli_spread_hist = _history(session, IndexSkewDaily.vix_voli_spread, before=as_of)
    sdex_hist = _history(session, IndexSkewDaily.sdex, before=as_of)
    tdex_hist = _history(session, IndexSkewDaily.tdex, before=as_of)
    vth_hist = _history(session, IndexSkewDaily.vix_tail_hedging_score, before=as_of)
    # TERM: positive raw spread = VIX9D > VIX = backwardation = stress. We do
    # NOT sign-flip — positive z already means more stressful.
    term_hist = _history(session, IndexSkewDaily.vix_term_9d_30d, before=as_of)
    vvol_hist = _history(session, IndexSkewDaily.vix_options_richness, before=as_of)

    z_voli = _z(voli_hist, latest.voli)
    z_voli_spread = _z(voli_spread_hist, latest.vix_voli_spread)
    z_sdex = _z(sdex_hist, latest.sdex)
    z_tdex = _z(tdex_hist, latest.tdex)
    z_vth = _z(vth_hist, latest.vix_tail_hedging_score)
    z_term = _z(term_hist, latest.vix_term_9d_30d)
    z_vvol = _z(vvol_hist, latest.vix_options_richness)

    p_voli = _percentile(voli_hist, latest.voli)
    p_sdex = _percentile(sdex_hist, latest.sdex)
    p_tdex = _percentile(tdex_hist, latest.tdex)
    p_term = _percentile(term_hist, latest.vix_term_9d_30d)
    p_vvol = _percentile(vvol_hist, latest.vix_options_richness)

    cards = _build_cards(
        latest=latest,
        z_voli=z_voli, z_voli_spread=z_voli_spread,
        z_sdex=z_sdex,
        z_tdex=z_tdex, z_vth=z_vth,
        z_term_signed=z_term,
        z_vvol=z_vvol,
        p_voli=p_voli, p_sdex=p_sdex, p_tdex=p_tdex,
        p_term_signed=p_term, p_vvol=p_vvol,
    )

    # Composite stress score.
    vsi = _vsi(cards)

    # Primary state.
    momentum = _riskdex_5d_z_delta(session, as_of=as_of)
    z_tail_composite = next((c.z_score for c in cards if c.name == "TAIL"), None)
    label, rationale = classify_state(
        z_level=z_voli,
        z_skew=z_sdex,
        z_tail=z_tail_composite,
        z_term=z_term,
        term_raw=latest.vix_term_9d_30d,
        riskdex_5d_z_delta=momentum,
    )

    # Overlays — additive tags that travel alongside the primary label.
    overlays: list[str] = []
    if z_vvol is not None and z_vvol >= VVOL_RICH_Z_MIN:
        overlays.append("VIX_OPTIONS_RICH")

    drivers = [abs(c.z_score) for c in cards if c.z_score is not None]
    confidence = min(max(drivers), 3.0) / 3.0 if drivers else None

    return RegimeRead(
        label=label,
        rationale=rationale,
        vsi=vsi,
        cards=cards,
        overlays=overlays,
        riskdex_5d_z_delta=momentum,
        confidence=confidence,
    )


# ── Signal emission ────────────────────────────────────────────────────


def _payload(read: RegimeRead) -> dict[str, Any]:
    return {
        "label": read.label,
        "rationale": read.rationale,
        "vsi": read.vsi,
        "overlays": read.overlays,
        "cards": [asdict(c) for c in read.cards],
        "riskdex_5d_z_delta": read.riskdex_5d_z_delta,
        "experimental": True,
        # Convenience: the legacy single-z fields old consumers (AM report)
        # read directly.
        "z_voli": next((c.z_score for c in read.cards if c.name == "LEVEL"), None),
        "z_sdex": next((c.z_score for c in read.cards if c.name == "SKEW"), None),
        "z_tdex": next((c.z_score for c in read.cards if c.name == "TAIL"), None),
        "z_term": next((c.z_score for c in read.cards if c.name == "TERM"), None),
        "z_vvol": next((c.z_score for c in read.cards if c.name == "VVOL"), None),
    }


def _last_emitted_label(session: Session, *, before: date) -> str | None:
    payload = session.execute(
        select(Signal.payload)
        .where(
            Signal.signal_type == SIGNAL_TYPE_STATE,
            Signal.symbol == REGIME_INDEX_SYMBOL,
            Signal.ts < datetime.combine(before, datetime.min.time()),
        )
        .order_by(Signal.ts.desc())
        .limit(1)
    ).scalar_one_or_none()
    if not payload or not isinstance(payload, dict):
        return None
    label = payload.get("label")
    return label if isinstance(label, str) else None


def emit_signals(session: Session, *, as_of: date | None = None) -> list[Signal]:
    as_of = as_of or eastern_now().date()
    read = read_regime(session, as_of=as_of)
    if read is None:
        log.warning("vol_regime.no_row", as_of=as_of.isoformat())
        return []

    ts = datetime.combine(as_of, datetime.min.time())

    # Idempotency on same-day same-label.
    existing = session.execute(
        select(Signal.payload)
        .where(
            Signal.signal_type == SIGNAL_TYPE_STATE,
            Signal.symbol == REGIME_INDEX_SYMBOL,
            Signal.ts == ts,
        )
        .limit(1)
    ).scalar_one_or_none()
    if (
        isinstance(existing, dict)
        and existing.get("label") == read.label
        and existing.get("overlays", []) == read.overlays
    ):
        log.info("vol_regime.skip_duplicate", as_of=as_of.isoformat(), label=read.label)
        return []

    prior_label = _last_emitted_label(session, before=as_of)

    inserts: list[Signal] = []
    state = Signal(
        ts=ts,
        symbol=REGIME_INDEX_SYMBOL,
        signal_type=SIGNAL_TYPE_STATE,
        payload=_payload(read),
        confidence=read.confidence,
    )
    inserts.append(state)

    if prior_label is not None and prior_label != read.label:
        transition_payload = dict(_payload(read))
        transition_payload["prior_label"] = prior_label
        inserts.append(
            Signal(
                ts=ts,
                symbol=REGIME_INDEX_SYMBOL,
                signal_type=SIGNAL_TYPE_TRANSITION,
                payload=transition_payload,
                confidence=read.confidence,
            )
        )

    for sig in inserts:
        session.add(sig)
    session.flush()
    log.info(
        "vol_regime.emitted",
        as_of=as_of.isoformat(),
        label=read.label,
        vsi=read.vsi,
        overlays=read.overlays,
        prior=prior_label,
        n=len(inserts),
    )
    return inserts


def run(session: Session) -> None:
    emit_signals(session)
    session.commit()
