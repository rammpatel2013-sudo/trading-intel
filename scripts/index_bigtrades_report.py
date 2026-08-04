"""Index big-trades report — the biggest SPX/SPY/QQQ option prints on the
market-wide Convex tape, GROUPED INTO STRUCTURES (spreads / flies / calendars /
rolls) instead of read leg-by-leg, with dealer-positioning context, rendered to
one self-contained mobile HTML and pushed to Telegram.

Why structures, not legs: on the index tape a single desk trade prints as
several rows. Same lot size across two lines in the same instant = a pair (a
vertical, a fly, or a calendar/roll — close near, open far). The per-leg
``aggressor_side`` is which side lifted/hit THAT leg; it is NOT the trade's
direction, and summing it into "bought vs sold" is misleading (you routinely see
a roll print "sell" on both legs). So we cluster by (root, ~timestamp), classify
the structure, and only read direction on genuinely standalone outrights — and
even then flag deep-ITM/financing lines as non-directional.

Reads the LIVE tape via ``ConvexClient.time_and_sales`` (ONE market-wide call)
plus the latest EOD positioning via the ``get_profile`` reader (no extra vendor
calls). Live-only — run during/after RTH (the tape zeroes after 4pm). The pure
builders import nothing from the vendor stack, so they render offline for tests.
Descriptor only (rule 4).

Run:
    python scripts/index_bigtrades_report.py             # build + push to Telegram
    python scripts/index_bigtrades_report.py --no-push   # build only
"""
from __future__ import annotations

import html as _html
from datetime import date
from pathlib import Path

import pandas as pd

# ── config ───────────────────────────────────────────────────────────────────
INDEX_ROOTS: tuple[str, ...] = ("SPX", "SPXW", "SPY", "QQQ")
PREMIUM_FLOOR: dict[str, float] = {"SPX": 250_000, "SPXW": 250_000, "SPY": 100_000, "QQQ": 100_000}
_DEFAULT_OUT = Path("reports") / "index_bigtrades.html"
_CLUSTER_WINDOW_S = 2.0   # legs within this many seconds (same root) = one structure
_VALID_COLS = (
    "time", "symbol", "bid_price", "ask_price", "price", "theo", "size", "value",
    "exchange_sale_conditions", "aggressor_side", "spot", "delta", "gamma",
    "vega", "theta", "volatility",
)
_SWEEP_CODES = frozenset({"I"})            # ISO / intermarket sweep
_BLOCK_CODES = frozenset({"t", "m", "D"})  # negotiated / block prints on the index tape


# ── helpers ──────────────────────────────────────────────────────────────────
def _epoch(t) -> float | None:
    ts = pd.to_datetime(t, errors="coerce", utc=True)
    return None if pd.isna(ts) else ts.value / 1e9


def _dte(expiration, asof: date) -> int | None:
    exp = pd.to_datetime(expiration, errors="coerce")
    return None if pd.isna(exp) else (exp.date() - asof).days


def _tenor(dte: int | None) -> str:
    if dte is None:
        return "?"
    return ("0-1w" if dte <= 7 else "~1m" if dte <= 45 else "3-6m" if dte <= 180
            else "6-12m" if dte <= 365 else "LEAP")


def _is_financing_leg(kind: str, delta, dte: int | None) -> bool:
    """Deep-ITM call (synthetic-long / financing) or long-dated high-delta LEAP."""
    ad = abs(delta) if isinstance(delta, (int, float)) else None
    if ad is None or dte is None or kind != "call":
        return False
    return ad >= 0.85 or (ad >= 0.70 and dte >= 365)


def _fmt_prem(v: float) -> str:
    a = abs(float(v or 0))
    if a >= 1e9:
        return f"${a/1e9:.2f}B"
    if a >= 1e6:
        return f"${a/1e6:.1f}M"
    if a >= 1e3:
        return f"${a/1e3:.0f}K"
    return f"${a:.0f}"


