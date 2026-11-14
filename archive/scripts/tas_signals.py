"""TAS flow x our GEX -> HTML trade-idea report.

Reads a daily option-tape capture (``data/tas/YYYY-MM-DD.csv`` from
``tas_capture.py``), ranks the unusual flow, then joins each name against OUR
stored gamma data on the NAS (net GEX, gamma flip, regime, call/put walls, ATM
IV) and writes a self-contained ``data/tas/signals_YYYY-MM-DD.html`` that
surfaces *confluence ideas* — where aggressive directional flow lines up with
the dealer-gamma backdrop.

This is a DESCRIPTIVE research report — a ranked watchlist of confluences for you
to investigate, NOT auto-generated trade signals (FlashAlpha rule 4: GEX is a
regime descriptor, not a signal; this script never writes to the signals table).
Not investment advice.

Run (during/after a capture; reads our DB read-only via .env DATABASE_URL):
    python scripts/tas_signals.py
    python scripts/tas_signals.py --date 2026-06-03 --top 15
"""
from __future__ import annotations

import argparse
import html
import re
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
from sqlalchemy import func, select

from trading_intel.config import get_settings
from trading_intel.memory.db import make_session_factory
from trading_intel.memory.models import GreeksSnapshot, OiChainEod

_ET = ZoneInfo("America/New_York")
_CONTRACT_RE = re.compile(r"^\.?([A-Za-z]+)(\d{6})([CcPp])(\d+(?:\.\d+)?)$")
_WALL_DTE = 60
_SHORT_DTE = 7


# ── load + decode the tape ─────────────────────────────────────────────


def _decode(symbol: str) -> tuple[str | None, object, str | None, float | None]:
    m = _CONTRACT_RE.match(str(symbol).strip())
    if not m:
        return None, None, None, None
    root, ymd, cp, strike = m.groups()
    try:
        exp = datetime.strptime(ymd, "%y%m%d").date()
    except ValueError:
        exp = None
    return root.upper(), exp, cp.upper(), float(strike)


def load_flow(path: Path, *, as_of: date) -> pd.DataFrame:
    """Load a capture CSV with decoded contract + signed-delta columns."""
    df = pd.read_csv(path)
    if df.empty:
        return df
    if "root" not in df.columns or df["root"].isna().all():
        dec = df["symbol"].map(_decode)
        df["root"] = [d[0] for d in dec]
        df["expiration"] = [d[1] for d in dec]
        df["strike"] = [d[3] for d in dec]
        df["cp"] = [d[2] for d in dec]
    else:
        df["cp"] = df["opt_kind"].astype(str).str[0].str.upper()
    df = df[df["root"].notna()].copy()
    df["expiration"] = pd.to_datetime(df["expiration"], errors="coerce").dt.date
    df["notional"] = pd.to_numeric(df["notional"], errors="coerce").fillna(0.0)
    df["size"] = pd.to_numeric(df["size"], errors="coerce").fillna(0.0)
    df["delta"] = pd.to_numeric(df["delta"], errors="coerce")
    df["spot"] = pd.to_numeric(df["spot"], errors="coerce")
    df["side"] = df["side"].astype(str).str.lower()
    df["dte"] = df["expiration"].map(lambda e: (e - as_of).days if isinstance(e, date) else None)
    is_call = df["cp"] == "C"
    df["otm"] = (is_call & (df["strike"] > df["spot"])) | (~is_call & (df["strike"] < df["spot"]))
    sign = df["side"].map({"buy": 1.0, "sell": -1.0}).fillna(0.0)
    df["sdd"] = (df["delta"] * df["size"] * 100.0 * df["spot"]).fillna(0.0) * sign
    return df


