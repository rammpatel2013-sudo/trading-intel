"""Phase 5 P5 — 9-gate swing card (pure)."""

from datetime import date

from trading_intel.swing import gates as sg

BULL = {
    "px_vs_sma50": 0.04,
    "rsi14": 62.0,
    "dex": 1.0e6,
    "gex": 2.0e6,
    "iv_rv": 1.0,
    "atm_iv_rank_252d": 0.3,
    "skew_pctile_252d": 0.5,
    "gex_pctile_252d": 0.5,
}


def test_clean_bull_passes_all_nine():
    c = sg.evaluate("ABC", BULL, as_of=date(2026, 11, 4), next_earnings=None)
    assert c.lean == "bullish"
    assert [g.name for g in c.gates] == list(sg.GATE_NAMES)
    assert c.passed == 9 and c.flagged


def test_missing_history_is_grey_not_pass_and_earnings_blocks():
    f = dict(BULL, atm_iv_rank_252d=None, skew_pctile_252d=None, gex_pctile_252d=None)
    c = sg.evaluate("ABC", f, as_of=date(2026, 11, 4), next_earnings=date(2026, 11, 10))
    by = {g.name: g.ok for g in c.gates}
    assert by["iv_rank"] is None and by["skew"] is None and by["gex"] is None
    assert by["earnings"] is False
    assert c.passed == 5 and not c.flagged


def test_price_only_gates():
    closes = [100 + i * 0.5 + (1 if i % 2 else -1) for i in range(80)]
    lean, t_ok, m_ok = sg.price_only_gates(closes, 79)
    assert lean == "bullish" and t_ok
    assert sg.price_only_gates(closes, 10) is None
