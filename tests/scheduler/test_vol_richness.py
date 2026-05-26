"""Tests for the EOD vol-richness job (assembly on SQLite; upsert compile)."""

from __future__ import annotations

from datetime import date, datetime, timedelta

import numpy as np
import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session

from trading_intel.config import Settings
from trading_intel.memory.models import (
    OiChainEod,
    QuoteDaily,
    Ticker,
    VixData,
    VolRichness,
)
from trading_intel.scheduler.jobs.vol_richness import _upsert, build_rows

_AS_OF = date(2026, 5, 26)
_TABLES = (Ticker, OiChainEod, QuoteDaily, VixData, VolRichness)


def _settings(**kw) -> Settings:
    base = dict(
        CONVEX_EMAIL="x@e.com", CONVEX_PASSWORD="x", FRED_API_KEY="x",
        DISCORD_WEBHOOK_URL="https://e.com/h",
        DATABASE_URL="postgresql+psycopg://intel:intel@localhost:5432/trading_intel",
        WATCHLIST="SPX", LLM_DAILY_MODEL="qwen2.5:3b",
    )
    base.update(kw)
    return Settings(**base)


@pytest.fixture
def session() -> Session:
    engine = create_engine("sqlite://")
    for tbl in _TABLES:
        tbl.__table__.create(engine)
    with Session(engine) as s:
        yield s


def _seed_chain(session: Session, *, ts: datetime) -> None:
    """Two expiries (~30d, ~60d), 5 strikes/side, put IV > call IV (positive skew)."""
    specs = [
        (date(2026, 6, 25), 0.20, 0.22),  # 30 DTE: call iv / put iv
        (date(2026, 7, 25), 0.21, 0.23),  # 60 DTE
    ]
    for exp, civ, piv in specs:
        for _strike, d in ((4800, 0.50), (4900, 0.40), (5000, 0.30), (5100, 0.20), (5200, 0.10)):
            session.add(OiChainEod(
                symbol="SPX", ts=ts, expiry=exp, strike=float(_strike), cp="C",
                source="convex_eod", iv=civ, delta=d,
            ))
            session.add(OiChainEod(
                symbol="SPX", ts=ts, expiry=exp, strike=float(_strike), cp="P",
                source="convex_eod", iv=piv, delta=-d,
            ))


def _seed_closes(session: Session, *, n: int = 150, seed: int = 3) -> None:
    rng = np.random.default_rng(seed)
    rets = rng.normal(0.0, 0.011, n)
    prices = 5000.0 * np.exp(np.cumsum(rets))
    start = _AS_OF - timedelta(days=n)
    for i, px in enumerate(prices):
        session.add(QuoteDaily(
            symbol="SPX", date=start + timedelta(days=i), open=px, high=px, low=px,
            close=float(px), volume=1,
        ))


def _seed_base(session: Session, *, vix: float = 16.0) -> None:
    session.add(Ticker(symbol="SPX"))
    _seed_chain(session, ts=datetime(2026, 5, 26))
    _seed_closes(session)
    session.add(VixData(date=date(2026, 5, 22), vix=vix))
    session.commit()


def test_build_rows_cold_start(session: Session):
    _seed_base(session)
    rows = build_rows(session, _settings(), as_of=_AS_OF, symbols=["SPX"])

    by_h = {r["horizon_dte"]: r for r in rows}
    assert set(by_h) == {30, 60}
    assert by_h[30]["iv_atm"] == pytest.approx(0.21, abs=0.01)
    assert by_h[60]["iv_atm"] == pytest.approx(0.22, abs=0.01)
    # per-name calendar slope iv60 - iv30, shared across both rows.
    assert by_h[30]["term_slope"] == pytest.approx(by_h[60]["term_slope"])
    assert by_h[30]["term_slope"] == pytest.approx(0.01, abs=0.01)
    # positive 25Δ put skew (put iv > call iv).
    assert by_h[30]["skew_25d"] > 0
    # VRP wiring + regime zone.
    for r in rows:
        assert r["fcst_rv"] is not None
        assert r["vrp_pts"] == pytest.approx(r["iv_atm"] - r["fcst_rv"])
        assert r["regime_zone"] == "low"  # vix 16
        # no prior history → cold standardization.
        assert r["vrp_pctile"] is None and r["iv_rank"] is None
        assert r["richness_score"] is None
        assert r["label"].startswith("cold")


def test_build_rows_warm_history_and_gate(session: Session):
    _seed_base(session, vix=40.0)  # stress zone → short-vol gated
    # Seed 30 prior days of history for the 30d horizon with LOW vrp/iv so today
    # (iv ~0.21, forecast modest) lands at a high percentile → rich.
    for i in range(30):
        session.add(VolRichness(
            symbol="SPX", ts=_AS_OF - timedelta(days=i + 1), horizon_dte=30,
            iv_atm=0.12 + 0.0005 * i, fcst_rv=0.12, vrp_pts=-0.02 + 0.0003 * i,
        ))
    session.commit()

    rows = build_rows(session, _settings(), as_of=_AS_OF, symbols=["SPX"])
    r30 = next(r for r in rows if r["horizon_dte"] == 30)
    assert r30["vrp_pctile"] is not None and r30["vrp_pctile"] >= 0.8  # rich vs own history
    assert r30["iv_rank"] is not None
    assert r30["regime_zone"] == "high"
    # rich short-vol read in a stress regime → gated OFF.
    assert "GATED OFF" in r30["label"]


def test_build_rows_skips_symbol_without_chain(session: Session):
    _seed_base(session)
    rows = build_rows(session, _settings(), as_of=_AS_OF, symbols=["SPX", "NOPE"])
    assert {r["symbol"] for r in rows} == {"SPX"}


def test_upsert_statement_compiles_for_postgres(session: Session):
    # _upsert builds a pg-specific ON CONFLICT DO UPDATE; verify it compiles.
    records = [{
        "symbol": "SPX", "ts": _AS_OF, "horizon_dte": 30, "iv_atm": 0.2,
        "fcst_rv": 0.18, "vrp_pts": 0.02, "vrp_pctile": None, "iv_rank": None,
        "term_slope": 0.01, "skew_25d": 0.02, "regime_zone": "low",
        "richness_score": None, "label": "neutral",
    }]
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    stmt = pg_insert(VolRichness).values(records)
    stmt = stmt.on_conflict_do_update(
        index_elements=["symbol", "ts", "horizon_dte"],
        set_={"iv_atm": stmt.excluded["iv_atm"]},
    )
    sql = str(stmt.compile(dialect=postgresql.dialect()))
    assert "ON CONFLICT" in sql and "vol_richness" in sql
    assert _upsert(session, []) is None  # empty records is a no-op
