"""Skew-based signal generators.

Per ADR-003 (revision 2), skew is signal-eligible. Five generators land here:

- ``SKEW_EXTREME_CALL_BIAS`` — the MU pattern: 252d RR percentile ≤ 0.02 AND
  butterfly not blown out (BF percentile ≤ 0.20). Surfaces names where calls
  are unusually rich vs puts.
- ``SKEW_TAIL_PUT_BID`` — 252d RR percentile ≥ 0.98 AND VIX zone not "high"
  (so we don't fire during stress where every name's put skew is bid). Tail
  hedging spike or pre-event risk reflex.
- ``SKEW_SHIFT_VS_SPX`` — |z(rr_25d_abnormal)| ≥ 2.5 vs a trailing window.
  Idiosyncratic skew dislocation — a name moved differently from what its
  VIX-beta predicts.
- ``VIX_TAIL_HEDGING_SPIKE`` — index-level: ``vix_tail_hedging_score`` in the
  top 5% of its trailing distribution. Market-wide signal, not per name.
- ``INDEX_SKEW_REGIME_FLIP`` — index-level: 5d momentum of SDEX > 95th pctile
  AND Cboe SKEW > 145. Broad de-risking phase indicator.

All five are flagged ``experimental=True`` in their payload until the Phase-5
backtest validates them. The architectural rule from CLAUDE.md is preserved:
``strategies/`` modules are the ONLY ones that write to the ``signals`` table.

The generators are run by a thin daily entrypoint (``scheduler/jobs/skew_signals.py``
in a follow-up day-7 PR) or ad-hoc from a notebook. Today each is a callable;
tomorrow they implement a ``SignalGenerator`` Protocol once that's pinned.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

import numpy as np
import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.sql import ColumnElement

from trading_intel.memory.models import (
    IndexSkewDaily,
    Signal,
    SkewSnapshot,
    VixData,
)
from trading_intel.timeutils import eastern_now
from trading_intel.vol.term_skew import build_regime_gate

log = structlog.get_logger(__name__)

#: Default horizon used for the per-name signals (matches ADR-003 §2.5).
DEFAULT_HORIZON_DTE = 30

#: Per-name percentile cutoffs (252d window).
RR_PCTILE_CALL_BIAS_CUTOFF = 0.02
RR_PCTILE_TAIL_PUT_CUTOFF = 0.98
BF_PCTILE_NOT_BLOWN_OUT = 0.20

#: Abnormal-RR z-score gate.
ABNORMAL_RR_Z_CUTOFF = 2.5

#: Index-level cutoffs.
VIX_TAIL_SCORE_PCTILE = 0.95
SDEX_5D_MOMO_PCTILE = 0.95
CBOE_SKEW_REGIME_CUTOFF = 145.0


@dataclass(frozen=True)
class SkewSignal:
    """A skew-derived signal record, ready for insertion into ``signals``."""

    ts: datetime
    symbol: str  # "INDEX" for market-level signals
    signal_type: str
    payload: dict[str, Any]
    confidence: float | None = None

    def to_row(self) -> dict[str, Any]:
        return {
            "ts": self.ts,
            "symbol": self.symbol,
            "signal_type": self.signal_type,
            "payload": self.payload,
            "confidence": self.confidence,
        }


# ── Per-name latest-row reader ─────────────────────────────────────────


@dataclass
class _LatestRow:
    """Lightweight projection of the latest ``skew_snapshots`` row for one symbol."""

    symbol: str
    ts: date
    rr_25d: float | None
    bf_25d: float | None
    rr_25d_pctile_252d: float | None
    bf_25d_pctile_252d: float | None
    rr_25d_abnormal: float | None
    abnormal_z: float | None = None  # filled in by the abnormal-z helper


def _latest_rows(
    session: Session, *, as_of: date, horizon_dte: int
) -> list[_LatestRow]:
    """All today's per-name rows at one horizon."""
    rows = session.execute(
        select(
            SkewSnapshot.symbol,
            SkewSnapshot.ts,
            SkewSnapshot.rr_25d,
            SkewSnapshot.bf_25d,
            SkewSnapshot.rr_25d_pctile_252d,
            SkewSnapshot.bf_25d_pctile_252d,
            SkewSnapshot.rr_25d_abnormal,
        ).where(SkewSnapshot.ts == as_of, SkewSnapshot.horizon_dte == horizon_dte)
    ).all()
    return [
        _LatestRow(
            symbol=r[0],
            ts=r[1],
            rr_25d=r[2],
            bf_25d=r[3],
            rr_25d_pctile_252d=r[4],
            bf_25d_pctile_252d=r[5],
            rr_25d_abnormal=r[6],
        )
        for r in rows
    ]


