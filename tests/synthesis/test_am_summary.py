"""Tests for the AM-report context builder + markdown renderer."""

from __future__ import annotations

from datetime import date, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from trading_intel.config import Settings
from trading_intel.memory.models import (
    FlowSnapshot,
    GreeksChain,
    GreeksSnapshot,
    IndexSkewDaily,
    IntradayFlow,
    SkewSnapshot,
    WatchlistEntry,
)
from trading_intel.memory.retrieval import ChunkHit
from trading_intel.synthesis import am_summary as am
from trading_intel.synthesis.am_summary import (
    AmContext,
    IndexSkewRead,
    MarketRead,
    ResearchTicker,
    SkewExtreme,
    TickerRegime,
    build_am_context,
    build_am_knowledge_query,
    render_am_markdown,
    render_am_markdown_fallback,
)

_TABLES = (
    GreeksSnapshot, GreeksChain, WatchlistEntry, FlowSnapshot,
    IntradayFlow, IndexSkewDaily, SkewSnapshot,
)


class StubLLM:
    """LLMProvider stub returning a fixed narrative."""

    def __init__(self, text: str = "Stub regime narrative.") -> None:
        self._text = text
        self.calls: list[str] = []

    def complete(self, prompt: str, *, model=None, max_tokens=2048) -> str:
        self.calls.append(prompt)
        return self._text

    def chat(self, messages, *, model=None, max_tokens=2048) -> str:
        return self._text

    def embed(self, text, *, model=None):
        return [[0.0]]


class FailingLLM(StubLLM):
    def complete(self, prompt: str, *, model=None, max_tokens=2048) -> str:
        raise RuntimeError("ollama down")


