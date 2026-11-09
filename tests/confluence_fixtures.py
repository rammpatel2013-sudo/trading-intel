"""Seeded SQLite DB for the confluence read-out tests (MCP tool + scripts)."""

from __future__ import annotations

from datetime import date, datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from trading_intel.memory.models import (
    GreeksSnapshot,
    OiChainEod,
    QuoteDaily,
    TasPrint,
    VixData,
    VixOptionsChain,
    VolSurfaceCM,
)
from trading_intel.timeutils import is_trading_session

TABLES = (OiChainEod, GreeksSnapshot, QuoteDaily, VixData, VixOptionsChain, VolSurfaceCM, TasPrint)
BOOK_DAYS = (date(2026, 11, 4), date(2026, 11, 5), date(2026, 11, 6))


def _sessions(end: date, n: int) -> list[date]:
    out, d = [], end
    while len(out) < n:
        if is_trading_session(d):
            out.append(d)
        d -= timedelta(days=1)
    return out[::-1]


def seeded_session(*, bars_after: int = 0) -> Session:
    """SPX books for 11/04-11/06, 260 SPX/SPY bars to 11/06 (+``bars_after`` later bars)."""
    eng = create_engine("sqlite://")
    for t in TABLES:
        t.__table__.create(eng)
    s = Session(eng)
    for i, d in enumerate(BOOK_DAYS):
        ts = datetime.combine(d, datetime.min.time())
        spot = 7350.0 + 10 * i
        s.add(GreeksSnapshot(symbol="SPX", ts=ts + timedelta(hours=17), spot=spot, source="convex"))
        for exp_off, dte in ((5, 5), (14, 14), (42, 42)):
            for k in range(6900, 7900, 25):
                for cp in ("C", "P"):
                    oi = 1000 + (30000 if k == 7000 else 0) + (8000 if k == 7650 and cp == "C" else 0)
                    s.add(OiChainEod(symbol="SPX", ts=ts, expiry=d + timedelta(days=exp_off),
                                     strike=float(k), cp=cp, dte=dte, oi=oi, oi_change=50 * (i + 1),
                                     iv=0.22 if cp == "P" else 0.18, source="convex_am"))
        s.add(VixData(date=d, vix=20.0 + i))
        for kind, k, oi in (("call", 30.0, 300_000), ("call", 25.0, 250_000), ("put", 18.0, 350_000)):
            s.add(VixOptionsChain(ts=d, expiration=date(2026, 11, 18), strike=k, opt_kind=kind, oi=oi))
        for rung in (7, 14, 30):
            for dl in (20.0, 25.0, 30.0, 50.0):
                for side in ("put", "call"):
                    s.add(VolSurfaceCM(symbol="SPX", ts=d, dte=rung, delta=dl, side=side,
                                       iv=0.25 if side == "put" else 0.18, spot=spot))
        s.add(TasPrint(captured_at=ts, ts=ts, trade_date=d, symbol=f".SPX261218P7400_{i}",
                       root="SPX", expiry=date(2026, 12, 18), strike=7400.0, cp="P", side="buy",
                       price=10.0, size=100, notional=5e7, leg_group=f"g{i}", source="convex_index"))
    days = _sessions(date(2026, 11, 6), 260)
    d = date(2026, 11, 7)
    while len(days) < 260 + bars_after:
        if is_trading_session(d):
            days.append(d)
        d += timedelta(days=1)
    for j, d in enumerate(days):
        c = 7000.0 + 2 * j
        s.add(QuoteDaily(symbol="SPX", date=d, open=c, high=c + 30, low=c - 30, close=c, volume=0))
        s.add(QuoteDaily(symbol="SPY", date=d, open=c / 10, high=c / 10 + 3, low=c / 10 - 3,
                         close=c / 10, volume=1_000_000))
        if d not in BOOK_DAYS:
            s.add(VixData(date=d, vix=18.0 + (j % 5)))
    s.commit()
    return s
