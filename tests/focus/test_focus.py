"""Focus names (2026-11-20): day rule, owner table, Convex -> DB feed, the writer, the collector budget,
the levels card, the cockpit box endpoint, and that older readers never see convex_focus rows."""

import datetime as dt
import gzip
import json
import math
import os
import re
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pandas as pd
import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from trading_intel import chain_bank as cb
from trading_intel import focus
from trading_intel.focus import card, feed
from trading_intel.memory.models import Base, FocusName, GreeksChain, GreeksSnapshot
from trading_intel.memory.models_flow import Bars1m

REPO = Path(__file__).resolve().parents[2]
CVX = REPO / "scripts" / "cvxmap"
sys.path.insert(0, str(CVX))
import cvx_collect  # noqa: E402
import cvx_live  # noqa: E402

FRI = dt.date(2026, 11, 20)
MON = dt.date(2026, 11, 23)


TABLES = [FocusName.__table__, GreeksChain.__table__, GreeksSnapshot.__table__, Bars1m.__table__]


@pytest.fixture
def db(tmp_path):
    eng = create_engine(f"sqlite:///{tmp_path / 'f.db'}")
    Base.metadata.create_all(eng, tables=TABLES)
    with Session(eng) as s:
        yield s


# ---------------------------------------------------------------- synthetic ConvexValue focus pull
def _bs(S, K, T, sig, cp):
    if T <= 0:
        T = 1 / 525600
    d1 = (math.log(S / K) + 0.5 * sig * sig * T) / (sig * math.sqrt(T))
    d2 = d1 - sig * math.sqrt(T)
    N = lambda x: 0.5 * (1 + math.erf(x / math.sqrt(2)))  # noqa: E731
    n = math.exp(-d1 * d1 / 2) / math.sqrt(2 * math.pi)
    px = S * N(d1) - K * N(d2) if cp == "C" else K * N(-d2) - S * N(-d1)
    delta = N(d1) if cp == "C" else N(d1) - 1
    return px, delta, n / (S * sig * math.sqrt(T)), S * n * math.sqrt(T) / 100


def _side(sym, S, K, T, cp, head):
    px, de, ga, ve = _bs(S, K, T, 0.45, cp)
    v = {p: 0 for p in cvx_collect.PARAMS}
    v.update({"bid_price": max(px - 0.05, 0.01), "ask_price": px + 0.05, "price": px, "volm": 100 + int(K) % 37,
              "oi": int(3000 * math.exp(-((K / S - (1.04 if cp == "C" else 0.96)) / 0.03) ** 2)) + 50, "volatility": 0.45, "delta": de, "gamma": ga, "vega": ve,
              "theta": -0.1, "volm_buy": 60, "volm_sell": 40, "size": 1})
    d = cvx_live.datetime.date(1970, 1, 1) + dt.timedelta(days=head)
    tag = ".%s%s%s%s" % (sym, d.strftime("%y%m%d"), cp, ("%g" % K).replace(".", "_"))
    return [tag] + [v[p] for p in cvx_collect.PARAMS]


def _entry(sym, S, step, day, dtes=(0, 7, 14, 21)):
    chain = []
    for dte in dtes:
        exp = day + dt.timedelta(days=dte)
        head = (exp - dt.date(1970, 1, 1)).days
        T = max(dte, 0.25) / 365
        rows = []
        k = round((S * 0.88) / step) * step
        while k <= S * 1.12:
            k = round(k, 4)
            rows.append([k, _side(sym, S, k, T, "C", head), _side(sym, S, k, T, "P", head)])
            k += step
        chain.append([head, rows])
    return {"spot": S, "chain": chain}


def _rec(ts, syms=(("NVDA", 180.3, 2.5), ("AMD", 151.2, 1.0)), day=FRI):
    return {"ts": ts, "pull": "focus", "symbols": [s for s, *_ in syms], "exps": [0, 1, 2, 3], "rng": 0.12,
            "params": cvx_collect.PARAMS, "elapsed_s": 0.5, "bytes": 1,
            "data": [_entry(s, S + 0.1 * int(ts[15:16] or 0), st, day) for s, S, st in syms]}


# ---------------------------------------------------------------- day rule + owner table
def test_clean():
    assert focus.clean([" nvda", "$amd", "NVDA", ""]) == ["NVDA", "AMD"]
    for bad in (["SPY"], ["NV DA"], ["A", "B", "C", "D", "E"]):
        with pytest.raises(focus.FocusError):
            focus.clean(bad)


