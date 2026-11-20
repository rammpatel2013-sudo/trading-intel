"""idxquote (2026-11-20): Cboe's delayed-quote API answered 403 from the NAS all day, so the VIX tab's regime board
and the vol board had no index quotes. Cboe stays first; after a fully refused round the same symbols come from FMP
via CVForge. No network here: the Cboe getter and the CVForge client are both fakes."""
import datetime
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "cvxmap"))
import idxquote as Q  # noqa: E402
import vixdash as V  # noqa: E402
import volx as X  # noqa: E402

TODAY = datetime.date(2026, 11, 20)
TS = 1795202491  # 2026-11-20 14:21:31 ET (the 11/20 probe's FMP quote time)


class FakeCV:
    """Answers FMP `quote` / `historical-price-eod/light` like the CVForge passthrough did on 11/20."""

    def __init__(self, missing=()):
        self.calls = []
        self.missing = set(missing)

    def fmp(self, endpoint, params):
        sym = params["symbol"]
        self.calls.append((endpoint, sym))
        if sym in self.missing:
            raise RuntimeError("no such symbol")
        if endpoint == "quote":
            px = {"^GSPC": 6992.35, "^VIX": 33.23, "^VIX9D": 34.9}.get(sym, 20.0)
            return [{"symbol": sym, "price": px, "previousClose": px - 1, "timestamp": TS}]
        days = [TODAY - datetime.timedelta(days=i) for i in range(0, 6)]   # newest first, includes today
        return [{"symbol": sym, "date": d.isoformat(), "price": 10.0 + i} for i, d in enumerate(days)]


def fb_with(cv, now=lambda: 1000.0):
    return Q.Fallback(client_factory=lambda: cv, now=now)


def test_et_iso_is_cboe_format_in_eastern():
    assert Q.et_iso(TS) == "2026-11-20T14:21:31"
    assert Q.et_iso(None) == "" and Q.et_iso("x") == ""


def test_quotes_map_symbols_and_keep_the_cboe_shape():
    cv = FakeCV(missing={"^VXAZN"})
    q = fb_with(cv).quotes(idx=["SPX", "VIX9D", "VXAZN"], etf=["VIXY"])
    assert ("quote", "^GSPC") in cv.calls and ("quote", "^VIX9D") in cv.calls and ("quote", "VIXY") in cv.calls
    assert set(q) == {"SPX", "VIX9D", "VIXY"}                        # one missing symbol never stops the rest
    assert q["SPX"] == {"v": 6992.35, "prev": 6991.35, "t": "2026-11-20T14:21:31", "c": 6992.35, "src": "fmp"}


def test_no_key_means_cboe_only_and_no_crash():
    def boom():
        raise RuntimeError("CVFORGE_API_KEY is not set in .env")

    fb = Q.Fallback(client_factory=boom)
    assert fb.quotes(idx=["VIX"]) == {} and fb.history(TODAY, idx=["VIX"]) == {}
    assert "CVFORGE_API_KEY" in fb.err


def test_breaker_pauses_cboe_then_retries():
    t = [1000.0]
    fb = fb_with(FakeCV(), now=lambda: t[0])
    assert fb.cboe_ok()
    fb.cboe_refused()
    assert not fb.cboe_ok()
    t[0] += Q.CBOE_PAUSE_S - 1
    assert not fb.cboe_ok()
    t[0] += 2
    assert fb.cboe_ok()


def test_history_sessions_before_today_oldest_first():
    h = fb_with(FakeCV()).history(TODAY, idx=["VIX"], etf=["VIXY"], keep=3)
    assert set(h) == {"VIX", "VIXY"}
    assert [d for d, _ in h["VIX"]] == [TODAY - datetime.timedelta(days=i) for i in (3, 2, 1)]
    assert all(d < TODAY for d, _ in h["VIXY"])


def _refuse(self, url, timeout=6):
    raise OSError("403 Forbidden")


def test_vix_tab_regime_falls_back_and_stops_asking_cboe(monkeypatch):
    calls = []

    def refuse(self, url, timeout=6):
        calls.append(url)
        raise OSError("403 Forbidden")

    monkeypatch.setattr(V._Regime, "_get", refuse)
    r = V._Regime()
    assert r._quotes() == {}                                             # not started: Cboe only, as before
    cv = FakeCV()
    r.fb = fb_with(cv)
    q = r._quotes()
    assert set(q) == set(V.QUOTE_SYMS) and q["VIX9D"]["v"] == 34.9 and q["VIX9D"]["src"] == "fmp"
    n = len(calls)
    q2 = r._quotes()                                                     # Cboe paused after the refused round
    assert len(calls) == n and set(q2) == set(V.QUOTE_SYMS)
    snap = V.build_regime(q, {}, TODAY)
    assert snap and snap["idx"]["VIX9D"] == 34.9


def test_vix_tab_keeps_cboe_quotes_when_they_come():
    def ok(self, url, timeout=6):
        class R:
            def json(self):
                return {"data": {"current_price": 21.0, "prev_day_close": 20.0, "last_trade_time": "2026-11-20T10:00:00"}}
        return R()

    r = V._Regime()
    cv = FakeCV()
    r.fb = fb_with(cv)
    r._get = ok.__get__(r)
    q = r._quotes()
    assert cv.calls == [] and q["VIX"]["src"] == "cboe" and q["VIX"]["v"] == 21.0


def test_vol_board_quotes_and_fund_history_fall_back(tmp_path, monkeypatch):
    monkeypatch.setattr(X.VolX, "_get", _refuse)
    cv = FakeCV()
    v = X.VolX(str(tmp_path), lambda: datetime.datetime(2026, 11, 20, 11, 0), fb_with(cv))
    q = v.quotes()
    assert set(q) == set(X.IDX + X.ETF) and all(x["src"] == "fmp" for x in q.values())
    h = v.history(TODAY)
    assert h.get("VIXY") and h.get("VIXM") and h.get("VIX")
    assert not any(e == "historical-price-eod/light" and s == "^VIXY" for e, s in cv.calls)   # funds keep plain symbols


def test_vol_board_without_fallback_is_unchanged(tmp_path, monkeypatch):
    monkeypatch.setattr(X.VolX, "_get", _refuse)
    v = X.VolX(str(tmp_path), lambda: datetime.datetime(2026, 11, 20, 11, 0))
    assert v.quotes() == {} and "403" in (v.err or "")


def test_volx_container_mounts_env_for_the_key():
    sh = (ROOT / "scripts" / "nas" / "run_volx.sh").read_text(encoding="utf-8")
    assert sh.count('-v "${REPO_DIR}/.env:/app/.env:ro"') == 2


def test_check_line_reads_both_files(tmp_path, capsys):
    import json
    (tmp_path / "live.json").write_bytes(json.dumps(
        {"VIXR": {"rows": [1, 2], "qsrc": ["fmp"], "idx": {"VIX9D": 34.9}}}).encode())
    (tmp_path / "volx.json").write_bytes(json.dumps({"q": {"VIX": {}}, "qsrc": ["fmp"]}).encode())
    assert Q.check(str(tmp_path)) == 0
    out = capsys.readouterr().out
    assert "VIX tab rows 2 sources ['fmp'] VIX9D 34.9 | vol board quotes 1 sources ['fmp']" in out
    assert Q.check(str(tmp_path / "missing")) == 1