def _abnormal_history(
    session: Session, symbol: str, *, before: date, horizon_dte: int, limit: int = 252
) -> list[float]:
    rows = session.execute(
        select(SkewSnapshot.rr_25d_abnormal)
        .where(
            SkewSnapshot.symbol == symbol,
            SkewSnapshot.horizon_dte == horizon_dte,
            SkewSnapshot.ts < before,
            SkewSnapshot.rr_25d_abnormal.is_not(None),
        )
        .order_by(SkewSnapshot.ts.desc())
        .limit(limit)
    ).all()
    return [float(r[0]) for r in rows]


def _z_score(history: list[float], current: float) -> float | None:
    if not history or len(history) < 20:
        return None
    arr = np.asarray(history, dtype=float)
    sd = float(arr.std(ddof=1))
    if not np.isfinite(sd) or sd <= 0:
        return None
    return (float(current) - float(arr.mean())) / sd


def _latest_vix(session: Session) -> float | None:
    val = session.execute(
        select(VixData.vix)
        .where(VixData.vix.is_not(None))
        .order_by(VixData.date.desc())
        .limit(1)
    ).scalar_one_or_none()
    return float(val) if val is not None else None


# ── Per-name generators ────────────────────────────────────────────────


def gen_extreme_call_bias(
    session: Session,
    *,
    as_of: date,
    horizon_dte: int = DEFAULT_HORIZON_DTE,
    rr_cutoff: float = RR_PCTILE_CALL_BIAS_CUTOFF,
    bf_cutoff: float = BF_PCTILE_NOT_BLOWN_OUT,
) -> list[SkewSignal]:
    """Names with 252d RR percentile ≤ ``rr_cutoff`` and BF percentile ≤ ``bf_cutoff``.

    The MU pattern: calls cheap (or, equivalently, puts unwound) at the wings,
    butterfly not blown out (so it's not a generic wing-cheapening regime).
    """
    ts = datetime.combine(as_of, datetime.min.time())
    out: list[SkewSignal] = []
    for row in _latest_rows(session, as_of=as_of, horizon_dte=horizon_dte):
        if row.rr_25d_pctile_252d is None or row.bf_25d_pctile_252d is None:
            continue
        if row.rr_25d_pctile_252d <= rr_cutoff and row.bf_25d_pctile_252d <= bf_cutoff:
            out.append(
                SkewSignal(
                    ts=ts,
                    symbol=row.symbol,
                    signal_type="SKEW_EXTREME_CALL_BIAS",
                    payload={
                        "horizon_dte": horizon_dte,
                        "rr_25d": row.rr_25d,
                        "rr_25d_pctile_252d": row.rr_25d_pctile_252d,
                        "bf_25d": row.bf_25d,
                        "bf_25d_pctile_252d": row.bf_25d_pctile_252d,
                        "experimental": True,
                    },
                )
            )
    return out


def gen_tail_put_bid(
    session: Session,
    *,
    as_of: date,
    horizon_dte: int = DEFAULT_HORIZON_DTE,
    cutoff: float = RR_PCTILE_TAIL_PUT_CUTOFF,
) -> list[SkewSignal]:
    """Names with extreme put bid (252d RR percentile ≥ ``cutoff``), VIX not stressed.

    During VIX stress (zone ``high``) every put skew gets bid, so the signal is
    not informative; gated out via ``vol.term_skew.build_regime_gate``.
    """
    gate = build_regime_gate(_latest_vix(session))
    if gate.zone == "high":
        return []

    ts = datetime.combine(as_of, datetime.min.time())
    out: list[SkewSignal] = []
    for row in _latest_rows(session, as_of=as_of, horizon_dte=horizon_dte):
        if row.rr_25d_pctile_252d is None:
            continue
        if row.rr_25d_pctile_252d >= cutoff:
            out.append(
                SkewSignal(
                    ts=ts,
                    symbol=row.symbol,
                    signal_type="SKEW_TAIL_PUT_BID",
                    payload={
                        "horizon_dte": horizon_dte,
                        "rr_25d": row.rr_25d,
                        "rr_25d_pctile_252d": row.rr_25d_pctile_252d,
                        "vix_zone": gate.zone,
                        "experimental": True,
                    },
                )
            )
    return out


