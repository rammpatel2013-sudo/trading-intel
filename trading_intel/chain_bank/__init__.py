"""Chain bank (2026-11-20): every option of ~1,550 names, hourly, into the foundational database.

Mithil 2026-11-20: "pull 1500 stock or all SPX n QQQ symbol and key etf ... all option and price, volume
data" -> "remember our foundational database. everything has to go through this" -> "u can build with
focus on database foundational and one source of truth" -> "all. i have enough space".

Owner tables (schema contract, one owner per concept; nothing written outside Postgres):
  greeks_chain      every contract, every run, source = BANK_SOURCE (+ root, spot, day VWAP / close)
  greeks_snapshots  one row per name per run, source = BANK_SOURCE: the house totals (same maths as the
                    CVForge sector rows: compute_exposures + gex_flip + positioning_extras) + IV30/IV90,
                    term, 25-delta skew, call/put OI, call/put $ premium
  bars_1m           1-minute OHLCV, source 'fmp' (the same rows flow_bank writes -> one truth, upsert)
Hour-on-hour flow and the option-$ VWAP are DERIVED from consecutive greeks_snapshots rows (not stored).
Readers that never filtered source keep today's behaviour by excluding BANK_SOURCE (rule 9).
This module = the pure parts. Descriptors only (rule 4); no AI (rule 7).
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Mapping, Sequence
from datetime import date, datetime

import numpy as np
import pandas as pd

BANK_SOURCE = "cvforge_bank"
INDEXES = ("SPX", "NDX", "RUT", "VIX", "XSP")
# cross-asset ETFs for the Macro Compass (bonds, commodities, world, sectors, vol) - on top of ETF_LIST
MACRO_ETFS = (
    "SPY QQQ IWM DIA RSP MDY IJR EFA EEM INDA FXI EWJ EWZ EWW EWY EWT KWEB "
    "SHY IEF TLT TIP LQD HYG JNK EMB "
    "GLD SLV USO UNG DBC GDX GDXJ COPX URA "
    "XLK XLF XLE XLV XLI XLY XLP XLU XLB XLRE XLC SMH SOXX IGV KRE XBI XHB XRT ITB IYR "
    "ARKK IBIT BITO UUP FXE FXY VXX UVXY SVXY TQQQ SQQQ SPXL SOXL"
).split()

SNAP_COLS = (
    "ticker", "underlying_ticker", "contract_type", "expiration_date", "strike_price", "implied_volatility",
    "delta", "gamma", "theta", "vega", "open_interest", "day_volume", "day_close", "day_vwap",
    "underlying_price",
)
PAGE = 10_000            # CVForge /query hard cap per call (probed 2026-11-20: max_rows above it is ignored)
GROUP_MAX = 9_500        # contracts per batch of underlyings, a little under the cap
_TICKER = re.compile(r"^O:([A-Z0-9.]+?)(\d{6})([CP])(\d{8})$")

# extra greeks_snapshots columns this job fills (migration 0066_chain_bank)
SNAP_EXTRA = ("iv30", "iv90", "ts_30_90", "skew25_30", "put25_30", "call25_30", "call_oi", "put_oi",
              "call_premium_usd", "put_premium_usd")


def universe(sp1500: Iterable[str], qqq: Iterable[str], etfs: Iterable[str]) -> list[str]:
    """Our spellings, deduplicated, sorted: indexes first, then everything else."""
    rest = {s.strip().upper() for s in [*sp1500, *qqq, *etfs, *MACRO_ETFS] if s and s.strip()}
    rest -= set(INDEXES)
    return list(INDEXES) + sorted(rest)


def batches(counts: Mapping[str, int], limit: int = GROUP_MAX) -> list[list[str]]:
    """Group underlyings so each SQL call stays under the row cap. Big chains (SPX ~30k) go alone and are paged."""
    out: list[list[str]] = []
    cur: list[str] = []
    n = 0
    for u, c in sorted(counts.items(), key=lambda kv: -kv[1]):
        if c >= limit:
            out.append([u])
            continue
        if cur and n + c > limit:
            out.append(cur)
            cur, n = [], 0
        cur.append(u)
        n += c
    if cur:
        out.append(cur)
    return out


def sql_list(vals: Sequence[str]) -> str:
    """Quoted SQL list. Symbols are checked against a strict pattern first (no injection)."""
    ok = [v for v in vals if v and all(ch.isalnum() or ch in ".:-/" for ch in v)]
    return ",".join(f"'{v}'" for v in ok)


def parse_ticker(t: str) -> tuple[str, str, str, float] | None:
    """CVForge contract ticker 'O:SPXW261211P06200000' -> (root, 'YYYY-MM-DD', 'C'|'P', strike)."""
    m = _TICKER.match(t or "")
    if not m:
        return None
    root, ymd, cp, k = m.groups()
    return root, f"20{ymd[:2]}-{ymd[2:4]}-{ymd[4:]}", cp, int(k) / 1000.0


def tidy(raw: pd.DataFrame, ours: Mapping[str, str]) -> pd.DataFrame:
    """CVForge snapshot rows -> one clean frame (our symbol spelling, root, typed numbers)."""
    p = [parse_ticker(str(t)) for t in raw["ticker"]]
    d = pd.DataFrame({
        "symbol": [ours.get(u, str(u).replace("I:", "")) for u in raw["underlying_ticker"]],
        "root": [x[0] if x else "" for x in p],
        "cp": raw["contract_type"].astype(str).str[0].str.upper(),
        "expiry": pd.to_datetime(raw["expiration_date"], errors="coerce"),
        "strike": pd.to_numeric(raw["strike_price"], errors="coerce"),
        "iv": pd.to_numeric(raw["implied_volatility"], errors="coerce"),
        "delta": pd.to_numeric(raw["delta"], errors="coerce"),
        "gamma": pd.to_numeric(raw["gamma"], errors="coerce"),
        "theta": pd.to_numeric(raw["theta"], errors="coerce"),
        "vega": pd.to_numeric(raw["vega"], errors="coerce"),
        "oi": pd.to_numeric(raw["open_interest"], errors="coerce"),
        "volume": pd.to_numeric(raw["day_volume"], errors="coerce"),
        "px_close": pd.to_numeric(raw["day_close"], errors="coerce"),
        "px_vwap": pd.to_numeric(raw["day_vwap"], errors="coerce"),
        "spot": pd.to_numeric(raw["underlying_price"], errors="coerce"),
    })
    return d[d["strike"].notna() & d["expiry"].notna() & d["cp"].isin(["C", "P"])].reset_index(drop=True)


def add_greeks(d: pd.DataFrame, ref: date, floor: date) -> pd.DataFrame:
    """Same synthesis as CVForgeClient.chain(): vanna, charm (per day), gxoi / dxoi / vxoi.
    Expiries before ``floor`` (settled) are dropped; IVs outside (0, 5) are kept as rows but get no vanna/charm."""
    from trading_intel.greeks.black_scholes import bs_charm, bs_vanna, years_to_expiry

    d = d[d["expiry"].dt.normalize() >= pd.Timestamp(floor)].copy()
    if d.empty:
        return d
    t = years_to_expiry(d["expiry"], ref)
    ok = (d["iv"] > 0) & (d["iv"] < 5) & (d["spot"] > 0)
    van = np.full(len(d), np.nan)
    cha = np.full(len(d), np.nan)
    m = ok.to_numpy()
    if m.any():
        with np.errstate(all="ignore"):
            van[m] = bs_vanna(d["spot"].to_numpy(float)[m], d["strike"].to_numpy(float)[m],
                              d["iv"].to_numpy(float)[m], np.asarray(t)[m])
            cha[m] = bs_charm(d["spot"].to_numpy(float)[m], d["strike"].to_numpy(float)[m],
                              d["iv"].to_numpy(float)[m], np.asarray(t)[m]) / 365.0
    d["vanna"], d["charm"] = van, cha
    oi = d["oi"].fillna(0.0)
    d["gxoi"] = d["gamma"].fillna(0.0) * oi
    d["dxoi"] = d["delta"].fillna(0.0) * oi
    d["vxoi"] = d["vega"].fillna(0.0) * oi
    d["cxoi"] = pd.Series(cha, index=d.index).fillna(0.0) * oi
    return d


def chain_records(d: pd.DataFrame, ts: datetime, stamp: datetime) -> list[dict]:
    """greeks_chain rows (source BANK_SOURCE)."""
    out = d[["symbol", "root", "cp", "strike", "oi", "volume", "delta", "gamma", "theta", "vega", "vanna", "charm",
             "iv", "gxoi", "dxoi", "vxoi", "cxoi", "spot", "px_vwap", "px_close"]].copy()
    out["expiry"] = d["expiry"].dt.date
    out["oi"] = out["oi"].round().astype("Int64")
    out["volume"] = out["volume"].round().astype("Int64")
    out = out.astype(object).where(out.notna(), None)
    recs = out.to_dict("records")
    for r in recs:
        r["ts"], r["source"], r["ingested_at"] = ts, BANK_SOURCE, stamp
    return recs


# ---------------------------------------------------------------- per-name vol summary
def _interp(x: np.ndarray, y: np.ndarray, at: float) -> float | None:
    ok = np.isfinite(x) & np.isfinite(y)
    x, y = x[ok], y[ok]
    if len(x) == 0:
        return None
    o = np.argsort(x)
    x, y = x[o], y[o]
    if at < x[0] or at > x[-1]:
        return None          # never extrapolate: no listed strike / delta on that side
    return float(np.interp(at, x, y))


def per_expiry(g: pd.DataFrame, spot: float) -> dict:
    """ATM IV (by strike at spot, calls+puts averaged) and 25-delta put / call IV for one expiry."""
    calls = g[g["cp"] == "C"]
    puts = g[g["cp"] == "P"]
    k = g.groupby("strike")["iv"].mean()
    atm = _interp(k.index.to_numpy(float), k.to_numpy(float), spot) if len(k) else None
    p25 = _interp(puts["delta"].to_numpy(float), puts["iv"].to_numpy(float), -0.25) if len(puts) > 1 else None
    c25 = _interp(calls["delta"].to_numpy(float), calls["iv"].to_numpy(float), 0.25) if len(calls) > 1 else None
    return {"atm": atm, "p25": p25, "c25": c25}


def const_maturity(dtes: Sequence[float], ivs: Sequence[float | None], target: float) -> float | None:
    """IV at a constant maturity: linear in total variance (iv^2 * t) between the expiries around it.
    Outside the listed range: the nearest expiry's IV, but only within 1.5x of its maturity."""
    pts = sorted((t, v) for t, v in zip(dtes, ivs, strict=False) if v is not None and v > 0 and t > 0)
    if not pts:
        return None
    if target <= pts[0][0]:
        return pts[0][1] if target >= pts[0][0] / 1.5 else None
    if target >= pts[-1][0]:
        return pts[-1][1] if target <= pts[-1][0] * 1.5 else None
    for (t1, v1), (t2, v2) in zip(pts, pts[1:], strict=False):
        if t1 <= target <= t2:
            w = v1 * v1 * t1 + (v2 * v2 * t2 - v1 * v1 * t1) * (target - t1) / (t2 - t1)
            return math.sqrt(w / target) if w > 0 else None
    return None


