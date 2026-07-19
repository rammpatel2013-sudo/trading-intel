"""Tests for trading_intel.backtest.regime_validate — SQLite, no network."""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from trading_intel.backtest.regime_validate import (
    REGIME_INDEX_SYMBOL,
    SIGNAL_TYPE,
    RegimeBacktestConfig,
    forward_returns,
    load_benchmark_closes,
    load_regime_signals,
    render_markdown,
    run_backtest,
)
from trading_intel.memory.models import QuoteDaily, Signal, Ticker

# ── Fixtures ───────────────────────────────────────────────────────────


@pytest.fixture
def session() -> Session:
    engine = create_engine("sqlite://")
    Ticker.__table__.create(engine)
    QuoteDaily.__table__.create(engine)
    Signal.__table__.create(engine)
    with Session(engine) as s:
        s.add(Ticker(symbol="SPY", is_active=True))
        s.commit()
        yield s


def _seed_quotes(
    session: Session,
    *,
    symbol: str = "SPY",
    start: date = date(2026, 1, 5),  # Monday
    n: int = 60,
    base_close: float = 400.0,
    step: float = 1.0,
) -> list[date]:
    """Insert ``n`` trading-day quotes (skip weekends) starting at ``start``.

    Closes are monotonically rising by ``step``. Returns the inserted dates.
    """
    dates: list[date] = []
    cur = start
    while len(dates) < n:
        if cur.weekday() < 5:  # Mon-Fri
            dates.append(cur)
        cur += timedelta(days=1)
    for i, d in enumerate(dates):
        session.add(
            QuoteDaily(
                symbol=symbol,
                date=d,
                open=base_close + i * step,
                high=base_close + i * step,
                low=base_close + i * step,
                close=base_close + i * step,
                volume=1,
            )
        )
    session.commit()
    return dates


def _add_signal(
    session: Session,
    *,
    on: date,
    label: str,
    overlays: list[str] | None = None,
    signal_type: str = SIGNAL_TYPE,
    symbol: str = REGIME_INDEX_SYMBOL,
) -> None:
    session.add(
        Signal(
            ts=datetime.combine(on, datetime.min.time()),
            symbol=symbol,
            signal_type=signal_type,
            payload={
                "label": label,
                "overlays": overlays or [],
                "experimental": True,
            },
            confidence=0.8,
        )
    )


# ── forward_returns: pure-math tests ───────────────────────────────────


def test_forward_returns_known_answer():
    closes = [
        (date(2026, 1, 5), 100.0),
        (date(2026, 1, 6), 101.0),
        (date(2026, 1, 7), 102.0),
        (date(2026, 1, 8), 103.0),
        (date(2026, 1, 9), 104.0),
    ]
    # 1d forward returns from days 0..3 (last has no horizon target).
    rets = forward_returns(closes, [d for d, _ in closes], horizon_days=1)
    assert rets.tolist() == pytest.approx(
        [101 / 100 - 1, 102 / 101 - 1, 103 / 102 - 1, 104 / 103 - 1], abs=1e-12
    )


def test_forward_returns_skips_off_calendar_dates_using_next_session():
    closes = [
        (date(2026, 1, 5), 100.0),  # Mon
        (date(2026, 1, 6), 101.0),  # Tue
        (date(2026, 1, 7), 102.0),  # Wed
    ]
    # Signal fired on Saturday Jan 3 → anchored to Monday Jan 5.
    # 1d forward = close(Tue) / close(Mon) - 1.
    rets = forward_returns(closes, [date(2026, 1, 3)], horizon_days=1)
    assert rets.tolist() == pytest.approx([101 / 100 - 1], abs=1e-12)


def test_forward_returns_drops_when_horizon_exceeds_series():
    closes = [
        (date(2026, 1, 5), 100.0),
        (date(2026, 1, 6), 101.0),
    ]
    # horizon=5 against a 2-row series: every signal drops.
    rets = forward_returns(closes, [date(2026, 1, 5)], horizon_days=5)
    assert rets.size == 0


def test_forward_returns_returns_empty_on_empty_inputs():
    assert forward_returns([], [date(2026, 1, 5)], horizon_days=1).size == 0
    assert forward_returns([(date(2026, 1, 5), 100.0)], [], horizon_days=1).size == 0


def test_forward_returns_rejects_non_positive_horizon():
    closes = [(date(2026, 1, 5), 100.0), (date(2026, 1, 6), 101.0)]
    assert forward_returns(closes, [date(2026, 1, 5)], horizon_days=0).size == 0


# ── DB loaders ─────────────────────────────────────────────────────────


def test_load_benchmark_closes_returns_sorted_pairs(session: Session):
    _seed_quotes(session, n=10)
    rows = load_benchmark_closes(session, "SPY")
    assert len(rows) == 10
    # Strictly increasing dates.
    assert all(rows[i][0] < rows[i + 1][0] for i in range(len(rows) - 1))
    # Closes are 400.0..409.0
    assert rows[0][1] == pytest.approx(400.0)
    assert rows[-1][1] == pytest.approx(409.0)


