#!/usr/bin/env python3
"""risk_tab.py - created 2026-11-05. Nightly "Risk" cockpit tab -> scripts/cvxmap/data/risktab.json.

What it answers: how likely is a 3%+ SPX drop inside the next 5 sessions (and 5%+ inside 10)?

Score (tested 2010-2026, two-way out-of-sample, see project doc claude/risk-tab-20261105.md):
    logistic on  ln(VIX)  +  VIX/VIX3M 1-year percentile  +  NFCI 1-year percentile
  (NFCI dated by its release, obs date + 5 days, so nothing peeks ahead).
  Out of sample AUC 0.765/0.773 vs VIX alone 0.709/0.751.
Every other tile is CONTEXT: shown with its own measured lift (drop odds when the tile is in the
top 10% of its last year), but it added nothing once the three score inputs were known.

Data (end of day, ZERO Convex calls):
  CVForge FMP passthrough  ^SPX ^VIX ^VIX3M ^VIX9D ^VIX1D ^VVIX ^COR1M, ETFs, S&P 500 members
  FRED API                 NFCI, STLFSI4, ICE BofA CCC + AAA OAS, HY OAS (ICE only 3 years)
  NAS DB                   squeezemetrics_daily (GEX, DIX), greeks_snapshots (our SPX + Mag7 gamma)
  breadth history          scripts/risk/breadth_seed.csv.gz (2010-2026 point-in-time S&P members),
                           extended nightly into reports/risk/breadth_hist.csv.gz from current members.
Descriptor only (rule 4) - not a trade signal.

Run (NAS):  bash /var/services/homes/drmithil/trading-intel/scripts/nas/run_script.sh risk_tab
Flags: --no-db (skip DB tiles) --out PATH
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[1]
DATA_DIR = REPO / "scripts" / "cvxmap" / "data"
SEED = REPO / "scripts" / "risk" / "breadth_seed.csv.gz"
STORE = REPO / "reports" / "risk" / "breadth_hist.csv.gz"
ET = ZoneInfo("America/New_York")
UA = {"User-Agent": "trading-intel/1.0"}
START = "2009-06-01"
WIN = 252          # percentile window (1 trading year)
MINP = 126
HIST_DAYS = 780    # ~3 years of score history in the json
LONG = 756         # ~3 years per tile for the enlarged view
SPARK = 130        # ~6 months per tile sparkline

# ---- frozen model (fit 2010-11 .. 2026-11 on 4,009 days; refit only after a new test) ----
MODEL = {
    "ev5": {"w": [-2.705735510436382, 0.3361974306124018, 0.373316002299314, 0.5620375605667934],
            "mu": [2.8455778946461985, 0.4934775613519799, 0.44054084204666827],
            "sd": [0.3085342845680298, 0.30331006533545113, 0.3572944109244021], "base": 0.0903},
    "ev10": {"w": [-3.126933137765251, 0.05527134000497978, 0.5509940745249847, 0.7818559456745807],
             "mu": [2.8455778946461985, 0.4934775613519799, 0.44054084204666827],
             "sd": [0.3085342845680298, 0.30331006533545113, 0.3572944109244021], "base": 0.0671},
}
# out-of-sample calibration: predicted band -> what actually happened (days)
CALIB5 = [[0, .03, .019, 955], [.03, .06, .037, 1081], [.06, .10, .081, 802], [.10, .15, .126, 419],
          [.15, .25, .224, 415], [.25, .40, .260, 300], [.40, 1, .405, 37]]
CALIB10 = [[0, .03, .016, 1637], [.03, .06, .047, 956], [.06, .10, .074, 470], [.10, .15, .130, 368],
           [.15, .25, .212, 425], [.25, .40, .157, 153]]

# tile -> (group, label, role, lift5, lift10, calm5, verdict)
# lift = P(3% drop in 5d | tile in top 10% of its year) / base, 2010-2026; calm = same, VIX < 20 days only
TILES = [
 ("vix", "score", "VIX level", "score", 2.70, 2.40, 2.87, "in score"),
 ("vix_vix3m", "score", "VIX ÷ VIX3M (term structure)", "score", 2.34, 2.00, 2.06, "in score"),
 ("nfci", "score", "Financial conditions (Chicago Fed NFCI)", "score", 2.63, 2.95, 3.45, "in score — adds the most beyond VIX"),
 ("vix9d_vix", "vol", "VIX9D ÷ VIX (front of the curve)", "context", 2.46, 2.45, 2.76, "same information as VIX ÷ VIX3M"),
 ("vix_floor_up", "vol", "VIX floor rising (20d low vs 3 months ago)", "context", 2.02, 2.03, 2.57, "adds nothing beyond the score"),
 ("vix1d_vix", "vol", "VIX1D ÷ VIX", "context", 2.87, 2.12, 1.95, "since 2022 only · adds nothing"),
 ("rv_ratio", "vol", "Realized vol: 1-month ÷ 3-month", "context", 1.78, 1.64, 1.55, "Kurt's trigger: above 1 = turbulence rising · adds nothing beyond the score"),
 ("vix_overshoot", "vol", "VIX overshoot (VIX rose more than SPX's move explains)", "context", 1.70, 1.62, 1.62, "adds nothing beyond the score"),
 ("vrp_low", "vol", "VIX minus realized vol (21-day)", "context", 1.48, 1.50, 1.15, "weak · low or negative = realized vol catching up with implied"),
 ("vvix", "vol", "VVIX", "drop", 1.97, 1.76, 1.69, "hurts the score out of sample"),
 ("pct20_low", "breadth", "% of S&P above 20-day MA", "context", 2.09, 1.83, 1.45, "adds nothing beyond the score"),
 ("pct50_low", "breadth", "% of S&P above 50-day MA", "context", 2.15, 1.76, 1.73, "adds nothing beyond the score"),
 ("pct100_low", "breadth", "% of S&P above 100-day MA", "context", 2.20, 1.99, 1.63, "adds nothing beyond the score"),
 ("pct200_low", "breadth", "% of S&P above 200-day MA", "context", 2.32, 2.37, 2.07, "best breadth tile · adds nothing once NFCI is in"),
 ("nhnl_low", "breadth", "New highs − new lows (10-day avg)", "context", 2.08, 1.72, 1.51, "adds nothing beyond the score"),
 ("ad_lag20", "breadth", "Advance-decline line, 20-day change", "context", 1.78, 1.68, 1.65, "adds nothing beyond the score"),
 ("mcosc_low", "breadth", "McClellan oscillator", "context", 1.71, 1.48, 1.98, "weak"),
 ("mcsum_low", "breadth", "McClellan summation vs its 1-year average", "context", 1.84, 1.53, 1.71, "weak"),
 ("mc_div", "breadth", "McClellan divergence (SPX near high, summation falling)", "context", 1.98, 1.38, 2.22, "weak · only when SPX is near its high"),
 ("sb_dn5", "breadth", "Stocks down 4%+ in the last 5 days (S&P members)", "context", 2.40, 2.40, 1.83, "strong alone · adds nothing beyond the score"),
 ("sectors_below_50", "breadth", "Sector score: sectors below 50-day (of 9)", "context", 2.06, 1.81, 1.68, "adds nothing beyond the score"),
 ("lqd_ief_lag20", "credit", "IG credit vs Treasuries (LQD ÷ IEF, 20d)", "context", 2.43, 2.43, 2.86, "best credit tile · adds nothing once NFCI is in"),
 ("hyg_ief_lag20", "credit", "High yield vs Treasuries (HYG ÷ IEF, 20d)", "context", 1.81, 1.95, 1.67, "adds nothing beyond the score"),
 ("move_chg20", "credit", "Bond volatility (MOVE), 20-day change", "context", 1.72, 1.58, 1.80, "adds nothing beyond the score"),
 ("hy_oas", "credit", "High-yield spread (Kurt's warning line 5%)", "short", 2.92, 2.85, 1.44, "only 3 years of data · cannot score"),
 ("ccc_aaa", "credit", "CCC − AAA spread", "short", 1.32, 0.74, 0.86, "only 3 years of data · cannot score"),
 ("ccc_aaa_chg20", "credit", "CCC − AAA spread, 20-day change", "short", 3.75, 4.62, 2.55, "strong in 3 years · too short to trust"),
 ("hb_lb_lag20", "appetite", "High beta vs low beta (SPHB ÷ SPLV, 20d)", "context", 1.76, 1.89, 1.81, "weak · hurts the score out of sample"),
 ("xly_xlp_lag20", "appetite", "Discretionary vs staples (XLY ÷ XLP, 20d)", "context", 1.94, 2.18, 2.01, "adds nothing beyond the score"),
 ("sqgex_low", "gamma", "Market-wide dealer gamma (GEX, low = warning)", "context", 2.23, 2.07, 1.63, "adds nothing beyond the score"),
 ("our_spx_gamma", "gamma", "Our SPX net gamma (latest)", "untested", None, None, None, "5 months of data · untested"),
 ("our_mag7_neg", "gamma", "Mag7 names with negative gamma", "untested", None, None, None, "5 months of data · untested"),
 ("cor1m", "corr", "Implied correlation COR1M", "context", 2.91, 2.93, 1.90, "strong alone · adds nothing beyond the score"),
 ("cor1m_chg20", "corr", "COR1M, 20-day change", "context", 2.04, 2.16, 1.96, "adds nothing beyond the score"),
 ("gld_lead20", "corr", "Gold vs SPX (GLD ÷ SPX, 20d)", "context", 2.21, 2.10, 1.64, "adds nothing beyond the score"),
 ("spx_below_ma50", "corr", "SPX below its 50-day", "context", 2.38, 2.03, 2.05, "adds nothing beyond the score"),
 ("bbl_close", "corr", "Distance above the Bull/Bear Line (0.9 × record close)", "context", 2.86, 3.12, 2.96, "strong alone (3× on calm days) · adds nothing beyond the score"),
 ("kurt_bear", "kurt", "Kurt's 4-part dashboard: gamma · flow · realized vol · breadth", "context", None, None, None, "ranks risk worse than our score (0.645 vs 0.754) · all 4 bearish = 31% drop odds"),
]
KURT_RATES = {0: 0.053, 1: 0.067, 2: 0.133, 3: 0.141, 4: 0.313}  # P(3% drop in 5 sessions) by bearish count, 2011-2026
FLAG_STATS = {"false_allclear": {"n": 47, "rate": 0.362, "score_avg": 0.239},
              "fear_release": {"n": 490, "rate": 0.084, "up20": 0.74, "up20_all": 0.67}}
GROUPS = [("score", "In the score"), ("vol", "Volatility"), ("breadth", "Breadth"), ("credit", "Credit"),
          ("appetite", "Risk appetite"), ("gamma", "Dealer gamma"), ("kurt", "Kurt's dashboard (rebuilt)"), ("corr", "Correlation · havens · trend")]
SECT = ["XLY", "XLP", "XLU", "XLK", "XLF", "XLE", "XLI", "XLB", "XLV"]
FMP_SYMS = ["^SPX", "^VIX", "^VIX3M", "^VIX9D", "^VIX1D", "^VVIX", "^COR1M", "^MOVE", "SPHB", "SPLV", "LQD", "IEF", "HYG",
            "GLD"] + SECT


def env_val(name: str) -> str:
    v = os.environ.get(name)
    if v:
        return v
    try:
        m = re.search(rf'^{name}\s*=\s*"?([^"\n]+?)"?\s*$', (REPO / ".env").read_text(), re.M)
        return m.group(1).strip() if m else ""
    except OSError:
        return ""


def http_json(url: str, headers: dict | None = None, tries: int = 3):
    err = None
    for a in range(tries):
        try:
            req = urllib.request.Request(url, headers={**UA, **(headers or {})})
            return json.loads(urllib.request.urlopen(req, timeout=60).read())
        except Exception as e:  # noqa: BLE001
            err = e
            time.sleep(1.5 * (a + 1))
    raise RuntimeError(f"GET failed {url[:90]}: {err}")


def fmp(endpoint: str, **params):
    base = env_val("CVFORGE_BASE_URL") or "https://tap.convexvalue.com/api/data"
    url = f"{base.rstrip('/')}/fmp/stable/{endpoint}?" + urllib.parse.urlencode(params)
    r = http_json(url, {"Authorization": "Bearer " + env_val("CVFORGE_API_KEY")})
    return r.get("data", r) if isinstance(r, dict) else r


def fmp_close(sym: str, frm: str) -> pd.Series:
    rows = fmp("historical-price-eod/light", symbol=sym, **{"from": frm, "to": date.today().isoformat()})
    if not isinstance(rows, list) or not rows:
        return pd.Series(dtype=float, name=sym)
    s = pd.Series({pd.Timestamp(r["date"]): float(r["price"]) for r in rows if r.get("price") is not None}, name=sym)
    return s.sort_index()


def fred(series: str, lag_days: int) -> pd.Series:
    key = env_val("FRED_API_KEY")
    url = ("https://api.stlouisfed.org/fred/series/observations?" +
           urllib.parse.urlencode({"series_id": series, "api_key": key, "file_type": "json",
                                   "observation_start": START}))
    j = http_json(url)
    s = pd.Series({pd.Timestamp(o["date"]) + pd.Timedelta(days=lag_days): float(o["value"])
                   for o in j.get("observations", []) if o["value"] not in (".", "")}, name=series)
    return s.sort_index()


# ---------------------------------------------------------------- DB (optional context)
def db_connect():
    url = env_val("DATABASE_URL").replace("postgresql+psycopg://", "postgresql://").replace("postgresql+psycopg2://", "postgresql://")
    try:
        import psycopg  # type: ignore
        return psycopg.connect(url)
    except ImportError:
        import psycopg2  # type: ignore
        return psycopg2.connect(url)


def db_rows(conn, sql: str, args=()):
    cur = conn.cursor()
    cur.execute(sql, args)
    out = cur.fetchall()
    cur.close()
    return out


# ---------------------------------------------------------------- breadth
def breadth_tail(seed: pd.DataFrame) -> pd.DataFrame:
    """Extend breadth after the seed's last date from current S&P members (FMP)."""
    last = seed.index.max()
    if pd.Timestamp(date.today()) - last < pd.Timedelta(days=1):
        return seed
    members = fmp("sp500-constituent")
    syms = sorted({m["symbol"] for m in members if m.get("symbol")})
    frm = (last - pd.Timedelta(days=420)).date().isoformat()
    with ThreadPoolExecutor(8) as ex:
        cols = list(ex.map(lambda s: fmp_close(s, frm), syms))
    P = pd.DataFrame({c.name: c for c in cols if len(c) > 50}).sort_index()
    if P.empty:
        return seed
    ma = {k: P.rolling(k, min_periods=int(k * .9)).mean() for k in (20, 50, 100, 200)}
    hi = P.rolling(252, min_periods=200).max()
    lo = P.rolling(252, min_periods=200).min()
    ret = P.pct_change(fill_method=None)
    have = P.notna()
    B = pd.DataFrame(index=P.index)
    for k, m in ma.items():
        B[f"pct{k}"] = 100 * ((P > m) & have).sum(axis=1) / (have & m.notna()).sum(axis=1).replace(0, np.nan)
    B["nh"] = ((P >= hi) & have).sum(axis=1)
    B["nl"] = ((P <= lo) & have).sum(axis=1)
    B["adv"] = (ret > 0).sum(axis=1)
    B["dec"] = (ret < 0).sum(axis=1)
    B["n"] = have.sum(axis=1)
    B["up4"] = ((ret >= 0.04) & have).sum(axis=1)
    B["dn4"] = ((ret <= -0.04) & have).sum(axis=1)
    new = B[B.index > last].round(2)
    return pd.concat([seed, new]) if len(new) else seed