def _lin(dtes: Sequence[float], vals: Sequence[float | None], target: float) -> float | None:
    pts = sorted((t, v) for t, v in zip(dtes, vals, strict=False) if v is not None)
    if not pts:
        return None
    if target <= pts[0][0]:
        return pts[0][1] if target >= pts[0][0] / 1.5 else None
    if target >= pts[-1][0]:
        return pts[-1][1] if target <= pts[-1][0] * 1.5 else None
    for (t1, v1), (t2, v2) in zip(pts, pts[1:], strict=False):
        if t1 <= target <= t2:
            return v1 + (v2 - v1) * (target - t1) / (t2 - t1)
    return None


def vol_summary(g: pd.DataFrame, spot: float, day: date) -> dict:
    """IV30 / IV90 (ATM, total-variance interpolation), IV30 / IV90 ratio, 30-day 25-delta put / call IV
    and skew (put minus call, decimals), call / put OI, call / put $ premium (day volume x day VWAP x 100,
    day close when VWAP is missing)."""
    call = g["cp"] == "C"
    oi = g["oi"].fillna(0)
    px = g["px_vwap"].fillna(g["px_close"]).fillna(0)
    prem = g["volume"].fillna(0) * px * 100
    r: dict = {"call_oi": float(oi[call].sum()), "put_oi": float(oi[~call].sum()),
               "call_premium_usd": float(prem[call].sum()), "put_premium_usd": float(prem[~call].sum())}
    v = g[(g["iv"] > 0) & (g["iv"] < 5)]
    dtes, atm, p25, c25 = [], [], [], []
    for exp, ge in v.groupby(v["expiry"].dt.date):
        t = (exp - day).days
        if t < 1:
            continue
        pe = per_expiry(ge, spot)
        dtes.append(float(t))
        atm.append(pe["atm"])
        p25.append(pe["p25"])
        c25.append(pe["c25"])
    r["iv30"] = const_maturity(dtes, atm, 30)
    r["iv90"] = const_maturity(dtes, atm, 90)
    r["ts_30_90"] = r["iv30"] / r["iv90"] if r["iv30"] and r["iv90"] else None
    r["put25_30"] = _lin(dtes, p25, 30)
    r["call25_30"] = _lin(dtes, c25, 30)
    r["skew25_30"] = (r["put25_30"] - r["call25_30"]) if r["put25_30"] is not None and r["call25_30"] is not None else None
    return r


