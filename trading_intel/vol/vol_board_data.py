"""Fetch + assemble the vol board's three panels from CVForge (+ banked skew).

All vendor access goes through ``clients/cvforge.py`` (rule 1). CVForge serves
the LIVE chain (smile + full expiry ladder, TLT reaches 851 DTE) but no
historical Greeks, so the IV history is rebuilt by re-solving implied vol from
``/mas`` daily contract closes against that day's underlying close — the only
path to a trailing constant-maturity series without waiting a year to bank one.

skew30 comes from the banked ``skew_snapshots`` (342 names, back to 2024-07)
rather than re-deriving a smile per day: it is already collected and far cheaper.
"""

from __future__ import annotations

import datetime as dt
import math

import numpy as np
import pandas as pd
import structlog

from trading_intel.greeks.black_scholes import (
    forward_from_parity,
    implied_vol_fwd,
)
from trading_intel.vol.vol_board import TENORS, atm_iv_by_expiry, cm_interp

log = structlog.get_logger(__name__)

_RATE = 0.04  # flat discount rate for the parity forward; cancels in the C/P pair

_CHAIN_SQL = """
SELECT expiration_date, strike_price, contract_type, ticker,
       implied_volatility, delta, underlying_price, open_interest, day_volume
FROM options_snapshots
WHERE underlying_ticker = '{sym}'
  AND implied_volatility IS NOT NULL
  AND strike_price BETWEEN {lo} AND {hi}
"""


def fetch_chain(client, symbol: str, *, band: float = 0.30) -> tuple[float, list[dict]]:
    """Live chain rows within ``band`` of spot, plus spot itself."""
    probe = client.query(
        f"SELECT underlying_price FROM options_snapshots WHERE underlying_ticker='{symbol}' LIMIT 1"
    )
    if probe.empty:
        raise ValueError(f"no CVForge chain for {symbol}")
    spot = float(probe["underlying_price"].iloc[0])
    rows = client.query(
        _CHAIN_SQL.format(sym=symbol, lo=round(spot * (1 - band), 2), hi=round(spot * (1 + band), 2)),
        max_rows=40_000,
    )
    return spot, rows.to_dict("records")


def dte_of(exp: str, ref: dt.date) -> int:
    return (dt.date.fromisoformat(str(exp)[:10]) - ref).days


def current_curve(rows: list[dict], spot: float, ref: dt.date) -> dict[int, float | None]:
    """Today's constant-maturity ATM IV at each tenor."""
    atm = atm_iv_by_expiry(rows, spot)
    by_dte: dict[int, float] = {}
    for exp, (_, iv) in atm.items():
        d = dte_of(exp, ref)
        if d > 0:
            by_dte[d] = iv
    if not by_dte:
        return {t: None for t in TENORS}
    d = np.array(sorted(by_dte), dtype=float)
    v = np.array([by_dte[int(x)] for x in d], dtype=float)
    return {t: cm_interp(d, v, float(t)) for t in TENORS}


def _underlying_closes(client, symbol: str, frm: dt.date, to: dt.date) -> dict[str, float]:
    res = client.fmp("historical-price-eod/light", {"symbol": symbol, "from": str(frm), "to": str(to)})
    arr = res if isinstance(res, list) else (res or {}).get("data", [])
    out = {}
    for r in arr or []:
        if r.get("date") and r.get("price") is not None:
            out[str(r["date"])[:10]] = float(r["price"])
    return out