def _sizes_matched(sizes: list[float], tol: float = 0.20) -> bool:
    s = [x for x in sizes if x]
    if len(s) < 2:
        return False
    return (max(s) - min(s)) / max(s) <= tol


# ── the core: cluster legs into structures ───────────────────────────────────
def group_structures(prints: list[dict], asof: date, window_s: float = _CLUSTER_WINDOW_S) -> list[dict]:
    """Cluster index prints by (root, ~timestamp) and classify each cluster.

    Returns a list of structure dicts, each with: kind label, legs, total premium,
    tenor, ``is_multi`` (>=2 legs), ``is_financing`` (structural/neutral), and
    ``is_directional`` (a readable outright).
    """
    rows = []
    for p in prints:
        rows.append({**p, "_t": _epoch(p.get("time")), "_dte": _dte(p.get("expiration"), asof)})
    rows.sort(key=lambda r: (str(r.get("root")), r["_t"] if r["_t"] is not None else 0.0))

    clusters: list[list[dict]] = []
    cur: list[dict] = []
    for r in rows:
        if cur and r.get("root") == cur[-1].get("root") and r["_t"] is not None \
                and cur[-1]["_t"] is not None and abs(r["_t"] - cur[-1]["_t"]) <= window_s:
            cur.append(r)
        else:
            if cur:
                clusters.append(cur)
            cur = [r]
    if cur:
        clusters.append(cur)
    return [_classify_structure(c) for c in clusters]


def _classify_structure(legs: list[dict]) -> dict:
    root = str(legs[0].get("root"))
    exps = sorted({str(l.get("expiration"))[:10] for l in legs})
    strikes = sorted({float(l["strike"]) for l in legs if isinstance(l.get("strike"), (int, float))})
    kinds = {str(l.get("opt_kind")).lower() for l in legs}
    total = sum(float(l.get("premium") or 0) for l in legs)
    dtes = [l["_dte"] for l in legs if l["_dte"] is not None]
    dte_min = min(dtes) if dtes else None
    kpref = "call" if kinds == {"call"} else "put" if kinds == {"put"} else "call/put"
    fin_leg = any(_is_financing_leg(str(l.get("opt_kind")).lower(), l.get("delta"), l["_dte"]) for l in legs)
    matched = _sizes_matched([float(l.get("size") or 0) for l in legs])

    n = len(legs)
    if n == 1:
        stype, label = "outright", None
    elif len(exps) == 1 and len(strikes) >= 2:
        base = {2: "vertical", 3: "fly", 4: "condor"}.get(len(strikes), "spread")
        stype = base
        lohi = "/".join(f"{s:,.0f}" for s in strikes)
        label = f"{lohi} {kpref} {base if base!='vertical' else 'spread'} · {exps[0]}"
    elif len(strikes) == 1 and len(exps) >= 2:
        stype = "calendar"
        label = f"{strikes[0]:,.0f} {kpref} calendar/roll · {exps[0]}→{exps[-1]}"
    elif kinds == {"call", "put"} and len(strikes) >= 2:
        stype = "risk-reversal"
        label = f"{'/'.join(f'{s:,.0f}' for s in strikes)} risk-reversal/collar · {exps[0]}"
    else:
        stype = "combo"
        label = f"{root} combo · {len(legs)} legs"

    # A multi-leg structure with a deep-ITM/financing leg = structural (neutral).
    # A single outright is directional only if near-dated, mid-delta, non-financing.
    is_directional = False
    if n == 1:
        l = legs[0]
        d = l.get("delta")
        ad = abs(d) if isinstance(d, (int, float)) else None
        is_directional = bool(
            not fin_leg and ad is not None and l["_dte"] is not None
            and l["_dte"] <= 365 and 0.15 <= ad <= 0.85
        )
    return {
        "root": root, "type": stype, "label": label, "legs": legs, "n": n,
        "premium": total, "tenor": _tenor(dte_min), "dte": dte_min,
        "expiries": exps, "strikes": strikes, "kinds": kpref,
        "is_multi": n >= 2, "size_matched": matched,
        "is_financing": fin_leg or stype in ("calendar",),
        "is_directional": is_directional,
        "has_sweep": any(str(l.get("condition") or "") in _SWEEP_CODES for l in legs),
    }


