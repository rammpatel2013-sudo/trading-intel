"""Letters model legs moved to the night (2026-11-12).

The morning ``letters_fetch`` (DSM 08:50) and the digest's inline fetch store the
letters only; ``letters_tag`` (DSM 23:00) runs the model legs with a bounded LLM.
"""

from __future__ import annotations

import contextlib
import sys
import types
from datetime import datetime
from unittest.mock import MagicMock

import pytest
import structlog

import trading_intel.letters as letters_pkg
import trading_intel.memory.db as db_mod
import trading_intel.synthesis.llm as llm_mod
from trading_intel.scheduler.jobs import letters_digest, letters_fetch, letters_tag
from trading_intel.scheduler.jobs.letters_tag import BoundedLLM, LettersTagBudgetError, stop_time


class _FakeLLM:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.n = 0

    def _hit(self, out):
        self.n += 1
        if self.fail:
            raise RuntimeError("laptop asleep")
        return out

    def complete(self, prompt, *, model=None, max_tokens=2048):
        return self._hit("text")

    def complete_json(self, prompt, *, model=None, max_tokens=2048):
        return self._hit("{}")

    def chat(self, messages, *, model=None, max_tokens=2048):
        return self._hit("reply")

    def embed(self, text, *, model=None):
        return self._hit([[0.1, 0.2]])


def _clock(*times: datetime):
    """Clock returning the given times in order, then the last one forever."""
    seq = list(times)

    def now() -> datetime:
        return seq.pop(0) if len(seq) > 1 else seq[0]

    return now


def _no_fetch(monkeypatch) -> None:
    """Stub both letter sources so run() touches no network."""
    monkeypatch.setattr(letters_fetch, "substack_sources", lambda: [])
    fake = types.ModuleType("trading_intel.letters.gmail_source")
    fake.fetch_new = lambda settings, root: []
    monkeypatch.setitem(sys.modules, "trading_intel.letters.gmail_source", fake)
    monkeypatch.setattr(letters_pkg, "gmail_source", fake, raising=False)


def _fake_session_factory(monkeypatch) -> None:
    @contextlib.contextmanager
    def _session():
        yield MagicMock()

    monkeypatch.setattr(db_mod, "make_session_factory", lambda settings: _session)


@pytest.fixture(autouse=True)
def _reset_structlog():
    yield
    structlog.reset_defaults()  # the entrypoints call structlog.configure()


# ── stop time ────────────────────────────────────────────────────────────────────


def test_stop_time_is_the_next_7am_eastern():
    assert stop_time(datetime(2026, 11, 12, 23, 0)) == datetime(2026, 11, 13, 7, 0)
    assert stop_time(datetime(2026, 11, 13, 3, 30)) == datetime(2026, 11, 13, 7, 0)
    assert stop_time(datetime(2026, 11, 13, 7, 0)) == datetime(2026, 11, 14, 7, 0)


# ── BoundedLLM ───────────────────────────────────────────────────────────────────


def test_bounded_llm_passes_every_method_through():
    inner = _FakeLLM()
    llm = BoundedLLM(
        inner, stop_at=datetime(2026, 11, 13, 7, 0), clock=_clock(datetime(2026, 11, 12, 23, 0))
    )
    assert llm.complete("p") == "text"
    assert llm.complete_json("p") == "{}"
    assert llm.chat([{"role": "user", "content": "hi"}]) == "reply"
    assert llm.embed("t") == [[0.1, 0.2]]
    assert llm.calls == 4 and inner.n == 4 and llm.stopped is None


def test_bounded_llm_makes_no_call_after_the_stop_time():
    inner = _FakeLLM()
    llm = BoundedLLM(
        inner, stop_at=datetime(2026, 11, 13, 7, 0), clock=_clock(datetime(2026, 11, 13, 7, 0))
    )
    with pytest.raises(LettersTagBudgetError):
        llm.complete("p")
    assert inner.n == 0
    assert llm.stopped == "reached 07:00 ET"


def test_bounded_llm_gives_up_after_three_failures_in_a_row():
    inner = _FakeLLM(fail=True)
    llm = BoundedLLM(
        inner, stop_at=datetime(2026, 11, 13, 7, 0), clock=_clock(datetime(2026, 11, 12, 23, 0))
    )
    for _ in range(3):
        with pytest.raises(RuntimeError):
            llm.complete("p")
    with pytest.raises(LettersTagBudgetError):
        llm.complete("p")
    assert inner.n == 3  # the 4th call never reached the laptop
    assert "3 failed" in (llm.stopped or "")


def test_bounded_llm_success_resets_the_failure_count():
    inner = _FakeLLM(fail=True)
    llm = BoundedLLM(
        inner, stop_at=datetime(2026, 11, 13, 7, 0), clock=_clock(datetime(2026, 11, 12, 23, 0))
    )
    for _ in range(2):
        with pytest.raises(RuntimeError):
            llm.complete("p")
    inner.fail = False
    llm.complete("p")
    inner.fail = True
    for _ in range(2):
        with pytest.raises(RuntimeError):
            llm.complete("p")
    assert llm.stopped is None  # never 3 in a row


# ── morning: letters only ────────────────────────────────────────────────────────