def _pick_contracts(rows: list[dict], spot: float, ref: dt.date, *, max_exp: int, per_exp: int) -> list[dict]:
    """ATM-ish contracts spanning the expiry ladder — the history's raw material.

    Several strikes per expiry so that on a past date the strike nearest THAT
    day's spot can be used; one fixed strike would drift off ATM as spot moved
    and the 'ATM' series would silently become a fixed-strike series.
    """
    by_exp: dict[str, list[dict]] = {}
    for r in rows:
        if r.get("ticker") is None:
            continue
        by_exp.setdefault(str(r["expiration_date"])[:10], []).append(r)
    exps = sorted(e for e in by_exp if dte_of(e, ref) > 5)
    if len(exps) > max_exp:
        used = set()
        for t in list(TENORS) + [14]:
            used.add(min(exps, key=lambda e: abs(dte_of(e, ref) - t)))
        # The two longest-dated expiries are what make a 360d tenor reachable on
        # dates a YEAR ago (360d back then is ~725d from today), so they are kept
        # unconditionally rather than competing with the near-tenor targets.
        used.update(exps[-2:])
        exps = sorted(used)
    out = []
    for e in exps:
        lst = sorted(by_exp[e], key=lambda r: abs(float(r["strike_price"]) - spot))
        seen = set()
        for r in lst:
            key = (float(r["strike_price"]), str(r["contract_type"]))
            if key in seen:
                continue
            seen.add(key)
            out.append(r)
            if len(seen) >= per_exp * 2:
                break
    return out


def _third_friday(year: int, month: int) -> dt.date:
    d = dt.date(year, month, 1)
    fridays = [x for x in range(1, 29) if dt.date(year, month, x).weekday() == 4]
    return dt.date(year, month, fridays[2])


def occ_ticker(symbol: str, exp: dt.date, is_call: bool, strike: float) -> str:
    """OCC-21 contract symbol, e.g. ``O:TLT270917P00082000``."""
    return f"O:{symbol.upper()}{exp:%y%m%d}{'C' if is_call else 'P'}{int(round(strike * 1000)):08d}"