def test_day_rule():
    assert focus.edit_day(dt.datetime(2026, 11, 20, 8, 0)) == FRI           # before the open: today
    assert focus.edit_day(dt.datetime(2026, 11, 20, 10, 0)) == MON          # in the session: next day
    assert focus.edit_day(dt.datetime(2026, 11, 20, 17, 0)) == MON          # after the close: next day
    assert focus.edit_day(dt.datetime(2026, 11, 21, 12, 0)) == MON          # weekend
    assert focus.edit_day(dt.datetime(2026, 11, 25, 18, 0)) == dt.date(2026, 11, 27)   # Thanksgiving skipped
    assert focus.locked(FRI, dt.datetime(2026, 11, 20, 9, 30)) and not focus.locked(FRI, dt.datetime(2026, 11, 20, 9, 29))


def test_set_state_live(db):
    pre = dt.datetime(2026, 11, 20, 8, 0)
    assert focus.set_picks(db, FRI, ["nvda", "AMD"], now=pre) == ["NVDA", "AMD"]
    focus.set_picks(db, FRI, ["TSLA", "NVDA"], now=pre)                       # replace
    assert focus.get_picks(db, FRI) == ["TSLA", "NVDA"]
    with pytest.raises(focus.FocusError):                                      # fixed after 09:30
        focus.set_picks(db, FRI, ["AAPL"], now=dt.datetime(2026, 11, 20, 9, 31))
    with pytest.raises(focus.FocusError):
        focus.set_picks(db, dt.date(2026, 11, 21), ["AAPL"], now=pre)          # Saturday
    s = focus.state(db, dt.datetime(2026, 11, 20, 11, 0))
    assert s["today_picks"] == ["TSLA", "NVDA"] and s["today_locked"] and s["edit_day"] == "2026-11-23"
    assert s["live"] == ["TSLA", "NVDA"]
    assert focus.live_picks(db, dt.datetime(2026, 11, 21, 11, 0)) == []        # weekend: nothing pulled
    rows = db.execute(select(FocusName)).scalars().all()
    assert [(r.slot, r.symbol, r.source) for r in rows] == [(1, "TSLA", "cockpit"), (2, "NVDA", "cockpit")]


# ---------------------------------------------------------------- Convex -> owner tables
def test_feed_records_and_idempotent_write(db):
    dec = cvx_live.decode(_rec("2026-11-20T10:05:00"))
    ts = dt.datetime(2026, 11, 20, 10, 5)
    chain, snaps = feed.records(dec, ["NVDA", "AMD"], FRI, ts, ts)
    assert chain and {r["source"] for r in chain} == {cb.FOCUS_SOURCE}
    assert {r["root"] for r in chain} == {"NVDA", "AMD"}
    assert {r["symbol"] for r in snaps} == {"NVDA", "AMD"} and all(r["source"] == cb.FOCUS_SOURCE for r in snaps)
    nv = next(r for r in snaps if r["symbol"] == "NVDA")
    assert nv["gex_total"] is not None and nv["iv30"] is None or nv["iv30"] > 0       # 21 DTE max -> iv30 within 1.5x
    assert all(r["vanna"] is not None for r in chain if r["iv"])
    assert feed.write(db, chain, snaps) == (len(chain), len(snaps))
    feed.write(db, chain, snaps)                                               # same pull again: no duplicates
    assert db.scalar(select(func.count()).select_from(GreeksChain)) == len(chain)
    assert db.scalar(select(func.count()).select_from(GreeksSnapshot)) == 2


def test_old_readers_skip_focus_rows(db):
    from trading_intel.dashboard import ticker_data, walls

    old, new = dt.datetime(2026, 11, 19, 16, 0), dt.datetime(2026, 11, 20, 10, 5)
    db.add(GreeksChain(symbol="NVDA", ts=old, expiry=FRI, strike=180, cp="C", oi=10, gxoi=1.0, source="convex"))
    db.add(GreeksChain(symbol="NVDA", ts=new, expiry=FRI, strike=200, cp="C", oi=10, gxoi=9.0, source=cb.FOCUS_SOURCE))
    db.add(GreeksChain(symbol="NVDA", ts=new, expiry=FRI, strike=210, cp="C", oi=10, gxoi=9.0, source=cb.BANK_SOURCE))
    db.commit()
    assert ticker_data.latest_chain_ts(db, "NVDA") == old
    h = walls.load_wall_history(db, "NVDA", days=5)
    assert len(h) == 1 and h[0]["call_wall"] == 180