def test_letters_only_run_never_touches_the_model(monkeypatch, tmp_path):
    _no_fetch(monkeypatch)
    monkeypatch.setattr(letters_fetch, "_store_source_notes", lambda session, root: 2)

    def _boom(*a, **k):
        raise AssertionError("model leg ran in a letters-only run")

    monkeypatch.setattr(letters_fetch, "ingest_folder", _boom)
    monkeypatch.setattr(letters_fetch, "_extract_source_signals", _boom)
    out = letters_fetch.run(MagicMock(), None, letters_dir=tmp_path)
    assert out["source_notes"] == 2 and out["signals"] == 0


def test_morning_entrypoint_never_builds_an_ollama_client(monkeypatch):
    def _no_ollama(*a, **k):
        raise AssertionError("the morning run must not build an Ollama client")

    monkeypatch.setattr(llm_mod, "OllamaProvider", _no_ollama)
    monkeypatch.setattr(letters_fetch, "ollama_up", _no_ollama)
    _fake_session_factory(monkeypatch)
    seen: dict = {}

    def _run(session, llm, **kwargs):
        seen["llm"] = llm
        return {}

    monkeypatch.setattr(letters_fetch, "run", _run)
    letters_fetch.main()
    assert seen == {"llm": None}


def test_digest_inline_fetch_is_letters_only(monkeypatch):
    monkeypatch.setattr(letters_digest, "_letters_fresh", lambda session, today: False)
    seen: dict = {}

    def _run(session, llm, **kwargs):
        seen["llm"] = llm
        return {}

    monkeypatch.setattr(letters_fetch, "run", _run)
    letters_digest._ensure_letters_fetched(MagicMock(), MagicMock(), datetime(2026, 11, 12).date())
    assert seen == {"llm": None}


# ── night: model legs ────────────────────────────────────────────────────────────


def test_model_run_extracts_levels_before_tagging(monkeypatch, tmp_path):
    _no_fetch(monkeypatch)
    monkeypatch.setattr(letters_fetch, "_store_source_notes", lambda session, root: 1)
    order: list[str] = []

    def _signals(session, llm, **kwargs):
        order.append("signals")
        return 4

    def _ingest(session, llm, **kwargs):
        order.append("ingest")
        return {"ingested": 3, "skipped": 0, "new_symbols": []}

    monkeypatch.setattr(letters_fetch, "_extract_source_signals", _signals)
    monkeypatch.setattr(letters_fetch, "ingest_folder", _ingest)
    out = letters_fetch.run(MagicMock(), _FakeLLM(), letters_dir=tmp_path)
    assert order == ["signals", "ingest"]
    assert out["signals"] == 4 and out["ingested"] == 3


def test_night_run_reports_calls_and_where_it_stopped(monkeypatch):
    # Starts 06:59 (stop = 07:00 the same morning); the clock then reads 07:01.
    clock = _clock(
        datetime(2026, 11, 13, 6, 59), datetime(2026, 11, 13, 6, 59), datetime(2026, 11, 13, 7, 1)
    )

    def _letters_run(session, llm, **kwargs):
        assert isinstance(llm, BoundedLLM)
        llm.complete("first")  # 06:59 — allowed
        with pytest.raises(LettersTagBudgetError):
            llm.complete("second")  # 07:01 — refused
        return {"signals": 1, "ingested": 0}

    monkeypatch.setattr(letters_tag, "letters_run", _letters_run)
    out = letters_tag.run(MagicMock(), _FakeLLM(), clock=clock)
    assert out["model_calls"] == 1
    assert out["stopped"] == "reached 07:00 ET"


def test_night_entrypoint_skips_when_the_laptop_is_asleep(monkeypatch, capsys):
    monkeypatch.setattr(letters_tag, "ollama_up", lambda host: False)

    def _no_db(settings):
        raise AssertionError("no DB work when Ollama is down")

    monkeypatch.setattr(db_mod, "make_session_factory", _no_db)
    letters_tag.main()
    assert "skipped tonight" in capsys.readouterr().out


def test_night_entrypoint_sets_a_per_call_timeout(monkeypatch):
    monkeypatch.setattr(letters_tag, "ollama_up", lambda host: True)
    built: dict = {}

    class _Provider:
        def __init__(self, settings, *, timeout=None):
            built["timeout"] = timeout

    monkeypatch.setattr(llm_mod, "OllamaProvider", _Provider)
    _fake_session_factory(monkeypatch)
    monkeypatch.setattr(letters_tag, "run", lambda session, llm, **kwargs: {})
    letters_tag.main()
    assert built == {"timeout": letters_tag.CALL_TIMEOUT_S}


def test_ollama_provider_passes_the_timeout_to_the_client(monkeypatch):
    seen: list[dict] = []

    class _Client:
        def __init__(self, **kwargs):
            seen.append(kwargs)

    monkeypatch.setattr(llm_mod.ollama, "Client", _Client)
    settings = MagicMock(OLLAMA_HOST="http://x:11434")
    llm_mod.OllamaProvider(settings)
    llm_mod.OllamaProvider(settings, timeout=600.0)
    assert [k["timeout"] for k in seen] == [None, 600.0]
