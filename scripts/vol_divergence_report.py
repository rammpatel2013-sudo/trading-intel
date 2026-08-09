"""SPX 1-Day Vol Divergence — Cboe VIX1D vs realized vol AND vs dealer gamma.

Replaces the (discontinued, 2021-frozen) Cboe "GAMMA" index the earlier standalone
script assumed. The one real, current series in that idea is **VIX1D** — Cboe's
1-Day Volatility Index (the "SVIX / -1-day IV"), which we pull live + full history
from Cboe. We pair it against two real, overlapping series we already have:

  Lens A — VIX1D vs SPX realized vol (short window)  → the 1-day vol-risk-premium
  Lens B — VIX1D vs SPX net dealer gamma (our GEX)   → implied vol vs positioning

Each lens: level & change correlation, per-day trend, current z-scores, a
divergence z + state, and an inline static-SVG overlay. Then a descriptive state
synthesis plus a clearly-labelled discretionary read (rule 4: descriptive of vol
and positioning, not advice). Phone-safe (server-side SVG, no JS, no CDN).

Data: VIX1D ← clients/cboe (CDN + history CSV); RV ← quotes_daily SPX/SPY closes;
GEX ← mcp.tools.get_gamma_history("SPX"). Read-only; no new vendor.

Run:
    python scripts/vol_divergence_report.py            # build + push to Telegram
    python scripts/vol_divergence_report.py --no-push  # build only
"""
from __future__ import annotations

import math
import sys
from datetime import date, datetime
from pathlib import Path

import structlog

log = structlog.get_logger(__name__)

_DEFAULT_OUT = Path("reports") / "vol_divergence.html"
_RV_WINDOW = 5           # fallback realized-vol window (auto-picked at runtime, see _best_window)
_WIN_CANDIDATES = (3, 5, 10, 20)   # realized windows tried; the one most correlated to VIX1D wins
_LOOKBACK = 252          # window for z-scores / correlations
_TREND_WIN = 20          # window for the per-day trend slope
_CHART_N = 120           # points shown in the overlay charts
_UNDERLYINGS = ("SPX", "^SPX", "SPY")
_GEX_SYMS = ("SPX", "SPXW", "^SPX")


# ── scalars / stats ─────────────────────────────────────────────────────────
def _num(x):
    try:
        return float(x) if x is not None else None
    except (TypeError, ValueError):
        return None


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def _std(xs):
    xs = [x for x in xs if x is not None]
    if len(xs) < 2:
        return None
    m = sum(xs) / len(xs)
    return (sum((x - m) ** 2 for x in xs) / (len(xs) - 1)) ** 0.5


def _z(x, xs):
    if x is None:
        return None
    m, s = _mean(xs), _std(xs)
    return (x - m) / s if (m is not None and s) else None


def _corr(a, b):
    n = min(len(a), len(b))
    if n < 3:
        return None
    a, b = a[-n:], b[-n:]
    ma, mb, sa, sb = _mean(a), _mean(b), _std(a), _std(b)
    if not sa or not sb:
        return None
    cov = sum((a[i] - ma) * (b[i] - mb) for i in range(n)) / (n - 1)
    return cov / (sa * sb)


def _slope(xs):
    n = len(xs)
    if n < 2:
        return None
    xbar = (n - 1) / 2
    ybar = _mean(xs)
    den = sum((i - xbar) ** 2 for i in range(n))
    if not den:
        return None
    return sum((i - xbar) * (xs[i] - ybar) for i in range(n)) / den


def _diffs(xs):
    return [xs[i] - xs[i - 1] for i in range(1, len(xs))]