def test_every_greeks_chain_reader_states_its_source():
    """Rule 9 guard (2026-11-20): a query on greeks_chain must say which source it reads, or the bank / focus rows
    (partial chains, other cadence) silently change its answer."""
    pat = re.compile(r"select\(GreeksChain\b|from greeks_chain\b|FROM greeks_chain\b", re.I)
    bad = []
    for f in list((REPO / "trading_intel").rglob("*.py")) + list((REPO / "scripts").rglob("*.py")):
        if "models" in f.name or "chain_bank" in str(f) or "focus" in str(f) or "jobs" in str(f):
            continue          # writers (jobs) and the two owners filter by source themselves
        lines = f.read_text(encoding="utf-8", errors="ignore").splitlines()
        for i, line in enumerate(lines):
            if pat.search(line) and "source" not in "\n".join(lines[max(0, i - 3):i + 6]).lower():
                bad.append(f"{f.relative_to(REPO)}:{i + 1}")
    assert not bad, f"say which source these read (e.g. source not in SIDE_SOURCES): {bad}"


# ---------------------------------------------------------------- the writer
def test_focus_live_writer(tmp_path, monkeypatch):
    import focus_live as FL

    monkeypatch.setattr(FL, "DATA", str(tmp_path))
    calls = []
    monkeypatch.setattr(FL, "to_db", lambda dec, syms, today, ts: calls.append((ts, tuple(syms))) or (1, 1))
    monkeypatch.setattr(FL.L, "now_et", lambda: dt.datetime(2026, 11, 20, 10, 7, 30))
    monkeypatch.setattr(FL.L, "today_et", lambda: FRI)
    raw = tmp_path / "focus_raw_20261120.ndjson.gz"
    with gzip.open(raw, "at", encoding="utf-8") as fh:
        for t in ("10:04:00", "10:05:00", "10:06:00"):
            fh.write(json.dumps(_rec("2026-11-20T" + t)) + "\n")
    assert FL.process() == 3
    live = json.loads((tmp_path / "focus_live.json").read_text())
    assert live["syms"] == ["NVDA", "AMD"]
    nv = live["NVDA"]
    assert nv["step"] == 2.5 and len(nv["rows"]) >= 15 and nv["dte"] == 0 and nv["spot"] > 170
    assert all(r["k"] in {round(x * 2.5, 4) for x in range(40, 120)} for r in nv["rows"])
    assert live["AMD"]["step"] == 1
    lines = (tmp_path / "focus_flow_NVDA_20261120.ndjson").read_text().splitlines()
    assert len(lines) == 3 and json.loads(lines[-1])["t"] == "10:06" and json.loads(lines[0])["s"] == "NVDA"
    tail = json.loads((tmp_path / "focus_tail_NVDA.json").read_text())
    assert tail["size"] == (tmp_path / "focus_flow_NVDA_20261120.ndjson").stat().st_size and tail["text"].count("\n") == 3
    # DB: the first pull of each name, then the 5-minute marks only
    assert [c[0][11:16] for c in calls] == ["10:04", "10:05"]
    with gzip.open(raw, "at", encoding="utf-8") as fh:                         # incremental pass
        fh.write(json.dumps(_rec("2026-11-20T10:07:00")) + "\n")
    assert FL.process() == 1
    assert len((tmp_path / "focus_flow_NVDA_20261120.ndjson").read_text().splitlines()) == 4
    assert not (tmp_path / "live.json").exists() and not list(tmp_path.glob("flow_2*"))   # never the index files


def test_strike_step():
    import focus_live as FL

    e = cvx_live.decode(_rec("2026-11-20T10:04:00"))
    assert FL.strike_step(e["NVDA"]) == 2.5 and FL.strike_step(e["AMD"]) == 1.0
    assert FL.strike_step({"spot": 0, "exps": []}) == 1.0


