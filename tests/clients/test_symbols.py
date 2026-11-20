"""clients.symbols (2026-11-19, DB wave A2 step 6): one spelling per symbol + the OCC contract key."""

import pytest

from trading_intel.clients.symbols import (
    asset_type_of,
    map_row,
    normalize_symbol,
    occ_symbol,
    parse_occ,
    vendor_symbol,
)


@pytest.mark.parametrize(
    "raw,ours",
    [("I:SPX", "SPX"), ("^GSPC", "SPX"), ("^VIX", "VIX"), ("^DJI", "DJX"), ("^TNX", "TNX"),
     ("BRK.B", "BRK-B"), ("BRK/B", "BRK-B"), ("brk-b", "BRK-B"), (" spy ", "SPY"), ("$SPX", "SPX"),
     ("BCE.TO", "BCE.TO"), ("", ""), (None, "")],
)
def test_normalize(raw, ours):
    assert normalize_symbol(raw) == ours


def test_vendor_spellings_round_trip():
    for v in ("convex", "cvforge", "od", "fmp", "yahoo", "finra"):
        for s in ("SPX", "BRK-B", "NVDA"):
            x = vendor_symbol(s, v)
            if x is not None:
                assert normalize_symbol(x) == s, (v, s, x)
    assert vendor_symbol("SPX", "cvforge") == "I:SPX" and vendor_symbol("SPX", "fmp") == "^GSPC"
    assert vendor_symbol("XSP", "fmp") is None and vendor_symbol("SPX", "finra") is None
    assert vendor_symbol("BRK-B", "finra") == "BRK/B" and vendor_symbol("BRK-B", "cvforge") == "BRK.B"
    with pytest.raises(ValueError):
        vendor_symbol("SPX", "bloomberg")


def test_asset_type_and_map_row():
    assert asset_type_of("SPX") == "index" and asset_type_of("QQQ", True) == "etf"
    assert asset_type_of("AAPL", None) == "stock"
    r = map_row("brk.b", "stock")
    assert r["symbol"] == "BRK-B" and r["fmp"] == "BRK-B" and r["od"] == "BRK.B"


def test_occ_key():
    assert occ_symbol("SPXW", "2026-11-18", "C", 3200) == "SPXW  261118C03200000"
    assert occ_symbol("nvda", "2026-11-20", "put", 2.5) == "NVDA  261120P00002500"
    assert len(occ_symbol("SPXW", "2026-11-18", "C", 3200)) == 21
    assert parse_occ("SPXW  261118C03200000") == ("SPXW", "2026-11-18", "C", 3200.0)
    assert parse_occ("BRKB1 261120C00500000") == ("BRKB1", "2026-11-20", "C", 500.0)   # adjusted root
    assert parse_occ("junk") is None