def _directional_read(outrights: list[dict]) -> tuple[int, str]:
    """Lean from standalone directional outrights only (side × call/put). Caveated."""
    lean = 0
    for s in outrights:
        l = s["legs"][0]
        side = 1 if str(l.get("aggressor_side")).lower() == "buy" else -1 if str(l.get("aggressor_side")).lower() == "sell" else 0
        cp = 1 if str(l.get("opt_kind")).lower() == "call" else -1
        lean += side * cp
    if not outrights:
        txt = "no standalone directional prints — all big flow is structures/rolls"
    elif lean <= -2:
        txt = "defensive — selling upside calls / buying downside puts (capping & hedging), not a pressed short"
    elif lean < 0:
        txt = "mildly defensive — some upside call supply"
    elif lean == 0:
        txt = "two-sided / mixed"
    elif lean >= 2:
        txt = "offensive — buying upside calls (leaning long)"
    else:
        txt = "mildly offensive"
    return lean, txt


# ── mobile HTML ──────────────────────────────────────────────────────────────
_TEMPLATE = r"""<!DOCTYPE html><html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1">
<title>Index Big Trades</title><style>
:root{--bg:#08090a;--card:#111618;--card2:#0d1214;--edge:#1c2427;--grn:#2fe0a6;--red:#ff5d6a;
--amb:#f4b942;--blu:#5db4ff;--vio:#b493ff;--txt:#e9eef0;--mut:#6c777d;--mono:"SF Mono",ui-monospace,"Roboto Mono",Menlo,monospace;}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--txt);font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
-webkit-font-smoothing:antialiased;display:flex;justify-content:center;padding:14px 11px 40px}
.app{width:100%;max-width:440px}
.num{font-family:var(--mono);font-variant-numeric:tabular-nums;letter-spacing:-.02em}
.head{padding:2px 4px 12px}
.head h1{font-size:19px;font-weight:800;letter-spacing:.3px}
.head .sub{font-size:11.5px;color:var(--mut);margin-top:3px}
.chip{display:inline-block;font-size:9.5px;font-weight:700;letter-spacing:.5px;padding:3px 7px;border-radius:20px;margin-left:6px}
.chip.live{background:rgba(47,224,166,.13);color:var(--grn)}
.card{background:var(--card);border:1px solid var(--edge);border-radius:15px;padding:13px 14px;margin-bottom:10px}
.lbl{font-size:10px;letter-spacing:1.5px;color:var(--mut);font-weight:700;text-transform:uppercase;margin-bottom:9px}
.pos{display:flex;gap:9px}
.pos .c{flex:1;background:var(--card2);border:1px solid var(--edge);border-radius:11px;padding:10px 11px}
.pos .sym{font-size:11px;color:var(--mut);font-weight:700;letter-spacing:.5px}
.pos .rg{font-size:15px;font-weight:800;margin:4px 0 2px}
.pos .rg.long{color:var(--grn)}.pos .rg.short{color:var(--red)}
.pos .mt{font-size:10.5px;color:var(--mut)}.pos .mt b{color:var(--txt);font-family:var(--mono)}
.read{background:linear-gradient(180deg,rgba(93,180,255,.08),rgba(93,180,255,.02));border-color:#26414f}
.read .big{font-size:13.5px;line-height:1.55;color:#dbe9f2}.read .big b{color:#fff}
.tally{display:flex;gap:8px;margin-top:11px}
.tally .t{flex:1;text-align:center;background:var(--card2);border:1px solid var(--edge);border-radius:10px;padding:8px 3px}
.tally .t .v{font-size:17px;font-weight:800;font-family:var(--mono)}
.tally .t .k{font-size:8.5px;color:var(--mut);letter-spacing:.4px;margin-top:2px}
.tally .t .v.vio{color:var(--vio)}.tally .t .v.blu{color:var(--blu)}.tally .t .v.amb{color:var(--amb)}
.st{padding:10px 2px;border-bottom:1px solid #141a1c}.st:last-child{border-bottom:0}
.st .r1{display:flex;justify-content:space-between;align-items:baseline;gap:8px}
.st .nm{font-size:13px;font-weight:700;line-height:1.3}
.st .pr{font-size:13px;font-weight:800;font-family:var(--mono);flex-shrink:0}
.st .r2{margin-top:4px;font-size:10.5px;color:var(--mut)}
.st .legs{margin-top:5px;font-size:10.5px;color:#9aa4a9;font-family:var(--mono);line-height:1.5}
.tag{display:inline-block;font-size:8.5px;font-weight:700;letter-spacing:.4px;padding:2px 5px;border-radius:5px;margin-right:5px}
.tag.spread{background:rgba(180,147,255,.15);color:var(--vio)}
.tag.roll{background:rgba(93,180,255,.14);color:var(--blu)}
.tag.fin{background:rgba(108,119,125,.16);color:#9aa4a9}
.tag.dir{background:rgba(47,224,166,.14);color:var(--grn)}
.tag.sw{background:rgba(244,185,66,.14);color:var(--amb)}
.side{display:inline-block;font-size:9px;font-weight:800;letter-spacing:.5px;padding:2px 6px;border-radius:6px}
.side.buy{background:rgba(47,224,166,.14);color:var(--grn)}.side.sell{background:rgba(255,93,106,.14);color:var(--red)}
.foot{padding:2px 4px;margin-top:6px}.foot p{font-size:10px;color:#5a656a;line-height:1.6;margin-bottom:5px}
</style></head><body><div class="app">__BODY__</div></body></html>"""