def ticker_flow(df: pd.DataFrame) -> pd.DataFrame:
    """Per-ticker flow rollup, ranked by premium."""
    is_call = df["cp"] == "C"
    short_otm = df["notional"].where(
        (pd.to_numeric(df["dte"], errors="coerce") <= _SHORT_DTE) & df["otm"], 0.0
    )
    g = df.assign(
        call_notional=df["notional"].where(is_call, 0.0),
        put_notional=df["notional"].where(~is_call, 0.0),
        buy_notional=df["notional"].where(df["side"] == "buy", 0.0),
        short_otm=short_otm,
    ).groupby("root")
    out = g.agg(
        prints=("notional", "size"),
        total_notional=("notional", "sum"),
        call_notional=("call_notional", "sum"),
        put_notional=("put_notional", "sum"),
        buy_notional=("buy_notional", "sum"),
        short_otm=("short_otm", "sum"),
        net_dollar_delta=("sdd", "sum"),
    ).reset_index()
    out["pct_buy"] = (out["buy_notional"] / out["total_notional"]).where(
        out["total_notional"] > 0, 0.0
    )
    out["short_otm_share"] = (out["short_otm"] / out["total_notional"]).where(
        out["total_notional"] > 0, 0.0
    )
    return out.sort_values("total_notional", ascending=False).reset_index(drop=True)


# ── our GEX context (read-only DB) ─────────────────────────────────────


def gex_context(session, root: str) -> dict | None:  # noqa: ANN001
    """Latest stored gamma backdrop for one name: spot/flip/regime/net GEX/walls."""
    snap = session.execute(
        select(GreeksSnapshot)
        .where(GreeksSnapshot.symbol == root)
        .order_by(GreeksSnapshot.ts.desc())
        .limit(1)
    ).scalar_one_or_none()
    ts = session.execute(
        select(OiChainEod.ts)
        .where(OiChainEod.symbol == root)
        .order_by(OiChainEod.ts.desc())
        .limit(1)
    ).scalar_one_or_none()
    if snap is None and ts is None:
        return None

    call_wall = put_wall = None
    if ts is not None:
        rows = session.execute(
            select(OiChainEod.strike, OiChainEod.cp, func.sum(OiChainEod.gxoi).label("g"))
            .where(
                OiChainEod.symbol == root, OiChainEod.ts == ts,
                OiChainEod.dte <= _WALL_DTE, OiChainEod.dte >= 0,
            )
            .group_by(OiChainEod.strike, OiChainEod.cp)
        ).all()
        calls = [(r.strike, r.g) for r in rows if str(r.cp).upper().startswith("C") and r.g]
        puts = [(r.strike, r.g) for r in rows if str(r.cp).upper().startswith("P") and r.g]
        call_wall = max(calls, key=lambda x: x[1])[0] if calls else None
        put_wall = max(puts, key=lambda x: x[1])[0] if puts else None

    spot = float(snap.spot) if snap and snap.spot is not None else None
    flip = float(snap.gex_flip) if snap and snap.gex_flip is not None else None
    regime = None
    if spot is not None and flip is not None:
        regime = "long" if spot >= flip else "short"
    return {
        "spot": spot, "flip": flip, "regime": regime,
        "gex_total": float(snap.gex_total) if snap and snap.gex_total is not None else None,
        "atm_iv": float(snap.atm_iv) if snap and snap.atm_iv is not None else None,
        "call_wall": call_wall, "put_wall": put_wall,
    }


def confluence(flow: pd.Series, gex: dict) -> dict:
    """Plain-English confluence read joining flow direction with the gamma backdrop."""
    sdd = float(flow["net_dollar_delta"])
    bias = "bullish" if sdd > 0 else "bearish" if sdd < 0 else "neutral"
    regime = gex.get("regime")
    cw, pw = gex.get("call_wall"), gex.get("put_wall")

    aligned = False
    if regime == "short" and bias in {"bullish", "bearish"}:
        aligned = True
        tgt = cw if bias == "bullish" else pw
        note = (
            f"{bias.title()} flow into a SHORT-gamma regime — dealer hedging "
            f"amplifies moves; a push "
            f"{'up' if bias == 'bullish' else 'down'} can feed on itself toward "
            f"the {'call' if bias == 'bullish' else 'put'} wall"
            + (f" ~{tgt:g}." if tgt is not None else ".")
        )
    elif regime == "long" and bias in {"bullish", "bearish"}:
        wall = cw if bias == "bullish" else pw
        note = (
            f"{bias.title()} flow but LONG-gamma regime dampens volatility; the "
            f"{'call' if bias == 'bullish' else 'put'} wall"
            + (f" ~{wall:g} likely acts as resistance/pin." if wall is not None
               else " likely caps the move.")
        )
    elif bias != "neutral":
        note = f"{bias.title()} flow; no gamma-flip read stored for this name yet."
    else:
        note = "Two-sided / hedged flow; no clear directional read."
    return {"bias": bias, "aligned": aligned, "note": note}


