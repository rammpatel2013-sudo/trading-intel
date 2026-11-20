"""Chain bank (2026-11-20): pure maths + one job run on a fake CVForge into the OWNER tables (SQLite)."""

import math
import re
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from trading_intel import chain_bank as cb
from trading_intel.memory.models import Base, GreeksChain, GreeksSnapshot
from trading_intel.memory.models_equity import EquityMember
from trading_intel.memory.models_flow import Bars1m
from trading_intel.scheduler.jobs import chain_bank as J

DAY = date(2026, 11, 20)
REPO = Path(__file__).resolve().parents[2]


def test_universe_indexes_first_and_deduped():
    u = cb.universe(["AAPL", "msft", "SPX"], ["AAPL", "PDD"], ["XLK", "BRK-B"])
    assert u[:5] == list(cb.INDEXES)
    assert u.count("AAPL") == 1 and "MSFT" in u and "PDD" in u and "BRK-B" in u and "TLT" in u
    assert u[5:] == sorted(u[5:])


def test_batches_respect_cap_and_isolate_big_chains():
    counts = {"I:SPX": 30_000, "SPY": 12_000, "A": 5_000, "B": 4_000, "C": 3_000, "D": 200}
    g = cb.batches(counts, limit=9_500)
    assert ["I:SPX"] in g and ["SPY"] in g
    for grp in g:
        if len(grp) > 1:
            assert sum(counts[x] for x in grp) <= 9_500
    assert sorted(x for grp in g for x in grp) == sorted(counts)


def test_sql_list_drops_unsafe():
    assert cb.sql_list(["AAPL", "I:SPX", "BRK.B", "x'; drop table t;--"]) == "'AAPL','I:SPX','BRK.B'"


def test_parse_ticker():
    assert cb.parse_ticker("O:SPXW261211P06200000") == ("SPXW", "2026-12-11", "P", 6200.0)
    assert cb.parse_ticker("O:BRK.B261218C00500000") == ("BRK.B", "2026-12-18", "C", 500.0)
    assert cb.parse_ticker("junk") is None


def test_const_maturity_total_variance():
    v = cb.const_maturity([20, 40], [0.2, 0.3], 30)
    assert v == pytest.approx(math.sqrt((0.8 + (3.6 - 0.8) / 2) / 30))
    assert cb.const_maturity([20, 40], [0.2, 0.3], 70) is None      # beyond 1.5x the last expiry
    assert cb.const_maturity([20, 40], [0.2, 0.3], 50) == 0.3       # flat within 1.5x
    assert cb.const_maturity([], [], 30) is None


def _raw(und_cv: str, spot=100.0, dtes=(21, 63), ivs=(0.30, 0.25), root=None, step=5.0, lo=70, hi=131):
    rows = []
    root = root or und_cv.replace("I:", "")
    for t, base in zip(dtes, ivs, strict=True):
        exp = DAY + timedelta(days=t)
        for k in np.arange(lo, hi, step):
            m = (k - spot) / spot
            for cp, d in (("call", max(0.02, min(0.98, 0.5 - 2.5 * m))), ("put", -max(0.02, min(0.98, 0.5 + 2.5 * m)))):
                rows.append({
                    "ticker": f"O:{root}{exp:%y%m%d}{cp[0].upper()}{int(round(k * 1000)):08d}",
                    "underlying_ticker": und_cv, "contract_type": cp, "expiration_date": exp.isoformat(),
                    "strike_price": float(k), "implied_volatility": base - 0.10 * m, "delta": d, "gamma": 0.01,
                    "theta": -0.01, "vega": 0.1, "open_interest": 100.0, "day_volume": 10.0 if cp == "call" else 20.0,
                    "day_close": 2.0, "day_vwap": 2.0, "underlying_price": spot})
    return pd.DataFrame(rows)


def test_vol_summary_one_name():
    raw = _raw("XYZ")
    d = cb.add_greeks(cb.tidy(raw, {"XYZ": "XYZ"}), DAY, DAY)
    s = cb.vol_summary(d, 100.0, DAY)
    assert 0.25 < s["iv30"] < 0.30 and s["iv90"] == pytest.approx(0.25)
    assert s["ts_30_90"] > 1 and s["skew25_30"] > 0
    assert s["call_premium_usd"] == pytest.approx(len(raw) / 2 * 10 * 2 * 100)
    assert s["put_oi"] == s["call_oi"] == pytest.approx(len(raw) / 2 * 100)


def test_greeks_match_cvforge_chain_synthesis():
    from trading_intel.greeks.black_scholes import bs_vanna, years_to_expiry

    d = cb.add_greeks(cb.tidy(_raw("XYZ"), {"XYZ": "XYZ"}), DAY, DAY)
    t = years_to_expiry(d["expiry"], DAY)
    assert np.allclose(d["vanna"], bs_vanna(100.0, d["strike"].to_numpy(float), d["iv"].to_numpy(float), t))
    assert np.allclose(d["gxoi"], d["gamma"] * d["oi"]) and np.allclose(d["dxoi"], d["delta"] * d["oi"])


def test_settled_expiries_dropped():
    d = cb.add_greeks(cb.tidy(_raw("XYZ", dtes=(0, 30), ivs=(0.3, 0.3)), {"XYZ": "XYZ"}), DAY, DAY + timedelta(days=1))
    assert (d["expiry"].dt.date > DAY).all()


