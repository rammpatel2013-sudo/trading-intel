"""Tests for the constant-maturity forward-IV job — pure, no Postgres.

``cm_interp`` (the total-variance constant-maturity interpolator) and
``build_rows`` are tested directly against a synthetic chain. ``run`` is exercised
with a fake source and a recording session (the Postgres ``pg_insert`` statements
are captured, not compiled), so no live DB / creds are needed.
"""

from __future__ import annotations

import math
from datetime import date

import numpy as np
import pandas as pd
import pytest
from sqlalchemy.dialects.postgresql.dml import Insert

from trading_intel.errors import DataSourceError
from trading_intel.scheduler.jobs.iv_tenor_snapshots import (
    build_rows,
    cm_interp,
    run,
)

_AS_OF = date(2026, 6, 23)

# Per-expiry ATM IV (decimal); wings are offset from this to create a skew.
_EXPIRIES = {10: 0.20, 40: 0.18, 100: 0.16}
_CALL_DELTAS = (0.50, 0.40, 0.25, 0.15, 0.08)
_PUT_DELTAS = (-0.50, -0.40, -0.25, -0.15, -0.08)


def _chain(as_of: date = _AS_OF) -> pd.DataFrame:
    """A synthetic delta-resolved chain spanning 10 / 40 / 100 DTE, both wings.

    Puts carry a small skew premium (downside richer) so the 25Δ risk reversal is
    positive — the usual equity shape — and we can assert on the sign downstream.
    """
    rows: list[dict] = []
    for dte, atm in _EXPIRIES.items():
        exp = pd.Timestamp(as_of) + pd.Timedelta(days=dte)
        for i, d in enumerate(_CALL_DELTAS):
            rows.append({
                "expiration": exp, "strike": 400 + i, "opt_kind": "call",
                "delta": d, "iv": atm + 0.01 * i,
            })
        for i, d in enumerate(_PUT_DELTAS):
            rows.append({
                "expiration": exp, "strike": 390 - i, "opt_kind": "put",
                "delta": d, "iv": atm + 0.02 * i,  # steeper put wing
            })
    return pd.DataFrame(rows)


# ── cm_interp ───────────────────────────────────────────────────────────


def test_cm_interp_brackets_and_total_variance():
    dte = np.array([10, 40, 100], dtype=float)
    iv = np.array([0.20, 0.18, 0.16], dtype=float)
    out = cm_interp(dte, iv, 30)
    assert out is not None
    # 30 DTE sits between 10 and 40 — read must fall between their vols.
    assert 0.18 <= out <= 0.20
    # Total-variance interpolation: reconstruct the expected value explicitly.
    var = iv**2 * dte / 365.0
    var_t = float(np.interp(30, dte, var))
    expected = math.sqrt(var_t / (30 / 365.0))
    assert out == pytest.approx(expected)


def test_cm_interp_no_extrapolation_outside_span():
    dte = np.array([10, 40], dtype=float)
    iv = np.array([0.20, 0.18], dtype=float)
    assert cm_interp(dte, iv, 90) is None  # beyond the last node
    assert cm_interp(dte, iv, 5) is None    # before the first node


def test_cm_interp_needs_two_finite_points():
    assert cm_interp(np.array([30.0]), np.array([0.2]), 30) is None
    assert cm_interp(np.array([30.0, 60.0]), np.array([np.nan, 0.2]), 45) is None


# ── build_rows ──────────────────────────────────────────────────────────


class _Settings:
    def __init__(self) -> None:
        self.iv_tenor_symbols = ["QQQ"]
        self.iv_tenor_dtes = [30, 90]
        self.iv_tenor_deltas = [15.0, 25.0]


class _FakeSource:
    def __init__(self, frames: dict, spot: float = 444.0) -> None:
        self.frames = frames
        self._spot = spot
        self.calls: list[str] = []

    def chain_long(self, symbol: str, **_: object) -> pd.DataFrame:
        self.calls.append(symbol)
        val = self.frames[symbol]
        if isinstance(val, Exception):
            raise val
        return val

    def spot(self, symbol: str) -> float:
        return self._spot


def test_build_rows_emits_one_row_per_tenor_with_skew():
    rows = build_rows(_FakeSource({"QQQ": _chain()}), _Settings(), as_of=_AS_OF)
    assert {r["tenor_dte"] for r in rows} == {30, 90}
    by_tenor = {r["tenor_dte"]: r for r in rows}

    for r in rows:
        assert r["symbol"] == "QQQ"
        assert r["spot"] == 444.0
        assert r["n_expiries"] == 3
        # All five IV fields interpolated.
        for k in ("iv_atm", "iv_call_15d", "iv_put_15d", "iv_call_25d", "iv_put_25d"):
            assert r[k] is not None and 0.0 < r[k] < 1.0
        # Equity skew: the downside (put) wing is bid over the same-delta call.
        assert r["iv_put_25d"] > r["iv_call_25d"]
        assert r["iv_put_15d"] > r["iv_call_15d"]

    # Term structure declines with tenor (front 20% -> back 16% ATM).
    assert by_tenor[30]["iv_atm"] > by_tenor[90]["iv_atm"]


def test_build_rows_skips_empty_and_failed_symbols():
    src = _FakeSource({"QQQ": pd.DataFrame(), "SPY": DataSourceError("down")})
    settings = _Settings()
    settings.iv_tenor_symbols = ["QQQ", "SPY"]
    assert build_rows(src, settings, as_of=_AS_OF) == []
    assert src.calls == ["QQQ", "SPY"]


# ── run orchestration ────────────────────────────────────────────────────


class _RecordingSession:
    def __init__(self) -> None:
        self.executed: list = []
        self.commits = 0

    def execute(self, stmt) -> None:
        self.executed.append(stmt)

    def commit(self) -> None:
        self.commits += 1


def test_run_seeds_tickers_upserts_and_commits():
    source = _FakeSource({"QQQ": _chain()})
    session = _RecordingSession()
    run(session, source, settings=_Settings())

    assert source.calls == ["QQQ"]
    inserts = [e for e in session.executed if isinstance(e, Insert)]
    # One ticker-seed insert + one snapshot upsert.
    assert len(inserts) == 2
    tables = {ins.table.name for ins in inserts}
    assert tables == {"tickers", "iv_tenor_snapshots"}
    assert session.commits == 1
