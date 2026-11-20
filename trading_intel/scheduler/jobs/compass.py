"""compass - created 2026-11-20 (Macro view). Mithil 04:17 "build macro view now".

Steps (default all three):
  collect  vendors -> DB: daily bars for the cross-asset grid into equity_bars (the equity pipeline, FMP via
           CVForge); a grid name with less than a year of bars gets its history from 2023 once.
  auction  DB only: bars_1m (the chain bank's 1-minute bars) -> auction_daily for the day.
  build    DB only: equity_bars + greeks_snapshots (chain bank) + oi_chain_daily (Convex EOD ATM IV / walls,
           history since 06/2026) + finra_short_volume + auction_daily -> compass_daily (one row per name, with
           the scanner hits), then scripts/cvxmap/data/compass.json + compass.html (cockpit Compass tab and
           http://192.168.1.211:8899/compass.html).
Run: python -m trading_intel.scheduler.jobs.compass [--day YYYY-MM-DD] [--steps collect,auction,build] [--force]
DSM "TI compass" Mon-Fri 16:35 and 18:50 (the second run adds the FINRA file and the Convex EOD IV):
    run_job.sh compass
Descriptors only (rule 4); maths in trading_intel/compass.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import sys
import uuid
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import structlog
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from trading_intel import compass as C
from trading_intel.chain_bank import BANK_SOURCE
from trading_intel.config import Settings, get_settings
from trading_intel.timeutils import eastern_now, is_trading_session

log = structlog.get_logger(__name__)
ROOT = Path(__file__).resolve().parents[3]
COCKPIT_DATA = ROOT / "scripts" / "cvxmap" / "data"
PAGE_SRC = ROOT / "scripts" / "cvxmap" / "compass.html"
RTH = (time(9, 30), time(16, 0))
PANEL_DAYS = 120
HIT_TOP = 15
YEAR_BARS = 250


def _f(x):  # noqa: ANN001, ANN202
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _r(x, n=4):  # noqa: ANN001, ANN202
    v = _f(x)
    return None if v is None else round(v, n)


def _prev_session(d: date) -> date:
    p = d - timedelta(days=1)
    for _ in range(10):
        if is_trading_session(p):
            return p
        p -= timedelta(days=1)
    return p


# ── collect ────────────────────────────────────────────────────────────
def collect(session: Session, settings: Settings, day: date) -> dict:
    from trading_intel.clients.equity_data import default_source
    from trading_intel.equity import parse, pipeline, store
    from trading_intel.memory.models_equity import EquityBar

    src = default_source(settings)
    n = dict(session.execute(select(EquityBar.symbol, func.count()).where(EquityBar.symbol.in_(C.GRID))
                             .group_by(EquityBar.symbol)).all())
    short = [s for s in C.GRID if n.get(s, 0) < YEAR_BARS]
    hist = 0
    if short:
        got = src.fetch_many(short, lambda s: src.bars(s, pipeline.HISTORY_START.isoformat()), label="compass_hist")
        for s, raw in got.items():
            hist += store.upsert(session, EquityBar, parse.bar_rows(s, raw), ["symbol", "date"], commit=False, chunk=2000)
        session.commit()
    fresh = pipeline.refresh_bars(session, src, [s for s in C.GRID if s not in short], day)
    return {"history_names": len(short), "history_rows": hist, "refresh_rows": fresh}


# ── auction ────────────────────────────────────────────────────────────
def _bars_day(session: Session, day: date, symbols: list[str] | None = None) -> pd.DataFrame:
    from trading_intel.memory.models_flow import Bars1m

    t0, t1 = datetime.combine(day, RTH[0]), datetime.combine(day, RTH[1])
    q = select(Bars1m.symbol, Bars1m.ts, Bars1m.open, Bars1m.high, Bars1m.low, Bars1m.close, Bars1m.volume,
               Bars1m.source).where(Bars1m.ts >= t0, Bars1m.ts < t1)
    if symbols:
        q = q.where(Bars1m.symbol.in_(symbols))
    df = pd.DataFrame(session.execute(q).all(),
                      columns=["symbol", "ts", "open", "high", "low", "close", "volume", "source"])
    if df.empty:
        return df
    df["ts"] = pd.to_datetime(df["ts"])
    # one source per minute (fmp first; the bars are the same vendor feed in both writers)
    df = df.sort_values(["symbol", "ts", "source"]).drop_duplicates(["symbol", "ts"])
    return df


def run_auction(session: Session, day: date) -> dict:
    from trading_intel.equity.store import upsert
    from trading_intel.memory.models_compass import AUCTION_FLOATS, AuctionDaily

    df = _bars_day(session, day)
    if df.empty:
        return {"names": 0}
    prev = _prev_session(day)
    prior = {r.symbol: {"val": r.val, "vah": r.vah} for r in session.execute(
        select(AuctionDaily.symbol, AuctionDaily.val, AuctionDaily.vah).where(AuctionDaily.date == prev)).all()}
    stamp = eastern_now().replace(microsecond=0)
    rows = []
    for sym, g in df.groupby("symbol"):
        a = C.auction(g, prior.get(sym))
        if not a:
            continue
        rec = {"symbol": sym, "date": day, "source": "bars_1m", "computed_at": stamp,
               **{k: a[k] for k in ("day_type", "open_vs_value", "close_vs_value", "poor_high", "poor_low", "n_minutes")},
               **{k: _f(a[k]) for k in AUCTION_FLOATS}}
        rows.append(rec)
    n = upsert(session, AuctionDaily, rows, ["symbol", "date"])
    return {"names": n}


# ── build: loads ───────────────────────────────────────────────────────
def _universe(session: Session, day: date) -> list[str]:
    from trading_intel.memory.models import GreeksSnapshot
    from trading_intel.memory.models_equity import EquityMember

    try:
        mem = set(session.execute(select(EquityMember.symbol).where(EquityMember.end_date.is_(None))).scalars())
    except Exception:  # noqa: BLE001 - column naming differs in old schemas
        session.rollback()
        mem = set(session.execute(select(EquityMember.symbol)).scalars())
    bank = set(session.execute(select(GreeksSnapshot.symbol).where(
        GreeksSnapshot.source == BANK_SOURCE, GreeksSnapshot.ts >= datetime.combine(day, time(0)),
        GreeksSnapshot.ts < datetime.combine(day + timedelta(days=1), time(0))).distinct()).scalars())
    return sorted(set(C.GRID) | mem | bank)


def _bars(session: Session, syms: list[str], day: date) -> dict[str, pd.DataFrame]:
    from trading_intel.memory.models_equity import EquityBar

    out: dict[str, list] = defaultdict(list)
    for i in range(0, len(syms), 500):
        part = syms[i:i + 500]
        for r in session.execute(select(EquityBar.symbol, EquityBar.date, EquityBar.close, EquityBar.high,
                                        EquityBar.low, EquityBar.volume)
                                 .where(EquityBar.symbol.in_(part), EquityBar.date <= day,
                                        EquityBar.date >= day - timedelta(days=400))
                                 .order_by(EquityBar.symbol, EquityBar.date)).all():
            out[r[0]].append(r[1:])
    return {s: pd.DataFrame(v, columns=["date", "close", "high", "low", "volume"]) for s, v in out.items()}


def _bank(session: Session, day: date) -> pd.DataFrame:
    """The chain bank's last run of each session (greeks_snapshots, source cvforge_bank), last ~13 months."""
    from trading_intel.memory.models import GreeksSnapshot

    since = datetime.combine(day - timedelta(days=400), time(0))
    end = datetime.combine(day + timedelta(days=1), time(0))
    ts = list(session.execute(select(GreeksSnapshot.ts).where(GreeksSnapshot.source == BANK_SOURCE,
                                                               GreeksSnapshot.ts >= since, GreeksSnapshot.ts < end)
                              .distinct()).scalars())
    last: dict[date, datetime] = {}
    for t in ts:
        t = t.replace(tzinfo=None)
        if t.date() not in last or t > last[t.date()]:
            last[t.date()] = t
    if not last:
        return pd.DataFrame()
    cols = ["symbol", "ts", "spot", "iv30", "iv90", "ts_30_90", "skew25_30", "gex_total", "gex_flip",
            "call_premium_usd", "put_premium_usd"]
    rows = session.execute(select(*[getattr(GreeksSnapshot, c) for c in cols])
                           .where(GreeksSnapshot.source == BANK_SOURCE, GreeksSnapshot.ts.in_(list(last.values())))).all()
    df = pd.DataFrame(rows, columns=cols)
    df["date"] = pd.to_datetime(df["ts"]).dt.tz_localize(None).dt.date if len(df) else []
    return df