def _structure_card(s: dict) -> str:
    tags = ""
    if s["type"] in ("vertical", "fly", "condor", "spread", "risk-reversal", "combo"):
        tags += f'<span class="tag spread">{s["type"].upper()}</span>'
    if s["type"] == "calendar":
        tags += '<span class="tag roll">ROLL</span>'
    if s["is_financing"]:
        tags += '<span class="tag fin">FINANCING · neutral</span>'
    if s["has_sweep"]:
        tags += '<span class="tag sw">SWEEP</span>'
    legs = "<br>".join(
        f'{int(l.get("size") or 0):,}× {int(l["strike"]):,}{"C" if str(l.get("opt_kind")).lower()=="call" else "P"} '
        f'{str(l.get("expiration"))[:10]} · Δ{float(l.get("delta") or 0):.2f} '
        f'<span style="opacity:.6">[{str(l.get("aggressor_side"))[:1].upper()}]</span>'
        for l in s["legs"]
    )
    return (
        f'<div class="st"><div class="r1"><div class="nm">{_html.escape(s["label"] or "")}</div>'
        f'<div class="pr">{_fmt_prem(s["premium"])}</div></div>'
        f'<div class="r2">{s["tenor"]} · {s["n"]} legs'
        f'{" · size-matched pair" if s["size_matched"] else ""}</div>'
        f'<div>{tags}</div><div class="legs">{legs}</div></div>'
    )


def _outright_card(s: dict) -> str:
    l = s["legs"][0]
    side = str(l.get("aggressor_side") or "mid").lower()
    side = side if side in ("buy", "sell") else "mid"
    k = "C" if str(l.get("opt_kind")).lower() == "call" else "P"
    tags = ""
    if s["is_financing"]:
        tags += '<span class="tag fin">FINANCING</span>'
    elif s["is_directional"]:
        tags += '<span class="tag dir">DIRECTIONAL</span>'
    if s["has_sweep"]:
        tags += '<span class="tag sw">SWEEP</span>'
    return (
        f'<div class="st"><div class="r1"><div class="nm">{s["root"]} {int(l["strike"]):,}{k} '
        f'<span style="color:var(--mut);font-weight:600;font-size:11px">· {s["tenor"]} · {str(l.get("expiration"))[:10]}</span></div>'
        f'<div class="pr">{_fmt_prem(s["premium"])}</div></div>'
        f'<div class="r2">{int(l.get("size") or 0):,}× · Δ{float(l.get("delta") or 0):.2f} · '
        f'IV {float(l.get("iv") or 0)*100:.0f}% &nbsp; <span class="side {side}">{side.upper()}?</span></div>'
        f'<div style="margin-top:5px">{tags}</div></div>'
    )


