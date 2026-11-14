"""Raw FMP JSON -> table rows for the equity foundation. Pure, unit-tested.

Point-in-time rule: a quarter's numbers count only from ``available_date`` = the filing's
``acceptedDate`` (else ``filingDate``, else period end + 45 days). Every later step joins on
that date, so nothing uses a number before the market could have seen it.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, timedelta


def _d(s: object) -> date | None:
    try:
        return date.fromisoformat(str(s)[:10])
    except (TypeError, ValueError):
        return None


def _f(x: object) -> float | None:
    try:
        v = float(x)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return v if v == v else None  # NaN -> None


def bar_rows(symbol: str, raw: Sequence[Mapping]) -> list[dict]:
    """``historical-price-eod/full`` rows -> equity_bars rows (oldest first, no market cap yet)."""
    out: dict[date, dict] = {}
    for r in raw:
        d = _d(r.get("date"))
        c = _f(r.get("close"))
        if d is None or c is None or c <= 0:
            continue
        vol = _f(r.get("volume"))
        out[d] = {
            "symbol": symbol,
            "date": d,
            "open": _f(r.get("open")),
            "high": _f(r.get("high")),
            "low": _f(r.get("low")),
            "close": c,
            "volume": int(vol) if vol is not None else None,
            "market_cap": None,
        }
    return [out[d] for d in sorted(out)]


FUND_COLUMNS = ("available_date", "fiscal_year", "period", "revenue", "gross_profit",
                "operating_income", "net_income", "ebitda", "eps_diluted", "interest_expense",
                "shares_diluted", "total_assets", "total_equity", "total_debt", "cash", "sbc",
                "operating_cash_flow", "capex", "free_cash_flow")


def fundamental_rows(symbol: str, stmts: Mapping[str, Sequence[Mapping]]) -> list[dict]:
    """Merge quarterly income / balance / cash-flow statements on period end."""
    by_end: dict[date, dict] = {}

    def slot(row: Mapping) -> dict | None:
        end = _d(row.get("date"))
        if end is None:
            return None
        s = by_end.setdefault(end, {"symbol": symbol, "period_end": end})
        avail = _d(row.get("acceptedDate")) or _d(row.get("filingDate"))
        if avail and (s.get("available_date") is None or avail > s["available_date"]):
            s["available_date"] = avail
        s.setdefault("fiscal_year", str(row.get("fiscalYear") or "")[:8] or None)
        s.setdefault("period", str(row.get("period") or "")[:4] or None)
        return s

    for r in stmts.get("income", []):
        s = slot(r)
        if s is None:
            continue
        s.update(
            revenue=_f(r.get("revenue")),
            gross_profit=_f(r.get("grossProfit")),
            operating_income=_f(r.get("operatingIncome")),
            net_income=_f(r.get("netIncome")),
            ebitda=_f(r.get("ebitda")),
            eps_diluted=_f(r.get("epsDiluted")),
            interest_expense=_f(r.get("interestExpense")),
            shares_diluted=_f(r.get("weightedAverageShsOutDil")),
        )
    for r in stmts.get("balance", []):
        s = slot(r)
        if s is None:
            continue
        eq = _f(r.get("totalStockholdersEquity"))
        s.update(
            total_assets=_f(r.get("totalAssets")),
            total_equity=eq if eq is not None else _f(r.get("totalEquity")),
            total_debt=_f(r.get("totalDebt")),
            cash=_f(r.get("cashAndCashEquivalents")),
        )
    for r in stmts.get("cash", []):
        s = slot(r)
        if s is None:
            continue
        s.update(
            sbc=_f(r.get("stockBasedCompensation")),
            operating_cash_flow=_f(r.get("operatingCashFlow")),
            capex=_f(r.get("capitalExpenditure")),
            free_cash_flow=_f(r.get("freeCashFlow")),
        )
    out = []
    for end in sorted(by_end):
        s = {**dict.fromkeys(FUND_COLUMNS), **by_end[end]}  # every row carries every column
        if s.get("available_date") is None:
            s["available_date"] = end + timedelta(days=45)
        out.append(s)
    return out


def profile_row(symbol: str, raw: Mapping, today: date) -> dict:
    return {
        "symbol": symbol,
        "name": (str(raw.get("companyName") or "")[:160] or None),
        "sector": (str(raw.get("sector") or "")[:48] or None),
        "industry": (str(raw.get("industry") or "")[:96] or None),
        "exchange": (str(raw.get("exchange") or "")[:24] or None),
        "is_etf": bool(raw.get("isEtf")) if raw.get("isEtf") is not None else None,
        "is_adr": bool(raw.get("isAdr")) if raw.get("isAdr") is not None else None,
        "ipo_date": _d(raw.get("ipoDate")),
        "updated": today,
    }
