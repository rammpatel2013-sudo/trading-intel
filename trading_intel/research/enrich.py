"""FMP extraction for the research report — field names confirmed via probe.

Endpoints + fields pinned with ``scripts/probe_fmp_fundamentals.py`` (2026-07-20). Each
endpoint call is guarded individually (``_fmp``) so one failure (e.g. ``quote`` 502s on
this tier) never zeros the whole panel; a missing field -> ``None`` -> blank in the
report. Vendor spoken only via the client (rule 1); descriptive context only (rule 4).

Gated on this CVForge tier (return blank for now): ``price-target-consensus``,
``grades-consensus``, ``institutional-ownership/symbol-ownership`` (all 404). Insider
(Form 4) via ``insider-trading/search`` works and drives the analyst panel.
"""

from __future__ import annotations

from trading_intel.errors import TradingIntelError

_ERRORS = (TradingIntelError, KeyError, ValueError, TypeError)


def _pick(d: object, *keys: str) -> object:
    if not isinstance(d, dict):
        return None
    for k in keys:
        v = d.get(k)
        if v not in (None, ""):
            return v
    return None


def _f(v: object) -> float | None:
    if v in (None, ""):
        return None
    try:
        return float(str(v).replace(",", "").replace("%", "").strip())
    except (TypeError, ValueError):
        return None


def _first(res: object) -> dict:
    if isinstance(res, list) and res and isinstance(res[0], dict):
        return res[0]
    if isinstance(res, dict):
        return res
    return {}


def _ratio(a: float | None, b: float | None) -> float | None:
    return (a / b) if (a is not None and b) else None


def _fmp(client: object, endpoint: str, **params: object) -> object:
    """Guarded single FMP call — returns ``None`` on any vendor/shape error."""
    try:
        return client.fmp(endpoint, params)  # type: ignore[attr-defined]
    except _ERRORS:
        return None


def fundamentals_from(
    profile: object,
    key_metrics: object,
    ratios: object,
    income: object,
    cashflow: object,
    balance: object,
) -> dict:
    """Normalise the fundamentals panel from raw FMP payloads (confirmed field names)."""
    prof, km, r = _first(profile), _first(key_metrics), _first(ratios)
    inc, cf, bs = _first(income), _first(cashflow), _first(balance)
    mkt_cap = _f(_pick(prof, "marketCap", "mktCap") or _pick(km, "marketCap"))
    revenue = _f(_pick(inc, "revenue"))
    sbc = _f(_pick(cf, "stockBasedCompensation"))
    fcf = _f(_pick(cf, "freeCashFlow"))
    return {
        "price": _f(_pick(prof, "price")),
        "market_cap": mkt_cap,
        "sector": _pick(prof, "sector"),
        "industry": _pick(prof, "industry"),
        # valuation
        "pe": _f(_pick(r, "priceToEarningsRatioTTM")),
        "ev_ebitda": _f(_pick(km, "evToEBITDATTM") or _pick(r, "enterpriseValueMultipleTTM")),
        "ev_sales": _f(_pick(km, "evToSalesTTM")),
        "p_fcf": _f(_pick(r, "priceToFreeCashFlowRatioTTM")),
        "fcf_yield": _f(_pick(km, "freeCashFlowYieldTTM")),
        # quality
        "roic": _f(_pick(km, "returnOnInvestedCapitalTTM")),
        "roe": _f(_pick(km, "returnOnEquityTTM") or _pick(r, "returnOnEquityTTM")),
        "gross_margin": _f(_pick(r, "grossProfitMarginTTM")),
        "oper_margin": _f(_pick(r, "operatingProfitMarginTTM")),
        "fcf_margin": _ratio(fcf, revenue),
        # balance sheet (debt pillar)
        "net_debt": _f(_pick(bs, "netDebt")),
        "net_debt_ebitda": _f(_pick(km, "netDebtToEBITDATTM")),
        "interest_coverage": _f(_pick(r, "interestCoverageRatioTTM")),
        # SBC drag
        "sbc": sbc,
        "sbc_pct_rev": _f(_pick(km, "stockBasedCompensationToRevenueTTM")) or _ratio(sbc, revenue),
        "sbc_pct_mktcap": _ratio(sbc, mkt_cap),
        # capital return
        "div_yield": _f(_pick(r, "dividendYieldTTM")),
    }


def institutional_from(rows: object) -> dict:
    """Institutional ownership summary (endpoint gated on this tier -> mostly blank)."""
    r = _first(rows)
    return {
        "inst_pct": _f(_pick(r, "ownershipPercent", "institutionalOwnershipPercentage")),
        "holders": _f(_pick(r, "investorsHolding", "numberOfInstitutionalHolders")),
        "change_pct": _f(_pick(r, "ownershipPercentChange", "investorsHoldingChange")),
    }


def analyst_from(estimates: object, insider: object) -> dict:
    """Forward estimate + Form-4 insider net (from ``insider-trading/search``)."""
    est = _first(estimates)
    rows = insider if isinstance(insider, list) else []
    buys = sum(
        1
        for t in rows
        if isinstance(t, dict) and str(_pick(t, "acquisitionOrDisposition") or "").upper() == "A"
    )
    sells = sum(
        1
        for t in rows
        if isinstance(t, dict) and str(_pick(t, "acquisitionOrDisposition") or "").upper() == "D"
    )
    return {
        "eps_next": _f(_pick(est, "epsAvg")),
        "rev_next": _f(_pick(est, "revenueAvg")),
        "insider_buys": buys,
        "insider_sells": sells,
    }


def pull_fundamentals(client: object, symbol: str) -> dict:
    sym = symbol.upper()
    return fundamentals_from(
        _fmp(client, "profile", symbol=sym),
        _fmp(client, "key-metrics-ttm", symbol=sym),
        _fmp(client, "ratios-ttm", symbol=sym),
        _fmp(client, "income-statement", symbol=sym, period="annual", limit=1),
        _fmp(client, "cash-flow-statement", symbol=sym, period="annual", limit=1),
        _fmp(client, "balance-sheet-statement", symbol=sym, period="annual", limit=1),
    )


def pull_institutional(client: object, symbol: str) -> dict:
    return institutional_from(
        _fmp(client, "institutional-ownership/symbol-ownership", symbol=symbol.upper())
    )


def pull_analyst(client: object, symbol: str) -> dict:
    sym = symbol.upper()
    return analyst_from(
        _fmp(client, "analyst-estimates", symbol=sym, period="annual", limit=1),
        _fmp(client, "insider-trading/search", symbol=sym, limit=40),
    )