# ---------------------------------------------------------------- features
def pct_tail(x: pd.Series, k: int) -> pd.Series:
    """1-year percentile (share of the last 252 values <= today, NaN ignored) for the last k rows."""
    v = x.values.astype(float)
    n = len(v)
    out = np.full(n, np.nan)
    for i in range(max(0, n - k), n):
        if np.isnan(v[i]):
            continue
        w = v[max(0, i - WIN + 1): i + 1]
        w = w[~np.isnan(w)]
        if len(w) >= MINP:
            out[i] = (w <= v[i]).mean()
    return pd.Series(out, index=x.index)


def build(X: pd.DataFrame, B: pd.DataFrame) -> pd.DataFrame:
    s = X["^SPX"]
    lr = lambda a, b, n=20: np.log(X[a] / X[b]) - np.log(X[a] / X[b]).shift(n)  # noqa: E731
    F = pd.DataFrame(index=X.index)
    F["vix"] = X["^VIX"]
    F["vix_vix3m"] = X["^VIX"] / X["^VIX3M"]
    F["vix9d_vix"] = X["^VIX9D"] / X["^VIX"]
    F["vix1d_vix"] = X["^VIX1D"] / X["^VIX"]
    F["vvix"] = X["^VVIX"]
    F["vix_floor_up"] = X["^VIX"].rolling(20).min() - X["^VIX"].rolling(60).min().shift(20)
    F["sqgex_low"] = -X["sqgex"] if "sqgex" in X else np.nan
    F["cor1m"] = X["^COR1M"]
    F["cor1m_chg20"] = X["^COR1M"] - X["^COR1M"].shift(20)
    F["lqd_ief_lag20"] = -lr("LQD", "IEF")
    F["hyg_ief_lag20"] = -lr("HYG", "IEF")
    F["hb_lb_lag20"] = -lr("SPHB", "SPLV")
    F["xly_xlp_lag20"] = -lr("XLY", "XLP")
    F["gld_lead20"] = np.log(X["GLD"] / s) - np.log(X["GLD"] / s).shift(20)
    F["spx_below_ma50"] = -(s / s.rolling(50).mean() - 1)
    B = B.reindex(X.index)
    for k in (20, 50, 100, 200):
        F[f"pct{k}_low"] = -B[f"pct{k}"]
    F["nhnl_low"] = -(B.nh - B.nl).rolling(10).mean()
    ad = (B.adv - B.dec).cumsum()
    F["ad_lag20"] = -((ad - ad.shift(20)) / B.n)
    rana = (B.adv - B.dec) / (B.adv + B.dec) * 1000
    osc = rana.ewm(span=19, adjust=False).mean() - rana.ewm(span=39, adjust=False).mean()
    summ = osc.cumsum()
    F["mcosc_low"] = -osc
    F["mcsum_low"] = -(summ - summ.rolling(252, min_periods=120).mean())
    near = (s / s.rolling(20).max() - 1) > -0.02
    F["mc_div"] = (-(summ - summ.shift(10))).where(near)
    F["sectors_below_50"] = sum((X[c] < X[c].rolling(50).mean()).astype(float) for c in SECT)
    F["nfci"] = X["NFCI"]
    F["ccc_aaa"] = X["CCC"] - X["AAA"]
    F["ccc_aaa_chg20"] = F["ccc_aaa"] - F["ccc_aaa"].shift(20)
    r = np.log(s).diff()
    rv21 = r.rolling(21).std() * np.sqrt(252)
    rv63 = r.rolling(63).std() * np.sqrt(252)
    F["rv_ratio"] = rv21 / rv63
    F["_vrp"] = X["^VIX"] - 100 * rv21
    F["vrp_low"] = -F["_vrp"]
    F["move_chg20"] = X["^MOVE"] - X["^MOVE"].shift(20)
    F["hy_oas"] = X["HY"]
    bbl = 0.9 * s.cummax()
    F["_bbl"] = bbl
    F["_bbl_pct"] = 100 * (s / bbl - 1)
    F["bbl_close"] = -(s / bbl - 1)
    F["sb_dn5"] = B["dn4"].rolling(5).sum() if "dn4" in B else np.nan
    # VIX overshoot: dVIX vs 100*ln return, OLS on the PRIOR 126 sessions (same model as vol/vix_overshoot.py)
    dv = X["^VIX"].diff()
    rr = 100 * r
    mx = rr.shift(1).rolling(126, min_periods=60).mean()
    my = dv.shift(1).rolling(126, min_periods=60).mean()
    cxy = (rr * dv).shift(1).rolling(126, min_periods=60).mean() - mx * my
    vxx = (rr * rr).shift(1).rolling(126, min_periods=60).mean() - mx * mx
    beta = cxy / vxx
    F["vix_overshoot"] = dv - (my - beta * mx + beta * rr)
    # systematic-flow models for Kurt's 4-part dashboard (vol-control: inverse vol, 10% target, cap 1.5; CTA: 4-window trend)
    rvb = 0.5 * rv21 + 0.5 * rv63
    E = (0.10 / rvb).clip(0, 1.5)
    F["_vc10"] = E - E.shift(10)
    sig = sum(np.tanh((np.log(s) - np.log(s.shift(n))) / (rv63 * np.sqrt(n / 252))) for n in (20, 60, 120, 250)) / 4
    pos = sig * (0.10 / rvb).clip(0, 2)
    F["_cta10"] = pos - pos.shift(10)
    F["_pct200"] = B["pct200"]
    # display values (sign restored, plain units)
    F["_osc"] = osc
    F["_summ_dev"] = summ - summ.rolling(252, min_periods=120).mean()
    F["_ad20"] = (ad - ad.shift(20)) / B.n
    return F