# ── HTML render ────────────────────────────────────────────────────────


def _m(x: float | None) -> str:
    return "—" if x is None or pd.isna(x) else f"${x:,.0f}"


def _badge(text: str, kind: str) -> str:
    return f'<span class="badge {kind}">{html.escape(text)}</span>'


def _idea_card(flow: pd.Series, gex: dict, conf: dict) -> str:
    root = html.escape(str(flow["root"]))
    bias = conf["bias"]
    bias_kind = {"bullish": "bull", "bearish": "bear"}.get(bias, "neu")
    star = " ⭐" if conf["aligned"] else ""
    regime = gex.get("regime")
    reg_txt = {"short": "SHORT γ (amplifying)", "long": "LONG γ (dampening)"}.get(regime, "n/a")
    rows = [
        ("Premium", _m(flow["total_notional"]) + f' &nbsp;<small>{int(flow["prints"])} prints</small>'),
        ("Calls / Puts", f'{_m(flow["call_notional"])} / {_m(flow["put_notional"])}'),
        ("Net $delta", _m(flow["net_dollar_delta"])),
        ("Buy %", f'{flow["pct_buy"] * 100:.0f}%'),
        ("Short-dated OTM", f'{flow["short_otm_share"] * 100:.0f}% of premium'),
        ("Spot / Flip", f'{gex.get("spot") or "—"} / {gex.get("flip") or "—"}'),
        ("Gamma regime", reg_txt),
        ("Net GEX", _m(gex.get("gex_total"))),
        ("Call wall / Put wall", f'{gex.get("call_wall") or "—"} / {gex.get("put_wall") or "—"}'),
        ("ATM IV", "—" if gex.get("atm_iv") is None else f'{gex["atm_iv"] * 100:.0f}%'),
    ]
    body = "".join(f"<tr><th>{k}</th><td>{v}</td></tr>" for k, v in rows)
    return f"""
    <div class="card {bias_kind}">
      <div class="card-h"><h3>{root}{star}</h3>{_badge(bias.upper(), bias_kind)}</div>
      <p class="note">{html.escape(conf['note'])}</p>
      <table>{body}</table>
    </div>"""


def _table(df: pd.DataFrame, cols: list[str], title: str, n: int = 12) -> str:
    if df is None or df.empty:
        return f"<h2>{title}</h2><p class='muted'>none</p>"
    use = [c for c in cols if c in df.columns]
    head = "".join(f"<th>{html.escape(c)}</th>" for c in use)
    body = ""
    for _, r in df[use].head(n).iterrows():
        tds = "".join(f"<td>{html.escape(str(r[c]))}</td>" for c in use)
        body += f"<tr>{tds}</tr>"
    return f"<h2>{title}</h2><table class='grid'><tr>{head}</tr>{body}</table>"