def _convex_eod(session: Session, day: date) -> pd.DataFrame:
    from trading_intel.memory.models import OiChainDaily

    rows = session.execute(select(OiChainDaily.symbol, OiChainDaily.ts, OiChainDaily.atm_iv_30d,
                                  OiChainDaily.call_wall, OiChainDaily.put_wall)
                           .where(OiChainDaily.source == "convex_eod", OiChainDaily.ts <= day,
                                  OiChainDaily.ts >= day - timedelta(days=400))).all()
    return pd.DataFrame(rows, columns=["symbol", "date", "iv", "call_wall", "put_wall"])


def _finra(session: Session, syms: list[str], day: date) -> dict[str, pd.DataFrame]:
    from trading_intel.memory.models_desk import FinraShortVolume

    out: dict[str, list] = defaultdict(list)
    for i in range(0, len(syms), 500):
        for r in session.execute(select(FinraShortVolume.symbol, FinraShortVolume.date, FinraShortVolume.short_volume,
                                        FinraShortVolume.total_volume)
                                 .where(FinraShortVolume.symbol.in_(syms[i:i + 500]), FinraShortVolume.date <= day,
                                        FinraShortVolume.date >= day - timedelta(days=100))
                                 .order_by(FinraShortVolume.symbol, FinraShortVolume.date)).all():
            out[r[0]].append(r[1:])
    return {s: pd.DataFrame(v, columns=["date", "short", "total"]) for s, v in out.items()}


