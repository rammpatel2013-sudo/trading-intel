"""Focus names: the night-before levels card (2026-11-20).

"old data provide me some level and idea about swing" - built ONLY from the foundational database: the hourly
chain bank (greeks_snapshots / greeks_chain, source cvforge_bank) and 1-minute bars (bars_1m). No vendor call.

Per name:
  levels   call / put wall (gamma x OI) in the next 7 days and out to 45 days, gamma flip, biggest OI strikes
           in the next 7 days, 5-session value area (POC / VAH / VAL from 1-minute volume), prior session
           high / low / close, 5-session high / low
  swing    expected move for 1 day and 5 days from IV30 (spot x IV30 x sqrt(days / 252))
  trend    last ~10 sessions, the session-end bank row: spot, IV30, IV30/IV90, 25-delta skew, net GEX
  read     plain lines (descriptors, not signals - rule 4)
"""

from __future__ import annotations

import math
from datetime import date, datetime, time, timedelta

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from trading_intel.chain_bank import BANK_SOURCE

RTH = (time(9, 30), time(16, 0))
VA_SHARE = 0.70


def _f(x) -> float | None:  # noqa: ANN001
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def value_area(bars: pd.DataFrame, share: float = VA_SHARE, bins: int = 60) -> dict | None:
    """Volume profile from 1-minute bars (each bar's volume at its typical price): POC, VAH, VAL."""
    b = bars.dropna(subset=["high", "low", "close", "volume"])
    b = b[b["volume"] > 0]
    if len(b) < 30:
        return None
    lo, hi = float(b["low"].min()), float(b["high"].max())
    if not hi > lo:
        return None
    edges = np.linspace(lo, hi, bins + 1)
    tp = ((b["high"] + b["low"] + b["close"]) / 3.0).to_numpy(float)
    idx = np.clip(np.searchsorted(edges, tp, side="right") - 1, 0, bins - 1)
    vol = np.bincount(idx, weights=b["volume"].to_numpy(float), minlength=bins)
    mid = (edges[:-1] + edges[1:]) / 2.0
    poc = int(vol.argmax())
    lo_i = hi_i = poc
    got, want = vol[poc], vol.sum() * share
    while got < want and (lo_i > 0 or hi_i < bins - 1):
        up = vol[hi_i + 1] if hi_i < bins - 1 else -1.0
        dn = vol[lo_i - 1] if lo_i > 0 else -1.0
        if up >= dn:
            hi_i += 1
            got += up
        else:
            lo_i -= 1
            got += dn
    return {"poc": round(float(mid[poc]), 2), "vah": round(float(edges[hi_i + 1]), 2),
            "val": round(float(edges[lo_i]), 2)}


def walls(chain: pd.DataFrame, spot: float, max_dte: int, day: date) -> dict:
    """Call / put wall (largest gamma x OI strike per side) for expiries within ``max_dte`` days."""
    c = chain[(chain["expiry"] >= day) & (chain["expiry"] <= day + timedelta(days=max_dte))]
    out: dict = {"call_wall": None, "put_wall": None}
    for side, key in (("C", "call_wall"), ("P", "put_wall")):
        s = c[c["cp"] == side]
        if s.empty:
            continue
        g = s.groupby("strike")["gxoi"].sum()
        if g.max() > 0:
            out[key] = float(g.idxmax())
    return out


def top_oi(chain: pd.DataFrame, day: date, max_dte: int = 7, n: int = 3) -> dict:
    c = chain[(chain["expiry"] >= day) & (chain["expiry"] <= day + timedelta(days=max_dte))]
    out = {}
    for side, key in (("C", "calls"), ("P", "puts")):
        s = c[c["cp"] == side].groupby("strike")["oi"].sum().sort_values(ascending=False).head(n)
        out[key] = [{"k": float(k), "oi": int(v)} for k, v in s.items() if v > 0]
    return out


def _pct(a: float | None, b: float | None) -> float | None:
    return (a / b - 1.0) if a is not None and b else None


def read_lines(c: dict) -> list[str]:
    """Plain-English descriptors for someone who is not an options specialist."""
    L: list[str] = []
    s, flip = c.get("spot"), c.get("gex_flip")
    if s and flip:
        if s > flip:
            L.append(f"Price {s:,.2f} is ABOVE the gamma flip {flip:,.2f}: dealers are mostly long gamma - "
                     "they sell rallies and buy dips, so moves tend to be damped.")
        else:
            L.append(f"Price {s:,.2f} is BELOW the gamma flip {flip:,.2f}: dealers are mostly short gamma - "
                     "they chase moves, so swings tend to run further.")
    w7 = c.get("walls_7d") or {}
    if s and w7.get("call_wall"):
        L.append(f"Call wall this week {w7['call_wall']:,.2f} ({_pct(w7['call_wall'], s):+.1%}): the strike "
                 "most likely to act as a ceiling / magnet into expiry.")
    if s and w7.get("put_wall"):
        L.append(f"Put wall this week {w7['put_wall']:,.2f} ({_pct(w7['put_wall'], s):+.1%}): the strike most "
                 "likely to act as a floor.")
    em = c.get("em") or {}
    if em.get("d1") and em.get("d5"):
        L.append(f"Options price a typical day of +/-{em['d1']:,.2f} and a typical week of +/-{em['d5']:,.2f} "
                 "(about 2 days in 3 stay inside).")
    t = c.get("trend") or []
    ivs = [x.get("iv30") for x in t if x.get("iv30")]
    if len(ivs) >= 4 and c.get("iv30"):
        avg = float(np.mean(ivs[:-1]))
        rel = c["iv30"] / avg - 1.0
        word = "richer than" if rel > 0.05 else "cheaper than" if rel < -0.05 else "in line with"
        L.append(f"IV30 {c['iv30']:.0%} is {word} its recent average {avg:.0%} - options are "
                 f"{'expensive' if rel > 0.05 else 'cheap' if rel < -0.05 else 'fairly priced'} vs the last "
                 f"{len(ivs)} sessions.")
    sk = [x.get("skew25") for x in t if x.get("skew25") is not None]
    if len(sk) >= 4:
        d = sk[-1] - sk[0]
        if abs(d) >= 0.01:
            L.append("Put skew has " + ("risen" if d > 0 else "fallen") + f" {abs(d):.1%} over the window: "
                     + ("more demand for downside protection." if d > 0 else "less demand for downside protection."))
    ts = c.get("ts_30_90")
    if ts and ts > 1.02:
        L.append(f"Term structure inverted (IV30 / IV90 {ts:.2f}): near-dated fear or an event (earnings?) is priced.")
    va = c.get("value_5d") or {}
    if s and va.get("val") and va.get("vah"):
        where = "inside" if va["val"] <= s <= va["vah"] else ("above" if s > va["vah"] else "below")
        L.append(f"5-day value area {va['val']:,.2f} - {va['vah']:,.2f} (POC {va['poc']:,.2f}); price is {where} it.")
    return L