def render_html(
    *, as_of: date, df: pd.DataFrame, ideas: list[str],
    combos: pd.DataFrame, sweeps: pd.DataFrame, blocks: pd.DataFrame,
) -> str:
    total = df["notional"].sum()
    net = df["sdd"].sum()
    css = """
    body{font-family:-apple-system,Segoe UI,Roboto,Arial,sans-serif;margin:0;background:#0f1115;color:#e6e8eb}
    .wrap{max-width:1100px;margin:0 auto;padding:24px}
    h1{font-size:22px;margin:0 0 4px} .sub{color:#9aa4b2;margin:0 0 20px;font-size:13px}
    .stats{display:flex;gap:18px;flex-wrap:wrap;margin-bottom:24px}
    .stat{background:#171a21;border:1px solid #232833;border-radius:10px;padding:12px 16px}
    .stat b{display:block;font-size:20px} .stat span{color:#9aa4b2;font-size:12px}
    .cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(330px,1fr));gap:14px}
    .card{background:#171a21;border:1px solid #232833;border-left:4px solid #4b5563;border-radius:10px;padding:14px}
    .card.bull{border-left-color:#16a34a} .card.bear{border-left-color:#dc2626}
    .card-h{display:flex;justify-content:space-between;align-items:center} .card-h h3{margin:0;font-size:17px}
    .note{color:#cbd5e1;font-size:13px;line-height:1.45;margin:8px 0 10px}
    .card table{width:100%;border-collapse:collapse;font-size:12.5px}
    .card th{text-align:left;color:#8b94a3;font-weight:500;padding:2px 0;width:46%}
    .card td{text-align:right;padding:2px 0}
    .badge{font-size:11px;padding:2px 8px;border-radius:999px;font-weight:600}
    .badge.bull{background:#0c2a1a;color:#4ade80} .badge.bear{background:#2a0f10;color:#f87171} .badge.neu{background:#22262e;color:#9aa4b2}
    h2{margin:28px 0 10px;font-size:16px;border-bottom:1px solid #232833;padding-bottom:6px}
    table.grid{width:100%;border-collapse:collapse;font-size:12.5px}
    table.grid th{text-align:left;color:#8b94a3;border-bottom:1px solid #232833;padding:6px 8px}
    table.grid td{padding:5px 8px;border-bottom:1px solid #181b22}
    .muted{color:#6b7280;font-size:13px}
    .disc{margin-top:30px;color:#6b7280;font-size:11.5px;line-height:1.5;border-top:1px solid #232833;padding-top:14px}
    """
    cards = "".join(ideas) if ideas else "<p class='muted'>No tracked names with both flow and stored gamma data today.</p>"
    return f"""<!doctype html><html><head><meta charset="utf-8">
<title>TAS x GEX signals — {as_of}</title><style>{css}</style></head><body><div class="wrap">
<h1>Options-flow × gamma confluence — {as_of}</h1>
<p class="sub">Generated from data/tas/{as_of}.csv joined with our stored GEX/wall/regime data. Descriptive research, not trade signals.</p>
<div class="stats">
  <div class="stat"><b>{len(df):,}</b><span>prints</span></div>
  <div class="stat"><b>{df['root'].nunique()}</b><span>tickers</span></div>
  <div class="stat"><b>{_m(total)}</b><span>total premium</span></div>
  <div class="stat"><b>{_m(net)}</b><span>net market $delta</span></div>
</div>
<h2>Top confluence ideas (tracked names — flow + our gamma backdrop)</h2>
<div class="cards">{cards}</div>
{_table(combos, ['root','structure','n_legs','total_notional','legs'], 'Multi-leg packages (combos)')}
{_table(sweeps, ['root','strike','cp','side','n_prints','total_size','total_notional'], 'Sweeps (tight-cluster aggression)')}
{_table(blocks, ['root','expiry','strike','cp','side','size','notional','dte'], 'Largest blocks')}
<p class="disc"><b>Not investment advice.</b> This is a descriptive confluence scan: it pairs aggressive option flow with our stored dealer-gamma regime (FlashAlpha rule 4 — GEX is a regime descriptor, not a signal). A ⭐ marks directional flow landing in a short-gamma (move-amplifying) regime; it is a place to look, not a recommendation. Gamma data is end-of-prior-day; flow is intraday. Do your own analysis and manage risk.</p>
</div></body></html>"""


# ── combos / sweeps / blocks (compact, reused from the analyzer logic) ──


def combos(df: pd.DataFrame) -> pd.DataFrame:
    d = df.copy()
    d["ts"] = pd.to_datetime(d["time"], errors="coerce")
    try:
        d["ts"] = d["ts"].dt.tz_localize(None)
    except (TypeError, AttributeError):
        pass
    rows = []
    for (root, ts), grp in d.dropna(subset=["ts"]).groupby(["root", "ts"]):
        legs = grp.drop_duplicates(["strike", "cp", "expiration"])
        if len(legs) < 2:
            continue
        cps = set(legs["cp"])
        nstr = legs["strike"].nunique()
        struct = ("straddle/synthetic" if cps == {"C", "P"} and nstr == 1
                  else "risk-reversal/combo" if cps == {"C", "P"}
                  else "vertical/spread" if nstr >= 2 else "multi-leg")
        desc = ", ".join(f"{x.cp}{x.strike:g} x{int(x.size)}" for x in legs.itertuples())
        rows.append({"root": root, "structure": struct, "n_legs": len(legs),
                     "total_notional": _m(grp["notional"].sum()), "legs": desc})
    out = pd.DataFrame(rows)
    return out.sort_values("total_notional", ascending=False).reset_index(drop=True) if not out.empty else out