# ---------------------------------------------------------------- the collector: never more than 3 requests a minute
def test_collector_budget(monkeypatch, tmp_path):
    monkeypatch.setattr(cvx_collect, "DATA", str(tmp_path))
    monkeypatch.setattr(cvx_collect, "login", lambda: object())
    monkeypatch.setattr(cvx_collect, "focus_symbols", lambda: ["NVDA", "AMD"])
    seen = []

    def fake_pull(S, spec):
        seen.append((len(clock) - 1, spec["name"], tuple(spec["symbols"])))
        return {"data": []}, 0.1, 10

    clock = []

    def fake_now():
        clock.append(1)
        n = len(clock)
        return dt.datetime(2026, 11, 20, 10, 0) if n < 200 else dt.datetime(2026, 11, 20, 17, 0)

    monkeypatch.setattr(cvx_collect, "pull", fake_pull)
    monkeypatch.setattr(cvx_collect, "now_et", fake_now)
    monkeypatch.setattr(cvx_collect.time, "sleep", lambda s: None)
    cvx_collect.main()
    per_cycle = {}
    cyc = -1
    for _i, name, syms in seen:
        if name == "idx":
            cyc += 1
        per_cycle.setdefault(cyc, []).append(name)
    assert all(len(v) <= 3 for v in per_cycle.values())
    assert all("focus" not in v for c, v in per_cycle.items() if c % 5 == 0)
    assert all("focus" in v for c, v in per_cycle.items() if c % 5)
    assert all(syms == ("NVDA", "AMD") for _i, n, syms in seen if n == "focus")
    assert (tmp_path / "focus_raw_20261120.ndjson.gz").exists()


def test_collector_no_picks_no_request(monkeypatch, tmp_path):
    monkeypatch.setattr(cvx_collect, "DATA", str(tmp_path))
    monkeypatch.setattr(cvx_collect, "_FOCUS", {"at": 0.0, "syms": [], "err_at": 0.0})
    import trading_intel.focus as F

    monkeypatch.setattr(F, "live_picks", lambda s: (_ for _ in ()).throw(RuntimeError("db down")))
    assert cvx_collect.focus_symbols() == []          # a database failure = no focus pull, no crash


# ---------------------------------------------------------------- the levels card
def test_value_area():
    ts = pd.date_range("2026-11-19 09:30", periods=200, freq="min")
    px = [100 + (i % 20) * 0.1 for i in range(200)]
    b = pd.DataFrame({"ts": ts, "high": [p + 0.05 for p in px], "low": [p - 0.05 for p in px], "close": px,
                      "volume": [1000 if 100.8 <= p <= 101.2 else 100 for p in px]})
    va = card.value_area(b)
    assert va["val"] <= va["poc"] <= va["vah"] and 100.7 <= va["poc"] <= 101.3


