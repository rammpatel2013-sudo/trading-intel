"""Scheduled job (NIGHT): the letters model legs — newsletter levels + ingest tagging.

Ollama runs on the laptop (the NAS has 3.8GB RAM). Until 2026-11-12 these legs ran
inside the 08:50 ``letters_fetch`` and kept the laptop busy past the open. The morning
run now stores the letters only; this job does the model work at night (DSM, daily
23:00 ET).

It re-runs ``letters_fetch.run`` with an LLM. Gmail and Substack are re-read for the
last 8 days (the ``--rm`` container keeps no files), the letter-body upsert is
idempotent, then the levels/scenarios extractor and the ingest tagging run. Ingest
dedups by SHA and commits per file, so a night that stops early carries on the next
night (rule 5).

Guards so model work never spills into the trading day:
* skip at once if Ollama does not answer (laptop asleep or off) — the morning run has
  already stored the letters;
* each model call times out after ``CALL_TIMEOUT_S``;
* no new model call at or after ``STOP_AT`` ET, or after ``MAX_FAILURES`` failed calls
  in a row (the laptop went to sleep mid-run).

Local LLM only (rule 7); descriptive research context, never a signal (rule 4).

Manual run:
    python -m trading_intel.scheduler.jobs.letters_tag
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import datetime, timedelta
from datetime import time as dtime
from typing import ParamSpec, TypeVar

import structlog
from sqlalchemy.orm import Session

from trading_intel.config import Settings, get_settings
from trading_intel.errors import TradingIntelError
from trading_intel.scheduler.jobs.letters_fetch import ollama_up
from trading_intel.scheduler.jobs.letters_fetch import run as letters_run
from trading_intel.synthesis.llm import LLMProvider
from trading_intel.timeutils import eastern_now

log = structlog.get_logger(__name__)

_P = ParamSpec("_P")
_R = TypeVar("_R")

#: No new model call at or after this Eastern time (letters_fetch 08:50, open 09:30).
STOP_AT = dtime(7, 0)
#: Seconds allowed per model call; a 7B tagging call on the laptop is far shorter.
CALL_TIMEOUT_S = 600.0
#: Failed model calls in a row before the night run gives up (laptop asleep / Ollama gone).
MAX_FAILURES = 3


class LettersTagBudgetError(TradingIntelError):
    """Raised in place of a model call once the night run's budget is spent."""


def stop_time(now: datetime) -> datetime:
    """The next ``STOP_AT`` (naive Eastern) after ``now``."""
    stop = datetime.combine(now.date(), STOP_AT)
    return stop if now < stop else stop + timedelta(days=1)


class BoundedLLM:
    """``LLMProvider`` proxy that refuses new calls after a deadline or repeated failures.

    The deadline is checked before each call, so at most one call (capped by the
    provider timeout) can run past it.
    """

    def __init__(
        self,
        inner: LLMProvider,
        *,
        stop_at: datetime,
        max_failures: int = MAX_FAILURES,
        clock: Callable[[], datetime] = eastern_now,
    ) -> None:
        self._inner = inner
        self._stop_at = stop_at
        self._max_failures = max_failures
        self._clock = clock
        self._failures = 0
        self.calls = 0
        self.stopped: str | None = None

    def _call(self, fn: Callable[_P, _R], *args: _P.args, **kwargs: _P.kwargs) -> _R:
        if self.stopped is None and self._clock() >= self._stop_at:
            self.stopped = f"reached {self._stop_at:%H:%M} ET"
        if self.stopped is None and self._failures >= self._max_failures:
            self.stopped = f"{self._failures} failed model calls in a row"
        if self.stopped is not None:
            raise LettersTagBudgetError(self.stopped)
        try:
            out = fn(*args, **kwargs)
        except Exception:
            self._failures += 1
            raise
        self._failures = 0
        self.calls += 1
        return out

    def complete(self, prompt: str, *, model: str | None = None, max_tokens: int = 2048) -> str:
        return self._call(self._inner.complete, prompt, model=model, max_tokens=max_tokens)

    def complete_json(
        self, prompt: str, *, model: str | None = None, max_tokens: int = 2048
    ) -> str:
        return self._call(self._inner.complete_json, prompt, model=model, max_tokens=max_tokens)

    def chat(
        self, messages: list[dict], *, model: str | None = None, max_tokens: int = 2048
    ) -> str:
        return self._call(self._inner.chat, messages, model=model, max_tokens=max_tokens)

    def embed(self, text: str | list[str], *, model: str | None = None) -> list[list[float]]:
        return self._call(self._inner.embed, text, model=model)


def run(
    session: Session,
    llm: LLMProvider,
    *,
    settings: Settings | None = None,
    clock: Callable[[], datetime] = eastern_now,
) -> dict:
    """Night model pass: ``letters_fetch.run`` with an LLM bounded to end by ``STOP_AT``."""
    settings = settings or get_settings()
    bound = log.bind(correlation_id=uuid.uuid4().hex, job="letters_tag")
    bounded = BoundedLLM(llm, stop_at=stop_time(clock()), clock=clock)
    result = letters_run(session, bounded, settings=settings)
    bound.info(
        "letters_tag.done",
        model_calls=bounded.calls,
        stopped=bounded.stopped,
        signals=result.get("signals"),
        ingested=result.get("ingested"),
    )
    return {**result, "model_calls": bounded.calls, "stopped": bounded.stopped}


def main() -> None:
    """Night entrypoint (DSM daily 23:00 ET)."""
    from trading_intel.memory.db import make_session_factory
    from trading_intel.synthesis.llm import OllamaProvider

    settings = get_settings()
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ]
    )
    if not ollama_up(settings.OLLAMA_HOST):
        log.warning("letters_tag.skipped", reason="ollama unreachable")
        print("Ollama not reachable (laptop asleep or off?) - model legs skipped tonight.")
        return
    llm = OllamaProvider(settings, timeout=CALL_TIMEOUT_S)
    session_factory = make_session_factory(settings)
    with session_factory() as session:
        result = run(session, llm, settings=settings)
    print(f"Done: {result}")


if __name__ == "__main__":
    main()
