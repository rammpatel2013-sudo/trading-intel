"""Desk brief - the top-pick card's company facts (2026-11-14). FMP via the CVForge key.

Served on our tier (probed 2026-11-14): profile, ratios-ttm, key-metrics-ttm, income-statement,
earnings, sec-filings-search/symbol, news/stock. NOT served: price-target-consensus,
grades-consensus, dividends -> the card shows "n/a" for target / rating. Every call is guarded:
a missing piece leaves a blank, never breaks the brief.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, timedelta
from typing import Any

import structlog

log = structlog.get_logger(__name__)


def _first(x: Any) -> dict:  # noqa: ANN401
    if isinstance(x, list) and x and isinstance(x[0], dict):
        return x[0]
    return x if isinstance(x, dict) else {}


def _get(src: Any, ep: str, params: Mapping) -> Any:  # noqa: ANN401
    try:
        return src._fmp(ep, dict(params))  # EquityDataSource: throttled + retried
    except Exception as exc:  # one missing block never breaks the card
        log.warning("desk.fundamentals.failed", endpoint=ep, error=str(exc)[:160])
        return None


def top_card(src: Any, symbol: str, day: date) -> dict:  # noqa: ANN401
    prof = _first(_get(src, "profile", {"symbol": symbol}))
    rat = _first(_get(src, "ratios-ttm", {"symbol": symbol}))
    km = _first(_get(src, "key-metrics-ttm", {"symbol": symbol}))
    inc = _get(src, "income-statement", {"symbol": symbol, "period": "quarter", "limit": 8}) or []
    earn = _get(src, "earnings", {"symbol": symbol, "limit": 6}) or []
    fil = (
        _get(
            src,
            "sec-filings-search/symbol",
            {
                "symbol": symbol,
                "from": (day - timedelta(days=45)).isoformat(),
                "to": day.isoformat(),
            },
        )
        or []
    )
    news = _get(src, "news/stock", {"symbols": symbol, "limit": 4}) or []

    quarters = []
    inc = [q for q in inc if isinstance(q, dict)]
    for i, q in enumerate(inc[:4]):
        prev = inc[i + 4] if i + 4 < len(inc) else None
        rev, prev_rev = q.get("revenue"), (prev or {}).get("revenue")
        quarters.append(
            {
                "date": q.get("date"),
                "period": f"{q.get('fiscalYear', '')} {q.get('period', '')}".strip(),
                "eps": q.get("epsDiluted", q.get("eps")),
                "revenue": rev,
                "rev_yoy": (100.0 * (rev / prev_rev - 1)) if rev and prev_rev else None,
            }
        )
    upcoming = sorted(
        (e for e in earn if isinstance(e, dict) and e.get("date") and e["date"] >= day.isoformat()),
        key=lambda e: e["date"],
    )
    last = sorted(
        (e for e in earn if isinstance(e, dict) and e.get("epsActual") is not None),
        key=lambda e: e.get("date", ""),
        reverse=True,
    )
    return {
        "symbol": symbol,
        "name": prof.get("companyName"),
        "industry": prof.get("industry"),
        "sector": prof.get("sector"),
        "market_cap": prof.get("marketCap"),
        "beta": prof.get("beta"),
        "range_52w": prof.get("range"),
        "pe_ttm": rat.get("priceToEarningsRatioTTM"),
        "div_yield": rat.get("dividendYieldTTM"),
        "net_debt_ebitda": km.get("netDebtToEBITDATTM"),
        "ev_ebitda": km.get("evToEBITDATTM"),
        "quarters": quarters,
        "next_earnings": (
            {"date": upcoming[0].get("date"), "eps_est": upcoming[0].get("epsEstimated")}
            if upcoming
            else None
        ),
        "last_earnings": (
            {
                "date": last[0].get("date"),
                "eps": last[0].get("epsActual"),
                "eps_est": last[0].get("epsEstimated"),
            }
            if last
            else None
        ),
        "filings": [
            {
                "form": f.get("formType"),
                "date": str(f.get("filingDate", ""))[:10],
                "link": f.get("finalLink") or f.get("link"),
            }
            for f in fil[:4]
            if isinstance(f, dict)
        ],
        "news": [
            {
                "title": n.get("title"),
                "site": n.get("site"),
                "date": str(n.get("publishedDate", ""))[:10],
                "url": n.get("url"),
            }
            for n in news[:3]
            if isinstance(n, dict)
        ],
        "target": None,
        "rating": None,  # not served on our FMP tier
    }


def hold_weaken(pick: Mapping, card: Mapping) -> dict:
    """His 'holds if / weakens if' block, written from the same tests that picked the name."""
    d = pick.get("dark") or {}
    holds = [
        f"dark buy ratio stays above 1 on 1W and 2W (now {d.get('w1') or 0:.2f} / {d.get('w2') or 0:.2f})",
        f"options stay more than 50% bullish (now {pick.get('bull') or 0:.0f}%)",
        "short-term gamma stays positive (moves get absorbed)",
    ]
    weak = [
        "dark trend turns to distributing (1W below 2W) or either ratio drops under 1",
        "bullish options share falls under 50%",
        "short-term gamma turns negative",
        f"a close below today's low ({pick.get('low') or 0:.2f})",
    ]
    ne = card.get("next_earnings") or {}
    if ne.get("date"):
        weak.append(f"earnings on {ne['date']} - the setup resets around it")
    return {"holds": holds, "weakens": weak}
