"""Regime-conditional forward-return validator.

Reads historical ``INDEX_VOL_REGIME`` signals (written by
``strategies.vol_regime``) and benchmark closes from ``quotes_daily``, then
computes per-state forward-return distributions at configurable horizons.

The output answers a single question per regime state: *given today's signal,
what is the distribution of the H-day forward return?* If a state does not
produce a return distribution materially different from the unconditional
baseline (same horizon, same date window), then either the state isn't
predictive or the thresholds need recalibration — which is the gating decision
before any leverage-policy module is built on top.

Read-only by design — no writes to ``signals`` or anything else.

Usage:

    from trading_intel.backtest.regime_validate import (
        RegimeBacktestConfig, run_backtest,
    )

    cfg = RegimeBacktestConfig(benchmark_symbol="SPY")
    result = run_backtest(session, cfg)
    for s in result.by_state:
        print(s.label, s.horizon_days, s.stats.mean, s.stats.ir, s.stats.n)
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from typing import Any

import numpy as np
import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from trading_intel.backtest.metrics import ReturnStats, lift_vs_baseline, summarize
from trading_intel.memory.models import QuoteDaily, Signal

log = structlog.get_logger(__name__)

#: The signal type emitted by ``strategies.vol_regime``.
SIGNAL_TYPE = "INDEX_VOL_REGIME"

#: Symbol used by the regime classifier for its INDEX-level emission.
REGIME_INDEX_SYMBOL = "INDEX"


# ── Config + result shapes ─────────────────────────────────────────────


@dataclass(frozen=True)
class RegimeBacktestConfig:
    """Knobs for ``run_backtest``.

    ``benchmark_symbol`` must exist in ``quotes_daily`` for the entire window;
    SPY is the default since it's the most reliable EOD series for US equity
    regime work and trades during regular hours that match the EOD signal.

    ``horizons_days`` are trading-day offsets (not calendar). A signal on day
    ``t`` is paired with the close from the ``h``-th *subsequent* trading day
    found in ``quotes_daily``.
    """

    benchmark_symbol: str = "SPY"
    horizons_days: tuple[int, ...] = (1, 5, 20)
    start: date | None = None
    end: date | None = None
    #: If ``True``, additionally compute a per-(label, overlay) split. The
    #: regime classifier emits a ``VIX_OPTIONS_RICH`` overlay that may turn out
    #: to materially shift the conditional distribution.
    split_by_overlay: bool = True


@dataclass(frozen=True)
class RegimeStateStats:
    """One row of the backtest output table."""

    label: str
    overlays: tuple[str, ...]  # () for "no overlay"; otherwise sorted tuple
    horizon_days: int
    stats: ReturnStats
    lift_vs_baseline: float | None  # mean - baseline_mean at same horizon


@dataclass(frozen=True)
class RegimeBacktestResult:
    """Top-level result returned by :func:`run_backtest`."""

    config: RegimeBacktestConfig
    n_signals_total: int
    date_range: tuple[date | None, date | None]
    by_state: list[RegimeStateStats]  # per (label, horizon), overlays=()
    by_state_with_overlay: list[RegimeStateStats]  # per (label, overlay-tuple, horizon)
    baseline: dict[int, ReturnStats]  # horizon -> unconditional baseline


# ── Internal record shape ──────────────────────────────────────────────


@dataclass(frozen=True)
class _SignalRecord:
    signal_date: date
    label: str
    overlays: tuple[str, ...]


def _normalize_overlays(payload: dict[str, Any] | None) -> tuple[str, ...]:
    if not payload:
        return ()
    raw = payload.get("overlays")
    if not isinstance(raw, list):
        return ()
    return tuple(sorted(str(x) for x in raw))


# ── DB loaders ─────────────────────────────────────────────────────────


def load_regime_signals(
    session: Session,
    *,
    start: date | None = None,
    end: date | None = None,
) -> list[_SignalRecord]:
    """Pull all ``INDEX_VOL_REGIME`` signals (optionally bounded) from the DB.

    Returns one record per signal row, ordered by date ascending. Bad/missing
    payloads (no ``label`` string) are silently skipped after a debug log.
    """
    stmt = (
        select(Signal.ts, Signal.payload)
        .where(
            Signal.signal_type == SIGNAL_TYPE,
            Signal.symbol == REGIME_INDEX_SYMBOL,
        )
        .order_by(Signal.ts.asc())
    )
    rows = session.execute(stmt).all()

    out: list[_SignalRecord] = []
    for ts, payload in rows:
        if not isinstance(payload, dict):
            log.debug("regime_backtest.skip_payload", reason="not_dict", ts=str(ts))
            continue
        label = payload.get("label")
        if not isinstance(label, str):
            log.debug("regime_backtest.skip_payload", reason="no_label", ts=str(ts))
            continue
        sig_date = ts.date()
        if start is not None and sig_date < start:
            continue
        if end is not None and sig_date > end:
            continue
        out.append(
            _SignalRecord(
                signal_date=sig_date,
                label=label,
                overlays=_normalize_overlays(payload),
            )
        )
    return out


def load_benchmark_closes(
    session: Session,
    symbol: str,
    *,
    start: date | None = None,
    end: date | None = None,
) -> list[tuple[date, float]]:
    """Pull ``(date, close)`` pairs for ``symbol`` in ascending order.

    The benchmark series is loaded once and reused for every horizon — the
    backtest is O(signals * horizons) lookups on an index map, not on the DB.
    """
    stmt = select(QuoteDaily.date, QuoteDaily.close).where(QuoteDaily.symbol == symbol)
    if start is not None:
        stmt = stmt.where(QuoteDaily.date >= start)
    if end is not None:
        stmt = stmt.where(QuoteDaily.date <= end)
    stmt = stmt.order_by(QuoteDaily.date.asc())
    rows = session.execute(stmt).all()
    return [(d, float(c)) for d, c in rows if c is not None]


# ── Forward-return math ────────────────────────────────────────────────


def forward_returns(
    closes: list[tuple[date, float]],
    signal_dates: list[date],
    horizon_days: int,
) -> np.ndarray:
    """Compute simple H-trading-day forward returns aligned to ``signal_dates``.

    Trading-day offsets are taken against the ordered ``closes`` series. A
    signal whose date is not in ``closes`` is anchored to the *next* trading
    day at or after that date — matching how an EOD signal at 16:50 ET would
    actually be acted on the following session. The return is then
    ``closes[anchor + horizon] / closes[anchor] - 1``.

    Signals whose anchor + horizon exceeds the series are dropped.
    """
    if not closes or not signal_dates or horizon_days <= 0:
        return np.asarray([], dtype=float)

    # Build parallel arrays + a date-to-index map for O(log n) lookups.
    dates_arr = [d for d, _ in closes]
    closes_arr = np.asarray([c for _, c in closes], dtype=float)
    n = len(closes)

    # binary-search anchor: first index whose date >= signal_date
    import bisect

    rets: list[float] = []
    for sig_date in signal_dates:
        anchor = bisect.bisect_left(dates_arr, sig_date)
        target = anchor + horizon_days
        if anchor >= n or target >= n:
            continue
        c0 = closes_arr[anchor]
        c1 = closes_arr[target]
        if c0 <= 0 or not np.isfinite(c0) or not np.isfinite(c1):
            continue
        rets.append(float(c1 / c0 - 1.0))
    return np.asarray(rets, dtype=float)


# ── Backtest assembly ──────────────────────────────────────────────────


def _bucket_dates(records: list[_SignalRecord]) -> tuple[
    dict[str, list[date]],
    dict[tuple[str, tuple[str, ...]], list[date]],
]:
    """Split records into ``(by_label, by_label_and_overlay)`` date buckets."""
    by_label: dict[str, list[date]] = defaultdict(list)
    by_label_overlay: dict[tuple[str, tuple[str, ...]], list[date]] = defaultdict(list)
    for r in records:
        by_label[r.label].append(r.signal_date)
        by_label_overlay[(r.label, r.overlays)].append(r.signal_date)
    return by_label, by_label_overlay


def run_backtest(
    session: Session,
    config: RegimeBacktestConfig | None = None,
) -> RegimeBacktestResult:
    """End-to-end backtest of regime signals against the benchmark.

    Read-only. Returns a fully-formed :class:`RegimeBacktestResult` ready for
    rendering or further analysis.
    """
    cfg = config or RegimeBacktestConfig()
    records = load_regime_signals(session, start=cfg.start, end=cfg.end)
    if not records:
        log.info("regime_backtest.no_signals")
        return RegimeBacktestResult(
            config=cfg,
            n_signals_total=0,
            date_range=(None, None),
            by_state=[],
            by_state_with_overlay=[],
            baseline={h: summarize(np.asarray([], dtype=float)) for h in cfg.horizons_days},
        )

    sig_dates = [r.signal_date for r in records]
    date_range = (min(sig_dates), max(sig_dates))

    closes = load_benchmark_closes(
        session,
        cfg.benchmark_symbol,
        start=cfg.start,
        end=cfg.end,
    )
    if not closes:
        log.warning(
            "regime_backtest.no_benchmark",
            symbol=cfg.benchmark_symbol,
            start=str(cfg.start),
            end=str(cfg.end),
        )

    by_label, by_label_overlay = _bucket_dates(records)
    all_dates = sig_dates  # for unconditional baseline

    by_state: list[RegimeStateStats] = []
    by_state_overlay: list[RegimeStateStats] = []
    baseline: dict[int, ReturnStats] = {}

    for h in cfg.horizons_days:
        baseline_returns = forward_returns(closes, all_dates, h)
        baseline_stats = summarize(baseline_returns)
        baseline[h] = baseline_stats

        for label, dates_for_label in sorted(by_label.items()):
            rets = forward_returns(closes, dates_for_label, h)
            stats = summarize(rets)
            by_state.append(
                RegimeStateStats(
                    label=label,
                    overlays=(),
                    horizon_days=h,
                    stats=stats,
                    lift_vs_baseline=lift_vs_baseline(stats, baseline_stats),
                )
            )

        if cfg.split_by_overlay:
            for (label, overlays), dates_for_combo in sorted(
                by_label_overlay.items(),
                key=lambda kv: (kv[0][0], kv[0][1]),
            ):
                rets = forward_returns(closes, dates_for_combo, h)
                stats = summarize(rets)
                by_state_overlay.append(
                    RegimeStateStats(
                        label=label,
                        overlays=overlays,
                        horizon_days=h,
                        stats=stats,
                        lift_vs_baseline=lift_vs_baseline(stats, baseline_stats),
                    )
                )

    log.info(
        "regime_backtest.complete",
        n_signals=len(records),
        n_states=len({r.label for r in records}),
        horizons=list(cfg.horizons_days),
        symbol=cfg.benchmark_symbol,
    )

    return RegimeBacktestResult(
        config=cfg,
        n_signals_total=len(records),
        date_range=date_range,
        by_state=by_state,
        by_state_with_overlay=by_state_overlay,
        baseline=baseline,
    )


# ── Markdown rendering ─────────────────────────────────────────────────


def _fmt_pct(x: float | None) -> str:
    if x is None:
        return "—"
    return f"{x * 100:+.2f}%"


def _fmt_ir(x: float | None) -> str:
    if x is None:
        return "—"
    return f"{x:+.3f}"


def _fmt_int(x: int | None) -> str:
    if x is None:
        return "—"
    return str(x)


def render_markdown(result: RegimeBacktestResult) -> str:
    """Render a human-readable markdown report from a backtest result.

    The report layout: header → unconditional baseline table → per-state
    (no-overlay) tables per horizon → per-state-with-overlay tables per
    horizon (if enabled). Designed for committing under ``reports/`` and
    referencing from ``docs/decisions/``.
    """
    cfg = result.config
    parts: list[str] = []
    parts.append("# Vol-Regime Backtest\n\n")
    parts.append(f"Benchmark: **{cfg.benchmark_symbol}**.  ")
    if result.date_range[0] and result.date_range[1]:
        parts.append(
            f"Signal window: {result.date_range[0].isoformat()} → "
            f"{result.date_range[1].isoformat()}.  "
        )
    parts.append(f"Signals: **{result.n_signals_total}**.  ")
    parts.append(f"Horizons: {', '.join(f'{h}d' for h in cfg.horizons_days)}.\n\n")

    parts.append("## Unconditional baseline\n\n")
    parts.append("| Horizon | n | mean | median | std | IR | hit | p05 | p95 |\n")
    parts.append("|---:|---:|---:|---:|---:|---:|---:|---:|---:|\n")
    for h in cfg.horizons_days:
        s = result.baseline[h]
        parts.append(
            f"| {h}d | {_fmt_int(s.n)} | {_fmt_pct(s.mean)} | {_fmt_pct(s.median)} | "
            f"{_fmt_pct(s.std)} | {_fmt_ir(s.ir)} | "
            f"{_fmt_pct(s.hit_rate)} | {_fmt_pct(s.p05)} | {_fmt_pct(s.p95)} |\n"
        )
    parts.append("\n")

    parts.append("## Per-state conditional returns\n\n")
    for h in cfg.horizons_days:
        parts.append(f"### Horizon = {h} trading days\n\n")
        parts.append(
            "| State | n | mean | median | IR | hit | lift vs baseline | p05 | p95 |\n"
        )
        parts.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|\n")
        for s in result.by_state:
            if s.horizon_days != h:
                continue
            parts.append(
                f"| {s.label} | {_fmt_int(s.stats.n)} | {_fmt_pct(s.stats.mean)} | "
                f"{_fmt_pct(s.stats.median)} | {_fmt_ir(s.stats.ir)} | "
                f"{_fmt_pct(s.stats.hit_rate)} | {_fmt_pct(s.lift_vs_baseline)} | "
                f"{_fmt_pct(s.stats.p05)} | {_fmt_pct(s.stats.p95)} |\n"
            )
        parts.append("\n")

    if cfg.split_by_overlay and result.by_state_with_overlay:
        parts.append("## Per-(state, overlay) conditional returns\n\n")
        for h in cfg.horizons_days:
            parts.append(f"### Horizon = {h} trading days\n\n")
            parts.append(
                "| State | Overlays | n | mean | IR | hit | lift vs baseline |\n"
            )
            parts.append("|---|---|---:|---:|---:|---:|---:|\n")
            for s in result.by_state_with_overlay:
                if s.horizon_days != h:
                    continue
                ovr = ", ".join(s.overlays) if s.overlays else "(none)"
                parts.append(
                    f"| {s.label} | {ovr} | {_fmt_int(s.stats.n)} | "
                    f"{_fmt_pct(s.stats.mean)} | {_fmt_ir(s.stats.ir)} | "
                    f"{_fmt_pct(s.stats.hit_rate)} | {_fmt_pct(s.lift_vs_baseline)} |\n"
                )
            parts.append("\n")

    parts.append(
        "## Interpretation gate\n\n"
        "A regime state is provisionally **validated** if its conditional mean "
        "differs from the baseline mean by at least 1.5x the baseline std at "
        "the 5d horizon AND the sample size is >= 30. Anything else is a "
        "calibration target, not a signal to act on.\n"
    )
    return "".join(parts)
