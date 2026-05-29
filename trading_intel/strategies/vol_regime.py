"""Index-level volatility regime classifier — Nations Indexes family.

Reads today's ``index_skew_daily`` row and classifies the market into one of
five vol regimes based on z-scored Nations descriptors:

- VOLI       — Nations VolDex (ATM IV gauge; Yahoo ``^VOLI``)
- TDEX       — Nations TailDex (deep-OTM put cost; Yahoo ``^TDEX``)
- SDEX       — Nations SkewDex (1σ-OTM put vs ATM; Yahoo ``^SDEX``)
- CallDex_p  — IV @ 15Δ call 30d (our proxy; Nations CallDex is sub-only)
- PutDex_p   — IV @ 15Δ put 30d  (our proxy)
- RiskDex_p  — PutDex_p / CallDex_p

Five regimes, each carries a directional trade bias documented in
``docs/playbooks/vol_regime.md``:

- ``COMPLACENT``        — sell premium; iron condors, short strangles
- ``BUILDING_STRESS``   — long convexity; ratio backspreads; cut delta
- ``ACUTE_TAIL``        — fade overpriced wings; put-credit spreads
- ``VOL_CRUSH_SETUP``   — long-dated short vol; collect rich front-month
- ``MIXED``             — no edge; sit out

This is the only place in the codebase that emits a regime *signal* from the
Nations descriptors (CLAUDE.md rule 4 — only ``strategies/`` writes to
``signals``). The descriptors themselves are populated EOD by
``scheduler/jobs/index_skew.py``.

Each daily run emits one ``INDEX_VOL_REGIME`` signal (state) plus, when the
regime differs from yesterday's, a ``VOL_REGIME_TRANSITION`` signal carrying
both labels so the AM summary and Discord can headline the change.

Experimental: payloads carry ``"experimental": True`` until the Phase-5
backtest validates the rules.
"""

from __future__ import annotations

from dataclasses import dataclass
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

#: Trailing window for z-scoring the Nations descriptors (≈ 1y).
Z_WINDOW = 252

#: Minimum history required before a z-score is meaningful (cold-row contract).
MIN_HISTORY = 40

#: 5d-change window for the RiskDex momentum gate in VOL_CRUSH_SETUP.
RISKDEX_MOMENTUM_LOOKBACK = 5

# ── Regime thresholds (z-scores unless noted) ──────────────────────────

COMPLACENT_Z_VOLI_MAX = -0.5
COMPLACENT_Z_TDEX_MAX = -0.5
COMPLACENT_Z_SDEX_MAX = 0.0

BUILDING_Z_TDEX_MIN = 0.75
BUILDING_Z_SDEX_MIN = 0.5
BUILDING_Z_VOLI_MAX = 1.0   # not yet acute

ACUTE_Z_VOLI_MIN = 1.0
ACUTE_Z_TDEX_MIN = 1.5
ACUTE_Z_SDEX_MIN = 1.0

CRUSH_Z_VOLI_MIN = 1.5
CRUSH_RISKDEX_5D_DELTA_MAX = -0.25  # z-units: RiskDex falling fast


REGIME_INDEX_SYMBOL = "INDEX"
SIGNAL_TYPE_STATE = "INDEX_VOL_REGIME"
SIGNAL_TYPE_TRANSITION = "VOL_REGIME_TRANSITION"


# ── Latest-row + history readers ───────────────────────────────────────


@dataclass(frozen=True)
class _LatestRow:
    """Projection of today's ``index_skew_daily`` row (Nations descriptors only)."""

    date: date
    voli: float | None
    tdex: float | None
    sdex: float | None
    calldex_proxy: float | None
    putdex_proxy: float | None
    riskdex_proxy: float | None


