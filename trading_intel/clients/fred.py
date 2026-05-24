"""FRED macro client — VIX and credit spreads.

The only module that talks to FRED (CLAUDE.md rule 1: vendor calls live in
``clients/``). Wraps ``fredapi`` so the rest of the system consumes plain
floats / pandas Series and never imports the vendor SDK directly.

Series used (all free, daily):
- ``VIXCLS``      — CBOE VIX close
- ``BAMLH0A0HYM2`` — ICE BofA US High-Yield OAS (credit stress)
- ``BAMLC0A0CM``  — ICE BofA US Corporate (IG) OAS

NOTE: MOVE (Treasury vol) is **not** freely available on FRED — it is left as
``None`` here; wire a dedicated source later if wanted. Descriptive macro inputs
only (FlashAlpha rule 4).
"""

from __future__ import annotations

import pandas as pd
import structlog

from trading_intel.config import Settings

log = structlog.get_logger(__name__)

VIX_SERIES = "VIXCLS"
HY_OAS_SERIES = "BAMLH0A0HYM2"
IG_OAS_SERIES = "BAMLC0A0CM"


class FredClient:
    """Thin wrapper over ``fredapi.Fred``. Inject ``fred`` in tests to avoid HTTP."""

    def __init__(self, settings: Settings, *, fred: object | None = None) -> None:
        if fred is not None:
            self._fred = fred
        else:
            from fredapi import Fred  # lazy: tests inject a fake and skip the SDK

            self._fred = Fred(api_key=settings.FRED_API_KEY.get_secret_value())

    def series(self, series_id: str) -> pd.Series:
        """Full daily series for ``series_id`` (NaNs dropped), oldest first."""
        s = self._fred.get_series(series_id)
        return pd.Series(dtype="float64") if s is None else pd.Series(s).dropna()

    def latest(self, series_id: str) -> float | None:
        s = self.series(series_id)
        return float(s.iloc[-1]) if not s.empty else None

    def vix_with_sd20(self) -> tuple[float | None, float | None]:
        """Latest VIX close + its trailing 20-observation standard deviation."""
        s = self.series(VIX_SERIES)
        if s.empty:
            return None, None
        last = float(s.iloc[-1])
        sd20 = float(s.tail(20).std(ddof=0)) if len(s) >= 2 else None
        return last, sd20

    def credit_spreads(self) -> tuple[float | None, float | None]:
        """Latest (HY OAS, IG OAS)."""
        return self.latest(HY_OAS_SERIES), self.latest(IG_OAS_SERIES)