def _auctions(session: Session, day: date) -> dict[str, list[dict]]:
    from trading_intel.memory.models_compass import AuctionDaily

    out: dict[str, list[dict]] = defaultdict(list)
    for r in session.execute(select(AuctionDaily).where(AuctionDaily.date <= day,
                                                        AuctionDaily.date >= day - timedelta(days=21))
                             .order_by(AuctionDaily.date)).scalars():
        out[r.symbol].append({k: getattr(r, k) for k in ("date", "open", "high", "low", "close", "vwap", "vwap_sd",
                                                         "poc", "vah", "val", "ib_high", "ib_low", "day_type",
                                                         "open_vs_value", "close_vs_value", "poor_high", "poor_low")})
    return out


def _bank_walls(session: Session, day: date, syms: list[str] | None = None) -> dict[str, dict]:
    """Call / put wall (largest gamma x OI strike, expiries within 45 days) from the bank's last chain of the day."""
    from trading_intel.memory.models import GreeksChain

    t0, t1 = datetime.combine(day, time(0)), datetime.combine(day + timedelta(days=1), time(0))
    last = session.execute(select(func.max(GreeksChain.ts)).where(GreeksChain.source == BANK_SOURCE,
                                                                   GreeksChain.ts >= t0, GreeksChain.ts < t1)).scalar()
    if last is None:
        return {}
    q = (select(GreeksChain.symbol, GreeksChain.cp, GreeksChain.strike, func.sum(GreeksChain.gxoi))
         .where(GreeksChain.source == BANK_SOURCE, GreeksChain.ts == last, GreeksChain.expiry <= day + timedelta(days=45))
         .group_by(GreeksChain.symbol, GreeksChain.cp, GreeksChain.strike))
    if syms:
        q = q.where(GreeksChain.symbol.in_(syms))
    df = pd.DataFrame(session.execute(q).all(), columns=["symbol", "cp", "strike", "g"])
    out: dict[str, dict] = {}
    if df.empty:
        return out
    df["g"] = pd.to_numeric(df["g"], errors="coerce").fillna(0)
    for (s, cp), g in df.groupby(["symbol", "cp"]):
        if g["g"].max() > 0:
            out.setdefault(s, {})["call_wall" if cp == "C" else "put_wall"] = float(g.loc[g["g"].idxmax(), "strike"])
    return out


