"""One AI job at a time on the laptop Ollama + the 08:00-09:30 Doc window (2026-11-13)."""
from datetime import datetime
from unittest.mock import MagicMock

import pytest

from trading_intel.synthesis import llm as llm_mod
from trading_intel.synthesis import ollama_lock as lk

MON_0815 = datetime(2026, 11, 16, 8, 15)
MON_1000 = datetime(2026, 11, 16, 10, 0)


def test_doc_window_is_weekday_0800_to_0930():
    assert lk.doc_window(datetime(2026, 11, 16, 8, 0))
    assert lk.doc_window(datetime(2026, 11, 16, 9, 29))
    assert not lk.doc_window(datetime(2026, 11, 16, 9, 30))
    assert not lk.doc_window(datetime(2026, 11, 16, 7, 59))
    assert not lk.doc_window(datetime(2026, 11, 14, 8, 30))   # Saturday


def test_other_jobs_are_turned_away_in_the_doc_window():
    with pytest.raises(lk.OllamaBusy) as e:
        with lk.ollama_turn(MagicMock(DATABASE_URL="sqlite://"), now=MON_0815):
            pass
    assert isinstance(e.value, ConnectionError)        # callers treat it like "Ollama off"


def test_doc_read_goes_ahead_and_no_db_means_no_lock():
    with lk.ollama_turn(MagicMock(DATABASE_URL="sqlite://"), purpose=lk.DOC_READ, now=MON_0815) as held:
        assert held is False
    with lk.ollama_turn(MagicMock(DATABASE_URL="sqlite://"), now=MON_1000) as held:
        assert held is False


class _Conn:
    def __init__(self, answers):
        self.answers, self.sql, self.closed = list(answers), [], False

    def execute(self, stmt, params=None):
        self.sql.append(str(stmt))
        val = self.answers.pop(0) if "try_advisory" in str(stmt) else True
        return MagicMock(scalar=lambda: val)

    def close(self):
        self.closed = True


def test_turn_waits_then_holds_and_releases(monkeypatch):
    conn = _Conn([False, True])
    monkeypatch.setattr(lk, "_connect", lambda s: conn)
    with lk.ollama_turn(MagicMock(), wait_s=5, poll_s=0, now=MON_1000) as held:
        assert held is True
    assert any("advisory_unlock" in s for s in conn.sql) and conn.closed


def test_turn_gives_up_when_another_job_keeps_it(monkeypatch):
    conn = _Conn([False] * 50)
    monkeypatch.setattr(lk, "_connect", lambda s: conn)
    with pytest.raises(lk.OllamaBusy):
        with lk.ollama_turn(MagicMock(), wait_s=0, poll_s=0, now=MON_1000):
            pass
    assert not any("advisory_unlock" in s for s in conn.sql) and conn.closed


def _provider(monkeypatch, now, purpose="general"):
    calls = []

    class _Client:
        def __init__(self, **kw):
            pass

        def generate(self, **kw):
            calls.append(kw)
            return {"response": "{}"}

    monkeypatch.setattr(llm_mod.ollama, "Client", _Client)
    monkeypatch.setattr(lk, "_now", lambda: now)
    s = MagicMock(OLLAMA_HOST="http://x:11434", DATABASE_URL="sqlite://")
    return llm_mod.OllamaProvider(s, purpose=purpose), calls


def test_provider_takes_turns_and_passes_num_ctx(monkeypatch):
    p, calls = _provider(monkeypatch, MON_0815)
    with pytest.raises(lk.OllamaBusy):
        p.complete("hi")
    assert calls == []
    p, calls = _provider(monkeypatch, MON_0815, purpose=lk.DOC_READ)
    p.complete_json("hi", num_ctx=12288)
    assert calls[0]["options"]["num_ctx"] == 12288
    p, calls = _provider(monkeypatch, MON_1000)
    p.complete("hi")
    assert len(calls) == 1
