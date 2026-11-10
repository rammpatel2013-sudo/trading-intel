"""wide_feed (2026-11-09): the Orbs / Volume profile feed. Built on a synthetic raw pull (no NAS data needed)."""
import gzip, json, math, sys, datetime
import pytest
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
            def contract(px, cp, i=i, sig=sig):
                v = {p: 0.0 for p in eff}
                v.update(bid_price=max(px - 0.05, 0.0), ask_price=px + 0.05, price=px,
                         oi=1000 + 37 * abs(i) + (300 if cp == "C" else 0), volm=500 + 11 * abs(i),
                         volm_buy=300 + 3 * abs(i), volm_sell=200 + 8 * abs(i), delta=0.5,
                         volatility=sig + (0.03 if (cp == "P") == (i < 0) else 0.0))
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
    assert j["v"] == 2 and j["slot_min"] == 1
    sl = j["syms"]["SPX"]["slots"]
    # 2026-11-09: one slot per pull (1 minute), nothing provisional
    assert [s["t"] for s in sl] == ["09:30", "09:31", "09:35", "09:36", "09:37"]
    assert all(s.get("dz") == 1 and "live" not in s for s in sl)
    assert len(j["syms"]["SPX"]["px"]) == 5
    assert json.loads((tmp_path / "wide_index.json").read_text())["days"] == ["20261106"]


def _blocks(spots):
    out = []
    for i, x in enumerate(spots):
        t = "2026-11-06T10:%02d:00" % i
        now = datetime.datetime.fromisoformat(t)
        rec = make_rec(t, spot=x, n_exp=3)
        # volume grows minute to minute (cumulative), so the change coding has something to do
        for e in rec["data"][0]["chain"]:
            for row in e[1]:
                for c in (row[1], row[2]):
                    eff = [p for p in rec["params"] if p not in L.DROPPED]
                    for f in ("volm", "volm_buy", "volm_sell"):
                        c[1 + eff.index(f)] += 7 * i
        out.append(W.build_slot("SPX", L.decode(rec)["SPX"], now.date(), now))
    return out


def test_day_file_coding_round_trip_exact_for_cumulative_fields():
    blocks = _blocks([7400.0, 7400.0, 7410.0, 7390.0])        # the strike window shifts between minutes
    assert blocks[0]["k"][0] != blocks[2]["k"][0]
    enc = W.encode_slots(blocks)
    assert all("k" not in e and e["dz"] == 1 for e in enc)
    dec = W.decode_slots(enc, 5.0)
    for a, b in zip(blocks, dec):
        assert b["k"] == a["k"]
        for g in ("0", "7"):
            for f in W.CUM_INT:
                assert b[f + g] == a[f + g]                       # contracts: exact
            for f in W.CUM_PREM:
                assert f + g not in b                               # 2026-11-09: premium not in the day file
                assert f + g in a and len(a[f + g]) == len(a["k"])  # ... the live block keeps it
            for f in W.SNAP:
                q = W.snap_scale(a[f + g])
                m = max(abs(x) for x in a[f + g])
                assert q == W.GQ or q <= m / W.SQ_MAX * 1.001        # 3 digits of the array's largest value
                assert all(abs(x - y) <= q / 2 * 1.0001 + 1e-12
                           for x, y in zip(a[f + g], b[f + g], strict=True))
    # cumulative changes are small numbers after the first slot
    assert max(abs(v) for v in enc[1]["c0"]) == 7 < max(abs(v) for v in enc[0]["c0"])


def test_live_files_written_only_for_fresh_pulls(tmp_path, monkeypatch):
    monkeypatch.setattr(W, "DATA", str(tmp_path))
    now = datetime.datetime(2026, 11, 6, 10, 41, 2)
    monkeypatch.setattr(L, "now_et", lambda: now)
    day = W.Day("20261106", live_mode=True)
    day.feed(make_rec("2026-11-06T10:20:00", n_exp=3))          # 21 min old: catch-up, no live file
    assert not (tmp_path / "wide_live_SPX.json").exists()
    day.feed(make_rec("2026-11-06T10:41:00", n_exp=3))          # fresh
    j = json.loads((tmp_path / "wide_live_SPX.json").read_text())
    assert j["slot"]["t"] == "10:41" and j["n"] == 2 and "dz" not in j["slot"] and j["slot_min"] == 1
    assert len(json.dumps(j)) < 60000                             # small enough to poll every 3 s
    assert day.live_writes == 1