# ── build: one name ────────────────────────────────────────────────────
def name_row(sym: str, day: date, bars: pd.DataFrame | None, bank: pd.DataFrame | None, cvx: pd.DataFrame | None,
             fin: pd.DataFrame | None, auc: list[dict] | None, walls: dict | None) -> dict | None:
    """Everything the Compass knows about one name on ``day`` (None when there is no daily bar for the day)."""
    if bars is None or bars.empty or bars["date"].iloc[-1] != day:
        return None
    c = bars["close"].astype(float).to_numpy()
    r: dict = {"symbol": sym, "date": day, "grp": C.GROUP_OF.get(sym, "stock"), "close": float(c[-1]),
               "ret1": C.pct_ret(c, 1), "ret5": C.pct_ret(c, 5), "ret20": C.pct_ret(c, 20),
               "hv10": C.hv(c, 10), "hv30": C.hv(c, 30), "bb_pctb": C.bollinger_pctb(c)}
    # implied vol: the bank once it has 20 sessions, else Convex EOD ATM IV (cleaned), else realized
    ivs, src = None, None
    if bank is not None and len(bank) and bank["iv30"].notna().sum() >= C.MIN_RANK_OBS:
        ivs, src = bank.set_index("date")["iv30"].dropna(), BANK_SOURCE
    elif cvx is not None and len(cvx) and cvx["iv"].notna().sum() >= 3:
        ivs, src = C.clean_iv(cvx.set_index("date")["iv"].dropna()), "convex_eod"
    elif bank is not None and len(bank) and bank["iv30"].notna().any():
        ivs, src = bank.set_index("date")["iv30"].dropna(), BANK_SOURCE
    if ivs is not None and len(ivs):
        ivs = ivs.sort_index()
        r["iv30"] = float(ivs.iloc[-1]) if ivs.index[-1] >= day - timedelta(days=4) else None
        prev = ivs[ivs.index < day]
        iv_prev = float(prev.iloc[-1]) if len(prev) else None
        hist = ivs[ivs.index > day - timedelta(days=365)]
        r["iv_rank"] = C.rank(hist.tolist(), r["iv30"])
    else:
        iv_prev, r["iv30"], r["iv_rank"] = None, None, None
    hv_prev = C.hv(c[:-1], 20)
    r["sigma_move"] = C.sigma_move(r["ret1"], iv_prev or hv_prev)
    r["iv_src"] = src if iv_prev else ("hv20" if hv_prev else None)
    r["carry"] = (r["iv30"] - r["hv10"]) if r.get("iv30") and r.get("hv10") else None
    if bank is not None and len(bank):
        b = bank.sort_values("date")
        t = b.iloc[-1] if b["date"].iloc[-1] == day else None
        if t is not None:
            r.update(ts_30_90=_f(t["ts_30_90"]), skew25=_f(t["skew25_30"]), gex=_f(t["gex_total"]),
                     gex_flip=_f(t["gex_flip"]))
            cp, pp = _f(t["call_premium_usd"]) or 0.0, _f(t["put_premium_usd"]) or 0.0
            r["prem_usd"] = cp + pp
            r["call_share"] = cp / (cp + pp) if cp + pp > 0 else None
            past = (b["call_premium_usd"].fillna(0) + b["put_premium_usd"].fillna(0)).iloc[:-1].tail(20)
            r["prem_x20"] = (r["prem_usd"] / past.mean()) if len(past) >= 5 and past.mean() > 0 else None
            sk = b["skew25_30"].dropna()
            r["skew_rank"] = C.rank(sk.iloc[:-1].tail(250).tolist(), r["skew25"]) if len(sk) > 1 else None
            r["skew_chg5"] = (float(sk.iloc[-1] - sk.iloc[-6])) if len(sk) >= 6 else None
    if not r.get("call_wall") and cvx is not None and len(cvx):
        last = cvx.sort_values("date").iloc[-1]
        if last["date"] >= day - timedelta(days=4):          # never a stale wall
            r["call_wall"], r["put_wall"] = _f(last["call_wall"]), _f(last["put_wall"])
    if walls:
        r.update({k: v for k, v in walls.items() if v})
    if fin is not None and len(fin) and fin["date"].iloc[-1] == day:
        r["dark_ratio"], r["dark_z"] = C.dark_stats(fin["short"].tolist(), fin["total"].tolist())
    if auc:
        a = auc[-1] if auc[-1]["date"] == day else None
        if a:
            r.update({k: a[k] for k in ("vwap", "vwap_sd", "poc", "vah", "val", "day_type", "open_vs_value",
                                        "close_vs_value", "poor_high", "poor_low")})
        r["naked_pocs"] = C.naked_pocs(auc[-10:])
        r["balance3"] = C.balance(auc)
    r["flags"] = C.flags(r)
    return r


