"""Telegram push mute list (2026-11-05): muted jobs skip the push, others still send."""

from __future__ import annotations

from types import SimpleNamespace

import trading_intel.clients.telegram as tg


def _client() -> tg.TelegramClient:
    return tg.TelegramClient(SimpleNamespace(TELEGRAM_BOT_TOKEN="t", TELEGRAM_CHAT_ID="1"))


def test_muted_job_skips_send(monkeypatch):
    monkeypatch.setattr(tg, "job_name", lambda: "breadth_report")
    monkeypatch.setattr(tg.httpx, "Client", lambda *a, **k: (_ for _ in ()).throw(AssertionError("sent")))
    assert _client().send_message("x") is False


def test_unmuted_job_still_sends(monkeypatch):
    sent = {}

    class _C:
        def __init__(self, *a, **k): ...
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def post(self, url, json=None, **k):
            sent["text"] = json["text"]
            return SimpleNamespace(status_code=200, text="")

    monkeypatch.setattr(tg, "job_name", lambda: "oi_am")
    monkeypatch.setattr(tg.httpx, "Client", _C)
    assert _client().send_message("hello") is True
    assert sent["text"] == "hello"


def test_env_override(monkeypatch):
    monkeypatch.setenv("TELEGRAM_UNMUTE", "breadth_report")
    monkeypatch.setenv("TELEGRAM_MUTE", "oi_am")
    assert not tg.is_muted("breadth_report")
    assert tg.is_muted("oi_am")
    assert not tg.is_muted("")


def test_job_name_from_module(monkeypatch):
    import sys
    fake = SimpleNamespace(__spec__=SimpleNamespace(name="trading_intel.scheduler.jobs.sector_report"))
    monkeypatch.setitem(sys.modules, "__main__", fake)
    assert tg.job_name() == "sector_report"