def snapshot_record(g: pd.DataFrame, symbol: str, day: date, ts: datetime, stamp: datetime) -> dict | None:
    """greeks_snapshots row for one name: the house totals (exactly what CVForgeClient.exposures returns for the
    sector rows) + the bank's vol / OI / premium columns."""
    from trading_intel.greeks.exposures import compute_exposures, positioning_extras
    from trading_intel.greeks.flip_point import gex_flip

    spots = g["spot"].dropna()
    if spots.empty or not (spots.median() > 0):
        return None
    spot = float(spots.median())
    ch = g.rename(columns={"cp": "opt_kind", "expiry": "expiration"}).copy()
    ch["opt_kind"] = ch["opt_kind"].map({"C": "call", "P": "put"})
    ch["underlying_price"] = spot
    live = ch[(ch["iv"] > 0) & (ch["iv"] < 5)]
    rec: dict = {"symbol": symbol, "ts": ts, "spot": spot, "source": BANK_SOURCE, "ingested_at": stamp,
                 "n_contracts": int(len(g))}
    if len(live):
        ex = compute_exposures(live, spot) or {}
        rec.update({k: ex.get(k) for k in ("gex_total", "dex_total", "vex_total", "chex_total", "atm_iv")})
        try:
            rec["gex_flip"] = gex_flip(live, spot)
        except Exception:  # noqa: BLE001 - a flip that cannot be found stays empty
            rec["gex_flip"] = None
        extra = positioning_extras(live, spot)
        rec.update({k: extra.get(k) for k in ("dex_flip", "call_volume", "put_volume", "call_notional", "put_notional")})
    rec.update(vol_summary(g, spot, day))
    for k, v in list(rec.items()):
        if isinstance(v, float) and not math.isfinite(v):
            rec[k] = None
    return rec
