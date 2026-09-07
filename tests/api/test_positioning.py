"""Unit tests for the cockpit assembly (pure mapping; dicts in → cockpit JSON out).

`assemble_cockpit` was split from the DB calls specifically so it could be tested
on captured samples, but had no coverage. These pin the snapshot-preference
behaviour: the cockpit must read the latest minute-resolution `greeks_snapshots`
row so an INTRADAY run differs from the EOD one, while still falling back to the
daily `gamma_history` row when the snapshot is absent or not yet enriched.
"""
from __future__ import annotations

from trading_intel.api.positioning import _flow_from_extras, assemble_cockpit

# Daily gamma_history row — yesterday's EOD read.
_HIST = {
    "rows": [{"date": "2026-09-04", "spot": 7717.81, "gex_flip": 7700.99,
              "gex_total": 24.7, "dex_total": 737.8, "regime": "long gamma",
              "atm_iv": 0.072}],
    "summary": {"current_regime": "long gamma", "current_gex": 24.7},
}
# Latest greeks_snapshots row — minutes old during RTH.
_EX = {
    "ts": "2026-09-04T15:42:00", "spot": 7731.40, "gex_flip": 7706.10,
    "gex_total": -18.2, "dex_total": -412.0, "dex_flip": 7690.0, "atm_iv": 0.081,
    "call_volume": 1200.0, "put_volume": 900.0,
    "call_notional": 5.0e6, "put_notional": 4.0e6,
}


def _c(**kw):
    base = dict(symbol="SPX", gamma_hist=_HIST, gex_term=None, straddle=None,
                skew30=None, skew0=None, extras=None, as_of=None)
    base.update(kw)
    return assemble_cockpit(**base)


def test_snapshot_wins_over_the_daily_row():
    out = _c(extras=_EX)
    assert out["spot"] == 7731.40                 # not 7717.81
    assert out["regime"]["gex_flip"] == 7706.10   # not 7700.99
    assert out["dex"]["total"] == -412.0          # not 737.8
    assert out["as_of"] == "2026-09-04T15:42:00"  # snapshot ts, not the date


def test_regime_follows_the_snapshot_sign_not_the_stale_label():
    """gamma_history says "long gamma"; the live snapshot is net short."""
    out = _c(extras=_EX)
    assert out["regime"]["label"] == "short gamma"
    assert out["regime"]["amplifying"] is True
    assert out["gex"]["total"] == -18.2


def test_falls_back_to_the_daily_row_without_a_snapshot():
    out = _c(extras=None)
    assert out["spot"] == 7717.81
    assert out["regime"]["gex_flip"] == 7700.99
    assert out["regime"]["label"] == "long gamma"
    assert out["as_of"] == "2026-09-04"


def test_partial_snapshot_falls_back_field_by_field():
    """A snapshot row predating the exposures() enrichment carries only some cols."""
    out = _c(extras={"ts": "2026-09-04T15:42:00", "spot": 7731.40})
    assert out["spot"] == 7731.40                 # from the snapshot
    assert out["regime"]["gex_flip"] == 7700.99   # fell back to the daily row
    assert out["regime"]["label"] == "long gamma"  # fell back to the label


def test_zero_gex_total_is_not_treated_as_missing():
    """0.0 is falsy — the code must test `is not None`, not truthiness."""
    out = _c(extras={"gex_total": 0.0})
    assert out["gex"]["total"] == 0.0
    assert out["regime"]["amplifying"] is False   # 0 is not short gamma


def test_flow_pending_until_the_row_is_enriched():
    assert _flow_from_extras({})["pending"] is True
    assert _flow_from_extras({"call_volume": 10.0})["pending"] is False
    assert _c(extras={"spot": 1.0})["meta"]["flow_pending"] is True


def test_put_call_ratio():
    f = _flow_from_extras(_EX)
    assert f["pc_ratio"] == 0.75
    assert _flow_from_extras({"call_volume": 0.0, "put_volume": 5.0})["pc_ratio"] is None