def _strike_grid(spot: float, step: float, n: int = 3) -> list[float]:
    base = round(spot / step) * step
    return [round(base + i * step, 2) for i in range(-(n // 2), n // 2 + 1)]


def historical_contracts(
    symbol: str, closes: dict[str, float], ref: dt.date, *, months_back: int = 15, strikes: int = 3
) -> list[dict]:
    """Synthesise the monthly expiry ladders that WERE listed over the past year.

    A constant-maturity history cannot be rebuilt from today's chain alone: the
    nearest expiry today was ~376 DTE a year ago, so short tenors are never
    bracketed on old dates. Expired contracts still serve ``/mas`` history, and
    OCC symbols are deterministic, so the past ladders are generated rather than
    looked up (no vendor catalog of expired contracts exists).

    Strikes are chosen around the underlying's close ~30 days before each expiry,
    so the picks are near the money for the period they cover.
    """
    if not closes:
        return []
    px = sorted(closes.items())
    med = float(np.median([v for _, v in px]))
    step = 1.0 if med < 200 else 5.0
    out: list[dict] = []
    for back in range(1, months_back + 1):
        m = ref.month - back
        y = ref.year + (m - 1) // 12
        exp = _third_friday(y, (m - 1) % 12 + 1)
        if exp >= ref:
            continue
        anchor = str(exp - dt.timedelta(days=30))
        ref_px = None
        for day, val in px:
            if day <= anchor:
                ref_px = val
            else:
                break
        if ref_px is None:
            ref_px = med
        for k in _strike_grid(ref_px, step, strikes):
            for is_call in (True, False):
                out.append({
                    "ticker": occ_ticker(symbol, exp, is_call, k),
                    "expiration_date": str(exp),
                    "strike_price": k,
                    "contract_type": "call" if is_call else "put",
                })
    return out


def build_history(
    client, symbol: str, rows: list[dict], spot: float, ref: dt.date,
    *, days: int = 400, max_exp: int = 6, per_exp: int = 3,
) -> pd.DataFrame:
    """Trailing constant-maturity ATM IV per tenor, rebuilt from contract closes.

    IV is solved against the forward implied by PUT-CALL PARITY at each
    (expiry, strike), not against spot: a dividend-paying underlying otherwise
    drives call IVs down and put IVs up with an error that grows in T (TLT 361d
    solved 5.1 vs 16.2 against a ~11.7 mark before this). Both legs are already
    fetched, so parity costs nothing extra and assumes no dividend yield.

    Returns a frame indexed by date, one column per tenor (vol points), plus
    ``spot``. A tenor not bracketed by two expiries that day is NaN, never
    extrapolated.
    """
    frm = ref - dt.timedelta(days=days)
    closes = _underlying_closes(client, symbol, frm, ref)
    if not closes:
        return pd.DataFrame()
    picks = _pick_contracts(rows, spot, ref, max_exp=max_exp, per_exp=per_exp)
    picks += historical_contracts(symbol, closes, ref, strikes=per_exp)
    log.info("vol_board.history.fetch", symbol=symbol, contracts=len(picks))

    # (day, expiry, strike) -> {"c": px, "p": px}
    legs: dict[tuple[str, str, float], dict[str, float]] = {}
    for c in picks:
        try:
            bars = client.aggs(str(c["ticker"]), multiplier=1, timespan="day", frm=frm, to=ref)
        except Exception as exc:  # noqa: BLE001 - one dead contract must not kill the board
            log.warning("vol_board.aggs_failed", ticker=c.get("ticker"), error=str(exc)[:120])
            continue
        if bars is None or len(bars) == 0:
            continue
        exp = str(c["expiration_date"])[:10]
        strike = float(c["strike_price"])
        side = "c" if str(c["contract_type"]).lower().startswith("c") else "p"
        for b in bars.to_dict("records"):
            ts, px = b.get("ts"), b.get("c")
            if ts is None or px is None or not float(px) > 0:
                continue
            legs.setdefault((str(pd.Timestamp(ts).date()), exp, strike), {})[side] = float(px)

    # day -> {dte: (iv, |strike - spot_that_day|)}
    obs: dict[str, dict[float, tuple[float, float]]] = {}
    for (day, exp, strike), leg in legs.items():
        if "c" not in leg or "p" not in leg:
            continue  # parity needs both legs
        s = closes.get(day)
        if s is None:
            continue
        tdays = (dt.date.fromisoformat(exp) - dt.date.fromisoformat(day)).days
        if tdays <= 0:
            continue
        t = tdays / 365.0
        fwd = forward_from_parity(leg["c"], leg["p"], strike, t, _RATE)
        if fwd is None:
            continue
        ivs = [
            iv for iv in (
                implied_vol_fwd(leg["c"], fwd, strike, t, _RATE, is_call=True),
                implied_vol_fwd(leg["p"], fwd, strike, t, _RATE, is_call=False),
            ) if iv is not None and 0.01 < iv < 3.0
        ]
        if not ivs:
            continue
        iv = float(np.mean(ivs))
        dist = abs(strike - s)
        cur = obs.setdefault(day, {}).get(float(tdays))
        if cur is None or dist < cur[1]:
            obs[day][float(tdays)] = (iv, dist)

    recs = []
    for day, best in sorted(obs.items()):
        if len(best) < 2:
            continue
        d = np.array(sorted(best), dtype=float)
        v = np.array([best[x][0] for x in d], dtype=float)
        rec: dict = {"date": day, "spot": closes.get(day)}
        for t in TENORS:
            cm = cm_interp(d, v, float(t))
            rec[t] = cm * 100.0 if cm is not None else math.nan
        recs.append(rec)
    if not recs:
        return pd.DataFrame()
    return pd.DataFrame(recs).set_index("date").sort_index()


def fetch_skew30(session, symbol: str, *, days: int = 60) -> dict[str, float]:
    """Banked 25d risk-reversal at the 30d horizon -> ``{date: skew_pts}``."""
    from sqlalchemy import text

    sql = text(
        "SELECT ts::date AS d, rr_25d FROM skew_snapshots "
        "WHERE symbol = :s AND horizon_dte = 30 AND rr_25d IS NOT NULL "
        "AND ts >= now() - (:n || ' days')::interval ORDER BY ts"
    )
    out = {}
    for r in session.execute(sql, {"s": symbol.upper(), "n": days}):
        out[str(r[0])] = float(r[1]) * 100.0
    return out


def fixed_strike_series(
    client, symbol: str, exp: str, strike: float, ref: dt.date, *, days: int = 40
) -> dict[str, float]:
    """IV history for ONE listed contract (the fixed-strike leg of panel 1).

    Same strike, same expiry, every day — so a move in this series is a genuine
    re-mark of that contract, not a delta bucket sliding as spot moves. Solved
    off the parity forward, so it is comparable to the vendor's mark.
    """
    frm = ref - dt.timedelta(days=days)
    closes = _underlying_closes(client, symbol, frm, ref)
    legs: dict[str, dict[str, float]] = {}
    for is_call in (True, False):
        try:
            bars = client.aggs(
                occ_ticker(symbol, dt.date.fromisoformat(exp), is_call, strike),
                multiplier=1, timespan="day", frm=frm, to=ref,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("vol_board.fs_failed", exp=exp, strike=strike, error=str(exc)[:100])
            continue
        if bars is None or len(bars) == 0:
            continue
        for b in bars.to_dict("records"):
            ts, px = b.get("ts"), b.get("c")
            if ts is None or px is None or not float(px) > 0:
                continue
            legs.setdefault(str(pd.Timestamp(ts).date()), {})["c" if is_call else "p"] = float(px)

    out: dict[str, float] = {}
    e = dt.date.fromisoformat(exp)
    for day, leg in sorted(legs.items()):
        if "c" not in leg or "p" not in leg or day not in closes:
            continue
        tdays = (e - dt.date.fromisoformat(day)).days
        if tdays <= 0:
            continue
        t = tdays / 365.0
        fwd = forward_from_parity(leg["c"], leg["p"], strike, t, _RATE)
        if fwd is None:
            continue
        ivs = [iv for iv in (
            implied_vol_fwd(leg["c"], fwd, strike, t, _RATE, is_call=True),
            implied_vol_fwd(leg["p"], fwd, strike, t, _RATE, is_call=False),
        ) if iv is not None and 0.01 < iv < 3.0]
        if ivs:
            out[day] = float(np.mean(ivs)) * 100.0
    return out


def focus_candidates(
    rows: list[dict], spot: float, ref: dt.date, target_dte: int = 30, n: int = 6
) -> list[tuple[str, float]]:
    """Candidate (expiry, strike) pairs for panel 1, nearest-the-money first.

    Returns several because the exact ATM strike is often barely traded — a
    listed contract with one print in 40 days yields a one-point 'history'. The
    caller walks these until one has enough daily bars to be a real series.
    """
    exps = {str(r["expiration_date"])[:10] for r in rows if dte_of(r["expiration_date"], ref) > 5}
    if not exps:
        raise ValueError("no usable expiries")
    exp = min(exps, key=lambda e: abs(dte_of(e, ref) - target_dte))
    ks = sorted({float(r["strike_price"]) for r in rows if str(r["expiration_date"])[:10] == exp},
                key=lambda k: abs(k - spot))
    return [(exp, k) for k in ks[:n]]


def otm_smile(rows: list[dict], spot: float, exp: str) -> list[tuple[float, float]]:
    """One IV per strike for ``exp``: puts below spot, calls above (the OTM wing).

    Taking every row would interleave the call and put at each strike and draw a
    sawtooth rather than a smile.
    """
    best: dict[float, float] = {}
    for r in rows:
        if str(r["expiration_date"])[:10] != exp or r.get("implied_volatility") is None:
            continue
        k = float(r["strike_price"])
        is_call = str(r["contract_type"]).lower().startswith("c")
        if (k >= spot) != is_call:
            continue
        best[k] = float(r["implied_volatility"]) * 100.0
    return sorted(best.items())
