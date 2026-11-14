"""Synthetic market for the equity tests: a fake CVForge that answers the FMP endpoints and chains."""

from __future__ import annotations

import itertools
import math
from datetime import date, timedelta

import numpy as np
import pandas as pd

END = date(2026, 11, 13)
SECTORS = ("Technology", "Healthcare", "Industrials", "Energy")


def bdays(n: int, end: date = END) -> list[date]:
    out, d = [], end
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d -= timedelta(days=1)
    return out[::-1]


def tickers(n: int) -> list[str]:
    return ["".join(t) for t in itertools.islice(itertools.product("ABCDEFGHJK", repeat=3), n)]


class World:
    """Prices with a market factor, sector moves, a beta tilt and a few planted setups."""

    def __init__(self, n_sp500: int = 70, n_ijh: int = 30, days: int = 650, seed: int = 7) -> None:
        rng = np.random.default_rng(seed)
        self.dates = bdays(days)
        syms = tickers(n_sp500 + n_ijh + 1)
        self.sp500 = syms[:n_sp500]
        self.ijh = syms[n_sp500 : n_sp500 + n_ijh]
        self.gone = syms[-1]  # left the S&P 500 a year ago
        self.all = self.sp500 + self.ijh + [self.gone] + ["SPY", "RSP", "QQQ", "IWM", "MDY", "IJR"]
        T = len(self.dates)
        m = rng.normal(0.0004, 0.01, T)
        sec = {s: rng.normal(0, 0.004, T) for s in SECTORS}
        self.sector = {s: SECTORS[i % len(SECTORS)] for i, s in enumerate(self.all)}
        self.beta = {s: 0.6 + 0.8 * rng.random() for s in self.all}
        self.close: dict[str, np.ndarray] = {}
        self.vol: dict[str, np.ndarray] = {}
        for i, s in enumerate(self.all):
            if s in ("SPY", "RSP", "QQQ", "IWM", "MDY", "IJR"):
                r = m + rng.normal(0, 0.001, T)
            else:
                r = self.beta[s] * m + sec[self.sector[s]] + rng.normal(0, 0.012, T)
            if i < 5:  # strong own movers (leaders)
                r = r + 0.0025
            px = 50 * np.exp(np.cumsum(r))
            v = np.full(T, 2_000_000.0)
            if i == 10:  # a box for 120 sessions then a breakout on the last day
                base = px[-121]
                px[-121:-1] = base * (1 + 0.05 * np.sin(np.arange(120) / 7))
                px[-1] = base * 1.12
                v[-1] = 6_000_000.0
            self.close[s] = px
            self.vol[s] = v

    # ── FMP passthrough ────────────────────────────────────────────────
    def fmp(self, endpoint: str, params: dict | None = None):
        p = params or {}
        if endpoint == "sp500-constituent":
            return [{"symbol": s} for s in self.sp500]
        if endpoint == "historical-sp500-constituent":
            d = self.dates[-250].isoformat()
            return [
                {
                    "date": d,
                    "addedTicker": self.sp500[0],
                    "removedTicker": self.gone,
                    "addedSecurity": "New Co",
                    "removedSecurity": "Old Co",
                    "changeType": "replacement",
                }
            ]
        if endpoint == "etf/holdings":
            return (
                [{"asset": s, "weightPercentage": 100 / len(self.ijh)} for s in self.ijh]
                if p.get("symbol") == "IJH"
                else []
            )
        sym = p.get("symbol")
        if endpoint == "historical-price-eod/full":
            start = date.fromisoformat(p["from"])
            if sym not in self.close:
                return []
            c, v = self.close[sym], self.vol[sym]
            rows = [
                {
                    "date": d.isoformat(),
                    "open": c[t],
                    "high": c[t] * 1.01,
                    "low": c[t] * 0.99,
                    "close": c[t],
                    "volume": v[t],
                }
                for t, d in enumerate(self.dates)
                if d >= start
            ]
            return rows[::-1]
        if endpoint == "profile":
            return [
                {
                    "symbol": sym,
                    "companyName": sym,
                    "sector": self.sector.get(sym),
                    "industry": "x",
                    "exchange": "NYSE",
                    "isEtf": sym in ("SPY", "RSP"),
                    "isAdr": False,
                    "ipoDate": "2000-01-01",
                }
            ]
        if endpoint in ("income-statement", "balance-sheet-statement", "cash-flow-statement"):
            return self._stmts(sym, endpoint, int(p.get("limit", 12)))
        raise ValueError(endpoint)

    def _stmts(self, sym: str, ep: str, limit: int) -> list[dict]:
        h = sum(map(ord, sym))
        out = []
        for k in range(limit):
            end = date(2026, 9, 30) - timedelta(days=91 * k)
            rev = 1e9 * (1 + (h % 7) / 10) * (1 - 0.02 * k)
            row = {
                "date": end.isoformat(),
                "acceptedDate": (end + timedelta(days=35)).isoformat() + " 08:00:00",
                "fiscalYear": str(end.year),
                "period": f"Q{(end.month - 1) // 3 + 1}",
            }
            if ep == "income-statement":
                row.update(
                    revenue=rev,
                    grossProfit=rev * 0.4,
                    operatingIncome=rev * 0.15,
                    netIncome=rev * 0.1,
                    ebitda=rev * 0.2,
                    epsDiluted=1.0,
                    interestExpense=1e7,
                    weightedAverageShsOutDil=1e8,
                )
            elif ep == "balance-sheet-statement":
                row.update(
                    totalAssets=8e9,
                    totalStockholdersEquity=3e9 + (h % 5) * 1e8,
                    totalDebt=2e9,
                    cashAndCashEquivalents=5e8,
                )
            else:
                row.update(
                    stockBasedCompensation=2e7,
                    operatingCashFlow=rev * 0.18,
                    capitalExpenditure=-rev * 0.05,
                    freeCashFlow=rev * 0.13,
                )
            out.append(row)
        return out

    # ── option chain ───────────────────────────────────────────────────
    def chain(self, sym: str) -> pd.DataFrame:
        spot = float(self.close[sym][-1])
        rows = []
        for dte in (30, 120, 200):
            t = dte / 365
            for k in np.linspace(0.6, 1.4, 33) * spot:
                d1 = (math.log(spot / k) + 0.5 * 0.09 * t) / (0.3 * math.sqrt(t))
                delta = 0.5 * (1 + math.erf(d1 / math.sqrt(2)))
                for kind, dl in (("call", delta), ("put", delta - 1)):
                    rows.append(
                        {
                            "opt_kind": kind,
                            "strike": round(k, 2),
                            "expiration": pd.Timestamp(END + timedelta(days=dte)),
                            "delta": dl,
                            "iv": 0.30,
                            "oi": 100.0,
                            "volume": 10.0,
                            "underlying_price": spot,
                        }
                    )
        return pd.DataFrame(rows)