def sweeps(df: pd.DataFrame, *, window_s: float = 6.0, min_legs: int = 3) -> pd.DataFrame:
    d = df.copy()
    d["ts"] = pd.to_datetime(d["time"], errors="coerce")
    try:
        d["ts"] = d["ts"].dt.tz_localize(None)
    except (TypeError, AttributeError):
        pass
    d = d.dropna(subset=["ts"])
    out = []
    for key, grp in d.sort_values("ts").groupby(["root", "expiration", "strike", "cp", "side"]):
        t = grp["ts"].tolist()
        s = 0
        for i in range(1, len(t) + 1):
            if i == len(t) or (t[i] - t[i - 1]).total_seconds() > window_s:
                chunk = grp.iloc[s:i]
                if len(chunk) >= min_legs:
                    out.append({"root": key[0], "strike": f"{key[2]:g}", "cp": key[3],
                                "side": key[4], "n_prints": len(chunk),
                                "total_size": int(chunk["size"].sum()),
                                "total_notional": _m(chunk["notional"].sum())})
                s = i
    o = pd.DataFrame(out)
    return o.sort_values("n_prints", ascending=False).reset_index(drop=True) if not o.empty else o


def blocks(df: pd.DataFrame, *, n: int = 15) -> pd.DataFrame:
    b = df.sort_values("notional", ascending=False).head(n).copy()
    b["size"] = b["size"].astype(int)
    b["notional"] = b["notional"].map(_m)
    b["expiry"] = b["expiration"].astype(str)
    return b[["root", "expiry", "strike", "cp", "side", "size", "notional", "dte"]]


# ── main ───────────────────────────────────────────────────────────────


def main() -> None:
    p = argparse.ArgumentParser(description="Join TAS flow with our GEX -> HTML signal report.")
    p.add_argument("--date", help="capture date YYYY-MM-DD (default today ET)")
    p.add_argument("--file", help="explicit CSV path")
    p.add_argument("--out-dir", default="data/tas")
    p.add_argument("--top", type=int, default=15, help="max tracked-name idea cards")
    args = p.parse_args()

    if args.file:
        csv_path = Path(args.file)
        try:
            as_of = date.fromisoformat(csv_path.stem)
        except ValueError:
            as_of = datetime.now(_ET).date()
    else:
        day = args.date or datetime.now(_ET).strftime("%Y-%m-%d")
        csv_path = Path(args.out_dir) / f"{day}.csv"
        as_of = date.fromisoformat(day)
    if not csv_path.exists():
        raise SystemExit(f"no capture CSV at {csv_path}")

    df = load_flow(csv_path, as_of=as_of)
    if df.empty:
        raise SystemExit(f"{csv_path} has no decodable prints")
    tk = ticker_flow(df)

    ideas: list[str] = []
    settings = get_settings()
    factory = make_session_factory(settings)
    with factory() as session:
        shown = 0
        for _, flow in tk.iterrows():
            if shown >= args.top:
                break
            gex = gex_context(session, str(flow["root"]))
            if gex is None:  # only show names we actually track / have gamma for
                continue
            ideas.append(_idea_card(flow, gex, confluence(flow, gex)))
            shown += 1

    out_html = render_html(
        as_of=as_of, df=df, ideas=ideas,
        combos=combos(df), sweeps=sweeps(df), blocks=blocks(df),
    )
    out_path = csv_path.with_name(f"signals_{as_of.isoformat()}.html")
    out_path.write_text(out_html, encoding="utf-8")
    print(f"wrote {out_path}  ({len(df):,} prints, {len(ideas)} confluence cards)")


if __name__ == "__main__":
    main()