def gen_shift_vs_spx(
    session: Session,
    *,
    as_of: date,
    horizon_dte: int = DEFAULT_HORIZON_DTE,
    z_cutoff: float = ABNORMAL_RR_Z_CUTOFF,
) -> list[SkewSignal]:
    """Idiosyncratic skew dislocations: |z(rr_25d_abnormal)| ≥ ``z_cutoff``."""
    ts = datetime.combine(as_of, datetime.min.time())
    out: list[SkewSignal] = []
    for row in _latest_rows(session, as_of=as_of, horizon_dte=horizon_dte):
        if row.rr_25d_abnormal is None:
            continue
        hist = _abnormal_history(
            session, row.symbol, before=as_of, horizon_dte=horizon_dte
        )
        z = _z_score(hist, row.rr_25d_abnormal)
        if z is None or abs(z) < z_cutoff:
            continue
        out.append(
            SkewSignal(
                ts=ts,
                symbol=row.symbol,
                signal_type="SKEW_SHIFT_VS_SPX",
                payload={
                    "horizon_dte": horizon_dte,
                    "rr_25d_abnormal": row.rr_25d_abnormal,
                    "z_score": z,
                    "direction": "call_dislodge" if z < 0 else "put_dislodge",
                    "experimental": True,
                },
                confidence=float(min(abs(z) / 5.0, 1.0)),
            )
        )
    return out


# ── Index-level generators ─────────────────────────────────────────────


def _index_history(
    session: Session,
    column: ColumnElement[float],
    *,
    before: date,
    limit: int = 300,
) -> list[float]:
    rows = session.execute(
        select(column)
        .where(column.is_not(None), IndexSkewDaily.date < before)
        .order_by(IndexSkewDaily.date.desc())
        .limit(limit)
    ).all()
    return [float(r[0]) for r in rows]


def _index_latest(
    session: Session, *, as_of: date
) -> tuple[float | None, float | None, float | None, list[float]]:
    """Today's (vix_tail_score, sdex, cboe_skew) plus the trailing 5d SDEX series."""
    row = session.execute(
        select(
            IndexSkewDaily.vix_tail_hedging_score,
            IndexSkewDaily.sdex,
            IndexSkewDaily.cboe_skew,
        ).where(IndexSkewDaily.date == as_of)
    ).first()
    if row is None:
        return (None, None, None, [])

    sdex_hist = session.execute(
        select(IndexSkewDaily.sdex)
        .where(IndexSkewDaily.sdex.is_not(None), IndexSkewDaily.date <= as_of)
        .order_by(IndexSkewDaily.date.desc())
        .limit(6)
    ).all()
    sdex_5d = [float(r[0]) for r in sdex_hist]
    return (
        float(row[0]) if row[0] is not None else None,
        float(row[1]) if row[1] is not None else None,
        float(row[2]) if row[2] is not None else None,
        sdex_5d,
    )


def _percentile(history: list[float], current: float) -> float | None:
    if current is None or not np.isfinite(current) or len(history) < 20:
        return None
    arr = np.asarray(history, dtype=float)
    return float(np.mean(arr <= float(current)))


def gen_vix_tail_hedging_spike(
    session: Session,
    *,
    as_of: date,
    cutoff: float = VIX_TAIL_SCORE_PCTILE,
) -> list[SkewSignal]:
    """Market-wide: today's vix_tail_hedging_score in the top 5% of its history."""
    score, _sdex, _skew, _ = _index_latest(session, as_of=as_of)
    if score is None:
        return []
    hist = _index_history(
        session, IndexSkewDaily.vix_tail_hedging_score, before=as_of
    )
    pct = _percentile(hist, score)
    if pct is None or pct < cutoff:
        return []
    return [
        SkewSignal(
            ts=datetime.combine(as_of, datetime.min.time()),
            symbol="INDEX",
            signal_type="VIX_TAIL_HEDGING_SPIKE",
            payload={
                "vix_tail_hedging_score": score,
                "pctile_252d": pct,
                "experimental": True,
            },
            confidence=float(min((pct - cutoff) / (1.0 - cutoff + 1e-9), 1.0)),
        )
    ]