def test_load_regime_signals_skips_bad_payloads(session: Session):
    quotes = _seed_quotes(session, n=10)
    _add_signal(session, on=quotes[0], label="COMPLACENT")
    # Wrong signal_type — filtered out.
    _add_signal(session, on=quotes[1], label="CRASH_HEDGING", signal_type="OTHER_TYPE")
    # Wrong symbol — filtered out.
    _add_signal(session, on=quotes[2], label="STEALTH_STRESS", symbol="SPY")
    # Missing label — filtered out at payload check.
    session.add(
        Signal(
            ts=datetime.combine(quotes[3], datetime.min.time()),
            symbol=REGIME_INDEX_SYMBOL,
            signal_type=SIGNAL_TYPE,
            payload={"overlays": []},  # no "label"
            confidence=0.5,
        )
    )
    # Valid second signal.
    _add_signal(session, on=quotes[4], label="MIXED")
    session.commit()

    records = load_regime_signals(session)
    labels = [r.label for r in records]
    assert labels == ["COMPLACENT", "MIXED"]


# ── End-to-end backtest ────────────────────────────────────────────────


def test_run_backtest_groups_returns_by_state_and_overlay(session: Session):
    quotes = _seed_quotes(session, n=30, base_close=100.0, step=1.0)
    # Five COMPLACENT signals on days 0..4. With monotone +1 closes, every
    # 1d forward return is exactly (next - cur) / cur.
    for d in quotes[0:5]:
        _add_signal(session, on=d, label="COMPLACENT")
    # Five CRASH_HEDGING signals on days 10..14, two with overlay.
    for d in quotes[10:13]:
        _add_signal(session, on=d, label="CRASH_HEDGING")
    for d in quotes[13:15]:
        _add_signal(session, on=d, label="CRASH_HEDGING", overlays=["VIX_OPTIONS_RICH"])
    session.commit()

    cfg = RegimeBacktestConfig(benchmark_symbol="SPY", horizons_days=(1,))
    result = run_backtest(session, cfg)

    assert result.n_signals_total == 10
    assert result.date_range == (quotes[0], quotes[14])

    by_label = {(s.label, s.horizon_days): s for s in result.by_state}
    assert by_label[("COMPLACENT", 1)].stats.n == 5
    assert by_label[("CRASH_HEDGING", 1)].stats.n == 5

    # Monotone rising series → every state's mean must be strictly positive.
    assert by_label[("COMPLACENT", 1)].stats.mean is not None
    assert by_label[("COMPLACENT", 1)].stats.mean > 0
    assert by_label[("CRASH_HEDGING", 1)].stats.mean > 0

    # Overlay split should give two CRASH_HEDGING rows: one with overlays=()
    # and one with overlays=("VIX_OPTIONS_RICH",).
    overlay_rows = [
        s
        for s in result.by_state_with_overlay
        if s.label == "CRASH_HEDGING" and s.horizon_days == 1
    ]
    assert {tuple(s.overlays) for s in overlay_rows} == {
        (),
        ("VIX_OPTIONS_RICH",),
    }
    # 3 without overlay, 2 with overlay.
    counts = {s.overlays: s.stats.n for s in overlay_rows}
    assert counts[()] == 3
    assert counts[("VIX_OPTIONS_RICH",)] == 2


def test_run_backtest_no_signals_returns_empty_result(session: Session):
    _seed_quotes(session, n=10)
    cfg = RegimeBacktestConfig(benchmark_symbol="SPY", horizons_days=(1, 5))
    result = run_backtest(session, cfg)
    assert result.n_signals_total == 0
    assert result.date_range == (None, None)
    assert result.by_state == []
    # baseline dict still keyed for each horizon — useful for downstream code.
    assert set(result.baseline.keys()) == {1, 5}


def test_run_backtest_baseline_is_unconditional(session: Session):
    quotes = _seed_quotes(session, n=30)
    # All signals labeled the same — baseline and state mean should agree.
    for d in quotes[0:20]:
        _add_signal(session, on=d, label="MIXED")
    session.commit()

    cfg = RegimeBacktestConfig(benchmark_symbol="SPY", horizons_days=(1,))
    result = run_backtest(session, cfg)

    state = next(s for s in result.by_state if s.label == "MIXED" and s.horizon_days == 1)
    baseline = result.baseline[1]
    assert state.stats.mean == pytest.approx(baseline.mean, abs=1e-12)
    # Lift is exactly zero (state == baseline by construction).
    assert state.lift_vs_baseline == pytest.approx(0.0, abs=1e-12)


def test_render_markdown_includes_state_and_baseline(session: Session):
    quotes = _seed_quotes(session, n=20)
    for d in quotes[0:6]:
        _add_signal(session, on=d, label="COMPLACENT")
    for d in quotes[6:12]:
        _add_signal(session, on=d, label="STEALTH_STRESS")
    session.commit()

    cfg = RegimeBacktestConfig(benchmark_symbol="SPY", horizons_days=(1, 5))
    result = run_backtest(session, cfg)
    md = render_markdown(result)

    assert "Vol-Regime Backtest" in md
    assert "Benchmark: **SPY**" in md
    assert "Unconditional baseline" in md
    assert "COMPLACENT" in md
    assert "STEALTH_STRESS" in md
    assert "Horizon = 1 trading days" in md
    assert "Horizon = 5 trading days" in md
