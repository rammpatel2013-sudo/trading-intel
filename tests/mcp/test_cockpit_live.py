"""get_cockpit_live / get_cockpit_surface - read the cockpit's live files (synthetic data)."""
from datetime import datetime
from types import SimpleNamespace

from trading_intel.mcp import cockpit_live as ck


def _row(k, spot, c_oi, p_oi, cv=100, pv=100, cvb=60, cvs=40, pvb=30, pvs=70):
    m = max(0.5, spot - k) if k < spot else 0.5
    n = max(0.5, k - spot) if k > spot else 0.5
    d = (spot - k) / 100.0
    return {"k": k, "cOI": c_oi, "pOI": p_oi, "cIV": 0.15, "pIV": 0.18 + max(0, spot - k) / 2000,
            "cD": max(0.01, min(0.99, 0.5 + d)), "pD": -max(0.01, min(0.99, 0.5 - d)),
            "cV": cv, "pV": pv, "cVb": cvb, "cVs": cvs, "pVb": pvb, "pVs": pvs, "cM": m, "pM": n,
            "cPrem": cv * m * 100 / 1e6, "pPrem": pv * n * 100 / 1e6, "gex": (c_oi - p_oi) / 1e4, "dex": 0.0,
            "cGEX": c_oi / 1e4, "pGEX": p_oi / 1e4}


def _live(spot=5000.0):
    rows = [_row(k, spot, c_oi=3000 if k >= 5000 else 500, p_oi=3000 if k < 5000 else 500) for k in range(4900, 5105, 5)]
    rows[[r["k"] for r in rows].index(5050)]["cGEX"] = 9.0
    rows[[r["k"] for r in rows].index(4950)]["pGEX"] = 8.0
    return {"ts": "2026-11-16T10:30:05", "SPX": {"spot": spot, "open": 5040.0, "pct": spot / 5040.0 - 1, "iv": 0.16,
            "straddle": 30.0, "exp": "2026-11-16", "step": 5, "T_greeks": 4.0 / (24 * 365), "rows": rows,
            "tl": [["10:00", 5030.0], ["10:15", 4990.0], ["10:30", spot]],
            "ser": [["10:00", 1.0, 0.5], ["10:30", -0.4, -0.2]],
            "term": [{"dte": 0, "iv": 0.16, "move": 0.006}, {"dte": 3, "iv": 0.14, "move": 0.012}], "mins_to_close": 330},
            "VIX": {"spot": 19.5}}


def test_summary_has_price_gamma_flow_and_skew():
    out = ck.summarize_live(_live(), "spx", now=datetime(2026, 11, 16, 10, 31, 5))
    assert out["found"] and out["age_min"] == 1.0 and out["spot"] == 5000.0 and out["pct_from_open"] < 0
    g = out["gamma"]
    assert g["call_wall"] == 5050 and g["put_wall"] == 4950
    assert g["flip"] is not None and 4990 <= g["flip"] <= 5010          # calls above, puts below -> flip near 5000
    assert out["expected_move"]["lower"] == 4970.0
    f = out["flow_0dte"]
    assert f["calls_bought"] > f["calls_sold"] and f["puts_sold"] > f["puts_bought"] and f["lean"] == "bullish"
    s = out["skew_0dte"]
    assert s["rr25_pts"] is not None and s["rr25_pts"] > 0                # puts richer than calls
    assert out["recent_low"] == 4990.0 and out["vix"] == 19.5 and out["gamma_trend"]["net_gex_now"] == -0.4


def test_missing_symbol_and_fetch_fallback(monkeypatch):
    assert ck.summarize_live({"ts": "x"}, "QQQ")["found"] is False
    calls = []

    def _get(url, timeout, params):
        calls.append(url)
        if "192.168" in url:
            raise OSError("lan down")
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"ok": 1})

    monkeypatch.setattr(ck.httpx, "get", _get)
    s = SimpleNamespace(COCKPIT_URL="http://192.168.1.211:8899", COCKPIT_URL_ALT="https://cockpit.example.ts.net")
    data, err = ck.fetch_json(s, "live.json")
    assert data == {"ok": 1} and err is None and calls[-1] == "https://cockpit.example.ts.net/live.json"


def test_surface_summary():
    sf = {"t": "10:30", "day": "2026-11-16", "x": 5000.0, "k": [4950, 5000, 5050], "beta": -1.6, "beta_src": "fit",
          "ex": [{"e": "2026-11-16", "dte": 0, "fam": "PM", "iv": 0.16}, {"e": "2026-11-20", "dte": 4, "fam": "PM", "iv": 0.14}],
          "g": {"oi": [[0.1, 0.5, -0.2], [1.0, 2.0, 0.5]]}, "va": {"oi": [[0.0, 0.1, 0.0], [0.2, 0.2, 0.2]]}}
    out = ck.summarize_surface(sf)
    assert out["found"] and out["biggest_gamma_expiry"] == "2026-11-20" and out["by_expiry"][0]["gamma_near_spot_bn"] == 0.4
    assert out["spot_vol_beta"] == -1.6