def _settings(**kw) -> Settings:
    base = dict(
        CONVEX_EMAIL="x@e.com",
        CONVEX_PASSWORD="x",
        FRED_API_KEY="x",
        DISCORD_WEBHOOK_URL="https://e.com/h",
        DATABASE_URL="postgresql+psycopg://intel:intel@localhost:5432/trading_intel",
        WATCHLIST="SPY,QQQ",
        LLM_DAILY_MODEL="qwen2.5:3b",
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


def _seed(session: Session) -> None:
    session.add_all(
        [
            GreeksSnapshot(
                symbol="SPY", ts=datetime(2026, 5, 22, 6, 45), spot=730.0,
                gex_total=1.0e7, gex_flip=725.0, atm_iv=0.18, source="convex",
            ),
            GreeksSnapshot(
                symbol="SPY", ts=datetime(2026, 5, 23, 6, 45), spot=735.0,
                gex_total=1.5e7, gex_flip=728.0, atm_iv=0.19, source="convex",
            ),
            GreeksSnapshot(
                symbol="QQQ", ts=datetime(2026, 5, 23, 6, 45), spot=480.0,
                gex_total=-5.0e6, gex_flip=485.0, atm_iv=0.21, source="convex",
            ),
        ]
    )
    session.add(
        WatchlistEntry(
            symbol="NVDA", source_doc_id=None, rationale="AI capex cycle still expanding",
            sentiment=0.6, confidence=0.8, themes=["AI capex"],
            added_at=datetime(2026, 5, 23, 5, 0), active=True,
        )
    )
    session.add(
        FlowSnapshot(
            symbol="SPY", ts=datetime(2026, 5, 23, 10, 0), source="convex",
            call_notional=3.0e6, put_notional=1.0e6, net_premium=2.0e6,
            put_call_ratio=0.33, tilt="offensive (call-heavy)", n_prints=5,
            top_prints=[], packages=[],
        )
    )
    today = datetime.now().replace(hour=10, minute=0, second=0, microsecond=0)
    session.add(
        IntradayFlow(
            symbol="SPY", ts=today, source="convex", expiry=today.date(), dte=0,
            strike=735.0, cp="C", spot=735.0, iv=0.19, gamma=0.01, delta=0.5,
            vanna=0.02, charm=0.03, volume=1000, volume_interval=100,
            gamma_vol=1234.0, delta_vol=500.0, vanna_vol=222.0, charm_vol=333.0,
            gamma_vol_iv=12.0, delta_vol_iv=5.0, vanna_vol_iv=2.0, charm_vol_iv=3.0,
        )
    )
    session.commit()


def test_build_context_unions_static_and_research(session: Session):
    _seed(session)
    ctx = build_am_context(session, _settings())

    symbols = [t.symbol for t in ctx.watchlist]
    assert symbols == ["SPY", "QQQ", "NVDA"]
    assert ctx.research_symbols == ["NVDA"]

    by_sym = {t.symbol: t for t in ctx.watchlist}
    assert by_sym["NVDA"].is_research is True
    assert by_sym["SPY"].is_research is False
    assert by_sym["SPY"].spot == pytest.approx(735.0)
    assert by_sym["SPY"].gex_dir == "up"
    assert by_sym["SPY"].flow_tilt == "offensive (call-heavy)"


def test_build_context_research_and_market(session: Session):
    _seed(session)
    ctx = build_am_context(session, _settings())

    assert len(ctx.research) == 1
    r = ctx.research[0]
    assert r.symbol == "NVDA"
    assert "AI capex" in r.rationale
    assert r.sentiment == pytest.approx(0.6)

    market_syms = {m.symbol for m in ctx.market}
    assert market_syms == {"SPY", "QQQ"}
    spy = next(m for m in ctx.market if m.symbol == "SPY")
    assert spy.gamma_vol == pytest.approx(1234.0)


def _ctx() -> AmContext:
    return AmContext(
        as_of=date(2026, 5, 23),
        market=[
            MarketRead(
                symbol="SPY", spot=735.0, gex_total=1.5e7, gamma_regime="long gamma",
                atm_iv=0.19, gex_dir="up", gamma_vol=1234.0, vanna_vol=222.0, charm_vol=333.0,
            )
        ],
        research=[
            ResearchTicker(
                symbol="NVDA", sentiment=0.6, confidence=0.8, themes="AI capex",
                rationale="AI capex cycle", source_doc_id=None,
            )
        ],
        watchlist=[
            TickerRegime(
                symbol="NVDA", is_research=True, spot=900.0, gex_total=1.0e6, gex_dir="up",
                gex_chg_wk=2.0e5, gamma_regime="long gamma", gex_flip=890.0, atm_iv=0.45,
                call_put_oi=1.2, vol_oi=0.5, skew=0.03, call_wall=950.0, put_wall=850.0,
                flow_tilt="offensive", net_premium=1.0e6, put_call_ratio=0.5,
            )
        ],
        static_symbols=["SPY"],
        research_symbols=["NVDA"],
    )


def test_render_with_llm_includes_narrative_and_tables():
    llm = StubLLM("MARKET IS CALM TODAY")
    md, meta = render_am_markdown(_ctx(), llm, _settings())
    assert "MARKET IS CALM TODAY" in md
    assert "## Data" in md
    assert "## Market regime" in md
    assert "NVDA" in md
    assert meta["used_llm"] is True
    assert meta["model"] == "qwen2.5:3b"
    assert meta["research_symbols"] == ["NVDA"]
    assert len(llm.calls) == 1


def test_render_falls_back_when_llm_fails():
    md, meta = render_am_markdown(_ctx(), FailingLLM(), _settings())
    assert meta["used_llm"] is False
    assert meta["model"] is None
    assert "deterministic regime tables" in md
    assert "## Market regime" in md


def test_fallback_is_self_contained():
    md = render_am_markdown_fallback(_ctx())
    assert md.startswith("# AM Report — 2026-05-23")
    assert "FlashAlpha rule 4" in md
    assert "NVDA" in md


# ── Methodology-grounding (search_knowledge wiring) ────────────────────


def test_build_knowledge_query_describes_regime():
    ctx = AmContext(
        as_of=date(2026, 5, 23),
        market=[
            MarketRead(
                symbol="SPY", spot=735.0, gex_total=1.5e7, gamma_regime="long gamma",
                atm_iv=0.19, gex_dir="up", gamma_vol=None, vanna_vol=None, charm_vol=None,
            )
        ],
        index_skew=IndexSkewRead(cboe_skew=140.0),
        skew_extremes=[
            SkewExtreme(symbol="NVDA", rr_25d=0.04, rr_pctile_252d=0.97,
                        label=None, bucket="put_bid"),
        ],
    )
    q = build_am_knowledge_query(ctx)
    assert "SPY long gamma" in q
    assert "net GEX up" in q
    assert "index skew" in q.lower()
    assert "put bid" in q
    for banned in ("buy", "sell", "target", "expect"):
        assert banned not in q.lower()


def test_render_without_session_skips_kb():
    llm = StubLLM("CALM")
    md, meta = render_am_markdown(_ctx(), llm, _settings())
    assert meta["n_kb_hits"] == 0
    assert meta["kb_sources"] == []
    assert "_Research grounding:" not in md
    assert "(no reference notes found)" in llm.calls[0]


def test_render_grounds_narrative_when_kb_hits(monkeypatch):
    hits = [
        ChunkHit(chunk_id=1, document_id=7, title="dealer_gamma_playbook",
                 text="In long-gamma regimes dealers dampen realized vol.", distance=0.1),
        ChunkHit(chunk_id=2, document_id=7, title="dealer_gamma_playbook",
                 text="Below the flip, hedging amplifies moves.", distance=0.2),
        ChunkHit(chunk_id=3, document_id=9, title="skew_term_notes",
                 text="Steep put-wing skew signals downside hedging demand.", distance=0.3),
    ]
    monkeypatch.setattr(am, "retrieve_chunks", lambda *a, **k: hits)

    llm = StubLLM("REGIME NARRATIVE")
    md, meta = render_am_markdown(_ctx(), llm, _settings(), session=object())

    prompt = llm.calls[0]
    assert "dampen realized vol" in prompt
    assert "downside hedging demand" in prompt
    assert meta["n_kb_hits"] == 2
    assert meta["kb_sources"] == ["dealer_gamma_playbook", "skew_term_notes"]
    assert "_Research grounding: dealer_gamma_playbook, skew_term_notes._" in md


def test_render_degrades_when_kb_retrieval_raises(monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("pgvector down")

    monkeypatch.setattr(am, "retrieve_chunks", _boom)
    llm = StubLLM("STILL FINE")
    md, meta = render_am_markdown(_ctx(), llm, _settings(), session=object())

    assert "STILL FINE" in md
    assert meta["used_llm"] is True
    assert meta["n_kb_hits"] == 0
    assert "(no reference notes found)" in llm.calls[0]
