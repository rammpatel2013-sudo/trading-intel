"""Phase 2 (2026-11-04): vendor side trusted only at the bid/ask of the print's own quote."""
import pandas as pd

from trading_intel.flow.aggregate import derive, rollup_by_name


def test_mid_prints_unsigned_and_signable_share():
    df = pd.DataFrame({
        "root": ["SPXW"] * 4, "cp": ["P"] * 4, "strike": [5900.0] * 4, "spot": [6000.0] * 4,
        "side": ["buy", "sell", "buy", "sell"], "notional": [100.0, 100.0, 100.0, 100.0],
        "size": [1, 1, 1, 1], "price": [1.0] * 4, "delta": [-0.2] * 4,
        "quote_loc": ["at_ask", "at_bid", "mid", None],
    })
    d = derive(df)
    assert list(d["side"]) == ["buy", "sell", "mid", "sell"]      # None = no quote recorded: kept
    r = rollup_by_name(d).iloc[0]
    assert r["buy_notional"] == 100 and r["sell_notional"] == 200
    assert abs(r["signable_pct"] - 0.75) < 1e-9
    assert abs(r["pct_buy_all"] - 0.50) < 1e-9          # with Convex's guess on the mid print