def gen_index_skew_regime_flip(
    session: Session,
    *,
    as_of: date,
    sdex_momo_pctile: float = SDEX_5D_MOMO_PCTILE,
    cboe_skew_cutoff: float = CBOE_SKEW_REGIME_CUTOFF,
) -> list[SkewSignal]:
    """Broad de-risking phase: SDEX 5d momentum top 5% AND Cboe SKEW > 145."""
    _score, sdex, cboe_skew, sdex_5d = _index_latest(session, as_of=as_of)
    if sdex is None or cboe_skew is None or len(sdex_5d) < 6:
        return []
    momo = sdex_5d[0] - sdex_5d[-1]  # newer minus 5d-ago

    # Build trailing 5d-momenta history (rolling window — approx via raw diffs).
    sdex_rows = session.execute(
        select(IndexSkewDaily.date, IndexSkewDaily.sdex)
        .where(IndexSkewDaily.sdex.is_not(None), IndexSkewDaily.date < as_of)
        .order_by(IndexSkewDaily.date.asc())
    ).all()
    if len(sdex_rows) < 30:
        return []
    series = [float(r[1]) for r in sdex_rows]
    momo_hist = [series[i] - series[i - 5] for i in range(5, len(series))]
    pct = _percentile(momo_hist, momo)
    if pct is None or pct < sdex_momo_pctile or cboe_skew < cboe_skew_cutoff:
        return []
    return [
        SkewSignal(
            ts=datetime.combine(as_of, datetime.min.time()),
            symbol="INDEX",
            signal_type="INDEX_SKEW_REGIME_FLIP",
            payload={
                "sdex": sdex,
                "sdex_5d_momentum": momo,
                "sdex_momo_pctile_252d": pct,
                "cboe_skew": cboe_skew,
                "experimental": True,
            },
            confidence=float(min((pct - sdex_momo_pctile) / (1.0 - sdex_momo_pctile + 1e-9), 1.0)),
        )
    ]


# ── Aggregator + writer ────────────────────────────────────────────────


@dataclass
class GeneratorRun:
    """Bundle of all signals produced in one run, for the writer to persist."""

    as_of: date
    signals: list[SkewSignal] = field(default_factory=list)


def run_all(session: Session, *, as_of: date | None = None) -> GeneratorRun:
    """Run every skew generator for today and return the collected signals."""
    as_of = as_of or eastern_now().date()
    result = GeneratorRun(as_of=as_of)
    result.signals.extend(gen_extreme_call_bias(session, as_of=as_of))
    result.signals.extend(gen_tail_put_bid(session, as_of=as_of))
    result.signals.extend(gen_shift_vs_spx(session, as_of=as_of))
    result.signals.extend(gen_vix_tail_hedging_spike(session, as_of=as_of))
    result.signals.extend(gen_index_skew_regime_flip(session, as_of=as_of))
    return result


def persist(session: Session, run: GeneratorRun) -> int:
    """Insert all generated signals into the ``signals`` table. Returns row count.

    No idempotency key: signals are append-only. Re-running on the same day
    appends duplicates — the caller (a scheduler job) should guard with a
    once-per-day cron.
    """
    if not run.signals:
        return 0
    session.add_all([Signal(**s.to_row()) for s in run.signals])
    session.commit()
    return len(run.signals)


def main() -> None:
    """Manual entrypoint: run all generators once, persist, log counts."""
    from trading_intel.config import get_settings
    from trading_intel.memory.db import make_session_factory

    settings = get_settings()
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ]
    )
    session_factory = make_session_factory(settings)
    correlation_id = uuid.uuid4().hex
    with session_factory() as session:
        result = run_all(session)
        n = persist(session, result)
    log.bind(correlation_id=correlation_id, job="skew_signals").info(
        "skew_signals.done", n=n, as_of=result.as_of.isoformat()
    )


if __name__ == "__main__":
    main()
