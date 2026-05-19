"""Smoke test — proves the package imports and config validates."""
def test_package_imports():
    import trading_intel
    assert trading_intel.__version__


def test_settings_load():
    from trading_intel.config import Settings
    s = Settings()
    assert s.CONVEX_EMAIL == "ci@example.com"
    assert s.watchlist_symbols