def _latest_row(session: Session, *, as_of: date) -> _LatestRow | None:
    row = session.execute(
        select(
            IndexSkewDaily.date,
            IndexSkewDaily.voli,
            IndexSkewDaily.tdex,
            IndexSkewDaily.sdex,
            IndexSkewDaily.calldex_proxy,
            IndexSkewDaily.putdex_proxy,
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
    """Trailing-distribution z-score; ``None`` if cold or pathological."""
    if current is None or not np.isfinite(current):
        return None
    if len(history) < MIN_HISTORY:
        return None
    arr = np.asarray(history, dtype=float)
    sd = float(arr.std(ddof=1))
    if not np.isfinite(sd) or sd <= 0:
        return None
    return (current - float(arr.mean())) / sd


def _riskdex_5d_z_delta(session: Session, *, as_of: date) -> float | None:
    """Z-score change in RiskDex over the trailing 5d.

    Used by the VOL_CRUSH_SETUP gate: RiskDex falling fast = put demand cooling
    relative to call demand = vol about to mean-revert.
    """
    hist = _history(session, IndexSkewDaily.riskdex_proxy, before=as_of, limit=Z_WINDOW + RISKDEX_MOMENTUM_LOOKBACK + 5)
    if len(hist) < MIN_HISTORY + RISKDEX_MOMENTUM_LOOKBACK:
        return None
    arr = np.asarray(hist, dtype=float)
    sd = float(arr.std(ddof=1))
    if not np.isfinite(sd) or sd <= 0:
        return None
    return (arr[-1] - arr[-1 - RISKDEX_MOMENTUM_LOOKBACK]) / sd


# ── Regime classifier ──────────────────────────────────────────────────


@dataclass(frozen=True)
class RegimeRead:
    """Today's regime label plus the z-scores that drove it."""

    label: str
    z_voli: float | None
    z_tdex: float | None
    z_sdex: float | None
    z_riskdex: float | None
    riskdex_5d_z_delta: float | None
    rationale: str
    confidence: float | None  # 0..1, max(|z_voli|, |z_tdex|, |z_sdex|) / 3.0


def classify_regime(
    *,
    z_voli: float | None,
    z_tdex: float | None,
    z_sdex: float | None,
    z_riskdex: float | None,
    riskdex_5d_z_delta: float | None,
) -> tuple[str, str]:
    """Return ``(label, rationale)`` from the four z-scores + RiskDex momentum.

    Rule order matters: ACUTE_TAIL takes precedence over the others when fired
    (you do not want a "complacent" tag when VOLI is in the top decile).
    """
    if z_voli is None or z_tdex is None or z_sdex is None:
        return ("MIXED", "insufficient history to z-score one or more descriptors")

    # 1. Acute tail panic — most restrictive, check first.
    if z_voli >= ACUTE_Z_VOLI_MIN and z_tdex >= ACUTE_Z_TDEX_MIN and z_sdex >= ACUTE_Z_SDEX_MIN:
        return (
            "ACUTE_TAIL",
            f"z(VOLI)={z_voli:.2f}≥{ACUTE_Z_VOLI_MIN}, "
            f"z(TDEX)={z_tdex:.2f}≥{ACUTE_Z_TDEX_MIN}, "
            f"z(SDEX)={z_sdex:.2f}≥{ACUTE_Z_SDEX_MIN}",
        )

    # 2. Vol crush — VOLI high but RiskDex rolling over.
    if (
        z_voli >= CRUSH_Z_VOLI_MIN
        and riskdex_5d_z_delta is not None
        and riskdex_5d_z_delta <= CRUSH_RISKDEX_5D_DELTA_MAX
    ):
        return (
            "VOL_CRUSH_SETUP",
            f"z(VOLI)={z_voli:.2f}≥{CRUSH_Z_VOLI_MIN}, "
            f"5d Δz(RiskDex)={riskdex_5d_z_delta:.2f}≤{CRUSH_RISKDEX_5D_DELTA_MAX}",
        )

    # 3. Building stress — tails / skew bid before ATM.
    if (
        z_tdex >= BUILDING_Z_TDEX_MIN
        and z_sdex >= BUILDING_Z_SDEX_MIN
        and z_voli < BUILDING_Z_VOLI_MAX
    ):
        return (
            "BUILDING_STRESS",
            f"z(TDEX)={z_tdex:.2f}≥{BUILDING_Z_TDEX_MIN}, "
            f"z(SDEX)={z_sdex:.2f}≥{BUILDING_Z_SDEX_MIN}, "
            f"z(VOLI)={z_voli:.2f}<{BUILDING_Z_VOLI_MAX}",
        )

    # 4. Complacent — sell-premium regime.
    if (
        z_voli <= COMPLACENT_Z_VOLI_MAX
        and z_tdex <= COMPLACENT_Z_TDEX_MAX
        and z_sdex <= COMPLACENT_Z_SDEX_MAX
    ):
        return (
            "COMPLACENT",
            f"z(VOLI)={z_voli:.2f}≤{COMPLACENT_Z_VOLI_MAX}, "
            f"z(TDEX)={z_tdex:.2f}≤{COMPLACENT_Z_TDEX_MAX}, "
            f"z(SDEX)={z_sdex:.2f}≤{COMPLACENT_Z_SDEX_MAX}",
        )

    # 5. Otherwise: mixed.
    return (
        "MIXED",
        f"z(VOLI)={z_voli:.2f}, z(TDEX)={z_tdex:.2f}, z(SDEX)={z_sdex:.2f} "
        "— no regime-rule conditions met",
    )


def read_regime(session: Session, *, as_of: date | None = None) -> RegimeRead | None:
    """Compute today's regime label or ``None`` if today's row is missing."""
    as_of = as_of or eastern_now().date()
    latest = _latest_row(session, as_of=as_of)
    if latest is None:
        return None

    voli_hist = _history(session, IndexSkewDaily.voli, before=as_of)
    tdex_hist = _history(session, IndexSkewDaily.tdex, before=as_of)
    sdex_hist = _history(session, IndexSkewDaily.sdex, before=as_of)
    riskdex_hist = _history(session, IndexSkewDaily.riskdex_proxy, before=as_of)

    z_voli = _z(voli_hist, latest.voli)
    z_tdex = _z(tdex_hist, latest.tdex)
    z_sdex = _z(sdex_hist, latest.sdex)
    z_riskdex = _z(riskdex_hist, latest.riskdex_proxy)
    momentum = _riskdex_5d_z_delta(session, as_of=as_of)

    label, rationale = classify_regime(
        z_voli=z_voli,
        z_tdex=z_tdex,
        z_sdex=z_sdex,
        z_riskdex=z_riskdex,
        riskdex_5d_z_delta=momentum,
    )

    drivers = [abs(z) for z in (z_voli, z_tdex, z_sdex) if z is not None]
    confidence = min(max(drivers), 3.0) / 3.0 if drivers else None

    return RegimeRead(
        label=label,
        z_voli=z_voli,
        z_tdex=z_tdex,
        z_sdex=z_sdex,
        z_riskdex=z_riskdex,
        riskdex_5d_z_delta=momentum,
        rationale=rationale,
        confidence=confidence,
    )


# ── Yesterday-vs-today regime memory ───────────────────────────────────


def _last_emitted_regime(session: Session, *, before: date) -> str | None:
    """Most recent state signal's label, or ``None`` if no prior row."""
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
    if not payload:
        return None
    label = payload.get("label") if isinstance(payload, dict) else None
    return label if isinstance(label, str) else None


# ── Signal emission ────────────────────────────────────────────────────


def _payload(read: RegimeRead) -> dict[str, Any]:
    return {
        "label": read.label,
        "rationale": read.rationale,
        "z_voli": read.z_voli,
        "z_tdex": read.z_tdex,
        "z_sdex": read.z_sdex,
        "z_riskdex": read.z_riskdex,
        "riskdex_5d_z_delta": read.riskdex_5d_z_delta,
        "experimental": True,
    }


def emit_signals(
    session: Session,
    *,
    as_of: date | None = None,
) -> list[Signal]:
    """Compute today's regime and write the state + (if changed) transition signals.

    Idempotent within the day: if a state signal for today already exists with
    the same label, we skip the write (no duplicate row, no duplicate Discord).

    Returns the list of newly-inserted ``Signal`` rows (may be empty).
    """
    as_of = as_of or eastern_now().date()
    read = read_regime(session, as_of=as_of)
    if read is None:
        log.warning("vol_regime.no_row", as_of=as_of.isoformat())
        return []

    ts = datetime.combine(as_of, datetime.min.time())

    # Idempotency: skip if today already has the same state signal.
    existing_today_label = session.execute(
        select(Signal.payload)
        .where(
            Signal.signal_type == SIGNAL_TYPE_STATE,
            Signal.symbol == REGIME_INDEX_SYMBOL,
            Signal.ts == ts,
        )
        .limit(1)
    ).scalar_one_or_none()
    if isinstance(existing_today_label, dict) and existing_today_label.get("label") == read.label:
        log.info("vol_regime.skip_duplicate", as_of=as_of.isoformat(), label=read.label)
        return []

    prior_label = _last_emitted_regime(session, before=as_of)

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
        transition = Signal(
            ts=ts,
            symbol=REGIME_INDEX_SYMBOL,
            signal_type=SIGNAL_TYPE_TRANSITION,
            payload=transition_payload,
            confidence=read.confidence,
        )
        inserts.append(transition)

    for sig in inserts:
        session.add(sig)
    session.flush()
    log.info(
        "vol_regime.emitted",
        as_of=as_of.isoformat(),
        label=read.label,
        prior=prior_label,
        n=len(inserts),
    )
    return inserts


def run(session: Session) -> None:
    """Scheduler entrypoint. Idempotent — re-runs are no-ops for the same label."""
    emit_signals(session)
    session.commit()
