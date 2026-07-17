"""Tests for the per-STRIKE vol-surface snapshot job — pure, no Postgres.

``build_rows`` is tested against a canned ``chain_long`` DataFrame (call+put rows per
strike) + a fake source: it should pick the OTM wing per (expiry, strike), store the signed
delta, and drop strikes outside the near-money delta band. ``run`` uses a recording session.
"""

from __future__ import annotations

from datetime import date

import pandas as pd
from sqlalchemy.dialects.postgresql.dml import Insert

from trading_intel.scheduler.jobs import surface_snapshots

_AS_OF = date(2026, 7, 17)
_E1 = date(2026, 7, 24)  # 7 dte
_E2 = date(2026, 8, 21)  # 35 dte
_SPOT = 7500.0

# strike -> (call_delta, put_delta, call_iv, put_iv)
_SPECS = {
    _E1: {
        7400: (0.70, -0.30, 0.145, 0.205),
        7500: (0.50, -0.50, 0.130, 0.132),
        7600: (0.30, -0.70, 0.110, 0.190),
        9000: (0.02, -0.98, 0.300, 0.400),  # out of delta band -> dropped
    },
    _E2: {
        7400: (0.72, -0.28, 0.150, 0.200),
        7500: (0.50, -0.50, 0.140, 0.141),
        7600: (0.32, -0.68, 0.120, 0.185),
    },
}


def _chain() -> pd.DataFrame:
    rows = []
    for exp, strikes in _SPECS.items():
        for k, (cd, pdel, civ, piv) in strikes.items():
            rows.append(
                {"symbol": "SPX", "expiration": exp.isoformat(), "strike": k,
                 "opt_kind": "C", "delta": cd, "iv": civ}
            )
            rows.append(
                {"symbol": "SPX", "expiration": exp.isoformat(), "strike": k,
                 "opt_kind": "P", "delta": pdel, "iv": piv}
            )
    return pd.DataFrame(rows)


class _FakeSource:
    def chain_long(self, symbol, **kw):  # noqa: ANN001, ANN003
        return _chain()

    def spot(self, symbol):  # noqa: ANN001
        return _SPOT


class _Settings:
    surface_symbols = ["SPX"]
    SURFACE_EXPIRIES = 12


def test_build_rows_picks_otm_wing_per_strike_and_drops_deep_wings():
    rows = surface_snapshots.build_rows(_FakeSource(), _Settings(), as_of=_AS_OF, symbols=["SPX"])

    # 3 in-band strikes x 2 expiries = 6 rows; the 9000 out-of-band strike is dropped.
    assert len(rows) == 6
    assert all(r["strike"] != 9000 for r in rows)

    def pick(k, ed):  # noqa: ANN001, ANN202
        return next(r for r in rows if r["strike"] == k and r["expiry_date"] == ed)

    # 7400 < spot -> OTM put wing (delta<0); 7600 > spot -> OTM call wing; 7500 == spot -> call.
    assert abs(pick(7400, _E1)["iv"] - 0.205) < 1e-9 and pick(7400, _E1)["delta"] < 0
    assert abs(pick(7600, _E1)["iv"] - 0.110) < 1e-9 and pick(7600, _E1)["delta"] > 0
    assert abs(pick(7500, _E1)["iv"] - 0.130) < 1e-9
    assert pick(7500, _E1)["dte"] == 7 and pick(7500, _E2)["dte"] == 35
    assert all(r["symbol"] == "SPX" and r["ts"] == _AS_OF and r["spot"] == _SPOT for r in rows)


class _RecordingSession:
    def __init__(self) -> None:
        self.executed: list = []
        self.commits = 0

    def execute(self, stmt) -> None:
        self.executed.append(stmt)

    def commit(self) -> None:
        self.commits += 1


def test_run_seeds_tickers_upserts_and_commits(monkeypatch):
    monkeypatch.setattr(
        surface_snapshots,
        "build_rows",
        lambda *a, **k: [
            {
                "symbol": "SPX", "ts": _AS_OF, "expiry_date": _E1, "dte": 7,
                "strike": 7500.0, "iv": 0.13, "delta": 0.5, "spot": _SPOT,
            }
        ],
    )
    session = _RecordingSession()
    n = surface_snapshots.run(session, _FakeSource(), settings=_Settings())

    assert n == 1
    inserts = [e for e in session.executed if isinstance(e, Insert)]
    assert {ins.table.name for ins in inserts} == {"tickers", "surface_snapshots"}
    assert session.commits == 1
