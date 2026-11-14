"""Equity data for the swing side - FMP endpoints on the CVForge key (ADR-004/005, rule 1).

Every endpoint used here was probed live on 2026-11-14 through the passthrough
(``sp500-constituent``, ``historical-sp500-constituent``, ``etf/holdings`` IJH/IJR,
``historical-price-eod/full``, ``income-statement`` / ``balance-sheet-statement`` /
``cash-flow-statement`` quarterly, ``profile``). Not served by the passthrough (404
"unknown FMP endpoint"): ``batch-quote``, ``eod-bulk``, ``*-bulk``, ``sp400-constituent``.

``fetch_many`` runs per-symbol calls on a small thread pool under a shared rate cap
(CVForge Research tier = 50,000/h ~ 13.9/s; we stay at 8/s so the NAS's other jobs
keep headroom). Returns raw JSON; parsing lives in ``trading_intel.equity.parse``.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import structlog

log = structlog.get_logger(__name__)

RATE_PER_SEC = 8.0
WORKERS = 6
INDEX_ETFS = {"SP400": "IJH", "SP600": "IJR"}


class _RateCap:
    """Shared minimum spacing between calls across threads."""

    def __init__(self, per_sec: float) -> None:
        self._gap = 1.0 / per_sec if per_sec > 0 else 0.0
        self._lock = threading.Lock()
        self._next = 0.0

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            slot = max(now, self._next)
            self._next = slot + self._gap
        delay = slot - now
        if delay > 0:
            time.sleep(delay)


class EquityDataSource:
    """Thin typed wrapper over ``CVForgeClient.fmp`` for the equity tables."""

    def __init__(self, cvforge: object, *, per_sec: float = RATE_PER_SEC) -> None:
        self._cv = cvforge
        self._cap = _RateCap(per_sec)

    def _fmp(
        self, endpoint: str, params: dict | None = None, *, retries: int = 2
    ) -> Any:  # noqa: ANN401
        err: Exception | None = None
        for attempt in range(retries + 1):
            self._cap.wait()
            try:
                return self._cv.fmp(endpoint, params or {})  # type: ignore[attr-defined]
            except Exception as exc:
                err = exc
                time.sleep(0.5 * (attempt + 1))
        raise err  # type: ignore[misc]

    # ── universe ─────────────────────────────────────────────────────────
    def sp500_current(self) -> list[dict]:
        data = self._fmp("sp500-constituent")
        return data if isinstance(data, list) else []

    def sp500_changes(self) -> list[dict]:
        data = self._fmp("historical-sp500-constituent")
        return data if isinstance(data, list) else []

    def etf_holdings(self, etf: str) -> list[dict]:
        data = self._fmp("etf/holdings", {"symbol": etf})
        return data if isinstance(data, list) else []

    # ── per symbol ───────────────────────────────────────────────────────
    def bars(self, symbol: str, start: str) -> list[dict]:
        data = self._fmp("historical-price-eod/full", {"symbol": symbol, "from": start})
        if isinstance(data, dict):
            data = data.get("historical") or []
        return data if isinstance(data, list) else []

    def statements(self, symbol: str, *, limit: int = 12) -> dict[str, list[dict]]:
        out: dict[str, list[dict]] = {}
        for key, ep in (
            ("income", "income-statement"),
            ("balance", "balance-sheet-statement"),
            ("cash", "cash-flow-statement"),
        ):
            data = self._fmp(ep, {"symbol": symbol, "period": "quarter", "limit": limit})
            out[key] = data if isinstance(data, list) else []
        return out

    def profile(self, symbol: str) -> dict:
        data = self._fmp("profile", {"symbol": symbol})
        if isinstance(data, list) and data and isinstance(data[0], dict):
            return data[0]
        return data if isinstance(data, dict) else {}

    # ── fan-out ──────────────────────────────────────────────────────────
    def fetch_many(
        self,
        symbols: Iterable[str],
        fn: Callable[[str], Any],
        *,
        workers: int = WORKERS,
        label: str = "fetch",
    ) -> dict[str, Any]:
        """``{symbol: fn(symbol)}``; a failed symbol is logged and left out."""
        syms = list(dict.fromkeys(symbols))
        out: dict[str, Any] = {}
        failed = 0

        def one(sym: str) -> tuple[str, Any, Exception | None]:
            try:
                return sym, fn(sym), None
            except Exception as exc:
                return sym, None, exc

        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            for i, (sym, val, exc) in enumerate(pool.map(one, syms), 1):
                if exc is None:
                    out[sym] = val
                else:
                    failed += 1
                if i % 250 == 0:
                    log.info(f"equity_data.{label}.progress", done=i, of=len(syms), failed=failed)
        log.info(f"equity_data.{label}.done", n=len(syms), ok=len(out), failed=failed)
        return out


def default_source(settings: object | None = None) -> EquityDataSource:
    """Composition helper: the source on the CVForge key."""
    from trading_intel.clients.cvforge import CVForgeClient
    from trading_intel.config import get_settings

    return EquityDataSource(CVForgeClient(settings or get_settings(), timeout=60.0))  # type: ignore[arg-type]
