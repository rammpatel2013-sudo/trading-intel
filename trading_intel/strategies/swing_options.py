"""Swing-setup signal generator (P4) — the validated writer to ``signals``.

Reads the banked ``swing_features`` rows (daily collector, P2) and emits per-name
candidate swing setups. Per CLAUDE.md, only ``strategies/`` modules write to the
``signals`` table; this is that writer for the swing system.

FlashAlpha rule 4 is respected two ways:
  1. A setup is a COMPOSITE — the Stage-1 conviction score (price/RSI/DEX lean +
     IV/RV richness, via ``trading_intel.swing.score_setup``) gated by a VOL
     PERCENTILE edge (252d ATM-IV rank). It is never a raw GEX/DEX crossing.
  2. Every signal is flagged ``experimental=True`` until the Phase-6 backtest
     validates it (mirrors ``strategies/skew.py``); the alerting layer must not
     promote experimental signals.

The pure decision (``evaluate``) is separated from the DB read (``_latest_features``)
so the gating is unit-tested without Postgres.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from trading_intel.memory.models import Signal, SwingFeature
from trading_intel.swing import score_setup
from trading_intel.timeutils import eastern_now

log = structlog.get_logger(__name__)

# ── Gates ──────────────────────────────────────────────────────────────
DEFAULT_MIN_SCORE = 60.0  # Stage-1 conviction floor (0-100)
IV_RANK_CHEAP_MAX = 0.40  # <= -> vol cheap (long-premium / debit context)
IV_RANK_RICH_MIN = 0.60  # >= -> vol rich (credit context)

SIGNAL_LONG = "SWING_SETUP_LONG"
SIGNAL_SHORT = "SWING_SETUP_SHORT"


@dataclass(frozen=True, slots=True)
class FeatureRow:
    """Projection of the latest ``swing_features`` row used by the generator."""

    symbol: str
    ts: date
    spot: float | None = None
    atm_iv: float | None = None
    rv20: float | None = None
    iv_rv: float | None = None
    rsi14: float | None = None
    sma50: float | None = None
    px_vs_sma50: float | None = None
    skew_25d: float | None = None
    gex: float | None = None
    dex: float | None = None
    atm_iv_rank_252d: float | None = None
    iv_rv_pctile_252d: float | None = None
    skew_pctile_252d: float | None = None
    gex_pctile_252d: float | None = None
    dex_pctile_252d: float | None = None

    def as_features(self) -> dict[str, Any]:
        """Canonical feature mapping for ``score_setup``."""
        return {
            "spot": self.spot,
            "atm_iv": self.atm_iv,
            "rv20": self.rv20,
            "iv_rv": self.iv_rv,
            "rsi14": self.rsi14,
            "sma50": self.sma50,
            "px_vs_sma50": self.px_vs_sma50,
            "skew_25d": self.skew_25d,
            "gex": self.gex,
            "dex": self.dex,
        }


@dataclass(frozen=True, slots=True)
class SwingSignal:
    """A swing-setup signal record, ready for insertion into ``signals``."""

    ts: datetime
    symbol: str
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


@dataclass
class GeneratorRun:
    """Bundle of signals produced in one run, for the writer to persist."""

    as_of: date
    signals: list[SwingSignal] = field(default_factory=list)


def _vol_context(iv_rank: float) -> str | None:
    """ "cheap"/"rich" vol context from 252d ATM-IV rank, or ``None`` if mid-range."""
    if iv_rank <= IV_RANK_CHEAP_MAX:
        return "cheap"
    if iv_rank >= IV_RANK_RICH_MIN:
        return "rich"
    return None


def evaluate(
    rows: Sequence[FeatureRow], *, as_of: date, min_score: float = DEFAULT_MIN_SCORE
) -> list[SwingSignal]:
    """Pure gating: banked feature rows -> candidate swing signals.

    A row emits iff (a) Stage-1 score >= ``min_score`` and the lean is directional,
    and (b) the 252d ATM-IV rank has matured (not ``None``) AND shows a vol edge
    (cheap or rich, not mid-range). The IV-rank gate is what makes this a matured
    composite rather than a raw greek crossing (rule 4).
    """
    ts = datetime.combine(as_of, datetime.min.time())
    out: list[SwingSignal] = []
    for r in rows:
        setup = score_setup(r.as_features())
        if setup.score < min_score or setup.lean == "neutral":
            continue
        iv_rank = r.atm_iv_rank_252d
        if iv_rank is None:  # history not matured -> not validated yet
            continue
        context = _vol_context(iv_rank)
        if context is None:  # no vol-percentile edge -> stand aside
            continue

        signal_type = SIGNAL_LONG if setup.lean == "bullish" else SIGNAL_SHORT
        confidence = round(min(1.0, (setup.score / 100.0) * (0.6 + abs(iv_rank - 0.5))), 3)
        payload: dict[str, Any] = {
            "experimental": True,  # not promotable to an alert until backtested (rule 4)
            "stage": "stage-1",
            "lean": setup.lean,
            "structure": setup.structure,
            "score": setup.score,
            "vol_context": context,
            "spot": r.spot,
            "atm_iv": r.atm_iv,
            "iv_rv": r.iv_rv,
            "rsi14": r.rsi14,
            "gex": r.gex,
            "dex": r.dex,
            "atm_iv_rank_252d": iv_rank,
            "skew_pctile_252d": r.skew_pctile_252d,
            "iv_rv_pctile_252d": r.iv_rv_pctile_252d,
            "note": "Descriptive candidate — not advice. Backtest is P6 (FlashAlpha rule 4).",
        }
        out.append(
            SwingSignal(
                ts=ts,
                symbol=r.symbol,
                signal_type=signal_type,
                payload=payload,
                confidence=confidence,
            )
        )
    return out


def _latest_features(session: Session, *, as_of: date) -> list[FeatureRow]:
    """Latest ``swing_features`` row per symbol with ``ts <= as_of`` (one per name)."""
    cols = (
        SwingFeature.symbol,
        SwingFeature.ts,
        SwingFeature.spot,
        SwingFeature.atm_iv,
        SwingFeature.rv20,
        SwingFeature.iv_rv,
        SwingFeature.rsi14,
        SwingFeature.sma50,
        SwingFeature.px_vs_sma50,
        SwingFeature.skew_25d,
        SwingFeature.gex,
        SwingFeature.dex,
        SwingFeature.atm_iv_rank_252d,
        SwingFeature.iv_rv_pctile_252d,
        SwingFeature.skew_pctile_252d,
        SwingFeature.gex_pctile_252d,
        SwingFeature.dex_pctile_252d,
    )
    result = session.execute(
        select(*cols).where(SwingFeature.ts <= as_of).order_by(SwingFeature.symbol, SwingFeature.ts)
    ).all()
    latest: dict[str, FeatureRow] = {}
    for row in result:
        latest[row[0]] = FeatureRow(*row)  # later ts overwrites earlier (ordered asc)
    return [latest[k] for k in sorted(latest)]


def run_all(
    session: Session, *, as_of: date | None = None, min_score: float = DEFAULT_MIN_SCORE
) -> GeneratorRun:
    """Evaluate the latest banked features and collect candidate swing signals."""
    as_of = as_of or eastern_now().date()
    rows = _latest_features(session, as_of=as_of)
    return GeneratorRun(as_of=as_of, signals=evaluate(rows, as_of=as_of, min_score=min_score))


def persist(session: Session, run: GeneratorRun) -> int:
    """Insert generated signals into ``signals``. Returns row count.

    Append-only like ``strategies/skew.py`` — the scheduler job guards with a
    once-per-day cron so re-runs don't duplicate.
    """
    if not run.signals:
        return 0
    session.add_all([Signal(**s.to_row()) for s in run.signals])
    session.commit()
    return len(run.signals)


def main() -> None:
    """Manual entrypoint: run the generator once, persist, log the count."""
    from trading_intel.config import get_settings
    from trading_intel.memory.db import make_session_factory

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ]
    )
    correlation_id = uuid.uuid4().hex
    bound = log.bind(correlation_id=correlation_id, job="swing_options")
    settings = get_settings()
    session_factory = make_session_factory(settings)
    with session_factory() as session:
        run = run_all(session)
        n = persist(session, run)
    bound.info("swing_options.done", as_of=run.as_of.isoformat(), signals=n)


if __name__ == "__main__":
    main()
