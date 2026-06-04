"""Full single-ticker positioning / flow / technicals HTML report.

The canonical "give me the report for <TICKER>" deliverable. Reads our stored
NAS data (read-only via ``.env`` DATABASE_URL) and writes a self-contained
``reports/<SYM>_<date>.html`` with:

  * stat cards (spot, close, gamma regime, flip, call/put wall, net GEX, IV, HV,
    RSI, flow tilt, skew read) + a written "read"
  * a COMBINED line panel: price · rolling gamma flip · rolling call/put walls
    (left $ axis) and IV · HV(20d) · 25d skew (right axis)
  * a 5-panel chart: candles + SMA20/50 + rolling flip + call/put wall history /
    volume / net-GEX line / 25d+10d risk-reversal skew / RSI-14
  * detail panels: gamma, volatility & skew, flow, technicals, raw greek
    exposures, GEX term structure
  * call/put wall day-over-day table + largest OI changes table

Descriptive regime/flow only (FlashAlpha rule 4) — not signals, not advice.
We refine this layout over time; keep it as the single source for the report.

Run:
    python scripts/ticker_report.py NVDA
    python scripts/ticker_report.py TSLA --days 240
"""
from __future__ import annotations

import argparse
import html
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
from plotly.subplots import make_subplots
from sqlalchemy import create_engine, text

import plotly.graph_objects as go  # noqa: E402  (kept with the other plotly import group)

from trading_intel.config import get_settings

_REPO_ROOT = Path(__file__).resolve().parents[1]
_OUT = _REPO_ROOT / "reports"


def _num(x: object, dp: int = 2) -> str:
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return "—"
    return f"{float(x):,.{dp}f}"


def _money(x: object) -> str:
    return "—" if x is None or (isinstance(x, float) and np.isnan(x)) else f"${float(x):,.0f}"


def _pct(x: object, dp: int = 0) -> str:
    return "—" if x is None or (isinstance(x, float) and np.isnan(x)) else f"{float(x) * 100:.{dp}f}%"


def _wall_history(eng, sym: str) -> pd.DataFrame:  # noqa: ANN001
    wr = pd.read_sql(
        text(
            "SELECT ts,cp,strike,SUM(gxoi) g FROM oi_chain_eod "
            "WHERE symbol=:s AND dte BETWEEN 0 AND 60 GROUP BY ts,cp,strike"
        ),
        eng, params={"s": sym},
    )
    if wr.empty:
        return pd.DataFrame(columns=["ts", "call_wall", "put_wall"])
    wr["ts"] = pd.to_datetime(wr["ts"])
    rows = []
    for ts, grp in wr.groupby("ts"):
        cd = grp[grp.cp.str.startswith("C")]
        pd_ = grp[grp.cp.str.startswith("P")]
        rows.append((
            ts,
            cd.loc[cd.g.idxmax(), "strike"] if not cd.empty else None,
            pd_.loc[pd_.g.idxmax(), "strike"] if not pd_.empty else None,
        ))
    return pd.DataFrame(rows, columns=["ts", "call_wall", "put_wall"]).sort_values("ts").reset_index(drop=True)