def _pdate(x):
    if isinstance(x, date):
        return x
    try:
        return datetime.strptime(str(x)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


# ── data assembly ───────────────────────────────────────────────────────────
def _realized_vol(closes_by_date: dict, window: int = _RV_WINDOW) -> dict:
    """Rolling annualized close-to-close realized vol (vol points) keyed by date."""
    items = sorted((d, c) for d, c in closes_by_date.items() if d is not None and c)
    dates = [d for d, _ in items]
    px = [c for _, c in items]
    out = {}
    for i in range(window, len(px)):
        rets = [math.log(px[j] / px[j - 1]) for j in range(i - window + 1, i + 1)
                if px[j - 1] > 0 and px[j] > 0]
        sd = _std(rets)
        if sd is not None:
            out[dates[i]] = sd * math.sqrt(252) * 100.0
    return out


def _underlying_closes(session, days: int = 520):
    from sqlalchemy import select

    from trading_intel.memory.models import QuoteDaily
    for sym in _UNDERLYINGS:
        rows = session.execute(
            select(QuoteDaily.date, QuoteDaily.close)
            .where(QuoteDaily.symbol == sym).order_by(QuoteDaily.date.desc()).limit(days)
        ).all()
        if len(rows) >= 30:
            return sym, {r.date: _num(r.close) for r in rows}
    return None, {}


def _gex_history(session):
    try:
        from trading_intel.mcp.tools import get_gamma_history
    except Exception:  # noqa: BLE001
        return None, {}
    for sym in _GEX_SYMS:
        try:
            d = get_gamma_history(session, sym, days=365)
        except Exception:  # noqa: BLE001
            d = None
        if d and d.get("rows"):
            out = {}
            for r in d["rows"]:
                dt, gx = _pdate(r.get("date")), _num(r.get("gex_total"))
                if dt is not None and gx is not None:
                    out[dt] = gx
            if len(out) >= 10:
                return sym, out
    return None, {}


# ── relationship metrics ────────────────────────────────────────────────────
def _align(a: dict, b: dict):
    common = sorted(set(a) & set(b))
    return common, [a[d] for d in common], [b[d] for d in common]


def _zseries(vals, lookback=_LOOKBACK):
    """Point-wise z vs the trailing ``lookback`` window (expanding at the start)."""
    out = []
    for i in range(len(vals)):
        lo = max(0, i - lookback + 1)
        out.append(_z(vals[i], vals[lo:i + 1]))
    return out


def _state(z):
    if z is None:
        return ("unknown", "neu")
    a = abs(z)
    if a >= 2.0:
        return ("EXTREME", "dn" if z > 0 else "up")
    if a >= 1.0:
        return ("elevated", "neu")
    return ("normal", "mut")


def _relation(name, a_by_date, b_by_date, *, same_unit):
    common, A, B = _align(a_by_date, b_by_date)
    if len(common) < 12:
        return {"name": name, "ok": False, "n": len(common)}
    Aw, Bw = A[-_LOOKBACK:], B[-_LOOKBACK:]
    lvl = _corr(Aw, Bw)
    chg = _corr(_diffs(A)[-_LOOKBACK:], _diffs(B)[-_LOOKBACK:])
    sa, sb = _slope(A[-_TREND_WIN:]), _slope(B[-_TREND_WIN:])
    za, zb = _z(A[-1], Aw), _z(B[-1], Bw)
    if same_unit:
        div = [A[i] - B[i] for i in range(len(A))]
        div_now = div[-1]
        div_z = _z(div_now, div[-_LOOKBACK:])
        chart = (common[-_CHART_N:], A[-_CHART_N:], B[-_CHART_N:], False)
    else:
        zA, zB = _zseries(A), _zseries(B)
        div = [(zA[i] - zB[i]) if (zA[i] is not None and zB[i] is not None) else None
               for i in range(len(A))]
        div_now = div[-1]
        div_z = div_now  # already in standard-deviation units
        chart = (common[-_CHART_N:], zA[-_CHART_N:], zB[-_CHART_N:], True)
    st, cls = _state(div_z)
    return {"name": name, "ok": True, "n": len(common), "dates": common,
            "a_last": A[-1], "b_last": B[-1], "za": za, "zb": zb,
            "lvl_corr": lvl, "chg_corr": chg, "slope_a": sa, "slope_b": sb,
            "div_now": div_now, "div_z": div_z, "state": st, "state_cls": cls,
            "chart": chart, "same_unit": same_unit}


# ── realized-window selection + backtest ────────────────────────────────────
def _best_window(vix1d: dict, closes: dict, candidates=_WIN_CANDIDATES):
    """Pick the realized-vol window most correlated with VIX1D (best tenor match)."""
    corrs, best = {}, (None, None)
    for w in candidates:
        rv = _realized_vol(closes, w)
        common, A, B = _align(vix1d, rv)
        c = _corr(A[-_LOOKBACK:], B[-_LOOKBACK:]) if len(common) >= 20 else None
        corrs[w] = c
        if c is not None and (best[1] is None or c > best[1]):
            best = (w, c)
    return (best[0] or _RV_WINDOW), best[1], corrs


def _backtest(vix1d: dict, closes: dict, window: int):
    """Historically, does the 1-day VRP mean-revert? Test VIX1D vs FORWARD realized.

    For each day with both a VRP (VIX1D − realized) and ``window`` days ahead of
    price, records forward realized vol and forward SPX return. Reports the
    predictive correlation, the IV-rich 'crush' hit-rate (forward realized landed
    below implied), the IV-cheap 'expansion' hit-rate, and forward outcomes bucketed
    by VRP z. In-sample historical evaluation (uses future data by construction) —
    a descriptive base-rate, not a live signal.
    """
    items = sorted((d, c) for d, c in closes.items() if d is not None and c)
    dates = [d for d, _ in items]
    px = [c for _, c in items]
    idx = {d: i for i, d in enumerate(dates)}
    rv = _realized_vol(closes, window)

    recs = []
    for d in sorted(set(vix1d) & set(rv)):
        i = idx.get(d)
        if i is None or i + window >= len(px):
            continue
        frets = [math.log(px[j] / px[j - 1]) for j in range(i + 1, i + window + 1)
                 if px[j - 1] > 0 and px[j] > 0]
        if len(frets) < 2:
            continue
        frv = _std(frets) * math.sqrt(252) * 100.0
        fret = (px[i + window] / px[i] - 1) * 100.0
        recs.append({"iv": vix1d[d], "rv": rv[d], "vrp": vix1d[d] - rv[d], "frv": frv, "fret": fret})
    if len(recs) < 40:
        return {"ok": False, "n": len(recs)}

    vrps = [x["vrp"] for x in recs]
    for x in recs:
        x["vz"] = _z(x["vrp"], vrps)
    pred_corr = _corr([x["iv"] for x in recs], [x["frv"] for x in recs])

    def _agg(g):
        return {"n": len(g), "frv": _mean([x["frv"] for x in g]),
                "fret": _mean([x["fret"] for x in g]), "iv": _mean([x["iv"] for x in g])}
    hi = [x for x in recs if x["vz"] is not None and x["vz"] >= 1.0]
    mid = [x for x in recs if x["vz"] is not None and -1.0 < x["vz"] < 1.0]
    lo = [x for x in recs if x["vz"] is not None and x["vz"] <= -1.0]
    crush = [x for x in hi if x["frv"] < x["iv"]]
    expand = [x for x in lo if x["frv"] > x["iv"]]
    return {"ok": True, "n": len(recs), "window": window, "pred_corr": pred_corr,
            "hi": _agg(hi), "mid": _agg(mid), "lo": _agg(lo),
            "crush_rate": (len(crush) / len(hi)) if hi else None, "rich_n": len(hi),
            "exp_rate": (len(expand) / len(lo)) if lo else None, "cheap_n": len(lo)}


# ── inline SVG overlay (phone-safe) ─────────────────────────────────────────
def _overlay(chart, la, lb):
    dates, A, B, zero = chart
    pts = [(A[i], B[i]) for i in range(len(A)) if A[i] is not None and B[i] is not None]
    if len(pts) < 3:
        return '<div class="na">insufficient overlap to chart</div>'
    xa = [p[0] for p in pts]
    xb = [p[1] for p in pts]
    lo = min(min(xa), min(xb))
    hi = max(max(xa), max(xb))
    rng = (hi - lo) or 1.0
    W, H, pad = 620, 130, 8
    n = len(pts)
    xstep = (W - 2 * pad) / (n - 1)
    yy = lambda v: round(H - pad - (v - lo) / rng * (H - 2 * pad), 1)
    xx = lambda i: round(pad + i * xstep, 1)
    pa = " ".join(f"{xx(i)},{yy(xa[i])}" for i in range(n))
    pb = " ".join(f"{xx(i)},{yy(xb[i])}" for i in range(n))
    out = [f'<svg viewBox="0 0 {W} {H}" width="100%">']
    if zero and lo < 0 < hi:
        out.append(f'<line x1="{pad}" y1="{yy(0)}" x2="{W-pad}" y2="{yy(0)}" class="z0"/>')
    out.append(f'<polyline points="{pa}" class="la"/>')
    out.append(f'<polyline points="{pb}" class="lb"/>')
    out.append(f'<text x="{W-4}" y="12" text-anchor="end" class="mk">{xa[-1]:.1f}</text></svg>')
    return "".join(out)


# ── format ──────────────────────────────────────────────────────────────────
def _f(v, nd=2):
    v = _num(v)
    return "—" if v is None else f"{v:.{nd}f}"


def _sz(v, nd=2):
    v = _num(v)
    return "—" if v is None else f"{v:+.{nd}f}"


def _gex_h(v):
    v = _num(v)
    if v is None:
        return "—"
    a = abs(v)
    for div, suf in ((1e9, "B"), (1e6, "M"), (1e3, "k")):
        if a >= div:
            return f"{v/div:+.1f}{suf}"
    return f"{v:+.0f}"


# ── reads (descriptive + discretionary) ─────────────────────────────────────
def _read_rv(rel):
    if not rel.get("ok"):
        return "Not enough overlapping history yet to read the 1-day vol-risk-premium."
    z = rel["div_z"]
    if z is None:
        return "VIX1D vs realized vol computed; divergence z pending more history."
    if z >= 1.5:
        return ("1-day implied vol sits well ABOVE realized (VRP rich) — the market is "
                "paying up for 0DTE protection relative to what SPX is actually delivering; "
                "historically this decays via an IV-crush unless a catalyst forces realized to catch up.")
    if z <= -1.5:
        return ("1-day implied vol sits BELOW realized (VRP cheap/negative) — 0DTE options look "
                "under-priced vs delivered movement; owning short-dated gamma has the edge here.")
    return ("1-day implied vs realized vol is near its normal spread — no vol-risk-premium extreme "
            "to fade or own right now.")


def _read_gex(rel, gex_last):
    if not rel.get("ok"):
        return "Not enough overlapping VIX1D/GEX history yet to read positioning vs implied vol."
    regime = "long gamma (dealers dampening)" if (gex_last or 0) > 0 else "short gamma (dealers amplifying)"
    z = rel["div_z"]
    tail = ""
    if z is not None and z >= 1.5:
        tail = " VIX1D is stretched high relative to dealer gamma — implied vol is leading positioning."
    elif z is not None and z <= -1.5:
        tail = " VIX1D is low relative to dealer gamma — implied vol is lagging the positioning signal."
    base = ("Dealers are in a " + regime + ". "
            + ("With short gamma, hedging feeds moves, so realized can spike and validate the high implied vol."
               if (gex_last or 0) < 0 else
               "With long gamma, hedging fades moves, so elevated implied vol tends to over-price the realized path."))
    return base + tail


def _near_term(rv_rel, gex_rel, gex_last):
    """Descriptive state + a clearly-labelled discretionary lean (rule 4)."""
    zrv = rv_rel.get("div_z") if rv_rel.get("ok") else None
    long_gamma = (gex_last or 0) > 0
    lean, why, probs = "balanced / no edge", [], (40, 35, 25)
    if zrv is not None and zrv >= 1.5 and long_gamma:
        lean = "range-bound, IV-crush bias"
        why = ["1-day IV rich vs realized (premium to fade)",
               "dealers long gamma → moves dampened",
               "absent a catalyst, the fear premium tends to bleed out"]
        probs = (55, 25, 20)  # range / up-drift / down-break
    elif zrv is not None and zrv <= -1.5 and not long_gamma:
        lean = "expansion risk, own short-dated gamma"
        why = ["1-day IV cheap vs realized (under-priced movement)",
               "dealers short gamma → moves amplified",
               "the setup favours a volatility expansion, either direction"]
        probs = (25, 40, 35)
    elif zrv is not None and zrv >= 1.5:
        lean = "lean range-bound (rich 1-day IV), watch positioning"
        why = ["1-day IV rich vs realized",
               "dealer gamma not confirming a dampening regime — mixed"]
        probs = (48, 30, 22)
    else:
        why = ["no vol-risk-premium extreme and no confirming gamma signal",
               "treat as a coin-flip until one lens goes to an extreme"]
    return lean, why, probs


# ── render ──────────────────────────────────────────────────────────────────
_CSS = """
:root{--bg:#0a0d13;--card:#111621;--card2:#0f141d;--line:#1d2431;--ink:#e9edf4;
--mut:#7a8494;--dim:#5b6472;--teal:#4d9fd6;--green:#35c46a;--red:#ef6f53;--gold:#e6b34d;--pu:#a98bf0}
*{box-sizing:border-box}html,body{margin:0}
body{background:radial-gradient(1100px 460px at 50% -10%,#131a26 0%,var(--bg) 60%);color:var(--ink);
font:15px/1.5 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;-webkit-font-smoothing:antialiased}
.wrap{max-width:820px;margin:0 auto;padding:20px 16px 40px}
.top{position:relative;text-align:center;padding:6px 0 12px}
.sigma{position:absolute;left:0;top:0;width:26px;height:26px;border:1px solid var(--line);border-radius:7px;
display:flex;align-items:center;justify-content:center;color:var(--teal);font-weight:700;background:var(--card2)}
h1{margin:0;font-size:23px;font-weight:800;letter-spacing:.04em}
.sub{margin-top:5px;color:var(--mut);font-size:12.5px}
h2{font-size:12.5px;text-transform:uppercase;letter-spacing:.05em;margin:20px 0 8px;color:var(--teal);
border-bottom:1px solid var(--line);padding-bottom:5px}h2 span{color:var(--mut);text-transform:none;letter-spacing:0}
.card{background:linear-gradient(180deg,var(--card),var(--card2));border:1px solid var(--line);border-radius:12px;padding:13px 15px;margin:9px 0}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:7px}
.kv{display:flex;justify-content:space-between;gap:10px;padding:4px 0;border-bottom:1px dashed var(--line);font-size:13.5px}
.kv .k{color:var(--mut)}.kv b{color:var(--ink)}
.mut{color:var(--mut);font-size:12px}.na{color:var(--dim);font-size:12.5px;padding:4px 0}
.up{color:var(--green)}.dn{color:var(--red)}.neu{color:var(--gold)}
.pill{display:inline-block;font-size:11px;font-weight:700;letter-spacing:.06em;padding:2px 9px;border-radius:20px;border:1px solid var(--line)}
.pill.dn{color:var(--red);border-color:#5a2f2c}.pill.up{color:var(--green);border-color:#2c5a44}.pill.neu{color:var(--gold);border-color:#5a4a2c}.pill.mut{color:var(--mut)}
.cap{font-size:11px;color:var(--mut);margin:8px 0 3px}
.leg{font-size:11px;color:var(--mut)}.leg .a{color:var(--teal)}.leg .b{color:var(--gold)}
.read{background:var(--card2);border-left:3px solid var(--teal);border-radius:0 8px 8px 0;padding:9px 12px;margin:8px 0;font-size:13px}
.disc{background:rgba(240,160,42,.07);border-left:3px solid var(--gold);border-radius:0 8px 8px 0;padding:10px 12px;margin:8px 0;font-size:13px}
svg text{fill:var(--mut);font-size:9px}svg text.mk{fill:var(--teal);font-weight:700}
polyline.la{fill:none;stroke:#4d9fd6;stroke-width:1.8}polyline.lb{fill:none;stroke:#e6b34d;stroke-width:1.6}
line.z0{stroke:#39425080;stroke-width:1;stroke-dasharray:3 3}
.foot{color:var(--dim);font-size:11px;margin-top:20px;border-top:1px solid var(--line);padding-top:10px;line-height:1.6}
"""


def _metric_rows(rel):
    if not rel.get("ok"):
        return f'<div class="na">insufficient overlapping history (n={rel.get("n",0)}).</div>'
    st = rel["state"]
    return (
        '<div class="grid">'
        f'<div class="kv"><span class="k">Level corr</span><b>{_f(rel["lvl_corr"])}</b></div>'
        f'<div class="kv"><span class="k">Δ corr</span><b>{_f(rel["chg_corr"])}</b></div>'
        f'<div class="kv"><span class="k">VIX1D z</span><b>{_sz(rel["za"])}</b></div>'
        f'<div class="kv"><span class="k">other z</span><b>{_sz(rel["zb"])}</b></div>'
        f'<div class="kv"><span class="k">Divergence z</span><b class="{rel["state_cls"]}">{_sz(rel["div_z"])}</b></div>'
        f'<div class="kv"><span class="k">State</span><b><span class="pill {rel["state_cls"]}">{st}</span></b></div>'
        '</div>')


def _pct(v):
    v = _num(v)
    return "—" if v is None else f"{v*100:.0f}%"


def _backtest_section(bt, win, win_corrs):
    wc = " · ".join(f"{w}d {_f(win_corrs.get(w))}" for w in _WIN_CANDIDATES if win_corrs.get(w) is not None)
    winline = (f'<div class="mut">Realized window auto-picked: <b>{win}d</b> '
               f'(highest corr to VIX1D){f" · corr by window: {wc}" if wc else ""}.</div>')
    if not bt.get("ok"):
        return (f'<div class="card">{winline}'
                f'<div class="na">Backtest pending — needs ~40+ overlapping sessions (have {bt.get("n",0)}).</div></div>')
    cr, er = bt.get("crush_rate"), bt.get("exp_rate")
    verdict = ("Weak/again-sample — treat the divergence as context, not a standalone edge.")
    if cr is not None and cr >= 0.6:
        verdict = (f"Thesis holds: when 1-day IV was rich (VRP z≥1), forward realized landed BELOW implied "
                   f"{_pct(cr)} of the time (n={bt['rich_n']}) — fading rich 1-day vol had a historical edge.")
    elif cr is not None and cr < 0.45:
        verdict = (f"Thesis does NOT hold on this sample: rich 1-day IV was followed by realized below implied "
                   f"only {_pct(cr)} of the time — no crush edge here.")

    def brow(lbl, g):
        return (f'<div class="kv"><span class="k">{lbl}</span>'
                f'<b>fwd RV {_f(g["frv"],1)}</b> <span class="mut">· fwd SPX {_sz(g["fret"],1)}% · n={g["n"]}</span></div>')
    return (
        f'<div class="card">{winline}'
        '<div class="grid" style="margin-top:6px">'
        f'<div class="kv"><span class="k">VIX1D→fwd-RV corr</span><b>{_f(bt["pred_corr"])}</b></div>'
        f'<div class="kv"><span class="k">IV-rich crush rate</span><b class="{"up" if (cr or 0)>=0.6 else "neu"}">{_pct(cr)}</b> <span class="mut">n={bt["rich_n"]}</span></div>'
        f'<div class="kv"><span class="k">IV-cheap expand rate</span><b>{_pct(er)}</b> <span class="mut">n={bt["cheap_n"]}</span></div>'
        '</div>'
        f'<div class="cap" style="margin-top:8px">Forward {bt["window"]}-day outcome by VRP z-bucket</div>'
        + brow("VRP high (z≥1) · IV rich", bt["hi"])
        + brow("VRP mid (|z|&lt;1)", bt["mid"])
        + brow("VRP low (z≤−1) · IV cheap", bt["lo"])
        + f'<div class="read">{verdict}</div></div>')


def _render(ctx) -> str:
    when = ctx["as_of"].strftime("%b %-d, %Y") if hasattr(ctx["as_of"], "strftime") else str(ctx["as_of"])
    v1d, rv, gex = ctx["vix1d_last"], ctx["rv_last"], ctx["gex_last"]
    rvrel, gxrel = ctx["rv_rel"], ctx["gex_rel"]
    w = ctx.get("window", _RV_WINDOW)
    bt = ctx.get("bt", {"ok": False})
    lean, why, probs = ctx["read"]
    live = f' <span class="mut">(live {_f(ctx["vix1d_live"])})</span>' if ctx.get("vix1d_live") is not None else ""
    regime = "long gamma" if (gex or 0) > 0 else "short gamma" if (gex or 0) is not None and (gex or 0) < 0 else "—"
    why_html = "".join(f"<li>{x}</li>" for x in why)
    bt_conf = ""
    if bt.get("ok") and bt.get("crush_rate") is not None:
        bt_conf = (f'<div class="mut" style="margin-top:6px">Backtest anchor: rich 1-day IV → realized below implied '
                   f'{_pct(bt["crush_rate"])} of the time historically.</div>')
    rva = _overlay(rvrel["chart"], "VIX1D", "RV") if rvrel.get("ok") else '<div class="na">no chart</div>'
    gxa = _overlay(gxrel["chart"], "VIX1D", "GEX") if gxrel.get("ok") else '<div class="na">no chart</div>'
    coldstart = ('<div class="mut" style="margin-top:6px">Dealer-gamma history is still short — '
                 'z-scores/correlation there will firm up as more sessions bank.</div>'
                 if (not gxrel.get("ok")) or gxrel.get("n", 0) < 40 else "")
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>SPX 1-Day Vol Divergence</title><style>{_CSS}</style></head>
<body><div class="wrap">
<div class="top"><div class="sigma">&#931;</div>
<h1>SPX 1-DAY VOL DIVERGENCE</h1>
<div class="sub">VIX1D vs realized vol &amp; dealer gamma &middot; {when} &middot; EOD</div></div>

<div class="card"><div class="grid">
<div class="kv"><span class="k">VIX1D (1-day IV)</span><b>{_f(v1d)}{live}</b></div>
<div class="kv"><span class="k">Realized vol ({w}d)</span><b>{_f(rv)}</b></div>
<div class="kv"><span class="k">1-day VRP (IV−RV)</span><b class="{"dn" if (v1d and rv and v1d-rv>0) else "up"}">{_sz((v1d-rv) if (v1d is not None and rv is not None) else None)}</b></div>
<div class="kv"><span class="k">SPX dealer gamma</span><b>{_gex_h(gex)} <span class="mut">({regime})</span></b></div>
</div></div>

<h2>Lens A · VIX1D vs realized vol <span>· the 1-day vol-risk-premium</span></h2>
<div class="card">{_metric_rows(rvrel)}
<div class="cap">VIX1D vs {w}-day realized (vol points, last {_CHART_N})</div>{rva}
<div class="leg"><span class="a">— VIX1D</span> &nbsp; <span class="b">— realized vol</span></div>
<div class="read">{_read_rv(rvrel)}</div></div>

<h2>Lens B · VIX1D vs dealer gamma <span>· implied vol vs positioning</span></h2>
<div class="card">{_metric_rows(gxrel)}
<div class="cap">z-scored overlay — VIX1D vs SPX net GEX (last {_CHART_N})</div>{gxa}
<div class="leg"><span class="a">— VIX1D (z)</span> &nbsp; <span class="b">— dealer GEX (z)</span></div>
<div class="read">{_read_gex(gxrel, gex)}</div>{coldstart}</div>

<h2>Backtest <span>· does the divergence pay?</span></h2>
{_backtest_section(bt, w, ctx.get("win_corrs") or {})}

<h2>Near-term read <span>· state + discretionary lean</span></h2>
<div class="card">
<div class="kv"><span class="k">Descriptive state</span><b>{_esc_state(rvrel, gxrel)}</b></div>
<div class="disc"><b>Discretionary read (not advice):</b> lean <b>{lean}</b>.
<ul style="margin:6px 0 0 -10px">{why_html}</ul>
<div class="mut" style="margin-top:6px">Rough scenario tilt — range/chop {probs[0]}% &middot; drift up {probs[1]}% &middot; break down {probs[2]}%.
Gap risk on an unscheduled catalyst is the main thing this can't see.</div>{bt_conf}</div></div>

<div class="foot">Sources: Cboe VIX1D (live quote + VIX1D_History.csv) · SPX/SPY realized vol from quotes_daily · SPX net GEX from get_gamma_history.
The Cboe "GAMMA" index is discontinued (history ends 2021-12-23) and is NOT used. Descriptive of vol &amp; positioning, not investment advice (rule 4).</div>
</div></body></html>"""


def _esc_state(rvrel, gxrel):
    a = rvrel["state"] if rvrel.get("ok") else "—"
    b = gxrel["state"] if gxrel.get("ok") else "—"
    return f"VRP {a} · positioning {b}"


# ── public build ────────────────────────────────────────────────────────────
def build(session, *, settings=None, cboe=None, out: Path | None = None) -> Path:
    from trading_intel.clients.cboe import CboeClient

    cboe = cboe or CboeClient()
    hist = cboe.vix1d_history()                       # [(date, close)] ascending
    vix1d = {d: c for d, c in hist}
    vix1d_live = None
    try:
        vix1d_live = cboe.vix1d()
    except Exception:  # noqa: BLE001
        pass

    _usym, closes = _underlying_closes(session)
    window, win_corr, win_corrs = _best_window(vix1d, closes) if closes else (_RV_WINDOW, None, {})
    rv = _realized_vol(closes, window) if closes else {}
    bt = _backtest(vix1d, closes, window) if closes else {"ok": False}
    _gsym, gex = _gex_history(session)

    rv_rel = _relation("rv", vix1d, rv, same_unit=True)
    gex_rel = _relation("gex", vix1d, gex, same_unit=False)

    as_of = (sorted(vix1d)[-1] if vix1d else (sorted(closes)[-1] if closes else date.today()))
    vix1d_last = vix1d.get(as_of) or (sorted(vix1d.items())[-1][1] if vix1d else None)
    rv_last = rv.get(sorted(rv)[-1]) if rv else None
    gex_last = gex.get(sorted(gex)[-1]) if gex else None
    read = _near_term(rv_rel, gex_rel, gex_last)

    ctx = {"as_of": as_of, "vix1d_last": vix1d_last, "vix1d_live": vix1d_live,
           "rv_last": rv_last, "gex_last": gex_last, "rv_rel": rv_rel, "gex_rel": gex_rel,
           "read": read, "window": window, "win_corr": win_corr, "win_corrs": win_corrs, "bt": bt}
    dest = Path(out) if out else _DEFAULT_OUT
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(_render(ctx), encoding="utf-8")
    log.info("vol_divergence.built", path=str(dest), as_of=str(as_of), vix1d=vix1d_last,
             rv_n=len(rv), gex_n=len(gex), window=window, bt_n=bt.get("n"))
    return dest


def main() -> None:
    from trading_intel.config import get_settings
    from trading_intel.memory.db import make_session_factory

    structlog.configure(processors=[structlog.processors.add_log_level,
                                    structlog.processors.TimeStamper(fmt="iso"),
                                    structlog.processors.JSONRenderer()])
    settings = get_settings()
    push = "--no-push" not in sys.argv[1:]
    sf = make_session_factory(settings)
    with sf() as session:
        path = build(session, settings=settings)
    if push:
        from trading_intel.clients.telegram import TelegramClient

        sent = TelegramClient(settings).send_document(path, caption="SPX 1-Day Vol Divergence — EOD")
        log.info("vol_divergence.pushed", path=str(path), telegram_sent=sent)
    print(f"vol divergence written: {path}")


if __name__ == "__main__":
    main()
