"""Full single-ticker positioning / flow / technicals HTML report (the standard).

The canonical "give me the report for <TICKER>" deliverable. Reads our stored
NAS data (read-only via ``.env`` DATABASE_URL) and writes a self-contained
``reports/<SYM>_<date>.html`` (plotly embedded inline, so it renders with no
network). Layout, in order:

  * 12 colour-coded stat cards + a written "read"
  * Combined line view: price · rolling gamma flip · rolling call/put walls · 25Δ skew
  * Two detailed panels (shared, data-window-clipped x): price + SMA20/50;
    25Δ/10Δ risk-reversal (skew) line + ATM IV
  * Detail grid: gamma, volatility & skew, options flow, technicals, raw greek
    exposures, GEX term-structure diverging bars (call vs put · net)
  * Unusual activity: Vol/OI ratio + day-over-day positioning change (ΔOI vs the
    PRIOR session's volume -> conversion / opening-vs-churn; see oi_changes.py)
  * Unusual flow: top-5 TAS prints today

Descriptive regime/flow only (FlashAlpha rule 4) — not signals, not advice.

Run:
    python scripts/ticker_report.py NVDA
    python scripts/ticker_report.py TSLA --days 240
"""
from __future__ import annotations

import argparse
import html
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from sqlalchemy import create_engine, text

from trading_intel.config import get_settings

_REPO_ROOT = Path(__file__).resolve().parents[1]
_OUT = _REPO_ROOT / "reports"

GRN, RED, NEU, AMB, ORG = "#4ade80", "#f87171", "#cbd5e1", "#e3b341", "#fb923c"


def _num(x: object, dp: int = 2) -> str:
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return "—"
    return f"{float(x):,.{dp}f}"


def _money(x: object) -> str:
    return "—" if x is None or (isinstance(x, float) and np.isnan(x)) else f"${float(x):,.0f}"


def _pct(x: object, dp: int = 0) -> str:
    return "—" if x is None or (isinstance(x, float) and np.isnan(x)) else f"{float(x) * 100:.{dp}f}%"


def _gex_col(x: float | None) -> str:
    return GRN if (x or 0) > 0 else RED if (x or 0) < 0 else NEU


def _rsi_col(x: float) -> str:
    return RED if x >= 70 else GRN if x <= 30 else NEU


def _tilt_col(t: str | None) -> str:
    t = (t or "").lower()
    if any(k in t for k in ("call", "offens", "bull")):
        return GRN
    if any(k in t for k in ("put", "defens", "bear")):
        return RED
    return NEU


def _skew_col(label: str | None) -> str:
    label = (label or "").lower()
    return GRN if "call" in label else RED if ("put" in label or "fear" in label) else NEU


def _voi_col(r: float) -> str:
    return ORG if r > 2 else AMB if r > 1 else NEU


def _conv_col(x: float | None) -> str:
    if x is None or np.isnan(x):
        return NEU
    return GRN if x >= 0.6 else AMB if x >= 0.3 else NEU


def _posn_label(d_oi: float, d_iv: float | None) -> tuple[str, str]:
    """Opening/closing/churn read from ΔOI + ΔIV (mirrors oi_changes.classify)."""
    rising, falling = d_oi > 0, d_oi < 0
    iv_known = d_iv is not None and not (isinstance(d_iv, float) and np.isnan(d_iv))
    if iv_known and rising:
        return ("opening · demand-led" if d_iv > 0 else "opening · supply-led"), GRN
    if iv_known and falling:
        return ("closing/unwind" if d_iv < 0 else "closing · firmer IV"), RED
    if rising:
        return "opening interest", GRN
    if falling:
        return "closing/unwind", RED
    return "little change", NEU


