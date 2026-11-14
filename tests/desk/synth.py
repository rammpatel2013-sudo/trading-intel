"""Fake vendors for the desk-brief tests (2026-11-14): FMP via a fake CVForge, Convex und, FINRA."""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

from trading_intel.timeutils import is_trading_session

END = date(2026, 11, 13)  # a Friday session


def sessions(end: date, n: int) -> list[date]:
    out, d = [], end
    while len(out) < n:
        if is_trading_session(d):
            out.append(d)
        d -= timedelta(days=1)
    return out[::-1]


DAYS = sessions(END + timedelta(days=40), 60)  # bars run past END so picks can be scored
HIST = [d for d in DAYS if d <= END]

# symbol: (sector, industry, is_etf, last-day 1D %, 5D direction, short share for FINRA, bullish %, st_gex)
SPEC = {
    "AAA": ("Consumer Defensive", "Packaged Foods", False, 2.5, "down", 0.72, 80, 5e6),
    "BBB": ("Consumer Defensive", "Packaged Foods", False, 1.8, "down", 0.68, 70, -4e6),
    "CCC": ("Utilities", "Regulated Water", False, 1.2, "down", 0.70, 30, 1e6),
    "DDD": ("Healthcare", "Biotechnology", False, 6.0, "down", 0.40, 90, 2e6),
    "EEE": ("Technology", "Software", False, -1.0, "down", 0.70, 80, 1e6),
    "FFF": ("Technology", "Software", False, 1.0, "up", 0.70, 80, 1e6),
    "LOW": ("Technology", "Software", False, 3.0, "down", 0.70, 80, 1e6),
    "SOXS": (None, "Asset Management", True, 7.0, "down", 0.50, 50, 0.0),
    "SPY": (None, "Asset Management", True, -0.4, "up", 0.50, 50, 0.0),
    "QQQ": (None, "Asset Management", True, -1.0, "up", 0.50, 50, 0.0),
}


def closes(sym: str) -> list[float]:
    _, _, _, d1, d5, *_ = SPEC[sym]
    base = 3.0 if sym == "LOW" else 100.0
    out = []
    for d in DAYS:
        if d < HIST[-6]:
            out.append(base)
        elif d < END:  # the 5 sessions before END drift down (or up)
            k = HIST.index(d) - (len(HIST) - 6)
            out.append(base * (1 - 0.02 * k) if d5 == "down" else base * (1 + 0.005 * k))
        elif d == END:
            out.append(out[-1] * (1 + d1 / 100))
        else:  # after END: AAA rises, others flat
            out.append(out[-1] * (1.004 if sym == "AAA" else 1.0))
    return out


class FakeCV:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def fmp(self, ep: str, params: dict | None = None):
        p = params or {}
        self.calls.append(ep)
        if ep == "company-screener":
            etf = p.get("isEtf") == "true"
            return [
                {
                    "symbol": s,
                    "companyName": (
                        "Direxion Daily Semiconductor Bear 3X" if s == "SOXS" else f"{s} Inc"
                    ),
                    "sector": v[0],
                    "industry": v[1],
                    "isEtf": v[2],
                    "avgVolume": 3_000_000,
                    "price": closes(s)[len(HIST) - 1],
                    "marketCap": 5e9,
                    "beta": 0.8,
                }
                for s, v in SPEC.items()
                if v[2] == etf
            ]
        if ep == "historical-price-eod/full":
            s, frm = p["symbol"], date.fromisoformat(p["from"])
            if s not in SPEC:
                return []
            cl = closes(s)
            return [
                {
                    "date": d.isoformat(),
                    "open": c * 0.995,
                    "high": c * 1.01,
                    "low": c * 0.98,
                    "close": c,
                    "volume": 3_000_000,
                }
                for d, c in zip(DAYS, cl, strict=True)
                if frm <= d <= self.today
            ]
        if ep == "profile":
            return [
                {
                    "companyName": f"{p['symbol']} Inc",
                    "industry": "Packaged Foods",
                    "marketCap": 5e9,
                    "beta": 0.6,
                }
            ]
        if ep == "ratios-ttm":
            return [{"priceToEarningsRatioTTM": 18.0, "dividendYieldTTM": 0.03}]
        if ep == "income-statement":
            return [
                {
                    "date": f"2026-0{9 - 3 * i if 9 - 3 * i > 0 else 1}-30",
                    "epsDiluted": 1.0,
                    "revenue": 1e9 * (1 + 0.01 * (8 - i)),
                }
                for i in range(8)
            ]
        if ep == "earnings":
            return [
                {"date": "2027-02-01", "epsEstimated": 1.1},
                {"date": "2026-10-30", "epsActual": 1.0, "epsEstimated": 0.9},
            ]
        if ep in ("sp500-constituent",):
            return [{"symbol": s} for s in SPEC]
        return []

    today = END

    def gex_by_underlying(self, start: date, end: date) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {
                    "symbol": s,
                    "spot": closes(s)[len(HIST) - 1],
                    "st_gex": v[7],
                    "total_gex": v[7] * 2,
                }
                for s, v in SPEC.items()
            ]
        )


class FakeApp:
    def __init__(self, split: bool = True) -> None:
        self.split = split

    def und_snapshot(self, *, min_option_volume: int = 1):
        out = []
        for s, v in SPEC.items():
            bull = v[6] / 100.0
            tot = 10_000.0 if self.split else 0.0
            out.append(
                {
                    "symbol": s,
                    "price": 0.0,
                    "change": 0.0,
                    "option_volume": 20_000.0,
                    "call_volume": 10_000.0,
                    "put_volume": 10_000.0,
                    "volm_call_buy": tot * bull / 2,
                    "volm_call_sell": tot * (1 - bull) / 2,
                    "volm_put_buy": tot * (1 - bull) / 2,
                    "volm_put_sell": tot * bull / 2,
                    "gxoi": 1.0,
                    "volatility": 0.3,
                }
            )
        return out


class FakeFinra:
    def short_volume_all(self, d: date):
        if d > END or not is_trading_session(d):
            return {}
        return {s: (1_000_000 * v[5], 1_000_000.0) for s, v in SPEC.items()}
