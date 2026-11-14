"""Daily closes for the factor cards from the repo's EXISTING price clients (created 2026-11-09).

Order of preference (no new vendor):
  1. ``CVForgeClient.fmp`` - the FMP passthrough risk_tab already uses
     (``historical-price-eod/dividend-adjusted``, then ``.../light``), deep history.
  (Yahoo fallback removed 2026-11-14 - FMP only.)

Returns a wide frame (DatetimeIndex x symbol). Never raises for one missing symbol;
the card for that factor reports "missing prices" instead.
"""
# ruff: noqa: ANN401  (duck-typed clients, as in phase5_tools)


from __future__ import annotations

from datetime import date
from typing import Any

import pandas as pd
import structlog

log = structlog.get_logger(__name__)

START = "1999-01-01"


def _fmp_rows(fmp: Any, sym: str, start: str) -> pd.Series:
    for ep in ("historical-price-eod/dividend-adjusted", "historical-price-eod/light"):
        try:
            rows = fmp(ep, {"symbol": sym, "from": start, "to": date.today().isoformat()})
        except Exception as exc:
            log.info("factor_prices.fmp_failed", symbol=sym, endpoint=ep, err=str(exc)[:120])
            continue
        if isinstance(rows, dict):
            rows = rows.get("data") or rows.get("historical") or []
        if not isinstance(rows, list) or not rows:
            continue
        out = {}
        for r in rows:
            v = r.get("adjClose", r.get("close", r.get("price")))
            if r.get("date") and v is not None:
                out[pd.Timestamp(r["date"])] = float(v)
        if len(out) > 50:
            return pd.Series(out, name=sym).sort_index()
    return pd.Series(dtype=float, name=sym)


def load_closes(
    symbols: list[str], *, settings: Any = None, use_cvforge: bool = True, start: str = START
) -> tuple[pd.DataFrame, dict[str, str]]:
    """Wide close frame + {symbol: source used}."""
    fmp = None
    if use_cvforge and settings is not None:
        try:
            from trading_intel.clients.cvforge import CVForgeClient

            fmp = CVForgeClient(settings, timeout=60.0).fmp
        except Exception as exc:
            log.info("factor_prices.cvforge_unavailable", err=str(exc)[:120])
    cols, src = {}, {}
    for s in symbols:  # FMP only since the 2026-11-14 Yahoo exit (no yfinance fallback)
        ser = _fmp_rows(fmp, s, start) if fmp is not None else pd.Series(dtype=float)
        if len(ser):
            cols[s] = ser
            src[s] = "fmp"
    frame = pd.DataFrame(cols).sort_index() if cols else pd.DataFrame()
    return frame, src