def build_html(prints: list[dict], positioning: dict, *, asof: date, now_str: str, top: int = 22) -> str:
    idx = [p for p in prints if str(p.get("root", "")).upper() in INDEX_ROOTS
           and float(p.get("premium") or 0) >= PREMIUM_FLOOR.get(str(p.get("root")).upper(), 1e9)]
    structures = group_structures(idx, asof)
    structures.sort(key=lambda s: s["premium"], reverse=True)
    multi = [s for s in structures if s["is_multi"]]
    outrights = [s for s in structures if not s["is_multi"]]
    dir_outrights = [s for s in outrights if s["is_directional"]]
    lean, lean_txt = _directional_read(dir_outrights)

    n_spread = sum(1 for s in multi if s["type"] != "calendar")
    n_roll = sum(1 for s in multi if s["type"] == "calendar")

    # positioning strip
    pos_cards = ""
    for sym in ("SPX", "SPY"):
        pz = positioning.get(sym)
        if not pz:
            continue
        longg = pz.get("spot", 0) >= pz.get("flip", 0)
        pos_cards += (
            f'<div class="c"><div class="sym">{sym}</div>'
            f'<div class="rg {"long" if longg else "short"}">{"LONG γ" if longg else "SHORT γ"}</div>'
            f'<div class="mt">flip <b>{pz.get("flip",0):,.0f}</b> · EOD {pz.get("as_of","")}</div></div>'
        )

    read = (
        f'The big dollars are <b>structures</b>, not directional bets — '
        f'<b>{n_spread}</b> spread{"s" if n_spread!=1 else ""} and <b>{n_roll}</b> deep-ITM '
        f'roll{"s" if n_roll!=1 else ""} (financing, directionally neutral). '
        f'The only standalone directional flow ({len(dir_outrights)} print{"s" if len(dir_outrights)!=1 else ""}): {lean_txt}. '
        f'<b>Per-leg buy/sell is which side lifted that leg — not the trade\'s direction; matched sizes = one structure.</b>'
    )

    mcards = "".join(_structure_card(s) for s in multi[:top])
    ocards = "".join(_outright_card(s) for s in outrights[:top])

    body = (
        f'<div class="head"><h1>Index Big Trades <span class="chip live">● STRUCTURES</span></h1>'
        f'<div class="sub">SPX · SPY · QQQ · grouped by structure · {now_str}</div></div>'
        f'<div class="card"><div class="lbl">Dealer positioning · last EOD book</div>'
        f'<div class="pos">{pos_cards}</div></div>'
        f'<div class="card read"><div class="lbl">The read</div><div class="big">{read}</div>'
        f'<div class="tally">'
        f'<div class="t"><div class="v vio">{n_spread}</div><div class="k">SPREADS/FLIES</div></div>'
        f'<div class="t"><div class="v blu">{n_roll}</div><div class="k">ROLLS</div></div>'
        f'<div class="t"><div class="v">{len(outrights)}</div><div class="k">OUTRIGHTS</div></div>'
        f'<div class="t"><div class="v amb">{len(dir_outrights)}</div><div class="k">DIRECTIONAL</div></div>'
        f'</div></div>'
        f'<div class="card"><div class="lbl">Structures · spreads · flies · rolls</div>{mcards or "<div class=r2 style=color:#6c777d>none</div>"}</div>'
        f'<div class="card"><div class="lbl">Outrights · single-leg (side caveated)</div>{ocards or "<div class=r2 style=color:#6c777d>none</div>"}</div>'
        f'<div class="foot">'
        f'<p>Legs clustered by (root, ±2s); matched size = one structure. FINANCING = deep-ITM call (synthetic long) or LEAP. '
        f'ROLL = same strike, two expiries (close near / open far).</p>'
        f'<p>Side shown as <b>[B]/[S]?</b> — OBSERVED aggressor per leg, NOT reliable trade direction. '
        f'Positioning flips from the last EOD get_profile book. Descriptor only — not advice.</p></div>'
    )
    return _TEMPLATE.replace("__BODY__", body)