SHOW = {  # tile -> (display column or callable, unit text, decimals)
 "vix": ("vix", "", 2), "vix_vix3m": ("vix_vix3m", "×", 3), "nfci": ("nfci", "", 3),
 "vix9d_vix": ("vix9d_vix", "×", 3), "vix_floor_up": ("vix_floor_up", " pts", 2), "vix1d_vix": ("vix1d_vix", "×", 3),
 "vvix": ("vvix", "", 1), "pct20_low": ("-pct20_low", "%", 0), "pct50_low": ("-pct50_low", "%", 0),
 "pct100_low": ("-pct100_low", "%", 0), "pct200_low": ("-pct200_low", "%", 0), "nhnl_low": ("-nhnl_low", "", 0),
 "ad_lag20": ("_ad20", " per member", 2), "mcosc_low": ("_osc", "", 0), "mcsum_low": ("_summ_dev", "", 0),
 "mc_div": ("-mc_div", " (10d chg)", 0), "sectors_below_50": ("sectors_below_50", " of 9", 0),
 "lqd_ief_lag20": ("-lqd_ief_lag20", "%", 2), "hyg_ief_lag20": ("-hyg_ief_lag20", "%", 2),
 "ccc_aaa": ("ccc_aaa", " pts", 2), "ccc_aaa_chg20": ("ccc_aaa_chg20", " pts", 2),
 "hb_lb_lag20": ("-hb_lb_lag20", "%", 2), "xly_xlp_lag20": ("-xly_xlp_lag20", "%", 2),
 "sqgex_low": ("-sqgex_low", " $bn", 2), "cor1m": ("cor1m", "", 2), "cor1m_chg20": ("cor1m_chg20", " pts", 2),
 "gld_lead20": ("gld_lead20", "%", 2), "spx_below_ma50": ("-spx_below_ma50", "%", 2),
 "rv_ratio": ("rv_ratio", "×", 2), "vix_overshoot": ("vix_overshoot", " pts", 2), "vrp_low": ("_vrp", " pts", 2),
 "sb_dn5": ("sb_dn5", " stocks", 0), "hy_oas": ("hy_oas", "%", 2), "move_chg20": ("move_chg20", " pts", 1),
 "bbl_close": ("_bbl_pct", "% above", 2),
}
ABOUT = {
 "vix": "The market's price for SPX swings over the next 30 days. Higher = more fear already priced.",
 "vix_vix3m": "1-month fear divided by 3-month fear. Above 1 means the near term is priced scarier than the next three months — stress is here now.",
 "nfci": "Chicago Fed weekly index of financial conditions (credit, funding, leverage, risk). Its rank against the past year adds the most beyond VIX. Counted from its release day.",
 "vix9d_vix": "9-day fear vs 30-day fear. Above 1 = the coming week is priced as the danger zone.",
 "vix_floor_up": "Is VIX's recent low higher than its low three months ago? A rising floor = fear not fully leaving.",
 "vix1d_vix": "Tomorrow's priced move vs the month's. A spike = an event or stress inside one day.",
 "rv_ratio": "How much SPX actually moved in the last month vs the last three. Above 1 = moves are getting bigger (Kurt turns cautious here).",
 "vix_overshoot": "Today's VIX change minus what SPX's move would normally cause. Positive = fear bid harder than the tape explains.",
 "vrp_low": "VIX minus what SPX actually realised. Near zero or negative = real moves are catching up with the fear priced.",
 "vvix": "Volatility of VIX. Shown for reference; it made the score worse in testing.",
 "pct20_low": "Share of S&P 500 members above their 20-day average. Low = short-term weakness is wide.",
 "pct50_low": "Share above the 50-day average.", "pct100_low": "Share above the 100-day average.",
 "pct200_low": "Share above the 200-day average. Kurt's regime line is 40%.",
 "nhnl_low": "New 52-week highs minus new lows, 10-day average. Negative = more stocks breaking down than up.",
 "ad_lag20": "Advancers minus decliners added up over 20 days, per member. Falling = most stocks are slipping.",
 "mcosc_low": "McClellan oscillator: short vs long average of daily breadth. Below zero = breadth momentum down.",
 "mcsum_low": "McClellan summation (running total of the oscillator) vs its own 1-year average.",
 "mc_div": "Only read when SPX is within 2% of its 20-day high: is the summation falling while price holds up?",
 "sb_dn5": "How many S&P 500 members fell 4% or more in a day, added over 5 days (Stockbee-style). Big counts = selling is broad, not just the big names.",
 "sectors_below_50": "How many of the 9 sector funds are below their 50-day average.",
 "lqd_ief_lag20": "Investment-grade bonds vs Treasuries over 20 days. Lagging = lenders want more pay for risk.",
 "hyg_ief_lag20": "Junk bonds vs Treasuries over 20 days.",
 "move_chg20": "Treasury-market volatility (the bond VIX), change over 20 days.",
 "hy_oas": "Extra yield on junk bonds over Treasuries. Kurt's warning line is 5% (where 2018 and 2022 selloffs topped). Only 3 years of history here.",
 "ccc_aaa": "Gap between the weakest (CCC) and strongest (AAA) corporate bond spreads.",
 "ccc_aaa_chg20": "20-day change in that gap.",
 "hb_lb_lag20": "High-beta stocks vs low-beta stocks over 20 days. Lagging = risk appetite fading.",
 "xly_xlp_lag20": "Consumer discretionary vs staples over 20 days.",
 "sqgex_low": "Market-wide dealer gamma. Low = dealers hedge in the direction of moves, so moves run further.",
 "our_spx_gamma": "Our own SPX net dealer gamma (evening snapshot) and where SPX sits vs the gamma flip. History starts May 2026, so the track record below counts drop days as they come; it is judged once there are 20.",
 "our_mag7_neg": "How many of the Mag7 names have negative dealer gamma (evening snapshot). History starts May 2026; judged once there are 20 drop days.",
 "cor1m": "Implied correlation: how much options price stocks moving together. High = macro fear, everything moves as one.",
 "cor1m_chg20": "20-day change in implied correlation.",
 "gld_lead20": "Gold vs SPX over 20 days. Gold leading = money hiding.",
 "spx_below_ma50": "How far SPX is from its 50-day average.",
 "bbl_close": "Norseman's Bull/Bear Line = 90% of SPX's record close. Close to it (or below) = deep pullback already under way.",
 "kurt_bear": "Kurt Altrichter's weekly dashboard rebuilt on our data: gamma (market gamma low), flow (vol-control and trend funds both selling), realized vol (1-month above 3-month), breadth (under 40% above the 200-day).",
}
MULT = {"sqgex_low": 1e-9}
PCT_UNITS = {"lqd_ief_lag20", "hyg_ief_lag20", "hb_lb_lag20", "xly_xlp_lag20", "gld_lead20", "spx_below_ma50"}


