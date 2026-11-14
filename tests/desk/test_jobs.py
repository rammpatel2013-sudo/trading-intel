"""Desk brief end to end on SQLite with fake vendors (2026-11-14)."""

from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from trading_intel.clients.equity_data import EquityDataSource
from trading_intel.desk import store
from trading_intel.mcp import desk_tools as dt
from trading_intel.memory.models import FinraDixDaily
from trading_intel.memory.models_desk import DESK_TABLES, DeskPick, DeskUndDaily, FinraShortVolume
from trading_intel.memory.models_equity import EQUITY_TABLES
from trading_intel.scheduler.jobs import desk_backfill, desk_brief, desk_snapshot, finra_dix

from .synth import DAYS, END, FakeApp, FakeCV, FakeFinra


@pytest.fixture()
def session():
    eng = create_engine("sqlite://")
    for t in (*EQUITY_TABLES, *DESK_TABLES, FinraDixDaily):
        t.__table__.create(eng)
    with Session(eng) as s:
        yield s


def _brief(session, tmp_path, cv=None, split=True):
    cv = cv or FakeCV()
    src = EquityDataSource(cv, per_sec=1e9)
    for d in [d for d in DAYS if d <= END][-12:]:
        store.save_finra(session, d, FakeFinra().short_volume_all(d))
    store.save_und(
        session, END, FakeApp(split).und_snapshot(), datetime(2026, 11, 13, 17, 15), split_ok=split
    )
    return (
        desk_brief.run(session, object(), cv=cv, src=src, day=END, page_dir=tmp_path, force=True),
        cv,
    )


def test_brief_picks_the_right_names(session, tmp_path):
    res, _ = _brief(session, tmp_path)
    assert res["top_pick"] == "AAA" and res["two_leg"] == 2
    p = store.load_brief(session, END).payload
    assert [r["symbol"] for r in p["two_leg"]] == ["AAA", "BBB"]
    assert p["div_a"] == ["CCC"] and p["div_b"] == ["DDD"]
    scr = {r["symbol"] for r in p["screen"]}
    assert scr == {"AAA", "BBB", "CCC", "DDD"}  # EEE down today, FFF up 5D, LOW under $5
    assert (
        p["etf_prints"][0]["symbol"] == "SOXS" and round(p["etf_prints"][0]["implied"], 1) == -2.3
    )
    assert p["coverage"]["finra"] and p["coverage"]["options"] and p["coverage"]["gamma"]
    assert p["top_card"]["pe_ttm"] == 18.0 and p["hold_weaken"]["holds"]
    assert len(p["matters"]) >= 2 and len(p["desk_read"]) >= 2
    html = (tmp_path / "desk_brief.html").read_text(encoding="utf-8")
    assert "Desk brief" in html and "AAA" in html and "viewport" in html
    roles = {(r.symbol, r.role) for r in session.execute(select(DeskPick)).scalars()}
    assert ("AAA", "top") in roles and ("DDD", "div_b") in roles
    res2, _ = _brief(session, tmp_path)  # idempotent
    assert res2["top_pick"] == "AAA"
    assert (
        session.execute(select(DeskPick).where(DeskPick.role == "top")).scalars().all().__len__()
        == 1
    )


def test_brief_without_options_split(session, tmp_path):
    res, _ = _brief(session, tmp_path, split=False)
    p = store.load_brief(session, END).payload
    assert not p["coverage"]["options"] and res["two_leg"] == 0 and res["top_pick"] is None
    assert {r["symbol"] for r in p["dark_only"]} == {"AAA", "BBB", "CCC"}
    assert "Options split not collected" in (tmp_path / "desk_brief.html").read_text(
        encoding="utf-8"
    )


def test_snapshot_job(session):
    out = desk_snapshot.run(
        session, object(), app=FakeApp(), day=END, now=datetime(2026, 11, 13, 17, 15)
    )
    assert out["split_ok"] and out["rows"] == 10
    out2 = desk_snapshot.run(
        session, object(), app=FakeApp(split=False), day=END, now=datetime(2026, 11, 13, 18, 30)
    )
    assert not out2["split_ok"]
    row = session.execute(select(DeskUndDaily).where(DeskUndDaily.symbol == "AAA")).scalar_one()
    assert row.call_buy is None  # the after-reset zeros are stored as missing
    assert desk_snapshot.run(session, object(), app=FakeApp(), day=END + timedelta(days=1))[
        "skipped"
    ]


def test_backfill_and_mcp(session, tmp_path, monkeypatch):
    monkeypatch.setattr(desk_brief, "COCKPIT_DATA", tmp_path)
    monkeypatch.setattr(desk_backfill, "eastern_now", lambda: datetime(2026, 11, 14, 13, 0))
    cv = FakeCV()
    out = desk_backfill.run(
        session,
        object(),
        sessions=15,
        app=FakeApp(),
        finra=FakeFinra(),
        cv=cv,
        src=EquityDataSource(cv, per_sec=1e9),
    )
    assert (
        out["day"] == END.isoformat()
        and out["finra_days_added"] == 15
        and out["und_split_ok"] is False
    )
    assert out["brief"]["screen"] == 4 and (tmp_path / "desk_brief.html").exists()
    rec = dt.record_ttwd(
        session,
        END.isoformat(),
        "AAA",
        ["AAA", "BBB"],
        ["CCC"],
        ["DDD"],
        ["AAA", "BBB", "CCC", "DDD", "FFF"],
    )
    assert rec["saved"] == 10  # 1 top + 2 two-leg + 1 + 1 divergence + 5 screen
    sym = dt.desk_symbol(session, "AAA")
    assert sym["dark_pool"][0]["pass"] and sym["in_latest_brief"]["date"] == END.isoformat()
    b = dt.desk_brief(session)
    assert b["date"] == END.isoformat() and "AAA" in b["screen"]
    cv.today = DAYS[-1]  # later: bars exist after END -> picks get scored
    desk_brief.refresh_bars(
        session,
        EquityDataSource(cv, per_sec=1e9),
        ["AAA", "BBB", "CCC", "DDD", "FFF", "SPY"],
        DAYS[-1],
    )
    sc = desk_brief.scorecard(session, DAYS[-1])
    assert sc["stats"]["ttwd"]["top"][5]["excess"] > 0


def test_finra_dix_keeps_every_symbol(session):
    finra_dix.run(
        session,
        object(),
        cv=FakeCV(),
        short={"AAA": (700.0, 1000.0), "ZZZ": (10.0, 100.0)},
        day=END,
    )
    syms = set(session.execute(select(FinraShortVolume.symbol)).scalars())
    assert syms == {"AAA", "ZZZ"}
    assert session.execute(select(FinraDixDaily)).scalars().first() is not None


def test_server_registers_desk_tools():
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
    assert {"get_desk_brief", "get_desk_symbol", "get_desk_scorecard", "record_ttwd_picks"} <= names