def build(sym: str, *, days: int = 180) -> str:
    """Build the report for ``sym`` and return the written file path."""
    sym = sym.strip().upper()
    eng = create_engine(get_settings().DATABASE_URL)

    def rows(sql: str, **p: object) -> list[tuple]:
        with eng.connect() as cx:
            return [tuple(r) for r in cx.execute(text(sql), p)]

    def one(sql: str, **p: object) -> tuple | None:
        r = rows(sql, **p)
        return r[0] if r else None

    P = {"s": sym}

    ohlc = pd.DataFrame(
        rows("SELECT date,open,high,low,close,volume,rv20,rv60 FROM quotes_daily "
             "WHERE symbol=:s ORDER BY date DESC LIMIT :n", s=sym, n=days),
        columns=["date", "open", "high", "low", "close", "volume", "rv20", "rv60"],
    ).iloc[::-1].reset_index(drop=True)
    if ohlc.empty:
        raise SystemExit(f"no data for {sym}")
    ohlc["date"] = pd.to_datetime(ohlc["date"])
    gam = pd.DataFrame(
        rows("SELECT ts,spot,gex_total,dex_total,vex_total,chex_total,gex_flip,atm_iv "
             "FROM greeks_snapshots WHERE symbol=:s ORDER BY ts DESC LIMIT :n", s=sym, n=days),
        columns=["ts", "spot", "gex_total", "dex_total", "vex_total", "chex_total", "gex_flip", "atm_iv"],
    ).iloc[::-1].reset_index(drop=True)
    gam["ts"] = pd.to_datetime(gam["ts"]).dt.tz_localize(None)
    skew = pd.DataFrame(
        rows("SELECT ts,rr_25d,rr_10d FROM skew_snapshots WHERE symbol=:s AND horizon_dte=30 "
             "ORDER BY ts DESC LIMIT :n", s=sym, n=days),
        columns=["ts", "rr_25d", "rr_10d"],
    ).iloc[::-1].reset_index(drop=True)
    if not skew.empty:
        skew["ts"] = pd.to_datetime(skew["ts"])

    # wall history (call/put wall per EOD snapshot, <=60 DTE)
    wr = pd.DataFrame(
        rows("SELECT ts,cp,strike,SUM(gxoi) g FROM oi_chain_eod WHERE symbol=:s "
             "AND dte BETWEEN 0 AND 60 GROUP BY ts,cp,strike", s=sym),
        columns=["ts", "cp", "strike", "g"],
    )
    wr["ts"] = pd.to_datetime(wr["ts"])
    wlist = []
    for ts, grp in wr.groupby("ts"):
        cd, pdf = grp[grp.cp.str.startswith("C")], grp[grp.cp.str.startswith("P")]
        wlist.append((ts, cd.loc[cd.g.idxmax(), "strike"] if not cd.empty else None,
                      pdf.loc[pdf.g.idxmax(), "strike"] if not pdf.empty else None))
    wh = pd.DataFrame(wlist, columns=["ts", "call_wall", "put_wall"]).sort_values("ts").reset_index(drop=True)

    wts = one("SELECT max(ts) FROM oi_chain_eod WHERE symbol=:s", **P)
    wts = wts[0] if wts else None
    voi = rows("SELECT strike,cp,oi,volume,(volume::float/NULLIF(oi,0)) r FROM oi_chain_eod "
               "WHERE symbol=:s AND ts=:t AND dte BETWEEN 0 AND 60 AND volume>=500 AND oi>=50 "
               "ORDER BY r DESC NULLS LAST LIMIT 7", s=sym, t=wts) if wts else []
    termcp = rows("SELECT expiry,dte,cp,SUM(gxoi) g FROM oi_chain_eod WHERE symbol=:s AND ts=:t "
                  "GROUP BY expiry,dte,cp ORDER BY dte", s=sym, t=wts) if wts else []
    tas = rows("SELECT ts,cp,strike,side,size,notional FROM tas_prints WHERE root=:s "
               "ORDER BY notional DESC LIMIT 5", **P)

    # day-over-day positioning: diff 2 latest snapshots, conversion vs PRIOR-session volume
    ts2 = [r[0] for r in rows("SELECT DISTINCT ts FROM oi_chain_eod WHERE symbol=:s "
                              "ORDER BY ts DESC LIMIT 2", **P)]
    posn_rows: list[tuple] = []
    if len(ts2) == 2:
        cur_ts, prev_ts = ts2
        cf = pd.DataFrame(rows("SELECT strike,cp,oi,iv FROM oi_chain_eod WHERE symbol=:s AND ts=:t "
                               "AND dte BETWEEN 0 AND 60", s=sym, t=cur_ts),
                          columns=["strike", "cp", "oi_curr", "iv_curr"])
        pf = pd.DataFrame(rows("SELECT strike,cp,oi,iv,volume FROM oi_chain_eod WHERE symbol=:s AND ts=:t "
                               "AND dte BETWEEN 0 AND 60", s=sym, t=prev_ts),
                          columns=["strike", "cp", "oi_prev", "iv_prev", "volume_prev"])
        m = cf.merge(pf, on=["strike", "cp"], how="left")
        m["d_oi"] = m["oi_curr"] - m["oi_prev"].fillna(0)
        m["d_iv"] = m["iv_curr"] - m["iv_prev"]
        m["conv"] = m["d_oi"].abs() / m["volume_prev"].where(m["volume_prev"] > 0, np.nan)
        m = m.reindex(m["d_oi"].abs().sort_values(ascending=False).index).head(8)
        for r in m.itertuples():
            lbl, lcol = _posn_label(r.d_oi, r.d_iv if pd.notna(r.d_iv) else None)
            posn_rows.append((r.strike, r.cp, r.d_oi,
                              r.volume_prev if pd.notna(r.volume_prev) else None,
                              r.conv if pd.notna(r.conv) else None, lbl, lcol))

    roll = one("SELECT gex_total,n_expirations FROM gex_rolling WHERE symbol=:s ORDER BY ts DESC LIMIT 1", **P)
    vr = one("SELECT iv_atm,fcst_rv,vrp_pts,label FROM vol_richness WHERE symbol=:s ORDER BY ts DESC LIMIT 1", **P)
    sk = one("SELECT rr_25d,rr_10d,bf_25d,vix_beta_60d,rr_25d_abnormal,label FROM skew_snapshots "
             "WHERE symbol=:s ORDER BY ts DESC LIMIT 1", **P)
    fl = one("SELECT call_notional,put_notional,put_call_ratio,tilt,n_prints FROM flow_snapshots "
             "WHERE symbol=:s ORDER BY ts DESC LIMIT 1", **P)

    close = ohlc["close"].astype(float)
    ohlc["sma20"], ohlc["sma50"] = close.rolling(20).mean(), close.rolling(50).mean()
    d = close.diff()
    rs = (d.clip(lower=0).ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
          / (-d).clip(lower=0).ewm(alpha=1 / 14, adjust=False, min_periods=14).mean())
    ohlc["rsi"] = 100 - 100 / (1 + rs)

    pos = [s for s in [gam.ts.min() if not gam.empty else None,
                       wh.ts.min() if not wh.empty else None,
                       (skew.ts.min() if not skew.empty else None)] if s is not None]
    start = min(pos) if pos else ohlc.date.min()
    end = ohlc.date.max()
    v = ohlc[ohlc.date >= start].reset_index(drop=True)
    rng, days_shown = [start, end], (end - start).days

    spot = float(gam.spot.dropna().iloc[-1])
    flip = float(gam.gex_flip.dropna().iloc[-1])
    gexL = float(gam.gex_total.dropna().iloc[-1])
    ivL = float(gam.atm_iv.dropna().iloc[-1])
    cwL = wh.call_wall.dropna().iloc[-1] if not wh.call_wall.dropna().empty else None
    pwL = wh.put_wall.dropna().iloc[-1] if not wh.put_wall.dropna().empty else None
    closeL = float(close.iloc[-1])
    rsiL = float(ohlc.rsi.dropna().iloc[-1])
    chg5 = float(close.iloc[-1] / close.iloc[-6] - 1) * 100 if len(close) >= 6 else 0.0
    hvL = float(ohlc.rv20.dropna().iloc[-1]) if ohlc.rv20.notna().any() else None
    rv60L = float(ohlc.rv60.dropna().iloc[-1]) if ohlc.rv60.notna().any() else None
    regime = "LONG γ (dampening)" if spot >= flip else "SHORT γ (amplifying)"

    # ── combined line view: price + flip + walls + 25Δ skew (no IV/HV) ──
    f2 = make_subplots(specs=[[{"secondary_y": True}]])
    f2.add_trace(go.Scatter(x=v.date, y=v.close, name="Price", line=dict(color="#e6e8eb", width=1.8)), secondary_y=False)
    f2.add_trace(go.Scatter(x=gam.ts, y=gam.gex_flip, name="Gamma flip", line=dict(color="#9aa4b2", width=1.5, dash="dot")), secondary_y=False)
    f2.add_trace(go.Scatter(x=wh.ts, y=wh.call_wall, name="Call wall", mode="lines+markers", line=dict(color="#3fb950", width=1.4, shape="hv")), secondary_y=False)
    f2.add_trace(go.Scatter(x=wh.ts, y=wh.put_wall, name="Put wall", mode="lines+markers", line=dict(color="#f85149", width=1.4, shape="hv")), secondary_y=False)
    if not skew.empty:
        f2.add_trace(go.Scatter(x=skew.ts, y=skew.rr_25d, name="Skew 25Δ", mode="lines+markers", line=dict(color="#e3b341", width=1.3)), secondary_y=True)
    f2.update_yaxes(title_text="price / level ($)", secondary_y=False)
    f2.update_yaxes(title_text="25Δ skew", secondary_y=True)
    f2.update_xaxes(range=rng)
    f2.update_layout(template="plotly_dark", height=520, width=1150, legend=dict(orientation="h", y=1.08),
                     margin=dict(l=55, r=58, t=44, b=26),
                     title=f"{sym} combined — price · flip · walls · skew (last {days_shown}d)")
    combo = f2.to_html(full_html=False, include_plotlyjs=True)

    # ── detailed: price+SMA ; skew line + IV ──
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.07,
                        row_heights=[0.62, 0.38], specs=[[{}], [{"secondary_y": True}]],
                        subplot_titles=[f"{sym} — price + SMAs", "25Δ / 10Δ risk reversal (skew) + ATM IV"])
    fig.add_trace(go.Candlestick(x=v.date, open=v.open, high=v.high, low=v.low, close=v.close, name="OHLC"), row=1, col=1)
    fig.add_trace(go.Scatter(x=v.date, y=v.sma20, name="SMA20", line=dict(color="#58a6ff", width=1.1)), row=1, col=1)
    fig.add_trace(go.Scatter(x=v.date, y=v.sma50, name="SMA50", line=dict(color="#d29922", width=1.1)), row=1, col=1)
    fig.update_xaxes(rangeslider_visible=False, row=1, col=1)
    if not skew.empty:
        fig.add_trace(go.Scatter(x=skew.ts, y=skew.rr_25d, name="25Δ RR", mode="lines+markers", line=dict(color="#e3b341")), row=2, col=1, secondary_y=False)
        fig.add_trace(go.Scatter(x=skew.ts, y=skew.rr_10d, name="10Δ RR", mode="lines+markers", line=dict(color="#db6d28")), row=2, col=1, secondary_y=False)
    fig.add_trace(go.Scatter(x=gam.ts, y=gam.atm_iv, name="ATM IV", line=dict(color="#58a6ff", width=1.4)), row=2, col=1, secondary_y=True)
    fig.add_hline(y=0, line=dict(color="#888", width=.6), row=2, col=1, secondary_y=False)
    fig.update_yaxes(title_text="25Δ/10Δ RR", row=2, col=1, secondary_y=False)
    fig.update_yaxes(title_text="ATM IV", row=2, col=1, secondary_y=True)
    fig.update_xaxes(range=rng)
    fig.update_layout(template="plotly_dark", height=760, width=1150, showlegend=True,
                      legend=dict(orientation="h", y=1.03), margin=dict(l=55, r=55, t=50, b=25))
    chart = fig.to_html(full_html=False, include_plotlyjs=False)

    # ── read ──
    reads = [f"Spot {_num(spot)} <b>{'above' if spot >= flip else 'below'}</b> the {_num(flip)} gamma flip — {regime}."]
    if fl and fl[2] is not None and fl[2] < 0.7:
        reads.append(f"Flow <b>call-heavy</b> (P/C {_num(fl[2])}, ${fl[0] / 1e6:.0f}M calls vs ${fl[1] / 1e6:.0f}M puts).")
    if sk and sk[0] is not None:
        reads.append(f"25Δ skew {_num(sk[0], 3)} — “{html.escape(str(sk[5]))}”.")
    reads.append(f"Price {_num(closeL)}, RSI {_num(rsiL, 0)}, {'+' if chg5 >= 0 else ''}{_num(chg5, 1)}% 5d.")
    if vr and vr[2] is not None:
        reads.append(f"IV {_pct(vr[0])} vs fcst RV {_pct(vr[1])} → VRP {_num(vr[2], 2)}pts; HV20 {_pct(hvL)}.")
    read = "".join(f"<li>{r}</li>" for r in reads)

    # ── cards ──
    reg_c = GRN if regime.startswith("LONG") else RED
    tilt_c = _tilt_col(fl[3]) if fl else NEU
    sk_c = _skew_col(sk[5]) if sk else NEU
    gx_c, rsi_c = _gex_col(gexL), _rsi_col(rsiL)

    def card(b: str, s: str, accent: str = "#2a2f3a", numcol: str = "#e6e8eb") -> str:
        return f'<div class="c" style="border-left-color:{accent}"><b style="color:{numcol}">{b}</b><span>{s}</span></div>'

    cards = (card(_num(spot), "spot", "#58a6ff") + card(_num(closeL), "last close", "#58a6ff")
             + card(regime.split()[0] + " γ", "gamma regime", reg_c, reg_c)
             + card(_num(flip, 1), "gamma flip", "#9aa4b2") + card(_num(cwL, 0), "call wall", GRN, GRN)
             + card(_num(pwL, 0), "put wall", RED, RED) + card(_money(gexL), "net GEX", gx_c, gx_c)
             + card(_pct(ivL), "ATM IV", "#58a6ff") + card(_pct(hvL), "HV 20d", "#db6d28")
             + card(_num(rsiL, 0), "RSI-14", rsi_c, rsi_c) + card((fl[3] if fl else "—"), "flow tilt", tilt_c, tilt_c)
             + card(((sk[5] or "—") if sk else "—"), "skew read", sk_c, sk_c))

    def trow(k: str, vv: str) -> str:
        return f"<tr><th>{k}</th><td>{vv}</td></tr>"

    gamma_tbl = "".join([trow("Net GEX", f"<span style='color:{gx_c}'>{_num(gexL, 0)}</span>"),
                         trow("Gamma flip", _num(flip, 1)), trow("Regime", f"<span style='color:{reg_c}'>{regime}</span>"),
                         trow("Call wall", f"<span style='color:{GRN}'>{_num(cwL, 0)}</span>"),
                         trow("Put wall", f"<span style='color:{RED}'>{_num(pwL, 0)}</span>"),
                         trow("Rolling GEX", (_num(roll[0], 0) + f" / {roll[1]} exp") if roll else "—")])
    vrp = vr[2] if vr else None
    vrp_cell = f"<span style='color:{GRN if (vrp or 0) < 0 else RED}'>{_num(vrp, 2)} pts</span>" if vrp is not None else "—"
    rr_cell = f"<span style='color:{sk_c}'>{_num(sk[0], 3)} / {_num(sk[1], 3)}</span>" if sk else "—"
    vol_tbl = "".join([trow("ATM IV", _pct(ivL)), trow("HV 20d / 60d", f"{_pct(hvL)} / {_pct(rv60L)}"),
                       trow("Forecast RV", _pct(vr[1]) if vr else "—"), trow("VRP", vrp_cell),
                       trow("25Δ/10Δ RR", rr_cell), trow("Abnormal RR", _num(sk[4], 3) if sk else "—")])
    flow_tbl = ("".join([trow("Call notional", f"<span style='color:{GRN}'>{_money(fl[0])}</span>"),
                         trow("Put notional", f"<span style='color:{RED}'>{_money(fl[1])}</span>"),
                         trow("Put/Call", _num(fl[2])), trow("Tilt", f"<span style='color:{tilt_c}'>{html.escape(str(fl[3]))}</span>"),
                         trow("Prints", str(fl[4]))]) if fl else "<tr><td>no flow</td></tr>")
    tech_tbl = "".join([trow("Last close", _num(closeL)), trow("RSI-14", f"<span style='color:{rsi_c}'>{_num(rsiL, 0)}</span>"),
                        trow("SMA-20 / 50", f"{_num(ohlc.sma20.dropna().iloc[-1])} / {_num(ohlc.sma50.dropna().iloc[-1])}"),
                        trow("5-day chg", f"<span style='color:{GRN if chg5 >= 0 else RED}'>{_num(chg5, 1)}%</span>")])
    greeks_tbl = "".join([trow("DEX", _num(float(gam.dex_total.dropna().iloc[-1]), 0)),
                          trow("VEX", _num(float(gam.vex_total.dropna().iloc[-1]), 0)),
                          trow("CHEX", _num(float(gam.chex_total.dropna().iloc[-1]), 0))])

    # GEX term diverging bars
    texp: dict = {}
    for e, dt, cp, g in termcp:
        dd = texp.setdefault((e, dt), {})
        dd[cp[0].upper()] = dd.get(cp[0].upper(), 0) + (g or 0)
    trows_t = [(e, dt, dd.get("C", 0), dd.get("P", 0), dd.get("C", 0) - dd.get("P", 0))
               for (e, dt), dd in sorted(texp.items(), key=lambda x: x[0][1])[:10]]
    ms = max([max(c2, p2) for _, _, c2, p2, _ in trows_t] or [1]) or 1
    trbars = "".join(
        f'<div class="termrow"><span class="lbl">{e} · {dt}d</span>'
        f'<div class="tbar"><div class="l"><div class="p" style="width:{(put / ms * 100):.0f}%"></div></div>'
        f'<div class="r"><div class="cc" style="width:{(call / ms * 100):.0f}%"></div></div></div>'
        f'<span class="termnet" style="color:{GRN if net > 0 else RED if net < 0 else NEU}">{_num(net, 0)}</span></div>'
        for e, dt, call, put, net in trows_t)
    term_html = f'<div class="termhdr"><span>← put GEX</span><span style="margin:0 auto">call GEX →</span><span>net</span></div>{trbars}'

    voi_rows = "".join(
        f"<tr><td>{_num(st, 0)}</td><td style='color:{GRN if cp.startswith('C') else RED}'>{cp}</td>"
        f"<td style='text-align:right'>{_num(vol, 0)}</td><td style='text-align:right'>{_num(oi, 0)}</td>"
        f"<td style='text-align:right;color:{_voi_col(r)};font-weight:600'>{_num(r, 2)}×</td></tr>"
        for st, cp, oi, vol, r in voi)
    posn_html = "".join(
        f"<tr><td>{_num(st, 0)}</td><td style='color:{GRN if cp.startswith('C') else RED}'>{cp}</td>"
        f"<td style='text-align:right;color:{GRN if (doi or 0) > 0 else RED}'>{('+' if (doi or 0) > 0 else '')}{_num(doi, 0)}</td>"
        f"<td style='text-align:right'>{_num(vp, 0)}</td>"
        f"<td style='text-align:right;color:{_conv_col(cv)};font-weight:600'>{(_num(cv * 100, 0) + '%') if cv is not None else '—'}</td>"
        f"<td style='color:{lcol}'>{lbl}</td></tr>"
        for st, cp, doi, vp, cv, lbl, lcol in posn_rows)
    if tas:
        tas_rows = "".join(
            f"<tr><td>{ts.strftime('%m-%d %H:%M')}</td><td style='color:{GRN if cp == 'C' else RED}'>{cp}{_num(st, 0)}</td>"
            f"<td style='color:{GRN if side == 'buy' else RED}'>{side}</td><td style='text-align:right'>{_num(sz, 0)}</td>"
            f"<td style='text-align:right'>{_money(no)}</td></tr>"
            for ts, cp, st, side, sz, no in tas)
        tas_html = f"<table class='grid'><tr><th>time</th><th>contract</th><th>side</th><th>size</th><th>premium</th></tr>{tas_rows}</table>"
    else:
        tas_html = "<p class='muted'>No prints captured yet today.</p>"

    css = (
        "body{font-family:-apple-system,Segoe UI,Roboto,Arial,sans-serif;margin:0;background:#0f1115;color:#e6e8eb}"
        ".wrap{max-width:1180px;margin:0 auto;padding:22px}h1{font-size:22px;margin:0 0 2px}.sub{color:#9aa4b2;font-size:12.5px;margin:0 0 14px}"
        ".cards{display:flex;flex-wrap:wrap;gap:9px;margin-bottom:16px}.c{background:#171a21;border:1px solid #232833;border-left:3px solid #2a2f3a;border-radius:9px;padding:8px 12px;min-width:80px}.c b{display:block;font-size:15px}.c span{color:#9aa4b2;font-size:10.5px}"
        ".read{background:#141b16;border:1px solid #1f3326;border-radius:10px;padding:12px 16px;margin-bottom:16px}.read h2{margin:0 0 6px;font-size:14px;color:#8fd3a6}.read ul{margin:0;padding-left:17px;line-height:1.55;font-size:13px}"
        "h2.sec{margin:20px 0 8px;font-size:14px;border-bottom:1px solid #232833;padding-bottom:5px}"
        ".grid3{display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px}.card{background:#171a21;border:1px solid #232833;border-radius:10px;padding:12px}.card h3{margin:0 0 6px;font-size:13px;color:#cbd5e1}"
        "table{width:100%;border-collapse:collapse;font-size:12px}.card th{text-align:left;color:#8b94a3;font-weight:500;padding:2px 0}.card td{text-align:right;padding:2px 0}"
        "table.grid th{text-align:left;color:#8b94a3;border-bottom:1px solid #232833;padding:5px 7px}table.grid td{padding:4px 7px;border-bottom:1px solid #181b22}"
        ".tw{display:grid;grid-template-columns:1fr 1fr;gap:12px}.tw h3{font-size:13px;color:#cbd5e1;margin:0 0 6px}.muted{color:#6b7280;font-size:12px}.note{color:#6b7280;font-size:10.5px;margin:3px 0 0}"
        ".disc{margin-top:18px;color:#6b7280;font-size:11px;border-top:1px solid #232833;padding-top:10px}"
        ".termhdr{display:flex;font-size:9.5px;color:#6b7280;margin-bottom:5px}.termhdr span:last-child{width:60px;text-align:right}"
        ".termrow{display:flex;align-items:center;gap:7px;font-size:11px;margin:3px 0}.termrow .lbl{width:96px;color:#9aa4b2;white-space:nowrap}"
        ".tbar{flex:1;display:flex;height:12px;background:#0f1115;border:1px solid #232833;border-radius:3px;overflow:hidden}.tbar .l{flex:1;display:flex;justify-content:flex-end}.tbar .r{flex:1}.tbar .p{background:#f85149;height:100%}.tbar .cc{background:#3fb950;height:100%}.termnet{width:60px;text-align:right}"
    )
    page = f"""<!doctype html><html><head><meta charset="utf-8"><title>{sym} report</title><style>{css}</style></head><body><div class="wrap">
<h1>{sym} — full positioning, flow &amp; technicals report</h1><p class="sub">trading-intel (NAS) · {datetime.now():%Y-%m-%d %H:%M} · window last {days_shown}d</p>
<div class="cards">{cards}</div><div class="read"><h2>The read</h2><ul>{read}</ul></div>
<h2 class="sec">Combined line view (price · flip · walls · skew)</h2>{combo}
<h2 class="sec">Price + SMAs &amp; skew/IV</h2>{chart}
<h2 class="sec">Detail</h2><div class="grid3">
<div class="card"><h3>Gamma / positioning</h3><table>{gamma_tbl}</table></div><div class="card"><h3>Volatility &amp; skew</h3><table>{vol_tbl}</table></div><div class="card"><h3>Options flow (latest)</h3><table>{flow_tbl}</table></div>
<div class="card"><h3>Technicals</h3><table>{tech_tbl}</table></div><div class="card"><h3>Greek exposures (raw)</h3><table>{greeks_tbl}</table></div><div class="card"><h3>GEX term — call vs put · net</h3>{term_html}</div></div>
<h2 class="sec">Unusual activity &amp; positioning change</h2><div class="tw">
<div><h3>Vol / OI ratio — today's volume vs standing OI (&gt;1× = fresh)</h3><table class="grid"><tr><th>strike</th><th>C/P</th><th>vol</th><th>OI</th><th>vol/OI</th></tr>{voi_rows or '<tr><td>none</td></tr>'}</table></div>
<div><h3>Positioning change — last completed session</h3><table class="grid"><tr><th>strike</th><th>C/P</th><th>ΔOI</th><th>vol</th><th>conv</th><th>read</th></tr>{posn_html or '<tr><td>need 2 days</td></tr>'}</table>
<p class="note">ΔOI settled this morning reflects the <b>prior</b> session; conv = |ΔOI| ÷ that session's volume (high = real positioning, low = churn).</p></div></div>
<h2 class="sec">Unusual flow — time &amp; sales (top 5 by premium, today)</h2>{tas_html}
<p class="disc"><b>Not investment advice.</b> OI settles T+1 — positioning reads are timed to the session that produced them. GEX term “net” = call − put gamma-OI. Descriptive (FlashAlpha rule 4); view clipped to the positioning-data window.</p>
</div></body></html>"""

    _OUT.mkdir(parents=True, exist_ok=True)
    path = _OUT / f"{sym}_{date.today().isoformat()}.html"
    path.write_text(page, encoding="utf-8")
    return str(path)


def main() -> None:
    p = argparse.ArgumentParser(description="Full single-ticker HTML report from stored NAS data.")
    p.add_argument("symbol", help="ticker, e.g. NVDA")
    p.add_argument("--days", type=int, default=180, help="history depth")
    args = p.parse_args()
    print("wrote", build(args.symbol, days=args.days))


if __name__ == "__main__":
    main()