def model_p(m: dict, vix: float, a: float, b: float) -> float | None:
    x = [math.log(vix) if vix and vix > 0 else float("nan"), a, b]
    if any(v is None or not math.isfinite(v) for v in x):
        return None
    z = m["w"][0] + sum(w * (v - mu) / sd for w, v, mu, sd in zip(m["w"][1:], x, m["mu"], m["sd"]))
    return 1 / (1 + math.exp(-z))


def contrib(m: dict, vix: float, a: float, b: float) -> list:
    x = [math.log(vix), a, b]
    return [round(w * (v - mu) / sd, 3) for w, v, mu, sd in zip(m["w"][1:], x, m["mu"], m["sd"])]


def band(p: float) -> tuple[str, str]:
    if p < .06:
        return "low", "Low"
    if p < .15:
        return "elev", "Elevated"
    if p < .25:
        return "high", "High"
    return "vhigh", "Very high"


def fnum(x, k=2):
    return None if x is None or not np.isfinite(x) else round(float(x), k)


GAMMA_MIN_DROPS = 20  # below this many drop days the gamma tiles stay "untested"


def gamma_track(gh: pd.DataFrame, spx: pd.Series) -> dict:
    """Own-gamma tiles: daily history + a running track record vs the 5-day 3% drop event.

    History is short (greeks_snapshots starts 2026-05), so this only reports counts; the
    tiles become "tested" once there are GAMMA_MIN_DROPS drop days to judge them on.
    """
    if gh is None or gh.empty:
        return {}
    fut = pd.concat([spx.shift(-k) for k in range(1, 6)], axis=1).min(axis=1)
    ev = ((fut / spx - 1) <= -0.03).astype(float)
    ev[spx.index[-5:]] = np.nan
    s = gh[gh.sym == "SPX"].set_index("d").sort_index()
    m = gh[gh.sym != "SPX"].pivot_table(index="d", columns="sym", values="gex")
    out = {}

    def rec(flag: pd.Series, on: str, off: str) -> dict:
        D = pd.DataFrame({"f": flag, "y": ev.reindex(flag.index)}).dropna()
        n_drop = int(D.y.sum())
        r = {"days": int(len(D)), "drop_days": n_drop, "since": D.index.min().date().isoformat() if len(D) else None}
        for k, lab in ((1, on), (0, off)):
            sub = D[D.f == k]
            r[lab] = {"n": int(len(sub)), "rate": fnum(sub.y.mean(), 3) if len(sub) else None}
        r["tested"] = n_drop >= GAMMA_MIN_DROPS
        return r

    if not s.empty:
        below = (s.spot < s.flip).astype(float).where(s.flip.notna())
        dist = 100 * (s.spot / s.flip - 1)
        out["our_spx_gamma"] = {
            "spark": [fnum(v, 1) for v in s.gex.iloc[-SPARK:].tolist()],
            "spark_dates": [d.date().isoformat() for d in s.index[-SPARK:]],
            "dist_hist": [fnum(v, 2) for v in dist.iloc[-SPARK:].tolist()],
            "track": {**rec(below, "below_flip", "above_flip"),
                      "labels": {"below_flip": "SPX below the flip", "above_flip": "SPX above the flip"}},
        }
    if not m.empty:
        neg = (m < 0).sum(axis=1).where(m.notna().sum(axis=1) >= 5)
        out["our_mag7_neg"] = {
            "spark": [fnum(v, 0) for v in neg.iloc[-SPARK:].tolist()],
            "spark_dates": [d.date().isoformat() for d in neg.index[-SPARK:]],
            "track": {**rec((neg >= 2).astype(float).where(neg.notna()), "two_plus_neg", "under_two"),
                      "labels": {"two_plus_neg": "2+ names negative", "under_two": "0-1 negative"}},
        }
    return out