# ── build: panel data (grid names + scanner hits) ──────────────────────
def _roll_hv(c: pd.Series, n: int) -> pd.Series:
    return np.log(c).diff().rolling(n).std() * math.sqrt(C.TRADING_DAYS)


def panel(sym: str, row: dict, bars: pd.DataFrame, bank: pd.DataFrame | None, cvx: pd.DataFrame | None,
          fin: pd.DataFrame | None, auc: list[dict] | None, term: list | None, gamma: dict | None) -> dict:
    b = bars.tail(PANEL_DAYS + 30).reset_index(drop=True)
    c = b["close"].astype(float)
    ma, sd = c.rolling(20).mean(), c.rolling(20).std(ddof=0)
    hv10, hv30, hv20 = _roll_hv(c, 10), _roll_hv(c, 30), _roll_hv(c, 20)
    dates = b["date"].tolist()
    iv = pd.Series(index=dates, dtype=float)
    if row.get("iv_src") == BANK_SOURCE and bank is not None and len(bank):
        iv = bank.set_index("date")["iv30"].reindex(dates)
    elif cvx is not None and len(cvx):
        iv = C.clean_iv(cvx.set_index("date")["iv"].dropna()).reindex(dates)
    ivp = iv.ffill().shift(1)
    vol_prev = ivp.where(ivp.notna(), hv20.shift(1).set_axis(dates))
    ret = c.pct_change().set_axis(dates)
    sm = ret / (vol_prev / math.sqrt(C.TRADING_DAYS))
    sk = bank.set_index("date")["skew25_30"].reindex(dates) if bank is not None and len(bank) else None
    dr = None
    if fin is not None and len(fin):
        f = fin.set_index("date")
        dr = (f["short"] / f["total"].where(f["total"] > 0)).reindex(dates)
    k = slice(-PANEL_DAYS, None)

    def L(s, n=4):  # noqa: ANN001, ANN202
        return [_r(x, n) for x in list(s)[k]]

    out = {"d": [str(x) for x in dates[k]], "c": L(c, 4), "bbu": L(ma + 2 * sd, 4), "bbl": L(ma - 2 * sd, 4),
           "ma": L(ma, 4), "hv10": L(hv10.set_axis(dates)), "hv30": L(hv30.set_axis(dates)), "iv": L(iv),
           "sm": L(sm, 3)}
    if sk is not None and sk.notna().any():
        out["sk"] = L(sk)
    if dr is not None and dr.notna().any():
        out["dr"] = L(dr)
    if auc:
        a = auc[-1]
        out["auc"] = {k2: (str(v) if isinstance(v, date) else _r(v, 4) if isinstance(v, float) else v)
                      for k2, v in a.items()}
    if term:
        out["term"] = term
    if gamma:
        out["gamma"] = gamma
    return out


