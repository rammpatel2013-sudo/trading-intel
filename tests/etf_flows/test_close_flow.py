"""Leveraged-ETF close flow (2026-11-19): the math, the job's DB round trip, the page."""

from __future__ import annotations

import importlib.util
from datetime import date, datetime, time
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from trading_intel.etf_flows import close_flow as cf
from trading_intel.etf_flows import close_flow_page as page

ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 11, 19, 15, 45)


def test_multiplier_sign_is_the_same_for_bull_and_bear() -> None:
    down = -0.02
    for lev in (3.0, 2.0, -1.0, -2.0, -3.0):
        f = cf.FundIn("X", lev, 1e6, 100.0, 100.0 * (1 + lev * down), NOW)
        r = cf.fund_flow(f, down, NOW)
        assert r["used"] == "own"
        assert r["rebalance_usd"] == pytest.approx(lev * (lev - 1) * 1e8 * down)
        assert r["rebalance_usd"] < 0  # every fund sells on a down day


def test_old_bar_falls_back_to_index_move_and_missing_shares_count() -> None:
    old = cf.FundIn("UPRO", 3.0, 1e6, 100.0, 90.0, datetime(2026, 11, 19, 15, 0))
    r = cf.fund_flow(old, -0.01, NOW)
    assert r["used"] == "index" and r["rebalance_usd"] == pytest.approx(6 * 1e8 * -0.01)
    g = cf.group_flow("SPX", [old, cf.FundIn("SSO", 2.0, None, 50.0, 49.0, NOW)], 7322.97, 7189.99, NOW)
    assert g["n_index_fallback"] == 1 and g["n_missing"] == 1


def test_group_reproduces_the_1119_card_scale() -> None:
    # 11/19 15:45 (FMP 1-min, shares-float): S&P funds summed to about -$1.5B ~ 4,300 ES
    funds = [
        cf.FundIn("UPRO", 3, 31293121, 3.92e9 / 31293121, 3.92e9 / 31293121 * (1 - 0.0537), NOW),
        cf.FundIn("SPXL", 3, 1e8, 58.6, 58.6 * (1 - 0.0545), NOW),
        cf.FundIn("SSO", 2, 105400898, 6.62e9 / 105400898, 6.62e9 / 105400898 * (1 - 0.0361), NOW),
    ]
    g = cf.group_flow("SPX", funds, 7322.97, 7189.99, NOW)
    assert g["direction"] == "SELL" and g["strength"] == "MODERATE"
    assert -1.4e9 < g["rebalance_usd"] < -1.2e9
    assert g["futures"] == pytest.approx(g["rebalance_usd"] / (50 * 7189.99))
    assert g["flip_level"] == 7322.97


def test_strength_labels() -> None:
    assert [cf.strength(x) for x in (1e8, -1.6e9, 3e9, -5e9)] == ["LIGHT", "MODERATE", "HEAVY", "EXTREME"]


def test_registry_has_both_index_groups() -> None:
    assert {m.symbol for m in cf.funds_for("SPX")} >= {"UPRO", "SPXL", "SSO", "SH", "SDS", "SPXU", "SPXS", "SPDN"}
    assert {m.symbol for m in cf.funds_for("NDX")} >= {"TQQQ", "SQQQ", "QLD", "QID", "PSQ"}


