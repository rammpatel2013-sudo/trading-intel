"""wide_feed (2026-11-09): the Orbs / Volume profile feed. Built on a synthetic raw pull (no NAS data needed)."""
import gzip, json, math, sys, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "cvxmap"))
import cvx_live as L          # noqa: E402
import cvx_collect as C       # noqa: E402
import wide_feed as W         # noqa: E402


def _bs(F, K, T, sig, kind):
    return L.bs76(F, K, T, sig, kind)


def make_rec(ts="2026-11-06T10:35:00", spot=7400.0, n_exp=6):
    """One fake 'idx' pull: SPX only, today's SPXW + next expiries (Mon..), strikes every 5 +-300."""
    now = datetime.datetime.fromisoformat(ts)
    today = now.date()
    params = list(C.PARAMS)
    eff = [p for p in params if p not in L.DROPPED]
    chain = []
    d = today
    for e in range(n_exp):
        if e:
            d = d + datetime.timedelta(days=1)
            while d.weekday() >= 5:
                d = d + datetime.timedelta(days=1)
        head = (d - datetime.date(1970, 1, 1)).days
        dte = (d - today).days
        T = (L.minutes_to_close(now) / 525600.0) if dte == 0 else dte / 365.0
        rows = []
        for i in range(-60, 61):
            k = spot + 5 * i
            sig = 0.16 + 0.0002 * abs(i)
            cpx, ppx = _bs(spot, k, T, sig, "C"), _bs(spot, k, T, sig, "P")
            def contract(px, cp):
                v = {p: 0.0 for p in eff}
                v.update(bid_price=max(px - 0.05, 0.0), ask_price=px + 0.05, price=px,
                         oi=1000 + 37 * abs(i) + (300 if cp == "C" else 0), volm=500 + 11 * abs(i),
                         volm_buy=300 + 3 * abs(i), volm_sell=200 + 8 * abs(i), delta=0.5)
                sym = ".SPXW%s%s%d" % (d.strftime("%y%m%d"), cp, int(k))
                return [sym] + [v[p] for p in eff]
            rows.append([k, contract(cpx, "C"), contract(ppx, "P")])
        chain.append([head, rows])
    return {"ts": ts, "pull": "idx", "symbols": ["SPX"], "exps": list(range(12)), "rng": 0.035,
            "params": params, "data": [{"spot": spot, "chain": chain}]}


def test_gamma_matches_writer_rows_on_the_0dte_book():
    rec = make_rec()
    now = datetime.datetime.fromisoformat(rec["ts"])
    dec = L.decode(rec)
    d = dec["SPX"]
    blk = W.build_slot("SPX", d, now.date(), now)
    # the writer's own rows for the same book, same T and sigma convention
    head0, book0 = L.pm_groups(d)[0]
    T = L.minutes_to_close(now) / 525600.0
    F, _ = L.parity_forward([(k, c, p) for k, (c, p) in book0.items()], d["spot"])
    sigma, _c = L.atm_iv(d["spot"], 5.0, book0, T, F)
    rows, _x = L.tos_rows("SPX", d["spot"], book0, T, sigma, {})
    by_k = dict(zip(blk["k"], blk["g0"]))
    va_k = dict(zip(blk["k"], blk["va0"]))
    for r in rows:
        assert abs(by_k[r["k"]] - r["gex"]) < 1e-6 + 1e-4 * abs(r["gex"])
        assert abs(va_k[r["k"]] - r["vanna"]) < 1e-6 + 1e-4 * abs(r["vanna"])


def test_width_and_books():
    rec = make_rec()
    now = datetime.datetime.fromisoformat(rec["ts"])
    blk = W.build_slot("SPX", L.decode(rec)["SPX"], now.date(), now)
    assert blk["k"][0] == 7200 and blk["k"][-1] == 7600 and len(blk["k"]) == 81
    assert [e["dte"] for e in blk["ex"]["0"]] == [0]
    assert all(1 <= e["dte"] <= 7 for e in blk["ex"]["7"]) and blk["ex"]["7"]
    # 1-7 DTE volume is the sum of several expiries -> larger than 0DTE alone at the same strike
    assert blk["c7"][40] > blk["c0"][40]


def test_raw_reader_skips_half_written_member_and_rebuild_writes_slots(tmp_path, monkeypatch):
    monkeypatch.setattr(W, "DATA", str(tmp_path))
    raw = tmp_path / "raw_20261106.ndjson.gz"
    times = ["2026-11-06T09:30:00", "2026-11-06T09:31:00", "2026-11-06T09:35:00", "2026-11-06T09:36:00"]
    for t in times:
        with gzip.open(raw, "at", encoding="utf-8") as fh:
            fh.write(json.dumps(make_rec(t, n_exp=3)) + "\n")
    good = raw.read_bytes()
    with gzip.open(raw, "at", encoding="utf-8") as fh:          # a 5th member, then cut it in half
        fh.write(json.dumps(make_rec("2026-11-06T09:37:00", n_exp=3)) + "\n")
    full = raw.read_bytes()
    raw.write_bytes(full[: len(good) + (len(full) - len(good)) // 2])
    rd = W.RawReader(str(raw))
    assert len(rd.new_records()) == 4
    raw.write_bytes(full)                                      # writer finishes the member
    assert len(rd.new_records()) == 1
    assert W.rebuild("20261106") == 0
    j = json.loads((tmp_path / "wide_20261106.json").read_text())
    sl = j["syms"]["SPX"]["slots"]
    assert [s["t"] for s in sl] == ["09:30", "09:35", "09:37"] and sl[-1].get("live") == 1
    assert len(j["syms"]["SPX"]["px"]) == 5
    assert json.loads((tmp_path / "wide_index.json").read_text())["days"] == ["20261106"]