def test_vix_pull_feeds_the_vix_map(tmp_path, monkeypatch):
    monkeypatch.setattr(W, "DATA", str(tmp_path))
    from .test_vix_map import make_vix_rec   # noqa: PLC0415
    day = W.Day("20261106")
    assert day.feed(make_vix_rec("2026-11-06T10:35:00"))
    assert day.feed(make_vix_rec("2026-11-06T10:36:00"))
    day.write()
    j = json.loads((tmp_path / "vixmap_20261106.json").read_text())
    assert [c["t"] for c in j["cols"]] == ["10:35", "10:36"] and j["cols"][-1]["live"] == 1
    assert not (tmp_path / "wide_20261106.json").exists()        # no idx pull yet -> no Orbs day file


def test_charm_matches_a_finite_difference_in_time():
    """2026-11-09 follow-ups: charm_u = d(delta)/dt with calendar time passing (r = q = 0), per year."""
    for s, k, t, sig in ((7400.0, 7400.0, 3 / 24 / 365, 0.14), (7400.0, 7350.0, 2 / 365, 0.18),
                         (7400.0, 7480.0, 0.5 / 365, 0.12), (612.0, 600.0, 6 / 365, 0.22)):
        h = t * 1e-4
        for kind in ("C", "P"):
            fd = (W.bs_delta0(s, k, t - h, sig, kind) - W.bs_delta0(s, k, t, sig, kind)) / h
            assert W.charm_u(s, k, t, sig) == pytest.approx(fd, rel=2e-3, abs=1e-6)
    assert W.charm_u(7400.0, 7400.0, 0.0, 0.2) == 0.0 and W.charm_u(7400.0, 7400.0, 0.01, None) == 0.0


def test_basis_fields_rebuild_the_map_books():
    """OI + vol = g + gv and OI + signed = g + gt, exactly the MAP's bookCP() books; vanna and charm the same way.
    Checked strike by strike on the 0DTE group against a direct sum over the raw contracts."""
    rec = make_rec()
    now = datetime.datetime.fromisoformat(rec["ts"])
    d = L.decode(rec)["SPX"]
    blk = W.build_slot("SPX", d, now.date(), now)
    grp, dte, t_g, sigma, bk, _ed, fwd, t_cal = W.expiry_books("SPX", d, now.date(), now)[0]
    assert grp == "0" and dte == 0 and t_cal == pytest.approx(L.minutes_to_close(now) / 525600.0)
    spot = d["spot"]
    for i, k in enumerate(blk["k"]):
        c, p = bk[k]
        gu, vu = W.greeks_u(spot, k, t_g, sigma)
        hu = W.charm_u(spot, k, t_cal, W.strike_iv(k, fwd, c, p, sigma))
        coi, poi, cv, pv = c["oi"], p["oi"], c["volm"], p["volm"]
        cb, cs, pb, ps = c["volm_buy"], c["volm_sell"], p["volm_buy"], p["volm_sell"]
        # the MAP's OI + signed book: call book cOI + (cs - cb), put book pOI + (pb - ps); dealer = calls - puts
        ois = (coi + cs - cb) - (poi + pb - ps)
        oiv = (coi + cv) - (poi + pv)
        gk, vk, hk = 100 * spot * spot * 0.01 / L.B, 100 * spot * 0.01 / L.B, 100 * spot / L.B / 8760
        assert blk["g0"][i] + blk["gt0"][i] == pytest.approx(gu * ois * gk, rel=1e-4, abs=2e-6)
        assert blk["g0"][i] + blk["gv0"][i] == pytest.approx(gu * oiv * gk, rel=1e-4, abs=2e-6)
        assert blk["va0"][i] + blk["vat0"][i] == pytest.approx(vu * ois * vk, rel=1e-4, abs=2e-6)
        assert blk["ch0"][i] == pytest.approx(hu * (coi - poi) * hk, rel=1e-4, abs=2e-6)
        assert blk["ch0"][i] + blk["chv0"][i] == pytest.approx(hu * oiv * hk, rel=1e-4, abs=2e-6)
        assert blk["ch0"][i] + blk["cht0"][i] == pytest.approx(hu * ois * hk, rel=1e-4, abs=2e-6)
    # per-strike IV: the synthetic smile rises away from the money, so the wing uses more than the flat ATM IV
    c, p = bk[blk["k"][0]]
    assert W.strike_iv(blk["k"][0], fwd, c, p, sigma) == pytest.approx(p["volatility"])
    # charm sign: an out-of-the-money call (strike above spot) loses delta as time passes; in-the-money gains
    assert W.charm_u(7400.0, 7500.0, 1 / 365, 0.15) < 0 < W.charm_u(7400.0, 7300.0, 1 / 365, 0.15)