def build(sym: str, *, days: int = 180) -> str:
    """Build the report for ``sym`` and return the written file path."""
    sym = sym.strip().upper()
    eng = create_engine(get_settings().DATABASE_URL)

    ohlc = pd.read_sql(
        text("SELECT date,open,high,low,close,volume,rv20,rv60 FROM quotes_daily "
             "WHERE symbol=:s ORDER BY date DESC LIMIT :n"),
        eng, params={"s": sym, "n": days},
    ).iloc[::-1].reset_index(drop=True)
    if ohlc.empty:
        raise SystemExit(f"no data for {sym}")
    ohlc["date"] = pd.to_datetime(ohlc["date"])
    gam = pd.read_sql(
        text("SELECT ts,spot,gex_total,dex_total,vex_total,chex_total,gex_flip,atm_iv "
             "FROM greeks_snapshots WHERE symbol=:s ORDER BY ts DESC LIMIT :n"),
        eng, params={"s": sym, "n": days},
    ).iloc[::-1].reset_index(drop=True)
    gam["ts"] = pd.to_datetime(gam["ts"]).dt.tz_localize(None)
    skew = pd.read_sql(
        text("SELECT ts,rr_25d,rr_10d FROM skew_snapshots WHERE symbol=:s AND horizon_dte=30 "
             "ORDER BY ts DESC LIMIT :n"),
        eng, params={"s": sym, "n": days},
    ).iloc[::-1].reset_index(drop=True)
    if not skew.empty:
        skew["ts"] = pd.to_datetime(skew["ts"])
    wh = _wall_history(eng, sym)

    with eng.connect() as cx:
        wts = cx.execute(text("SELECT max(ts) FROM oi_chain_eod WHERE symbol=:s"), {"s": sym}).scalar()
        oichg = cx.execute(text(
            "SELECT strike,cp,oi,oi_change,volume FROM oi_chain_eod WHERE symbol=:s AND ts=:t "
            "AND dte BETWEEN 0 AND 60 AND oi_change IS NOT NULL ORDER BY abs(oi_change) DESC LIMIT 8"
        ), {"s": sym, "t": wts}).fetchall() if wts else []
        roll = cx.execute(text("SELECT gex_total,n_expirations FROM gex_rolling WHERE symbol=:s "
                               "ORDER BY ts DESC LIMIT 1"), {"s": sym}).fetchone()
        term = cx.execute(text("SELECT expiration,dte,gex FROM gex_term WHERE symbol=:s AND "
                               "ts=(SELECT max(ts) FROM gex_term WHERE symbol=:s) ORDER BY dte LIMIT 8"),
                          {"s": sym}).fetchall()
        vr = cx.execute(text("SELECT iv_atm,fcst_rv,vrp_pts,label FROM vol_richness WHERE symbol=:s "
                             "ORDER BY ts DESC LIMIT 1"), {"s": sym}).fetchone()
        sk = cx.execute(text("SELECT rr_25d,rr_10d,bf_25d,vix_beta_60d,rr_25d_abnormal,label "
                             "FROM skew_snapshots WHERE symbol=:s ORDER BY ts DESC LIMIT 1"),
                        {"s": sym}).fetchone()
        fl = cx.execute(text("SELECT call_notional,put_notional,put_call_ratio,tilt,n_prints "
                             "FROM flow_snapshots WHERE symbol=:s ORDER BY ts DESC LIMIT 1"),
                        {"s": sym}).fetchone()

    close = ohlc["close"].astype(float)
    ohlc["sma20"] = close.rolling(20).mean()
    ohlc["sma50"] = close.rolling(50).mean()
    d = close.diff()
    rs = (d.clip(lower=0).ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
          / (-d).clip(lower=0).ewm(alpha=1 / 14, adjust=False, min_periods=14).mean())
    ohlc["rsi"] = 100 - 100 / (1 + rs)

    spot = float(gam.spot.dropna().iloc[-1]); flip = float(gam.gex_flip.dropna().iloc[-1])
    gexL = float(gam.gex_total.dropna().iloc[-1]); ivL = float(gam.atm_iv.dropna().iloc[-1])
    cwL = wh.call_wall.dropna().iloc[-1] if not wh.call_wall.dropna().empty else None
    pwL = wh.put_wall.dropna().iloc[-1] if not wh.put_wall.dropna().empty else None
    closeL = float(close.iloc[-1]); rsiL = float(ohlc.rsi.dropna().iloc[-1])
    chg5 = float(close.iloc[-1] / close.iloc[-6] - 1) * 100 if len(close) >= 6 else 0.0
    hvL = float(ohlc.rv20.dropna().iloc[-1]) if ohlc.rv20.notna().any() else None
    regime = "LONG γ (dampening)" if spot >= flip else "SHORT γ (amplifying)"

    # Narrow the VIEW to the window where we actually have positioning data (gamma/
    # walls/skew). Indicators above are computed on full history so SMA50/RSI stay
    # valid; ``v`` is just the clipped slice we plot. Auto-caps near the 30d prune.
    pos = [s for s in [gam.ts.min() if not gam.empty else None,
                       wh.ts.min() if not wh.empty else None,
                       (skew.ts.min() if not skew.empty else None)] if s is not None]
    start = min(pos) if pos else ohlc.date.min()
    end = ohlc.date.max()
    v = ohlc[ohlc.date >= start].reset_index(drop=True)
    rng = [start, end]
    days_shown = (end - start).days

    # combined line panel
    f2 = make_subplots(specs=[[{"secondary_y": True}]])
    f2.add_trace(go.Scatter(x=v.date, y=v.close, name="Price", line=dict(color="#e6e8eb", width=1.8)), secondary_y=False)
    f2.add_trace(go.Scatter(x=gam.ts, y=gam.gex_flip, name="Gamma flip", line=dict(color="#9aa4b2", width=1.5, dash="dot")), secondary_y=False)
    f2.add_trace(go.Scatter(x=wh.ts, y=wh.call_wall, name="Call wall", mode="lines+markers", line=dict(color="#3fb950", width=1.4, shape="hv")), secondary_y=False)
    f2.add_trace(go.Scatter(x=wh.ts, y=wh.put_wall, name="Put wall", mode="lines+markers", line=dict(color="#f85149", width=1.4, shape="hv")), secondary_y=False)
    f2.add_trace(go.Scatter(x=gam.ts, y=gam.atm_iv, name="IV (ATM)", line=dict(color="#58a6ff", width=1.3)), secondary_y=True)
    f2.add_trace(go.Scatter(x=v.date, y=v.rv20, name="HV (20d)", line=dict(color="#db6d28", width=1.3)), secondary_y=True)
    if not skew.empty:
        f2.add_trace(go.Scatter(x=skew.ts, y=skew.rr_25d, name="Skew 25Δ", mode="lines+markers", line=dict(color="#e3b341", width=1.3)), secondary_y=True)
    f2.update_yaxes(title_text="price / level ($)", secondary_y=False)
    f2.update_yaxes(title_text="IV · HV · skew", secondary_y=True)
    f2.update_xaxes(range=rng)
    f2.update_layout(template="plotly_dark", height=540, width=1150, legend=dict(orientation="h", y=1.07),
                     margin=dict(l=55, r=60, t=46, b=28),
                     title=f"{sym} combined — price · flip · walls · IV · HV · skew (last {days_shown}d)")
    # include_plotlyjs=True embeds the library inline so the file renders with no
    # network (the in-app preview can't fetch a CDN). chart below reuses it (=False).
    combo = f2.to_html(full_html=False, include_plotlyjs=True)

    # detailed panels — order: price, volume, RSI, [Net GEX vs price & walls], skew.
    # shared_xaxes=True aligns every panel to the same time range.
    titles = [f"{sym} — price · flip · call/put wall history · SMAs", "Volume", "RSI-14",
              "Net GEX (rolling) vs price &amp; call/put walls", "25Δ / 10Δ risk reversal (skew)"]
    fig = make_subplots(rows=5, cols=1, shared_xaxes=True, vertical_spacing=0.03,
                        row_heights=[0.40, 0.10, 0.12, 0.23, 0.15],
                        specs=[[{}], [{}], [{}], [{"secondary_y": True}], [{}]],
                        subplot_titles=titles)
    fig.add_trace(go.Candlestick(x=v.date, open=v.open, high=v.high, low=v.low, close=v.close, name="OHLC"), row=1, col=1)
    fig.add_trace(go.Scatter(x=v.date, y=v.sma20, name="SMA20", line=dict(color="#58a6ff", width=1)), row=1, col=1)
    fig.add_trace(go.Scatter(x=v.date, y=v.sma50, name="SMA50", line=dict(color="#d29922", width=1)), row=1, col=1)
    fig.add_trace(go.Scatter(x=gam.ts, y=gam.gex_flip, name="Gamma flip", line=dict(color="#9aa4b2", width=1.6, dash="dot")), row=1, col=1)
    fig.add_trace(go.Scatter(x=wh.ts, y=wh.call_wall, name="Call wall", mode="lines+markers", line=dict(color="#3fb950", width=1.4, shape="hv")), row=1, col=1)
    fig.add_trace(go.Scatter(x=wh.ts, y=wh.put_wall, name="Put wall", mode="lines+markers", line=dict(color="#f85149", width=1.4, shape="hv")), row=1, col=1)
    fig.update_xaxes(rangeslider_visible=False, row=1, col=1)
    upc = v.close.astype(float) >= v.open.astype(float)
    fig.add_trace(go.Bar(x=v.date, y=v.volume, name="Vol", marker_color=["rgba(63,185,80,.6)" if u else "rgba(248,81,73,.6)" for u in upc]), row=2, col=1)
    fig.add_trace(go.Scatter(x=v.date, y=v.rsi, name="RSI", line=dict(color="#56d364")), row=3, col=1)
    fig.add_hline(y=70, line=dict(color="#f85149", width=.5, dash="dot"), row=3, col=1)
    fig.add_hline(y=30, line=dict(color="#3fb950", width=.5, dash="dot"), row=3, col=1)
    # row 4: Net GEX (rolling) on the right axis vs price + walls on the left $ axis
    fig.add_trace(go.Scatter(x=v.date, y=v.close, name="Price (line)", line=dict(color="#e6e8eb", width=1.3)), row=4, col=1, secondary_y=False)
    fig.add_trace(go.Scatter(x=wh.ts, y=wh.call_wall, name="Call wall", mode="lines+markers", line=dict(color="#3fb950", width=1.2, shape="hv"), showlegend=False), row=4, col=1, secondary_y=False)
    fig.add_trace(go.Scatter(x=wh.ts, y=wh.put_wall, name="Put wall", mode="lines+markers", line=dict(color="#f85149", width=1.2, shape="hv"), showlegend=False), row=4, col=1, secondary_y=False)
    fig.add_trace(go.Scatter(x=gam.ts, y=gam.gex_total, name="Net GEX (rolling)", line=dict(color="#bc8cff", width=1.6)), row=4, col=1, secondary_y=True)
    fig.update_yaxes(title_text="price ($)", row=4, col=1, secondary_y=False)
    fig.update_yaxes(title_text="net GEX", row=4, col=1, secondary_y=True)
    if not skew.empty:
        fig.add_trace(go.Scatter(x=skew.ts, y=skew.rr_25d, name="25Δ RR", mode="lines+markers", line=dict(color="#e3b341")), row=5, col=1)
        fig.add_trace(go.Scatter(x=skew.ts, y=skew.rr_10d, name="10Δ RR", mode="lines+markers", line=dict(color="#db6d28")), row=5, col=1)
    fig.add_hline(y=0, line=dict(color="#888", width=.6), row=5, col=1)
    fig.update_xaxes(range=rng)
    fig.update_layout(template="plotly_dark", height=1650, width=1150, showlegend=True, legend=dict(orientation="h", y=1.02), margin=dict(l=55, r=55, t=55, b=25))
    chart = fig.to_html(full_html=False, include_plotlyjs=False)

    reads = [f"Spot {_num(spot)} is <b>{'above' if spot >= flip else 'below'}</b> the {_num(flip)} gamma flip — {regime}."]
    if fl and fl[2] is not None and fl[2] < 0.7:
        reads.append(f"Flow <b>call-heavy</b> (P/C {_num(fl[2])}, ${fl[0] / 1e6:.0f}M calls vs ${fl[1] / 1e6:.0f}M puts) — “{html.escape(str(fl[3]))}”.")
    if sk and sk[0] is not None:
        reads.append(f"25Δ skew {_num(sk[0], 3)} — “{html.escape(str(sk[5]))}”.")
    reads.append(f"Price {_num(closeL)}, RSI {_num(rsiL, 0)}, {'+' if chg5 >= 0 else ''}{_num(chg5, 1)}% 5d.")
    if vr and vr[2] is not None:
        reads.append(f"IV {_pct(vr[0])} vs forecast RV {_pct(vr[1])} → VRP {_num(vr[2], 2)} pts; HV(20d) {_pct(hvL)}.")
    read = "".join(f"<li>{r}</li>" for r in reads)

    def card(b: str, s: str, col: str = "#e6e8eb") -> str:
        return f'<div class="c"><b style="color:{col}">{b}</b><span>{s}</span></div>'

    cards = (card(_num(spot), "spot") + card(_num(closeL), "last close")
             + card(regime.split()[0] + " γ", "gamma regime", "#4ade80" if regime.startswith("LONG") else "#f87171")
             + card(_num(flip, 1), "gamma flip") + card(_num(cwL, 0), "call wall", "#3fb950")
             + card(_num(pwL, 0), "put wall", "#f87171") + card(_money(gexL), "net GEX")
             + card(_pct(ivL), "ATM IV") + card(_pct(hvL), "HV 20d") + card(_num(rsiL, 0), "RSI-14")
             + card(fl[3] if fl else "—", "flow tilt") + card((sk[5] or "—") if sk else "—", "skew read"))

    def trow(k: str, v: str) -> str:
        return f"<tr><th>{k}</th><td>{v}</td></tr>"

    gamma_tbl = "".join([trow("Net GEX", _num(gexL, 0)), trow("Gamma flip", _num(flip, 1)), trow("Regime", regime),
                         trow("Flip dist", _num((spot - flip) / flip * 100, 1) + "%"), trow("Call wall", _num(cwL, 0)),
                         trow("Put wall", _num(pwL, 0)), trow("Rolling GEX", (_num(roll[0], 0) + f" / {roll[1]} exp") if roll else "—")])
    rv60L = float(ohlc.rv60.dropna().iloc[-1]) if ohlc.rv60.notna().any() else None
    vol_tbl = "".join([trow("ATM IV", _pct(ivL)), trow("HV 20d / 60d", f"{_pct(hvL)} / {_pct(rv60L)}"),
                       trow("Forecast RV", _pct(vr[1]) if vr else "—"), trow("VRP", _num(vr[2], 2) + " pts" if (vr and vr[2] is not None) else "—"),
                       trow("Richness", html.escape(str(vr[3])) if (vr and vr[3]) else "—"),
                       trow("25Δ/10Δ RR", f"{_num(sk[0], 3)} / {_num(sk[1], 3)}" if sk else "—"),
                       trow("Abnormal RR", _num(sk[4], 3) if sk else "—")])
    flow_tbl = "".join([trow("Call notional", _money(fl[0])), trow("Put notional", _money(fl[1])),
                        trow("Put/Call", _num(fl[2])), trow("Tilt", html.escape(str(fl[3]))), trow("Prints", str(fl[4]))]) if fl else "<tr><td>no flow</td></tr>"
    tech_tbl = "".join([trow("Last close", _num(closeL)), trow("RSI-14", _num(rsiL, 0)),
                        trow("SMA-20", _num(ohlc.sma20.dropna().iloc[-1])), trow("SMA-50", _num(ohlc.sma50.dropna().iloc[-1])),
                        trow("5-day chg", _num(chg5, 1) + "%")])
    greeks_tbl = "".join([trow("DEX", _num(float(gam.dex_total.dropna().iloc[-1]), 0)),
                          trow("VEX", _num(float(gam.vex_total.dropna().iloc[-1]), 0)),
                          trow("CHEX", _num(float(gam.chex_total.dropna().iloc[-1]), 0))])
    term_tbl = "".join(f"<tr><td>{e}</td><td>{dt}</td><td style='text-align:right'>{_num(gx, 0)}</td></tr>" for e, dt, gx in term)
    wallhist = "".join(f"<tr><td>{ts.strftime('%m-%d')}</td><td style='text-align:right'>{_num(cw, 0)}</td><td style='text-align:right'>{_num(pw, 0)}</td></tr>" for ts, cw, pw in wh.itertuples(index=False))
    oich = "".join(f"<tr><td>{_num(s, 0)}</td><td>{cp}</td><td style='text-align:right'>{_num(oi, 0)}</td><td style='text-align:right;color:{'#4ade80' if (chg or 0) > 0 else '#f87171'}'>{('+' if (chg or 0) > 0 else '')}{_num(chg, 0)}</td><td style='text-align:right'>{_num(vol, 0)}</td></tr>" for s, cp, oi, chg, vol in oichg)

    css = ("body{font-family:-apple-system,Segoe UI,Roboto,Arial,sans-serif;margin:0;background:#0f1115;color:#e6e8eb}"
           ".wrap{max-width:1180px;margin:0 auto;padding:22px}h1{font-size:22px;margin:0 0 2px}.sub{color:#9aa4b2;font-size:12.5px;margin:0 0 14px}"
           ".cards{display:flex;flex-wrap:wrap;gap:9px;margin-bottom:16px}.c{background:#171a21;border:1px solid #232833;border-radius:9px;padding:8px 12px;min-width:80px}.c b{display:block;font-size:15px}.c span{color:#9aa4b2;font-size:10.5px}"
           ".read{background:#141b16;border:1px solid #1f3326;border-radius:10px;padding:12px 16px;margin-bottom:16px}.read h2{margin:0 0 6px;font-size:14px;color:#8fd3a6}.read ul{margin:0;padding-left:17px;line-height:1.55;font-size:13px}"
           "h2.sec{margin:20px 0 8px;font-size:14px;border-bottom:1px solid #232833;padding-bottom:5px}"
           ".grid3{display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px}.card{background:#171a21;border:1px solid #232833;border-radius:10px;padding:12px}.card h3{margin:0 0 6px;font-size:13px;color:#cbd5e1}"
           "table{width:100%;border-collapse:collapse;font-size:12px}.card th{text-align:left;color:#8b94a3;font-weight:500;padding:2px 0}.card td{text-align:right;padding:2px 0}"
           "table.grid th{text-align:left;color:#8b94a3;border-bottom:1px solid #232833;padding:5px 7px}table.grid td{padding:4px 7px;border-bottom:1px solid #181b22}"
           ".tw{display:grid;grid-template-columns:1fr 1fr;gap:12px}.disc{margin-top:18px;color:#6b7280;font-size:11px;border-top:1px solid #232833;padding-top:10px}")
    page = f"""<!doctype html><html><head><meta charset="utf-8"><title>{sym} report</title><style>{css}</style></head><body><div class="wrap">
<h1>{sym} — full positioning, flow &amp; technicals report</h1><p class="sub">trading-intel (NAS) · {datetime.now():%Y-%m-%d %H:%M}</p>
<div class="cards">{cards}</div>
<div class="read"><h2>The read</h2><ul>{read}</ul></div>
<h2 class="sec">Combined line view (price · flip · walls · IV · HV · skew)</h2>{combo}
<h2 class="sec">Detailed panels</h2>{chart}
<h2 class="sec">Detail</h2><div class="grid3">
<div class="card"><h3>Gamma / positioning</h3><table>{gamma_tbl}</table></div>
<div class="card"><h3>Volatility &amp; skew</h3><table>{vol_tbl}</table></div>
<div class="card"><h3>Options flow (latest)</h3><table>{flow_tbl}</table></div>
<div class="card"><h3>Technicals</h3><table>{tech_tbl}</table></div>
<div class="card"><h3>Greek exposures (raw)</h3><table>{greeks_tbl}</table></div>
<div class="card"><h3>GEX term structure</h3><table class="grid"><tr><th>expiry</th><th>dte</th><th>gex</th></tr>{term_tbl or '<tr><td>—</td></tr>'}</table></div>
</div>
<h2 class="sec">Call / put wall — day over day</h2><div class="tw">
<table class="grid"><tr><th>date</th><th>call wall</th><th>put wall</th></tr>{wallhist}</table>
<div><h3 style="font-size:13px;color:#cbd5e1;margin:0 0 6px">Largest OI changes (≤60 DTE)</h3>
<table class="grid"><tr><th>strike</th><th>C/P</th><th>OI</th><th>ΔOI</th><th>vol</th></tr>{oich or '<tr><td>none</td></tr>'}</table></div>
</div>
<p class="disc"><b>Not investment advice.</b> Descriptive regime/flow descriptors (FlashAlpha rule 4).</p>
</div></body></html>"""

    _OUT.mkdir(parents=True, exist_ok=True)
    path = _OUT / f"{sym}_{datetime.now():%Y-%m-%d}.html"
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
