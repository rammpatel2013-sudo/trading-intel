"""Map CVForge FMP fundamentals -> ``FactorInputs`` (pure, tolerant).

FMP spells the same ratio several ways across endpoints and tiers, so each metric
lists candidate keys and the first numeric hit wins. Everything is optional — a
missing key becomes ``None`` and the factor compute simply skips it. Momentum comes
from the ``/mas`` daily closes. Pure so the mapping is unit-tested without a vendor.

NOTE: confirm the FMP field spellings against the live ``ratios-ttm`` /
``key-metrics-ttm`` / ``financial-growth`` / ``profile`` payloads on first run —
the candidate lists below are best-effort and degrade gracefully if a key is absent.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

import numpy as np

from trading_intel.factors.compute import FactorInputs

# our metric -> candidate FMP keys (ratios-ttm / key-metrics-ttm)
_RATIO_KEYS: dict[str, tuple[str, ...]] = {
    "pe": ("priceToEarningsRatioTTM", "peRatioTTM"),
    "pb": ("priceToBookRatioTTM", "pbRatioTTM", "priceToBookRatio"),
    "ps": ("priceToSalesRatioTTM", "priceSalesRatioTTM", "priceToSalesRatio"),
    "ev_ebitda": (
        "enterpriseValueOverEBITDATTM",
        "evToEbitdaTTM",
        "enterpriseValueMultipleTTM",
    ),
    "roe": ("returnOnEquityTTM", "roeTTM"),
    "roic": ("returnOnInvestedCapitalTTM", "roicTTM"),
    "gross_margin": ("grossProfitMarginTTM", "grossProfitMargin"),
    "net_margin": ("netProfitMarginTTM", "netIncomeMarginTTM", "netProfitMargin"),
    "fcf_margin": ("operatingCashFlowSalesRatioTTM", "freeCashFlowMarginTTM"),
    "debt_to_equity": ("debtToEquityRatioTTM", "debtEquityRatioTTM"),
    "current_ratio": ("currentRatioTTM", "currentRatio"),
}
_GROWTH_KEYS: dict[str, tuple[str, ...]] = {
    "revenue_growth": ("revenueGrowth", "growthRevenue"),
    "eps_growth": ("epsgrowth", "epsGrowth", "growthEPS", "netIncomeGrowth"),
}
_PROFILE_KEYS: dict[str, tuple[str, ...]] = {"beta": ("beta",)}


def _first_num(d: Mapping[str, object], keys: tuple[str, ...]) -> float | None:
    for k in keys:
        v = d.get(k)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return float(v)
        if isinstance(v, str):
            try:
                return float(v)
            except ValueError:
                continue
    return None


def _rec(payload: object) -> Mapping[str, object]:
    """FMP returns a list-of-one or a dict; normalize to the record dict."""
    if isinstance(payload, list):
        return payload[0] if payload and isinstance(payload[0], Mapping) else {}
    return payload if isinstance(payload, Mapping) else {}


def momentum_returns(closes: Sequence[float] | None) -> tuple[float | None, float | None]:
    """(3m, 12m) trailing returns from daily closes (~63 / ~252 sessions back)."""
    if closes is None:
        return None, None
    arr = np.asarray(closes, dtype=float)
    if arr.size == 0:
        return None, None

    def ret(k: int) -> float | None:
        return float(arr[-1] / arr[-(k + 1)] - 1.0) if arr.size > k else None

    return ret(63), ret(252)


def extract_inputs(
    symbol: str,
    *,
    profile: object = None,
    ratios: object = None,
    key_metrics: object = None,
    growth: object = None,
    closes: Sequence[float] | None = None,
) -> FactorInputs:
    """Build ``FactorInputs`` from the fetched FMP payloads (+ momentum closes)."""
    prof, rat, km, grw = _rec(profile), _rec(ratios), _rec(key_metrics), _rec(growth)
    merged_ratio: dict[str, object] = {**km, **rat}  # ratios-ttm wins over key-metrics

    kw: dict[str, float | None] = {
        metric: _first_num(merged_ratio, keys) for metric, keys in _RATIO_KEYS.items()
    }
    for metric, keys in _GROWTH_KEYS.items():
        kw[metric] = _first_num(grw, keys)
    kw["beta"] = _first_num(prof, _PROFILE_KEYS["beta"])
    kw["ret_3m"], kw["ret_12m"] = momentum_returns(closes)

    return FactorInputs(symbol=symbol, **{k: v for k, v in kw.items() if v is not None})