def _term_gamma(session: Session, sym: str, day: date, spot: float | None) -> tuple[list, dict | None]:
    """From the bank's last chain of ``day``: ATM IV per expiry (today, ~1 week and ~1 month ago when banked)
    and call / put gamma x OI by strike (expiries within 45 days, strikes within 15% of spot)."""
    from trading_intel.memory.models import GreeksChain

    def last_ts(d: date):  # noqa: ANN202
        return session.execute(select(func.max(GreeksChain.ts)).where(
            GreeksChain.symbol == sym, GreeksChain.source == BANK_SOURCE,
            GreeksChain.ts >= datetime.combine(d - timedelta(days=3), time(0)),
            GreeksChain.ts < datetime.combine(d + timedelta(days=1), time(0)))).scalar()

    def chain(ts):  # noqa: ANN001, ANN202
        return pd.DataFrame(session.execute(select(GreeksChain.expiry, GreeksChain.strike, GreeksChain.cp,
                                                   GreeksChain.iv, GreeksChain.gxoi, GreeksChain.spot)
                                            .where(GreeksChain.symbol == sym, GreeksChain.source == BANK_SOURCE,
                                                   GreeksChain.ts == ts)).all(),
                            columns=["expiry", "strike", "cp", "iv", "gxoi", "spot"])

    term, gamma = [], None
    for label, d in (("now", day), ("1w", day - timedelta(days=7)), ("1m", day - timedelta(days=30))):
        ts = last_ts(d)
        if ts is None:
            continue
        ch = chain(ts)
        if ch.empty:
            continue
        s0 = _f(ch["spot"].median()) or spot
        if not s0:
            continue
        pts = []
        for exp, g in ch[(ch["iv"] > 0) & (ch["iv"] < 5)].groupby("expiry"):
            dte = (pd.Timestamp(exp).date() - ts.date()).days
            if dte < 1 or dte > 400:
                continue
            k = g.groupby("strike")["iv"].mean()
            if len(k) < 2 or not (k.index.min() <= s0 <= k.index.max()):
                continue
            pts.append([dte, round(float(np.interp(s0, k.index.to_numpy(float), k.to_numpy(float))), 4)])
        if pts:
            term.append({"label": label, "pts": sorted(pts)})
        if label == "now":
            near = ch[(pd.to_datetime(ch["expiry"]).dt.date <= day + timedelta(days=45))
                      & (ch["strike"].between(s0 * 0.85, s0 * 1.15))]
            if len(near):
                g = near.groupby(["strike", "cp"])["gxoi"].sum().unstack(fill_value=0.0)
                gamma = {"k": [float(x) for x in g.index], "c": [_r(x, 1) for x in g.get("C", pd.Series(0, g.index))],
                         "p": [_r(x, 1) for x in g.get("P", pd.Series(0, g.index))], "spot": s0}
    return term, gamma