# ── live fetch + positioning (vendor imports inside; not needed for tests) ────
def _num(v):
    n = pd.to_numeric(v, errors="coerce")
    return float(n) if pd.notna(n) else None


def _fetch_prints(settings, *, limit: int = 300) -> list[dict]:
    from trading_intel.clients.convex import ConvexClient

    df = ConvexClient(settings).time_and_sales(None, limit=limit, orderby="value", cols=_VALID_COLS)
    out: list[dict] = []
    if df is None or df.empty:
        return out
    for _, r in df.iterrows():
        out.append({
            "time": str(r.get("time")), "symbol": r.get("symbol"), "root": r.get("root"),
            "expiration": r.get("expiration"), "strike": _num(r.get("strike")),
            "opt_kind": r.get("opt_kind"), "size": _num(r.get("size")),
            "premium": _num(r.get("premium", r.get("value"))), "condition": r.get("exchange_sale_conditions"),
            "aggressor_side": r.get("aggressor_side"), "spot": _num(r.get("spot")),
            "delta": _num(r.get("delta")), "iv": _num(r.get("iv", r.get("volatility"))),
        })
    return out


def _positioning(settings) -> dict:
    out: dict = {}
    try:
        from trading_intel.mcp import profile_tool as pt
        from trading_intel.memory.db import make_session_factory
        with make_session_factory(settings)() as s:
            for sym in ("SPX", "SPY"):
                p = pt.get_profile(s, sym)
                g = ((p or {}).get("profiles") or {}).get("gamma") or {}
                if p and p.get("found") and g.get("flip") is not None:
                    out[sym] = {"flip": g["flip"], "spot": p.get("spot"), "as_of": p.get("as_of")}
    except Exception:  # noqa: BLE001 — positioning is optional context
        pass
    return out


def build(*, settings=None, out_path: str | None = None, top: int = 22) -> str:
    from trading_intel.config import get_settings
    from trading_intel.timeutils import eastern_now

    settings = settings or get_settings()
    now = eastern_now()
    html = build_html(_fetch_prints(settings), _positioning(settings), asof=now.date(),
                      now_str=now.strftime("as of %Y-%m-%d %H:%M ET"), top=top)
    out = (Path(out_path) if out_path else _DEFAULT_OUT).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    return str(out)


def run(*, settings=None, push: bool = True, top: int = 22) -> str:
    from trading_intel.config import get_settings

    settings = settings or get_settings()
    path = build(settings=settings, top=top)
    if push:
        from trading_intel.clients.telegram import TelegramClient
        TelegramClient(settings).send_document(path, caption="Index big trades — by structure (live tape)")
    return path


def main() -> None:
    import argparse

    import structlog

    ap = argparse.ArgumentParser(description="Index big-trades (structure-grouped) report → Telegram.")
    ap.add_argument("--no-push", action="store_true", help="build only; do not push")
    ap.add_argument("--top", type=int, default=22)
    args = ap.parse_args()
    structlog.configure(processors=[structlog.processors.add_log_level,
                                    structlog.processors.TimeStamper(fmt="iso"),
                                    structlog.processors.JSONRenderer()])
    print("index-bigtrades written:", run(push=not args.no_push, top=args.top))


if __name__ == "__main__":
    main()