def test_early_close_matches_the_cockpit_calendar() -> None:
    spec = importlib.util.spec_from_file_location("market_cal", ROOT / "scripts" / "cvxmap" / "market_cal.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    assert cf.EARLY_CLOSE == mod.EARLY_CLOSE
    assert cf.session_close(date(2026, 11, 27)) == time(13, 0)
    assert cf.session_close(date(2026, 11, 19)) == time(16, 0)


class FakeCvf:
    """FMP 1-minute bars for 11/18 (prior) and 11/19 (today): index -2%, funds follow their leverage."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def fmp(self, endpoint: str, params: dict):  # noqa: ANN201
        self.calls.append((endpoint, params))
        sym = params["symbol"]
        lev = cf.REGISTRY[sym].leverage if sym in cf.REGISTRY else 1.0
        base = 7322.97 if sym == "^GSPC" else 25000.0 if sym == "^NDX" else 50.0
        rows = []
        if params["from"] == "2026-11-18":
            rows.append({"date": "2026-11-18 15:59:00", "open": base, "high": base, "low": base,
                         "close": base, "volume": 1})
        px = base * (1 + lev * -0.02)
        for hm in ("09:30", "15:40"):
            rows.append({"date": f"2026-11-19 {hm}:00", "open": px, "high": px, "low": px, "close": px, "volume": 1})
        return list(reversed(rows))  # FMP sends newest first


@pytest.fixture()
def session():
    import trading_intel.memory.models_close_flow  # noqa: F401
    import trading_intel.memory.models_flow  # noqa: F401
    from trading_intel.memory.models import Base, LetfSharesSnapshot, QuoteDaily

    eng = create_engine("sqlite://")
    tables = [Base.metadata.tables[t] for t in ("bars_1m", "letf_close_flow", "letf_shares_snapshots", "quotes_daily")]
    Base.metadata.create_all(eng, tables=tables)
    s = sessionmaker(bind=eng)()
    for m in cf.REGISTRY.values():
        if m.underlying in cf.GROUPS:
            s.add(LetfSharesSnapshot(symbol=m.symbol, ts=date(2026, 11, 19), shares_outstanding=10_000_000))
    s.add(QuoteDaily(symbol="SPX", date=date(2026, 11, 18), open=7322.97, high=7322.97, low=7322.97,
                     close=7322.97, volume=0))
    s.commit()
    yield s
    s.close()


def test_one_pass_reads_back_from_the_db(session, tmp_path, monkeypatch) -> None:  # noqa: ANN001
    from trading_intel.scheduler.jobs import letf_close_live as job

    cvf = FakeCvf()
    res = job.one_pass(session, cvf, NOW)
    assert not res["errors"]
    spx = next(g for g in res["groups"] if g["group"] == "SPX")
    assert spx["index_ret"] == pytest.approx(-0.02)
    assert spx["direction"] == "SELL" and spx["n_missing"] == 0 and spx["n_index_fallback"] == 0
    # every SPX fund: 10M shares x $50, own move = lev x -2%  ->  sum lev(lev-1) x 5e8 x -0.02
    want = sum(m.leverage * (m.leverage - 1) for m in cf.funds_for("SPX")) * 5e8 * -0.02
    assert spx["rebalance_usd"] == pytest.approx(want)
    # second pass: the prior day is already in the DB -> only today is pulled
    n = len(cvf.calls)
    job.one_pass(session, cvf, datetime(2026, 11, 19, 15, 47))
    assert all(p["from"] == "2026-11-19" for _, p in cvf.calls[n:])
    out = job.write_outputs(session, res, tmp_path)
    html = out.read_text(encoding="utf-8")
    assert "SELL" in html and "ES" in html and "<script" not in html and "flips above" in html
    assert (tmp_path / job.JSON_OUT).exists()


def test_run_once_skips_a_holiday(session) -> None:  # noqa: ANN001
    from trading_intel.scheduler.jobs import letf_close_live as job

    assert job.run(session, None, None, once=True, now_fn=lambda: datetime(2026, 11, 26, 15, 0)) == 0


def test_mcp_tool_reads_the_table(session) -> None:  # noqa: ANN001
    from trading_intel.mcp.close_flow_tools import close_flow
    from trading_intel.scheduler.jobs import letf_close_live as job

    job.one_pass(session, FakeCvf(), NOW)
    out = close_flow(session, "2026-11-19", with_funds=False)
    assert out["groups"]["SPX"]["latest"]["direction"] == "SELL"
    assert out["groups"]["NDX"]["latest"]["future"] == "NQ"


def test_page_without_prices() -> None:
    g = cf.group_flow("SPX", [], 7322.97, None, NOW)
    res = {"day": date(2026, 11, 19), "ts": NOW, "close": time(16, 0), "groups": [g], "errors": []}
    assert "no price yet" in page.render(res, {})