def test_card_build(db):
    for i, d in enumerate([dt.date(2026, 11, 16) + dt.timedelta(days=k) for k in range(5)]):
        db.add(GreeksSnapshot(symbol="NVDA", ts=dt.datetime.combine(d, dt.time(16, 5)), spot=175 + i, gex_total=1e8,
                              gex_flip=172.0, iv30=0.40 + 0.02 * i, iv90=0.42, ts_30_90=(0.40 + 0.02 * i) / 0.42,
                              skew25_30=0.03 + 0.005 * i, source=cb.BANK_SOURCE))
    last = dt.datetime(2026, 11, 20, 16, 5)
    for k, gc, gp, oi in ((175, 1, 5, 900), (180, 2, 1, 500), (185, 7, 0, 2000), (190, 3, 0, 100)):
        for exp in (FRI + dt.timedelta(days=7), FRI + dt.timedelta(days=35)):
            db.add(GreeksChain(symbol="NVDA", ts=last, expiry=exp, strike=k, cp="C", oi=oi, gxoi=gc, source=cb.BANK_SOURCE))
            db.add(GreeksChain(symbol="NVDA", ts=last, expiry=exp, strike=k, cp="P", oi=oi // 2, gxoi=gp, source=cb.BANK_SOURCE))
    for dday in (17, 18, 19, 20):
        for m in range(390):
            t = dt.datetime(2026, 11, dday, 9, 30) + dt.timedelta(minutes=m)
            p = 178 + (m % 30) * 0.1
            db.add(Bars1m(symbol="NVDA", ts=t, source="fmp", open=p, high=p + 0.1, low=p - 0.1, close=p, volume=1000))
    db.commit()
    c = card.build(db, "NVDA", dt.datetime(2026, 11, 20, 17, 0))
    assert c["in_bank"] and c["spot"] == 179 and c["walls_7d"] == {"call_wall": 185.0, "put_wall": 175.0}
    assert c["top_oi_7d"]["calls"][0] == {"k": 185.0, "oi": 2000}
    assert c["prior"]["d"] == "2026-11-20" and c["value_5d"]["poc"] > 177
    assert c["em"]["d1"] == pytest.approx(179 * 0.48 * math.sqrt(1 / 252), abs=0.01)
    assert len(c["trend"]) == 5 and any("ABOVE the gamma flip" in x for x in c["read"])
    assert any("richer than" in x for x in c["read"]) and any("Put skew has risen" in x for x in c["read"])
    assert card.build(db, "ZZZZ", dt.datetime(2026, 11, 20, 17, 0)) == {"symbol": "ZZZZ", "asof": "2026-11-20T17:00", "in_bank": False}


def test_focus_card_job(db, tmp_path):
    from trading_intel.scheduler.jobs import focus_card as J

    focus.set_picks(db, MON, ["NVDA"], now=dt.datetime(2026, 11, 20, 17, 0))
    out = J.run(db, out_dir=tmp_path, now=dt.datetime(2026, 11, 20, 17, 0))
    j = json.loads((tmp_path / "focus_card.json").read_text())
    assert list(j["cards"]) == ["NVDA"] and j["cards"]["NVDA"]["in_bank"] is False and out["picks"]["edit"] == ["NVDA"]


# ---------------------------------------------------------------- the cockpit box endpoint
def _port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def _call(url, body=None, headers=None):
    req = urllib.request.Request(url, data=None if body is None else json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json", **(headers or {})},
                                 method="GET" if body is None else "POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


@pytest.mark.parametrize("key", ["", "s3cret"])
def test_serve_focus_api(tmp_path, key):
    dbf = tmp_path / "f.db"
    Base.metadata.create_all(create_engine(f"sqlite:///{dbf}"), tables=TABLES)
    srv = tmp_path / "cvxmap"
    (srv / "data").mkdir(parents=True)
    (srv / "serve_cvx.py").write_bytes((CVX / "serve_cvx.py").read_bytes())
    (srv / "data" / "x.json").write_text('{"a":1}')
    env = dict(os.environ, PYTHONPATH=str(REPO), DATABASE_URL=f"sqlite:///{dbf}", CONVEX_EMAIL="a@b.c",
               CONVEX_PASSWORD="x", OLLAMA_HOST="http://x", FRED_API_KEY="x", DISCORD_WEBHOOK_URL="https://x.y/z",
               FOCUS_KEY=key)
    port = _port()
    p = subprocess.Popen([sys.executable, str(srv / "serve_cvx.py"), str(port)], env=env,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = "http://127.0.0.1:%d/" % port
    try:
        for _ in range(60):
            try:
                urllib.request.urlopen(base + "x.json", timeout=1)
                break
            except Exception:
                time.sleep(0.1)
        st, j = _call(base + "api/focus")
        assert st == 200 and j["max"] == 4 and j["key_needed"] == bool(key)
        day = j["edit_day"]
        hdr = {"X-Focus-Key": key} if key else {}
        if key:
            assert _call(base + "api/focus", {"day": day, "symbols": ["NVDA"]})[0] == 403
        st, j = _call(base + "api/focus", {"day": day, "symbols": ["nvda", "amd"]}, hdr)
        assert st == 200 and j["edit"] == ["NVDA", "AMD"]
        st, j = _call(base + "api/focus", {"day": day, "symbols": ["A", "B", "C", "D", "E"]}, hdr)
        assert st == 400 and "at most 4" in j["error"]
        assert _call(base + "api/focus", {"day": "2020-01-02", "symbols": ["A"]}, hdr)[0] == 400   # past day: fixed
        assert urllib.request.urlopen(base + "x.json", timeout=2).status == 200                    # files as before
        for _ in range(50):                                                                        # card in the background
            if (srv / "data" / "focus_card.json").exists():
                break
            time.sleep(0.1)
        assert json.loads((srv / "data" / "focus_card.json").read_text())["cards"]["NVDA"]["in_bank"] is False
    finally:
        p.terminate()
        p.wait(5)