def test_pacer_spaces_calls():
    t = [0.0]
    slept = []
    p = J.Pacer(60, clock=lambda: t[0], sleep=lambda x: slept.append(x))
    for _ in range(3):
        p.wait()
    assert slept == [1.0, 2.0]


class FakeCV:
    def __init__(self, chain: pd.DataFrame):
        self.chain = chain

    def _post(self, path, body):
        sql = body["sql"]
        if "count(*)" in sql:
            g = self.chain.groupby("underlying_ticker").size()
            return {"rows": [{"u": u, "n": int(n)} for u, n in g.items() if f"'{u}'" in sql]}
        names = [u for u in self.chain["underlying_ticker"].unique() if f"'{u}'" in sql]
        d = self.chain[self.chain["underlying_ticker"].isin(names)].sort_values("ticker")
        off = int(sql.split("offset")[1])
        return {"rows": d.iloc[off:off + cb.PAGE].to_dict("records")}

    def fmp(self, ep, params):
        if ep == "etf/holdings":
            return [{"asset": "XYZ"}]
        return [{"date": f"{params['from']} 09:31:00", "open": 1, "high": 2, "low": 1, "close": 2, "volume": 100},
                {"date": "2026-11-19 15:59:00", "open": 1, "high": 1, "low": 1, "close": 1, "volume": 5}]


def _db():
    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng, tables=[EquityMember.__table__, GreeksChain.__table__, GreeksSnapshot.__table__,
                                          Bars1m.__table__])
    return eng


def test_job_writes_owner_tables_and_keeps_both_roots():
    # SPX: AM root 'SPX' and PM root 'SPXW' on the SAME expiry + strikes (monthly) -> both kept (root in the key)
    spx = pd.concat([_raw("I:SPX", spot=6000, root="SPX", step=1, lo=5000, hi=7000),
                     _raw("I:SPX", spot=6000, root="SPXW", step=1, lo=5000, hi=7000)], ignore_index=True)
    chain = pd.concat([spx, _raw("XYZ"), _raw("SPY", spot=600, lo=500, hi=700)], ignore_index=True)
    assert len(spx) > cb.PAGE                                         # SPX is paged
    eng = _db()
    with Session(eng) as s:
        s.add(EquityMember(symbol="XYZ", index_code="SP500", start_date=date(2020, 1, 1), end_date=None, source="t"))
        s.commit()
        kw = dict(settings=object(), cvforge=FakeCV(chain), force=True, pacer=J.Pacer(10**9, sleep=lambda x: None))
        r1 = J.run(s, now=datetime(2026, 11, 20, 11, 5), **kw)
        assert not r1["errors"], r1["errors"]
        assert r1["options"]["rows"] == len(chain) == r1["options"]["chain_rows"]
        n = s.execute(select(func.count()).select_from(GreeksChain)).scalar()
        assert n == len(chain)                                         # nothing lost, nothing doubled
        roots = {r for (r,) in s.execute(select(GreeksChain.root).where(GreeksChain.symbol == "SPX").distinct())}
        assert roots == {"SPX", "SPXW"}
        assert set(s.execute(select(GreeksChain.source).distinct()).scalars()) == {cb.BANK_SOURCE}
        snap = {r.symbol: r for r in s.execute(select(GreeksSnapshot)).scalars()}
        assert set(snap) == {"SPX", "XYZ", "SPY"}
        x = snap["XYZ"]
        assert x.source == cb.BANK_SOURCE and x.iv30 and x.iv90 and x.skew25_30 > 0 and x.n_contracts > 0
        assert x.gex_total is not None and x.call_premium_usd > 0 and x.call_oi > 0
        bars = s.execute(select(Bars1m)).scalars().all()
        assert {b.symbol for b in bars} >= {"SPX", "XYZ"} and all(b.ts.date() == DAY and b.source == "fmp" for b in bars)
        # next hour: new rows under a new ts; bars upserted (no duplicates)
        r2 = J.run(s, now=datetime(2026, 11, 20, 12, 5), **kw)
        assert not r2["errors"]
        assert s.execute(select(func.count()).select_from(GreeksChain)).scalar() == 2 * len(chain)
        assert s.execute(select(func.count()).select_from(GreeksSnapshot)).scalar() == 6
        assert s.execute(select(func.count()).select_from(Bars1m)).scalar() == len(bars)


def test_job_skips_weekend():
    with Session(_db()) as s:
        assert J.run(s, settings=object(), cvforge=object(), now=datetime(2026, 11, 21, 11, 5))["skipped"]


def test_every_greeks_snapshots_reader_states_its_source():
    """Rule 9 guard: a query on greeks_snapshots must say which source it reads, or the hourly bank rows
    (source cvforge_bank) silently change its answer."""
    pat = re.compile(r"select\(GreeksSnapshot|from greeks_snapshots|FROM greeks_snapshots", re.I)
    bad = []
    for f in list((REPO / "trading_intel").rglob("*.py")) + list((REPO / "scripts").rglob("*.py")):
        if "models" in f.name or "chain_bank" in str(f):
            continue
        lines = f.read_text(encoding="utf-8", errors="ignore").splitlines()
        for i, line in enumerate(lines):
            if pat.search(line) and "source" not in "\n".join(lines[max(0, i - 3):i + 10]).lower():
                bad.append(f"{f.relative_to(REPO)}:{i + 1}")
    assert not bad, f"say which source these read (e.g. source != 'cvforge_bank'): {bad}"
