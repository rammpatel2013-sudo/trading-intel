"""The script-report wrappers import and resolve their scripts/ module."""
import importlib

import pytest

from trading_intel.scheduler.jobs._script_report import load_script

WRAPPERS = {
    "index_bigtrades_report": "build",
    "iv_tenor_report": "build",
    "watchlist_gex_dex_report": "build",
    "swing_report": "build",
    "dealer_positioning_report": "build",
}


@pytest.mark.parametrize("name", sorted(WRAPPERS))
def test_wrapper_imports_and_script_has_build(name):
    mod = importlib.import_module(f"trading_intel.scheduler.jobs.{name}")
    assert callable(mod.run) and callable(mod.main)
    assert callable(getattr(load_script(name), WRAPPERS[name]))


def test_push_never_raises(monkeypatch, tmp_path):
    from trading_intel.scheduler.jobs import _script_report as sr

    class Boom:
        def __init__(self, *_a, **_k):
            raise RuntimeError("no telegram")

    import trading_intel.clients.telegram as tg

    monkeypatch.setattr(tg, "TelegramClient", Boom)
    assert sr.push(tmp_path / "x.html", "cap", settings=None) is False
