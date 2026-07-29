"""fetch_new must query Trash too — the owner deletes read letters to declutter,
so the daily job has to reach mail Gmail has moved to Trash (retained ~30d)."""

from __future__ import annotations

from pathlib import Path

from trading_intel.letters import gmail_source


class _List:
    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs

    def execute(self) -> dict:
        return {"messages": []}  # empty -> fetch_new returns [] without touching disk


class _Messages:
    def __init__(self, sink: dict) -> None:
        self._sink = sink

    def list(self, **kwargs):
        self._sink.update(kwargs)
        return _List(**kwargs)


class _Users:
    def __init__(self, sink: dict) -> None:
        self._sink = sink

    def messages(self) -> _Messages:
        return _Messages(self._sink)


class _Svc:
    def __init__(self, sink: dict) -> None:
        self._sink = sink

    def users(self) -> _Users:
        return _Users(self._sink)


def test_fetch_new_includes_spam_trash(monkeypatch, tmp_path: Path) -> None:
    sink: dict = {}
    monkeypatch.setattr(gmail_source, "_service", lambda _settings: _Svc(sink))
    out = gmail_source.fetch_new(object(), tmp_path, days=8)
    assert out == []
    assert sink.get("includeSpamTrash") is True  # the fix
    assert sink.get("userId") == "me"


def test_fetch_new_returns_empty_without_service(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(gmail_source, "_service", lambda _settings: None)
    assert gmail_source.fetch_new(object(), tmp_path) == []
