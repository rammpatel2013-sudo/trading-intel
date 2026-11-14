"""End to end on SQLite: weekly job -> backfill-style bars -> daily job -> report + MCP reads."""

from datetime import timedelta

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from trading_intel.clients.equity_data import EquityDataSource
from trading_intel.equity import pipeline as pl
from trading_intel.mcp import equity_tools as et
from trading_intel.memory.models_equity import (
    EQUITY_TABLES,
    EquityBar,
    EquityCallIv,
    EquityCandidate,
    EquityFactorReturn,
    EquityGateDaily,
    EquityMember,
    EquityStockFactor,
    ExternalNhNl,
)
from trading_intel.scheduler.jobs import equity_daily, equity_weekly

from .synth import END, World


@pytest.fixture(scope="module")
def world():
    return World(n_sp500=70, n_ijh=30, days=650)


@pytest.fixture()
def session():
    eng = create_engine("sqlite://")
    for m in EQUITY_TABLES:
        m.__table__.create(eng)
    with Session(eng) as s:
        yield s


def _nh(world: World):
    return [
        {
            "date": d.isoformat(),
            "total_issues": 4700,
            "nyse_new_highs": 40,
            "nyse_new_lows": 20,
            "nyse_issues": 2000,
            "nasdaq_new_highs": 30,
            "nasdaq_new_lows": 25,
            "nasdaq_issues": 2500,
        }
        for d in world.dates[-60:]
    ]


def test_weekly_then_daily(session, world, monkeypatch, tmp_path):
    src = EquityDataSource(world, per_sec=1e9)
    res = equity_weekly.run(session, src=src, day=END)
    assert res["members"] == 100 and res["new_names"] == 100
    members = session.execute(select(EquityMember)).scalars().all()
    assert {m.symbol for m in members if m.end_date is not None} == {world.gone}
    # history for everyone (what the backfill does) + market caps
    ivs = pl.store.load_members(session)
    pl.refresh_bars(session, src, pl.target_symbols(ivs), END)
    pl.fill_market_caps(session)
    assert session.execute(select(func.count()).select_from(EquityBar)).scalar() > 60_000
    mc = (
        session.execute(
            select(EquityBar.market_cap)
            .where(EquityBar.symbol == world.sp500[3])
            .order_by(EquityBar.date.desc())
        )
        .scalars()
        .first()
    )
    assert mc and mc > 1e9
    monkeypatch.setattr("trading_intel.clients.thetradingtools.fetch_nhnl", lambda **k: _nh(world))
    monkeypatch.chdir(tmp_path)
    out = equity_daily.run(session, src=src, cv=world, day=END, page_dir=tmp_path)
    assert out["gate"]["color"] in ("green", "amber", "red")
    page = tmp_path / "equity.html"
    assert out["page"] == str(page) and "Is it safe to buy stocks?" in page.read_text(encoding="utf-8")
    assert session.execute(select(func.count()).select_from(EquityFactorReturn)).scalar() >= 10
    assert session.execute(select(func.count()).select_from(EquityStockFactor)).scalar() >= 100
    assert session.execute(select(func.count()).select_from(ExternalNhNl)).scalar() > 0
    cands = session.execute(select(EquityCandidate)).scalars().all()
    assert any(c.list_name == "breakout" and c.symbol == world.all[10] for c in cands)
    assert session.execute(select(func.count()).select_from(EquityCallIv)).scalar() > 0
    g = session.execute(select(EquityGateDaily)).scalars().one()
    assert g.date == END and g.votes and "_inputs" in g.votes
    # idempotent re-run (rule 5)
    equity_daily.run(session, src=src, cv=world, day=END, page_dir=tmp_path)
    assert session.execute(select(func.count()).select_from(EquityGateDaily)).scalar() == 1
    # MCP reads
    eg = et.equity_gate(session)
    assert eg["found"] and eg["votes"] and eg["color"] == g.color
    ec = et.equity_candidates(session)
    assert ec["found"] and "leader" in ec["lists"]
    sf = et.stock_factors(session, world.sp500[3])
    assert sf["found"] and set(sf["exposures"]) >= {"momentum", "beta"}
    ft = et.factor_tides(session, 5)
    assert ft["found"] and "momentum" in ft["styles_pct"]
    bk = et.book(
        session,
        {world.sp500[3]: 5000, world.sp500[4]: 5000, "IJH": 10000},
        etf_lookup=lambda s: world.fmp("etf/holdings", {"symbol": s}),
    )
    assert bk["found"] and bk["covered_weight"] > 0.9 and bk["predicted_vol_pct"] > 0


def test_daily_skips_weekend_and_stale(session, world, tmp_path):
    src = EquityDataSource(world, per_sec=1e9)
    assert (
        equity_daily.run(session, src=src, cv=world, day=END + timedelta(days=1), page_dir=tmp_path)[
            "skipped"
        ]
        == "not_a_trading_session"
    )
    assert (
        equity_daily.run(session, src=src, cv=world, day=END, page_dir=tmp_path)["skipped"]
        == "no_universe"
    )
    equity_weekly.run(session, src=src, day=END)
    nxt = END + timedelta(days=3)  # Monday: vendor still on Friday
    assert (
        equity_daily.run(session, src=src, cv=world, day=nxt, page_dir=tmp_path)["skipped"]
        == "source_stale"
    )


def test_server_registers_equity_tools():
    pytest.importorskip("fastmcp")
    import asyncio

    from sqlalchemy.orm import sessionmaker

    from trading_intel.config import Settings
    from trading_intel.mcp.server import build_server

    class _LLM:
        pass

    mcp = build_server(
        Settings(), llm=_LLM(), session_factory=sessionmaker(bind=create_engine("sqlite://"))
    )
    lister = getattr(mcp, "list_tools", None) or mcp.get_tools
    tools = asyncio.run(lister())
    names = {t.name for t in tools} if not isinstance(tools, dict) else set(tools)
    assert {
        "get_equity_gate",
        "get_equity_candidates",
        "get_stock_factors",
        "get_factor_tides",
        "check_book",
    } <= names