# ── build ──────────────────────────────────────────────────────────────
def build(session: Session, day: date, out_dir: Path | None = None) -> dict:
    from trading_intel.equity.store import upsert
    from trading_intel.memory.models_compass import COMPASS_FLOATS, CompassDaily

    syms = _universe(session, day)
    bars = _bars(session, syms, day)
    bank = _bank(session, day)
    bank_by = {s: g.sort_values("date").reset_index(drop=True) for s, g in bank.groupby("symbol")} if len(bank) else {}
    cvx = _convex_eod(session, day)
    cvx_by = {s: g.sort_values("date").reset_index(drop=True) for s, g in cvx.groupby("symbol")} if len(cvx) else {}
    fin = _finra(session, syms, day)
    auc = _auctions(session, day)
    walls = _bank_walls(session, day)
    rows = []
    for s in syms:
        r = name_row(s, day, bars.get(s), bank_by.get(s), cvx_by.get(s), fin.get(s), auc.get(s), walls.get(s))
        if r:
            rows.append(r)
    stamp = eastern_now().replace(microsecond=0)
    db_rows = [{"symbol": r["symbol"], "date": day, "grp": r["grp"], "iv_src": r.get("iv_src"),
                "flags": ",".join(r["flags"])[:400], "computed_at": stamp,
                **{k: _f(r.get(k)) for k in COMPASS_FLOATS}} for r in rows]
    upsert(session, CompassDaily, db_rows, ["symbol", "date"])

    by = {r["symbol"]: r for r in rows}
    scanners = []
    for sid, (cat, title, meaning) in C.SCANNERS.items():
        hits = [r for r in rows if sid in r["flags"]]
        key = {"big_move": lambda r: -abs(r.get("sigma_move") or 0), "iv_high": lambda r: -(r.get("iv_rank") or 0),
               "iv_low": lambda r: r.get("iv_rank") or 0, "carry_rich": lambda r: -(r.get("carry") or 0),
               "carry_cheap": lambda r: r.get("carry") or 0, "term_inverted": lambda r: -(r.get("ts_30_90") or 0),
               "prem_surge": lambda r: -(r.get("prem_x20") or 0), "dark_spike": lambda r: -(r.get("dark_z") or 0),
               "dark_high_falling": lambda r: -(r.get("dark_z") or 0),
               "near_wall": lambda r: min(abs((w or 1e9) / r["close"] - 1) for w in (r.get("call_wall"), r.get("put_wall"))),
               }.get(sid, lambda r: -(r.get("prem_usd") or 0))
        hits.sort(key=key)
        scanners.append({"id": sid, "cat": cat, "title": title, "meaning": meaning, "n": len(hits),
                         "hits": [r["symbol"] for r in hits[:HIT_TOP]]})
    # charts for EVERY name, one small file each (scripts/cvxmap/data/compass_p/<SYM>.json, fetched when a name is
    # opened); the bank's term structure + gamma by strike for the grid, the scanner hits and today's focus names
    rich = set(C.GRID) | {s for sc in scanners for s in sc["hits"]}
    try:
        from trading_intel import focus as _focus

        rich |= set(_focus.get_picks(session, day))
    except Exception:  # noqa: BLE001 - focus_names may not exist in an old schema
        session.rollback()
    folder = Path(out_dir or os.environ.get("DASH_DIR") or COCKPIT_DATA)
    pdir = folder / "compass_p"
    pdir.mkdir(parents=True, exist_ok=True)
    panels = 0
    for s, r in by.items():
        try:
            term, gamma = (_term_gamma(session, s, day, r.get("close"))
                           if s in rich and bank_by.get(s) is not None else ([], None))
            pj = panel(s, r, bars[s], bank_by.get(s), cvx_by.get(s), fin.get(s), auc.get(s), term, gamma)
            pj["date"] = day.isoformat()
            (pdir / f"{s.replace('/', '_')}.json").write_text(json.dumps(pj, separators=(",", ":"), default=str),
                                                              encoding="utf-8")
            panels += 1
        except Exception as exc:  # noqa: BLE001 - one bad panel never blanks the page
            log.warning("compass.panel_failed", symbol=s, error=str(exc)[:160])
    keep = ("grp", "close", "ret1", "ret5", "ret20", "sigma_move", "hv10", "hv30", "iv30", "iv_rank", "iv_src",
            "carry", "ts_30_90", "skew25", "skew_rank", "bb_pctb", "dark_ratio", "dark_z", "prem_usd", "prem_x20",
            "call_share", "gex", "gex_flip", "call_wall", "put_wall", "vwap", "poc", "vah", "val", "day_type",
            "open_vs_value", "close_vs_value", "flags")
    names = {r["symbol"]: {k: (_r(r.get(k), 4) if isinstance(r.get(k), float) else r.get(k)) for k in keep
                           if r.get(k) not in (None, [], "")} for r in rows}
    cov = {"names": len(rows), "bank_iv": sum(1 for r in rows if r.get("iv_src") == BANK_SOURCE),
           "convex_iv": sum(1 for r in rows if r.get("iv_src") == "convex_eod"),
           "iv_rank": sum(1 for r in rows if r.get("iv_rank") is not None),
           "dark": sum(1 for r in rows if r.get("dark_ratio") is not None),
           "auction": sum(1 for r in rows if r.get("poc") is not None),
           "bank_days": int(bank["date"].nunique()) if len(bank) else 0}
    out = {"v": 2, "date": day.isoformat(), "generated": stamp.isoformat(), "groups": C.GROUPS,
           "scanners": scanners, "names": names, "coverage": cov,
           "note": "Descriptive, not signals. sigma-move = today's move / (yesterday's IV30 / sqrt 252); HV20 "
                   "when no IV. IV rank = share of the past year's IV30 at or below today's (needs 20 sessions). "
                   "Dark ratio = FINRA off-exchange short volume / total (mostly market makers filling buys)."}
    tmp = folder / "compass.json.tmp"
    tmp.write_text(json.dumps(out, separators=(",", ":"), default=str), encoding="utf-8")
    os.replace(tmp, folder / "compass.json")
    if PAGE_SRC.exists():
        shutil.copyfile(PAGE_SRC, folder / "compass.html")
    return {"rows": len(rows), "panels": panels, **cov}


