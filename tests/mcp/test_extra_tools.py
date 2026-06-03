"""Unit tests for the extra MCP tool wrappers (one per collected table).

Same harness as ``tests/mcp/test_tools.py``: SQLite in-memory ``Session``,
create only the tables a test touches, insert ORM rows, call the pure tool
function and assert the JSON-serialisable shape. No FastMCP involved.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from trading_intel.config import Settings
from trading_intel.mcp import extra_tools as et
from trading_intel.mcp import tools
from trading_intel.memory.models import (
    DeltaFlow,
    GexRolling,
    GexTerm,
    GreeksSnapshot,
    IndexSkewDaily,
    IntradayFlow,
    LiveGex,
    OiChainEod,
    ResearchNote,
    Signal,
    SkewSnapshot,
    SurfaceReport,
    VixData,
    VixOptionsChain,
    VolRichness,
    WatchlistEntry,
)

_TABLES = (
    OiChainEod,
    GexRolling,
    GexTerm,
    VolRichness,
    VixData,
    IndexSkewDaily,
    VixOptionsChain,
    LiveGex,
    IntradayFlow,
    DeltaFlow,
    ResearchNote,
    SurfaceReport,
    WatchlistEntry,
    Signal,
    GreeksSnapshot,
    SkewSnapshot,
)


def _settings(**kw) -> Settings:
    base = dict(
        CONVEX_EMAIL="x@e.com",
        CONVEX_PASSWORD="x",
        FRED_API_KEY="x",
        DISCORD_WEBHOOK_URL="https://e.com/h",
        DATABASE_URL="postgresql+psycopg://intel:intel@localhost:5432/trading_intel",
        WATCHLIST="SPY,QQQ,NVDA",
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


# ── walls / oi_changes ─────────────────────────────────────────────────


def test_get_walls_empty(session: Session) -> None:
    out = et.get_walls(session, "NVDA")
    assert out == {"symbol": "NVDA", "found": False, "call_wall": None, "put_wall": None}


def test_get_walls_picks_max_gxoi_strike_per_side(session: Session) -> None:
    ts = datetime(2026, 6, 2)
    rows = [
        # calls: 225 has the most gamma-OI
        ("C", 220.0, 5.0),
        ("C", 225.0, 9.0),
        ("C", 230.0, 4.0),
        # puts: 200 has the most
        ("P", 200.0, 8.0),
        ("P", 210.0, 3.0),
    ]
    for cp, strike, gx in rows:
        session.add(
            OiChainEod(
                symbol="NVDA",
                ts=ts,
                expiry=date(2026, 6, 19),
                strike=strike,
                cp=cp,
                dte=17,
                oi=1000,
                oi_change=10,
                gxoi=gx,
                source="convex_eod",
            )
        )
    session.add(GreeksSnapshot(symbol="NVDA", ts=ts, spot=214.0, gex_total=1.0))
    session.commit()

    out = et.get_walls(session, "nvda", dte_max=60)
    assert out["found"] is True
    assert out["call_wall"] == 225.0
    assert out["put_wall"] == 200.0
    assert out["spot"] == 214.0
    assert out["call_top_strikes"][0]["strike"] == 225.0


def test_get_walls_respects_dte_max(session: Session) -> None:
    ts = datetime(2026, 6, 2)
    # the biggest call gamma is far-dated and must be excluded by dte_max
    session.add(
        OiChainEod(
            symbol="NVDA",
            ts=ts,
            expiry=date(2027, 1, 1),
            strike=300.0,
            cp="C",
            dte=200,
            oi=1,
            oi_change=0,
            gxoi=99.0,
            source="convex_eod",
        )
    )
    session.add(
        OiChainEod(
            symbol="NVDA",
            ts=ts,
            expiry=date(2026, 6, 19),
            strike=225.0,
            cp="C",
            dte=17,
            oi=1,
            oi_change=0,
            gxoi=5.0,
            source="convex_eod",
        )
    )
    session.commit()
    out = et.get_walls(session, "NVDA", dte_max=60)
    assert out["call_wall"] == 225.0


def test_get_oi_changes_ranks_by_abs_change(session: Session) -> None:
    ts = datetime(2026, 6, 2)
    data = [("C", 225.0, 500), ("P", 200.0, -1200), ("C", 230.0, 100)]
    for cp, strike, chg in data:
        session.add(
            OiChainEod(
                symbol="NVDA",
                ts=ts,
                expiry=date(2026, 6, 19),
                strike=strike,
                cp=cp,
                dte=17,
                oi=1000,
                oi_change=chg,
                volume=50,
                gxoi=1.0,
                source="convex_eod",
            )
        )
    session.commit()
    out = et.get_oi_changes(session, "NVDA", top=2)
    assert out["found"] is True
    assert out["count"] == 2
    assert out["rows"][0]["strike"] == 200.0  # largest |change|
    assert out["net_call_oi_change"] == 600.0
    assert out["net_put_oi_change"] == -1200.0


# ── gex term ───────────────────────────────────────────────────────────


def test_get_gex_term_empty(session: Session) -> None:
    out = et.get_gex_term(session, "NVDA")
    assert out["found"] is False


def test_get_gex_term_joins_rolling_and_term(session: Session) -> None:
    ts = datetime(2026, 6, 2)
    session.add(
        GexRolling(
            symbol="NVDA",
            ts=ts,
            spot=214.0,
            window_days=180,
            gex_total=123.0,
            n_expirations=2,
            source="convex",
        )
    )
    session.add(
        GexTerm(
            symbol="NVDA", ts=ts, expiration=date(2026, 6, 19), dte=17, gex=80.0, source="convex"
        )
    )
    session.add(
        GexTerm(
            symbol="NVDA", ts=ts, expiration=date(2026, 7, 17), dte=45, gex=43.0, source="convex"
        )
    )
    session.commit()
    out = et.get_gex_term(session, "NVDA")
    assert out["found"] is True
    assert out["gex_total"] == 123.0
    assert out["count"] == 2
    assert out["term"][0]["dte"] == 17


# ── vol richness ───────────────────────────────────────────────────────


def test_get_vol_richness_latest_per_symbol(session: Session) -> None:
    for d, vrp in ((date(2026, 5, 30), 1.0), (date(2026, 6, 2), 2.5)):
        session.add(
            VolRichness(symbol="NVDA", ts=d, horizon_dte=30, iv_atm=0.5, vrp_pts=vrp, label="rich")
        )
    session.commit()
    out = et.get_vol_richness(session, ["NVDA"], settings=_settings(), horizon_dte=30)
    assert out["found"] is True
    assert out["count"] == 1
    assert out["rows"][0]["vrp_pts"] == 2.5  # the newer row
    assert out["rows"][0]["label"] == "rich"


# ── vix / index skew / vix options ─────────────────────────────────────


def test_get_vix_series_and_summary(session: Session) -> None:
    for i, vix in enumerate((18.0, 19.0, 22.0)):
        session.add(
            VixData(
                date=date(2026, 5, 30) + timedelta(days=i),
                vix=vix,
                vvix=100.0 + i,
                vix9d=21.0,
                vix3m=20.0,
                vega_zone="mid",
            )
        )
    session.commit()
    out = et.get_vix(session, days=30)
    assert out["found"] is True
    assert out["count"] == 3
    assert out["summary"]["vix"] == 22.0
    assert out["summary"]["term_9d_3m"] == pytest.approx(1.0)


def test_get_index_skew_series(session: Session) -> None:
    session.add(
        IndexSkewDaily(
            date=date(2026, 6, 2), cboe_skew=140.0, sdex=25.0, vix_tail_hedging_score=0.7
        )
    )
    session.commit()
    out = et.get_index_skew(session, days=30)
    assert out["found"] is True
    assert out["rows"][-1]["cboe_skew"] == 140.0


def test_get_vix_options_call_share(session: Session) -> None:
    ts = date(2026, 6, 2)
    session.add(
        VixOptionsChain(ts=ts, expiration=date(2026, 6, 18), strike=20.0, opt_kind="call", oi=300.0)
    )
    session.add(
        VixOptionsChain(ts=ts, expiration=date(2026, 6, 18), strike=15.0, opt_kind="put", oi=100.0)
    )
    session.commit()
    out = et.get_vix_options(session)
    assert out["found"] is True
    assert out["call_oi_share"] == pytest.approx(0.75)


# ── live gex / intraday / delta flow ───────────────────────────────────


def test_get_live_gex_signs_by_side(session: Session) -> None:
    ts = datetime(2026, 6, 2, 15, 50)
    session.add(LiveGex(symbol="NVDA", ts=ts, strike=215.0, cp="C", gxoi=10.0, spot=214.0))
    session.add(LiveGex(symbol="NVDA", ts=ts, strike=215.0, cp="P", gxoi=4.0, spot=214.0))
    session.commit()
    out = et.get_live_gex(session, "NVDA")
    assert out["found"] is True
    assert out["spot"] == 214.0
    # net at 215 = +10 (call) - 4 (put) = 6
    assert out["by_strike"][0] == {"strike": 215.0, "net_gex": 6.0}


def test_get_intraday_flow_series_and_strikes(session: Session) -> None:
    ts = datetime(2026, 6, 2, 15, 50)
    session.add(
        IntradayFlow(
            symbol="SPX",
            ts=ts,
            expiry=date(2026, 6, 2),
            dte=0,
            strike=5000.0,
            cp="C",
            spot=5005.0,
            gamma_vol=12.0,
            delta_vol=3.0,
            volume=100,
        )
    )
    session.commit()
    out = et.get_intraday_flow(session, "SPX")
    assert out["found"] is True
    assert out["by_strike"][0]["strike"] == 5000.0
    assert len(out["series"]) == 1


def test_get_delta_flow_recent(session: Session) -> None:
    ts = datetime.now() - timedelta(hours=1)
    session.add(
        DeltaFlow(
            symbol="NVDA", ts=ts, spot=214.0, call_notional_all=1000.0, put_notional_all=400.0
        )
    )
    session.commit()
    out = et.get_delta_flow(session, "NVDA", days=5)
    assert out["found"] is True
    assert out["summary"]["net_notional_all"] == 600.0


# ── research / surface / watchlist / signals ───────────────────────────


def test_get_research_note_latest(session: Session) -> None:
    session.add(ResearchNote(symbol="NVDA", as_of=date(2026, 5, 30), note_md="old"))
    session.add(
        ResearchNote(
            symbol="NVDA", as_of=date(2026, 6, 2), note_md="new", sources="10-K", model="qwen2.5:3b"
        )
    )
    session.commit()
    out = et.get_research_note(session, "NVDA")
    assert out["found"] is True
    assert out["note_md"] == "new"
    assert out["sources"] == "10-K"


def test_get_surface_report_latest(session: Session) -> None:
    session.add(
        SurfaceReport(
            symbol="NVDA", as_of=date(2026, 6, 2), report_md="surf", flow_source="flow_snapshots"
        )
    )
    session.commit()
    out = et.get_surface_report(session, "NVDA")
    assert out["found"] is True
    assert out["report_md"] == "surf"


def test_get_research_watchlist_active_only(session: Session) -> None:
    session.add(WatchlistEntry(symbol="CYTK", rationale="cardiac", sentiment=0.6, active=True))
    session.add(WatchlistEntry(symbol="OLD", rationale="stale", active=False))
    session.commit()
    out = et.get_research_watchlist(session, active_only=True)
    assert out["count"] == 1
    assert out["rows"][0]["symbol"] == "CYTK"


def test_get_signals_filter_and_recent(session: Session) -> None:
    recent = datetime.now() - timedelta(hours=2)
    session.add(
        Signal(
            ts=recent, symbol="NVDA", signal_type="options_flow", confidence=0.8, payload={"x": 1}
        )
    )
    session.add(Signal(ts=recent, symbol="AAPL", signal_type="fib", confidence=0.5))
    session.commit()
    out = et.get_signals(session, "NVDA", days=30)
    assert out["count"] == 1
    assert out["rows"][0]["symbol"] == "NVDA"
    assert out["rows"][0]["payload"] == {"x": 1}


# ── enrichment of existing tools ───────────────────────────────────────


def test_gamma_history_includes_dex_vex_chex(session: Session) -> None:
    session.add(
        GreeksSnapshot(
            symbol="NVDA",
            ts=datetime(2026, 6, 2, 6, 45),
            spot=214.0,
            gex_total=300.0,
            dex_total=50.0,
            vex_total=-20.0,
            chex_total=5.0,
            gex_flip=210.0,
            atm_iv=0.6,
        )
    )
    session.commit()
    out = tools.get_gamma_history(session, "NVDA", days=30)
    assert out["found"] is True
    row = out["rows"][-1]
    assert row["dex_total"] == 50.0
    assert row["vex_total"] == -20.0
    assert row["chex_total"] == 5.0


def test_skew_history_full_columns(session: Session) -> None:
    today = date.today()
    session.add(
        SkewSnapshot(
            symbol="NVDA",
            ts=today,
            horizon_dte=30,
            atm_iv=0.55,
            rr_25d=-0.30,
            rr_10d=-0.5,
            bf_25d=0.12,
            bf_10d=0.2,
            rr_25d_pctile_252d=0.15,
            rr_25d_pctile_63d=0.2,
            vix_beta_60d=1.1,
            rr_25d_abnormal=-0.05,
            shift_slide_label="slide",
            label="call-bid",
        )
    )
    session.commit()
    out = tools.get_skew_history(session, "NVDA", horizon_dte=30, days=60)
    assert out["found"] is True
    row = out["rows"][-1]
    assert row["bf_25d"] == 0.12
    assert row["vix_beta_60d"] == 1.1
    assert row["rr_25d_abnormal"] == -0.05
    assert out["summary"]["label"] == "call-bid"
    assert out["summary"]["bias"] == "upside calls bid"