def build(session: Session, sym: str, asof: datetime) -> dict:
    """The card for one name as of ``asof`` (naive Eastern). ``in_bank`` False when the bank has nothing."""
    from trading_intel.memory.models import GreeksChain, GreeksSnapshot
    from trading_intel.memory.models_flow import Bars1m

    since = asof - timedelta(days=16)
    snaps = session.execute(
        select(GreeksSnapshot).where(GreeksSnapshot.symbol == sym, GreeksSnapshot.source == BANK_SOURCE,
                                     GreeksSnapshot.ts >= since, GreeksSnapshot.ts <= asof)
        .order_by(GreeksSnapshot.ts)
    ).scalars().all()
    card: dict = {"symbol": sym, "asof": asof.isoformat(timespec="minutes"), "in_bank": bool(snaps)}
    if not snaps:
        return card
    last = snaps[-1]
    card.update({"bank_ts": last.ts.isoformat(timespec="minutes"), "spot": _f(last.spot),
                 "gex_total": _f(last.gex_total), "dex_total": _f(last.dex_total), "gex_flip": _f(last.gex_flip),
                 "iv30": _f(last.iv30), "iv90": _f(last.iv90), "ts_30_90": _f(last.ts_30_90),
                 "skew25": _f(last.skew25_30), "call_oi": _f(last.call_oi), "put_oi": _f(last.put_oi),
                 "call_prem": _f(last.call_premium_usd), "put_prem": _f(last.put_premium_usd)})
    by_day: dict[date, GreeksSnapshot] = {}
    for r in snaps:
        by_day[r.ts.date()] = r                    # session-end row per day
    card["trend"] = [{"d": d.isoformat(), "spot": _f(r.spot), "iv30": _f(r.iv30), "ts": _f(r.ts_30_90),
                      "skew25": _f(r.skew25_30), "gex": _f(r.gex_total)} for d, r in sorted(by_day.items())][-10:]
    s, iv = card["spot"], card["iv30"]
    if s and iv:
        card["em"] = {"d1": round(s * iv * math.sqrt(1 / 252), 2), "d5": round(s * iv * math.sqrt(5 / 252), 2)}

    rows = session.execute(
        select(GreeksChain.expiry, GreeksChain.strike, GreeksChain.cp, GreeksChain.oi, GreeksChain.gxoi)
        .where(GreeksChain.symbol == sym, GreeksChain.source == BANK_SOURCE, GreeksChain.ts == last.ts)
    ).all()
    if rows:
        ch = pd.DataFrame(rows, columns=["expiry", "strike", "cp", "oi", "gxoi"])
        ch["expiry"] = pd.to_datetime(ch["expiry"]).dt.date
        ch[["oi", "gxoi"]] = ch[["oi", "gxoi"]].apply(pd.to_numeric, errors="coerce").fillna(0.0)
        day = asof.date()
        card["walls_7d"] = walls(ch, s or 0.0, 7, day)
        card["walls_45d"] = walls(ch, s or 0.0, 45, day)
        card["top_oi_7d"] = top_oi(ch, day)

    bars = session.execute(
        select(Bars1m.ts, Bars1m.high, Bars1m.low, Bars1m.close, Bars1m.volume)
        .where(Bars1m.symbol == sym, Bars1m.ts >= asof - timedelta(days=10), Bars1m.ts <= asof)
        .order_by(Bars1m.ts)
    ).all()
    if bars:
        b = pd.DataFrame(bars, columns=["ts", "high", "low", "close", "volume"]).drop_duplicates("ts")
        b = b[(b["ts"].dt.time >= RTH[0]) & (b["ts"].dt.time < RTH[1])]
        days = sorted(b["ts"].dt.date.unique())
        if days:
            last5 = b[b["ts"].dt.date.isin(days[-5:])]
            card["value_5d"] = value_area(last5)
            pd_ = b[b["ts"].dt.date == days[-1]]
            card["prior"] = {"d": days[-1].isoformat(), "high": _f(pd_["high"].max()), "low": _f(pd_["low"].min()),
                             "close": _f(pd_["close"].iloc[-1])}
            card["range_5d"] = {"high": _f(last5["high"].max()), "low": _f(last5["low"].min())}
    card["read"] = read_lines(card)
    return card