def run(session: Session, settings: Settings | None = None, day: date | None = None,
        steps=("collect", "auction", "build"), force: bool = False, out_dir: Path | None = None) -> dict:  # noqa: ANN001
    settings = settings or get_settings()
    day = day or eastern_now().date()
    bound = log.bind(correlation_id=uuid.uuid4().hex, job="compass")
    if not force and not is_trading_session(day):
        bound.info("compass.skip", reason="not_a_trading_session")
        return {"skipped": "not_a_trading_session"}
    out: dict = {}
    errors = []
    for step, fn in (("collect", lambda: collect(session, settings, day)), ("auction", lambda: run_auction(session, day)),
                     ("build", lambda: build(session, day, out_dir))):
        if step not in steps:
            continue
        try:
            out[step] = fn()
        except Exception as exc:  # noqa: BLE001 - a failed collect never stops the build
            session.rollback()
            errors.append(f"{step}: {type(exc).__name__}: {str(exc)[:200]}")
    out["errors"] = errors
    (bound.info if not errors else bound.error)("compass.done", **{k: v for k, v in out.items() if k != "collect"})
    try:
        from trading_intel.scheduler import track

        track.report(rows_attempted=(out.get("build") or {}).get("names"), rows_written=(out.get("build") or {}).get("rows"))
    except Exception:  # noqa: S110 - bookkeeping only
        pass
    return out


def main(argv: list[str] | None = None) -> int:
    from trading_intel.memory.db import make_session_factory

    ap = argparse.ArgumentParser(prog="compass")
    ap.add_argument("--day")
    ap.add_argument("--steps", default="collect,auction,build")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args(argv)
    s = get_settings()
    with make_session_factory(s)() as session:
        res = run(session, s, date.fromisoformat(a.day) if a.day else None, tuple(a.steps.split(",")), a.force)
    print(json.dumps(res, default=str))
    return 1 if res.get("errors") else 0


if __name__ == "__main__":
    sys.exit(main())