def run(use_db: bool = True, out: Path | None = None) -> dict:
    t0 = time.time()
    with ThreadPoolExecutor(8) as ex:
        ser = list(ex.map(lambda s: fmp_close(s, START), FMP_SYMS))
    X = pd.DataFrame({s.name: s for s in ser}).sort_index()
    X = X[X["^SPX"].notna()]
    fr = {"NFCI": ("NFCI", 5), "STLFSI": ("STLFSI4", 6), "CCC": ("BAMLH0A3HYC", 1), "AAA": ("BAMLC0A1CAAA", 1), "HY": ("BAMLH0A0HYM2", 1)}
    for col, (sid, lag) in fr.items():
        try:
            X[col] = fred(sid, lag).reindex(X.index, method="ffill")
        except Exception as e:  # noqa: BLE001
            print("FRED fail", sid, e)
            X[col] = np.nan
    ccc_first = X["CCC"].first_valid_index()
    if ccc_first is not None:
        X.loc[X.index < ccc_first, "HY"] = np.nan
    db_ctx = {}
    if use_db:
        try:
            conn = db_connect()
            rows = db_rows(conn, "select date, gex, dix from squeezemetrics_daily order by date")
            sq = pd.DataFrame(rows, columns=["date", "sqgex", "dix"]).set_index("date")
            sq.index = pd.to_datetime(sq.index)
            X = X.join(sq.astype(float))
            mag = ["AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA"]
            rows = db_rows(conn, """select distinct on (symbol) symbol, ts, gex_total, gex_flip, spot from greeks_snapshots
                                     where symbol = any(%s) and gex_total <> 0 and ts > now() - interval '5 days'
                                     order by symbol, ts desc""", (["SPX"] + mag,))
            g = {r[0]: (r[1], float(r[2]) if r[2] is not None else None) for r in rows}
            fl = {r[0]: (float(r[3]) if r[3] is not None else None, float(r[4]) if r[4] is not None else None) for r in rows}
            if "SPX" in g:
                fx, sp_ = fl.get("SPX", (None, None))
                db_ctx["our_spx_gamma"] = {"value": fnum(g["SPX"][1], 1), "at": str(g["SPX"][0])[:16],
                                           "flip": fnum(fx, 0), "spot": fnum(sp_, 0),
                                           "flip_dist": fnum(100 * (sp_ / fx - 1), 2) if fx and sp_ else None}
            mg = [s for s in mag if s in g and g[s][1] is not None]
            if mg:
                db_ctx["our_mag7_neg"] = {"value": sum(1 for s in mg if g[s][1] < 0), "of": len(mg),
                                          "at": str(max(g[s][0] for s in mg))[:16]}
            # daily history (last snapshot of each day; exact-zero rows are failed walks)
            rows = db_rows(conn, """select distinct on (symbol, ts::date) symbol, ts::date, gex_total, gex_flip, spot
                                     from greeks_snapshots where symbol = any(%s) and gex_total <> 0
                                     order by symbol, ts::date, ts desc""", (["SPX"] + mag,))
            gh = pd.DataFrame(rows, columns=["sym", "d", "gex", "flip", "spot"])
            gh["d"] = pd.to_datetime(gh["d"])
            for c_ in ("gex", "flip", "spot"):
                gh[c_] = gh[c_].astype(float)
            db_ctx["_hist"] = gh
            conn.close()
        except Exception as e:  # noqa: BLE001
            print("DB context skipped:", e)
    seed_src = STORE if STORE.exists() else SEED
    seed = pd.read_csv(seed_src, index_col=0, parse_dates=True)
    try:
        B = breadth_tail(seed)
        STORE.parent.mkdir(parents=True, exist_ok=True)
        B.to_csv(STORE, compression="gzip")
    except Exception as e:  # noqa: BLE001
        print("breadth extend failed, using stored:", e)
        B = seed
    F = build(X, B)
    if ccc_first is not None:
        F.loc[F.index < ccc_first, ["ccc_aaa", "ccc_aaa_chg20"]] = np.nan
    K = HIST_DAYS + 5
    P = {t[0]: pct_tail(F[t[0]], K if t[0] in ("vix_vix3m", "nfci") else 3) for t in TILES if t[0] in F}
    dates = F.index
    last = F.index[-1]
    # ---- score now + history
    hist = []
    for d in dates[-HIST_DAYS:]:
        v, a, b = F.at[d, "vix"], P["vix_vix3m"].get(d), P["nfci"].get(d)
        p5 = model_p(MODEL["ev5"], v, a, b)
        p10 = model_p(MODEL["ev10"], v, a, b)
        hist.append([d.date().isoformat(), fnum(X.at[d, "^SPX"], 2), fnum(p5, 4), fnum(p10, 4)])
    # flags per history day: false all-clear and fear release
    vv = F["vix"]
    p5s = pd.Series({pd.Timestamp(h[0]): (h[2] if h[2] is not None else np.nan) for h in hist})
    crush = ((vv / vv.shift(1) - 1) <= -0.12).reindex(p5s.index).fillna(False)
    fa = crush & (p5s.rolling(5).max().shift(1) >= 0.20)
    v20 = vv.rolling(20).max()
    fr_all = (vv <= 0.75 * v20) & (vv < X["^VIX3M"]) & (v20 >= 25)
    fr = fr_all.reindex(p5s.index).fillna(False)
    for h in hist:
        dd = pd.Timestamp(h[0])
        h.extend([int(bool(fa.get(dd, False))), int(bool(fr.get(dd, False)))])
    # realized drop-starts (5d worst close <= -3%) for the chart
    s = X["^SPX"]
    worst5 = pd.concat([s.shift(-k) for k in range(1, 6)], axis=1).min(axis=1) / s - 1
    ev = (worst5 <= -0.03)
    starts = [d.date().isoformat() for d in ev.index[(ev & ~ev.shift(1, fill_value=False))] if d >= dates[-HIST_DAYS]]
    vix, a, b = F.at[last, "vix"], P["vix_vix3m"].get(last), P["nfci"].get(last)
    p5 = model_p(MODEL["ev5"], vix, a, b)
    p10 = model_p(MODEL["ev10"], vix, a, b)
    # what followed days with a similar score (in-sample description, last 3 years shown, all years counted)
    full_p5 = []
    allP_v3 = pct_tail(F["vix_vix3m"], len(F))
    allP_nf = pct_tail(F["nfci"], len(F))
    for d in dates:
        full_p5.append(model_p(MODEL["ev5"], F.at[d, "vix"], allP_v3.get(d), allP_nf.get(d)))
    fp = pd.Series(full_p5, index=dates, dtype=float)
    sim = None
    if p5 is not None:
        lo_, hi_ = p5 * 0.8, p5 * 1.25
        m = (fp >= lo_) & (fp <= hi_) & worst5.notna()
        if m.sum() >= 20:
            sim = {"n": int(m.sum()), "drop_rate": fnum(ev[m].mean(), 3),
                   "median_worst5": fnum(100 * worst5[m].median(), 2), "p10_worst5": fnum(100 * worst5[m].quantile(.1), 2),
                   "lo": fnum(lo_, 3), "hi": fnum(hi_, 3)}
    # ---- tiles
    sq_long = pct_tail(F["sqgex_low"], LONG + 5) if "sqgex_low" in F else pd.Series(np.nan, index=F.index)
    kurt_parts = pd.DataFrame({
        "gamma": sq_long >= 0.8,
        "flow": (F["_vc10"] < 0) & (F["_cta10"] < 0),
        "rv": F["rv_ratio"] > 1,
        "breadth": F["_pct200"] < 40,
    })
    kurt_n = kurt_parts.sum(axis=1).where(sq_long.notna())
    gtrack = gamma_track(db_ctx.pop("_hist", None), X["^SPX"].dropna()) if use_db else {}
    tiles = []
    for tid, grp, label, role, l5, l10, calm, verdict in TILES:
        t = {"id": tid, "group": grp, "label": label, "role": role, "lift5": l5, "lift10": l10, "calm5": calm,
             "verdict": verdict}
        if tid in db_ctx:
            t["about"] = ABOUT.get(tid, "")
            t.update(db_ctx[tid])
            t.update(gtrack.get(tid, {}))
            tr = t.get("track") or {}
            if tr:
                on, off = [k for k in tr if isinstance(tr[k], dict) and k != "labels"]
                lb = tr.get("labels", {})
                a_, b_ = tr[on], tr[off]
                rate = lambda z: "–" if z["rate"] is None else f"{100 * z['rate']:.1f}%"  # noqa: E731
                t["verdict"] = (f"{'tested' if tr['tested'] else 'not enough drops yet'} · {tr['drop_days']} drop days "
                                f"in {tr['days']} · {lb.get(on, on)} {rate(a_)} (n {a_['n']}) vs "
                                f"{lb.get(off, off)} {rate(b_)} (n {b_['n']})")
                if tr["tested"]:
                    t["role"] = "context"
            t["unit"] = "" if tid == "our_spx_gamma" else f" of {db_ctx[tid].get('of', 7)}"
            if tid == "our_mag7_neg":
                t["warn"] = db_ctx[tid]["value"] >= 2
            elif tid == "our_spx_gamma":
                t["warn"] = (db_ctx[tid]["value"] or 0) < 0
            tiles.append(t)
            continue
        t["about"] = ABOUT.get(tid, "")
        if tid == "kurt_bear":
            kn = kurt_n.iloc[-1]
            kp = kurt_parts.iloc[-1]
            t.update({"value": None if not np.isfinite(kn) else int(kn), "unit": " of 4 bearish",
                      "warn": bool(np.isfinite(kn) and kn >= 3), "rates": KURT_RATES,
                      "parts": [{"name": "Gamma (market gamma low)", "bear": bool(kp["gamma"])},
                                {"name": "Flow (vol-control + trend funds selling)", "bear": bool(kp["flow"])},
                                {"name": "Realized vol (1-month above 3-month)", "bear": bool(kp["rv"])},
                                {"name": "Breadth (under 40% above 200-day)", "bear": bool(kp["breadth"])}],
                      "spark": [fnum(v, 0) for v in kurt_n.iloc[-SPARK:].tolist()],
                      "long": [fnum(v, 0) for v in kurt_n.iloc[-LONG:].tolist()], "thr": 3})
            tiles.append(t)
            continue
        if tid not in SHOW or tid not in F:
            if role == "untested":
                t["value"] = None
                tiles.append(t)
            continue
        col, unit, k = SHOW[tid]
        sign = -1 if col.startswith("-") else 1
        series = sign * F[col.lstrip("-")]
        mult = 100 if tid in PCT_UNITS else MULT.get(tid, 1)
        t["value"] = fnum(series.iloc[-1] * mult, k)
        t["unit"] = unit
        pc = P.get(tid, pd.Series(dtype=float)).iloc[-1] if tid in P else np.nan
        t["pct"] = fnum(pc, 3)
        t["warn"] = bool(np.isfinite(pc) and pc >= 0.9)
        sp = (series.iloc[-SPARK:] * mult).tolist()
        t["spark"] = [fnum(v, 4) for v in sp]
        t["since"] = (F[col.lstrip("-")].first_valid_index() or last).date().isoformat()
        t["long"] = [fnum(v, 4) for v in (series.iloc[-LONG:] * mult).tolist()]
        try:  # the top-10% stress mark of the past year, in display units
            ft, dsp = F[tid].iloc[-WIN:], series.iloc[-WIN:]
            ok = ft.notna() & dsp.notna()
            same = np.corrcoef(ft[ok], dsp[ok])[0, 1] >= 0 if ok.sum() > 20 else True
            t["thr"] = fnum(dsp[ok].quantile(0.9 if same else 0.1) * mult, 4) if ok.sum() > 20 else None
        except Exception:  # noqa: BLE001
            t["thr"] = None
        tiles.append(t)
    m5 = MODEL["ev5"]
    obj = {
        "generated": datetime.now(ET).isoformat(timespec="minutes"),
        "asof": last.date().isoformat(),
        "score": {
            "p5": fnum(p5, 4), "p10": fnum(p10, 4), "base5": m5["base"], "base10": MODEL["ev10"]["base"],
            "band": band(p5)[0] if p5 is not None else None, "band_label": band(p5)[1] if p5 is not None else None,
            "inputs": [
                {"id": "vix", "label": "VIX level", "value": fnum(vix, 2)},
                {"id": "vix_vix3m", "label": "VIX ÷ VIX3M", "value": fnum(F.at[last, "vix_vix3m"], 3), "pct": fnum(a, 3)},
                {"id": "nfci", "label": "NFCI", "value": fnum(F.at[last, "nfci"], 3), "pct": fnum(b, 3)},
            ],
            "contrib": contrib(m5, vix, a, b) if p5 is not None else None,
            "prev": [h[2] for h in hist[-6:-1]],
            "max20": fnum(max([h[2] for h in hist[-20:] if h[2] is not None] or [np.nan]), 4),
            "similar": sim,
            # lets the cockpit re-score intraday from the live VIX + VIX3M quotes (NFCI is weekly, held)
            "live_ref": {
                "v3": [fnum(v, 4) for v in F["vix_vix3m"].dropna().iloc[-(WIN - 1):].tolist()],
                "nfci_pct": fnum(b, 4), "minp": MINP,
                "m5": {k: MODEL["ev5"][k] for k in ("w", "mu", "sd")},
                "m10": {k: MODEL["ev10"][k] for k in ("w", "mu", "sd")},
            },
        },
        "flags": {
            "false_allclear": {**FLAG_STATS["false_allclear"], "on": bool(hist[-1][4]) if hist else False},
            "fear_release": {**FLAG_STATS["fear_release"], "on": bool(hist[-1][5]) if hist else False},
        },
        "tile_dates": [d.date().isoformat() for d in dates[-LONG:]],
        "calib5": CALIB5, "calib10": CALIB10,
        "hist": hist, "drop_starts": starts,
        "tiles": tiles, "groups": GROUPS,
        "method": {
            "event": "SPX worst close over the next 5 sessions at least 3% below today (base 9.0% of days, 2010-2026)",
            "event10": "worst close over the next 10 sessions at least 5% below (base 6.7%)",
            "auc": {"vix_only": [0.709, 0.751], "score": [0.765, 0.773]},
            "fit": "logistic, 4,009 days 2010-11 to 2026-11; trained on one half, tested on the other, both ways",
            "lift": "tile lift = drop odds when the tile is in the top 10% of its own last year, divided by the base rate",
        },
        "runtime_s": round(time.time() - t0, 1),
    }
    out = out or DATA_DIR / "risktab.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, separators=(",", ":"), allow_nan=False, default=str))
    tmp.replace(out)
    return obj


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-db", action="store_true")
    ap.add_argument("--out", type=Path)
    a = ap.parse_args()
    o = run(use_db=not a.no_db, out=a.out)
    sc = o["score"]
    print(f"risktab.json asof {o['asof']}: 5-session 3% drop odds {sc['p5']:.1%} (base {sc['base5']:.0%}), "
          f"10-session 5% {sc['p10']:.1%} · {sc['band_label']} · {len(o['tiles'])} tiles · {o['runtime_s']}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
