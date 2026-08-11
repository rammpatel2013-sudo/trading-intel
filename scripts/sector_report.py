"""Sector lead/lag + fragility report — one self-contained HTML, pushed to Telegram.

Canonical generator + CLI for the sector report (see MEMORY ``sector-report``).
Mirrors ``scripts/cockpit_report.py``: layout defined once here, the HTML
template INLINED. ``trading_intel.reports.build_sector`` loads this module's
``build()`` so the MCP ``generate_sector_report`` tool produces the identical file.

PHONE RULE (report-deploy-workflow): rendered ENTIRELY SERVER-SIDE — every card,
table, and sparkline is a static HTML/SVG string emitted by Python, NO
client-side <script>, NO CDN — so it opens in Telegram's in-app phone viewer.

Reads the CVForge-fed ``greeks_snapshots`` (SPDRs) + ``sector_snapshots``, and
computes correlation / dispersion / breadth TRENDS from free yfinance history —
NO Convex calls (rule 1). The brain (ranking + LEAP flags) is the pure
``market.sector_scan``. Descriptor only (FlashAlpha rule 4).

Run:
    python scripts/sector_report.py            # build + push to Telegram
    python scripts/sector_report.py --no-push  # build only
"""
from __future__ import annotations

import html as _html
import math
from pathlib import Path

import structlog

log = structlog.get_logger(__name__)

_DEFAULT_OUT = Path("reports") / "sector.html"


# ── formatting + tiny helpers (server-side ports of the old JS) ──────────────
def _finite(x: object) -> bool:
    return isinstance(x, (int, float)) and math.isfinite(x)


def _g(o: object, k: str, d: object = None) -> object:
    return o.get(k, d) if isinstance(o, dict) else d


def _pct(x: object, d: int = 1) -> str:
    return f"{float(x) * 100:.{d}f}%" if _finite(x) else "—"


def _spct(x: object, d: int = 1) -> str:
    if not _finite(x):
        return "—"
    x = float(x)
    return ("+" if x >= 0 else "−") + f"{abs(x) * 100:.{d}f}%"


def _abbr(x: object, d: int = 1) -> str:
    if not _finite(x):
        return "—"
    x = float(x)
    s = "−" if x < 0 else ""
    a = abs(x)
    if a >= 1e9:
        return f"{s}{a / 1e9:.{d}f}B"
    if a >= 1e6:
        return f"{s}{a / 1e6:.{d}f}M"
    if a >= 1e3:
        return f"{s}{a / 1e3:.{d}f}K"
    return f"{s}{a:.{d}f}"


def _esc(s: object) -> str:
    return _html.escape("" if s is None else str(s))


def _spark(vals: object, w: int = 88, h: int = 22, color: str = "#5aa9e6", fill: bool = False) -> str:
    v = [float(x) if _finite(x) else None for x in (vals or [])]
    ok = [x for x in v if x is not None]
    if len(ok) < 2:
        return '<span class="mutv" style="font-size:9.5px">building…</span>'
    mn, mx = min(ok), max(ok)
    rg = (mx - mn) or 1
    n = len(v)
    xy = []
    for i, x in enumerate(v):
        if x is None:
            continue
        xy.append((i / (n - 1) * w, h - 2 - ((x - mn) / rg) * (h - 4)))
    d = " ".join(("L" if i else "M") + f"{px:.1f} {py:.1f}" for i, (px, py) in enumerate(xy))
    lx, ly = xy[-1]
    area = (
        f'<path d="{d} L {lx:.1f} {h} L {xy[0][0]:.1f} {h} Z" fill="{color}" opacity="0.10"/>'
        if fill
        else ""
    )
    return (
        f'<svg width="{w}" height="{h}" viewBox="0 0 {w} {h}" style="vertical-align:middle">'
        f'{area}<path d="{d}" fill="none" stroke="{color}" stroke-width="1.5"/>'
        f'<circle cx="{lx:.1f}" cy="{ly:.1f}" r="1.9" fill="{color}"/></svg>'
    )


def _shift_label(sh: object) -> str:
    if not _finite(sh):
        return '<span class="mutv">— building</span>'
    sh = float(sh)
    v = f"{abs(sh * 100):.2f}"
    if sh < -0.0005:
        return f'<span class="pos">▼ {v} → calls</span>'
    if sh > 0.0005:
        return f'<span class="neg">▲ {v} → puts</span>'
    return '<span class="mutv">flat</span>'


# ── card renderers ───────────────────────────────────────────────────────────
def _gate_card(P: dict) -> str:
    c = _g(P, "correlation", {}) or {}
    t = _g(P, "trends", {}) or {}
    open_ = _g(c, "gate_open")
    lbl = str(_g(c, "regime_label") or "n/a").split(" — ")[0].upper()
    cls = "grn" if open_ else ("red" if lbl == "HIGH" else "amb")
    avg = _g(c, "avg_corr", {}) or {}
    a21, a63 = _g(avg, "21d"), _g(avg, "63d")
    disp = _g(c, "dispersion")
    big = "OPEN — dispersion" if open_ else f"CAUTION — {lbl.lower()} correlation"
    sub = (
        "sectors decoupled — a single-sector LEAP actually isolates that sector"
        if open_
        else "sectors move together — a single-sector LEAP is mostly index beta"
    )
    return (
        '<div class="card"><div class="lbl">Correlation gate · single-sector bets</div>'
        f'<div class="gate"><div><div class="big">{_esc(big)}</div>'
        f'<div class="sub">{sub}</div></div>'
        f'<div class="pill {cls}">{"GO" if open_ else "GATE"}</div></div>'
        '<div class="metrics">'
        f'<div>AVG CORR 21D<b>{"—" if a21 is None else f"{float(a21):.2f}"}</b></div>'
        f'<div>AVG CORR 63D<b>{"—" if a63 is None else f"{float(a63):.2f}"}</b></div>'
        f'<div>DISPERSION<b>{"—" if disp is None else f"{float(disp) * 100:.2f}"}</b></div></div>'
        '<div class="sparks">'
        f'<div><span>corr 21d</span>{_spark(_g(t, "corr21"), color="#5aa9e6")}</div>'
        f'<div><span>corr 63d</span>{_spark(_g(t, "corr63"), color="#8a7fe0")}</div>'
        f'<div><span>dispersion</span>{_spark(_g(t, "dispersion"), color="#f4b942")}</div>'
        "</div></div>"
    )


def _internals_card(P: dict) -> str:
    i = _g(P, "internals", {}) or {}
    if not _g(i, "n"):
        return ""
    healthy = _g(i, "healthy")
    col = "amb" if healthy is None else ("grn" if healthy else "red")
    trend = _g(i, "trend") or []
    spk = ""
    if [x for x in trend if x is not None]:
        c = "#ff5d6a" if col == "red" else ("#2fe0a6" if col == "grn" else "#f4b942")
        ndays = len([x for x in trend if x is not None])
        spk = (
            f'<div class="sparks"><div><span>% sectors up · {ndays}d</span>'
            f"{_spark(trend, color=c, fill=True)}</div></div>"
        )
    idx_up = _g(i, "index_up")
    idx_txt = "—" if idx_up is None else ("up" if idx_up else "down")
    pill = "MIXED" if healthy is None else ("HEALTHY" if healthy else "FRAGILE")
    return (
        '<div class="card"><div class="lbl">Market internals · sector breadth vs index</div>'
        f'<div class="gate"><div><div class="big">{_g(i, "n_up", 0)}/{_g(i, "n", 0)} sectors up '
        f'<span class="mutv" style="font-weight:400">· SPY {idx_txt}</span></div>'
        f'<div class="sub">{_esc(_g(i, "divergence", ""))}</div></div>'
        f'<div class="pill {col}">{pill}</div></div>{spk}</div>'
    )


def _banner(P: dict) -> str:
    lead = " · ".join(_g(P, "leaders", []) or []) or "—"
    lag = " · ".join(_g(P, "laggards", []) or []) or "—"
    return (
        '<div class="card" style="display:flex;gap:14px">'
        '<div style="flex:1"><div class="lbl" style="margin-bottom:5px">Leaders</div>'
        f'<div class="pos" style="font-weight:700">{_esc(lead)}</div></div>'
        '<div style="flex:1"><div class="lbl" style="margin-bottom:5px">Laggards</div>'
        f'<div class="neg" style="font-weight:700">{_esc(lag)}</div></div></div>'
    )


def _howto_card(P: dict) -> str:
    gate = _g(_g(P, "correlation", {}) or {}, "gate_open")
    gtxt = "OPEN" if gate else "gated"
    gcls = "pos" if gate else "neg"
    return (
        '<div class="card how"><div class="lbl">How to read this → finding a LEAP-long</div><ol>'
        f'<li><b>Gate first.</b> The correlation gate must be OPEN (low / dispersion) or a single-sector '
        f'LEAP is really just index beta. Right now: <b class="{gcls}">{gtxt}</b>.</li>'
        "<li><b>Pick from the leaders.</b> Long-gamma (stable) sector, above its gamma flip (cushion "
        "under spot), positive 21-day momentum — the top of the ranking.</li>"
        "<li><b>Buy cheap vega.</b> A LEAP is long vega, so favour a LOW ATM-IV percentile — you want "
        "implied vol cheap at entry.</li>"
        "<li><b>The skew shift is the trigger.</b> Watch the 25Δ RR rotate from put-rich toward the call "
        "side (▼ falling) — that's real money starting to bid calls, early accumulation, the LEAP-call "
        "window. Rising RR (▲, fear bidding puts) = wait.</li>"
        "<li><b>Confirm at the wall.</b> The call wall above spot is the target / resistance. If "
        "fixed-strike vol there is OFFERED the wall pins (favour call spreads into it); if it's BID the "
        "level is set to break (favour straight calls).</li></ol>"
        '<div class="empty">These are descriptor flags, not a signal — size and pick the actual contract '
        "in your validated strategy layer.</div></div>"
    )


def _iv_cell(s: dict) -> str:
    ivp = _g(s, "iv_pctile")
    if ivp is None:
        atm = _g(s, "atm_iv")
        return f'<td>{"—" if atm is None else _pct(atm)}<span class="mutv"> ·—</span></td>'
    ivp = float(ivp)
    cls = "pos" if ivp < 0.35 else ("neg" if ivp > 0.7 else "mutv")
    return f'<td>{_pct(_g(s, "atm_iv"))} <span class="{cls}">{round(ivp * 100)}p</span></td>'


def _table_card(P: dict) -> str:
    rows = []
    for s in _g(P, "sectors", []) or []:
        if _g(s, "gamma_regime") is None:
            rows.append(
                f'<tr><td class="l">{_g(s, "rank", "")}</td>'
                f'<td class="l sym">{_esc(_g(s, "symbol"))}</td>'
                '<td class="l"><span class="tag na">pending</span></td>'
                '<td colspan="4" class="mutv" style="text-align:left">awaiting CVForge snapshot</td>'
                '<td><span class="setup na">n/a</span></td></tr>'
            )
            continue
        stab = _g(s, "stability") or "na"
        stab_txt = "long γ" if stab == "stable" else ("short γ" if stab == "fragile" else "—")
        mom = _g(s, "ret_21d")
        momc = "mutv" if mom is None else ("pos" if float(mom) >= 0 else "neg")
        cush = _g(s, "gflip_cushion")
        cushc = "mutv" if cush is None else ("pos" if float(cush) >= 0 else "neg")
        lead_score = _g(s, "lead_score")
        leap = _g(s, "leap") or None
        setup = _g(leap, "setup") if leap else "na"
        rows.append(
            "<tr>"
            f'<td class="l">{_g(s, "rank", "")}</td>'
            f'<td class="l sym">{_esc(_g(s, "symbol"))}</td>'
            f'<td class="l"><span class="tag {stab}">{stab_txt}</span></td>'
            f'<td class="{cushc}">{_spct(cush)}</td>'
            f"{_iv_cell(s)}"
            f'<td class="{momc}">{_spct(mom)}</td>'
            f'<td>{"—" if lead_score is None else f"{float(lead_score):.2f}"}</td>'
            f'<td><span class="setup {setup or "na"}">{setup or "n/a"}</span></td></tr>'
        )
    return (
        '<div class="card"><div class="lbl">Lead → lag · fragility ranking</div>'
        '<table><thead><tr><th class="l">#</th><th class="l">ETF</th><th class="l">γ regime</th>'
        '<th>flip cush</th><th>ATM IV·pct</th><th>21d</th><th>score</th><th>LEAP</th>'
        f'</tr></thead><tbody>{"".join(rows)}</tbody></table></div>'
    )


def _walls_card(P: dict) -> str:
    priced = [s for s in (_g(P, "sectors", []) or []) if _g(s, "gamma_regime") is not None]
    if not priced:
        return ""
    # sort by rr25_shift ascending (most call-side / most negative first), None last
    priced = sorted(priced, key=lambda s: (_g(s, "rr25_shift") is None, _g(s, "rr25_shift") if _finite(_g(s, "rr25_shift")) else 0.0))

    def wdist(w: object, spot: object) -> str:
        if w is None or not spot:
            return ""
        r = float(w) / float(spot) - 1
        return f' <span class="mutv">{"+" if r >= 0 else "−"}{abs(r) * 100:.1f}%</span>'

    items = []
    for s in priced:
        rr = _g(s, "rr25")
        rrt = "—" if rr is None else (("+" if float(rr) >= 0 else "−") + f"{abs(float(rr)) * 100:.2f}")
        rrc = "mutv" if rr is None else ("neg" if float(rr) >= 0 else "pos")
        fp = _g(s, "footprint", {}) or {}
        if _g(fp, "pending"):
            fr = '<span class="mutv">fx: 2nd day</span>'
        else:
            read = str(_g(fp, "read") or "")
            if not read:
                fr = ""
            elif "HOLD" in read:
                fr = '<span class="pos">fx: offered · hold</span>'
            elif "BREAK" in read:
                fr = '<span class="neg">fx: bid · break</span>'
            else:
                fr = '<span class="mutv">fx: mixed</span>'
        sh = _g(s, "rr25_shift")
        skcol = "#2fe0a6" if (_finite(sh) and float(sh) < 0) else ("#ff5d6a" if (_finite(sh) and float(sh) > 0) else "#6c777d")
        cw, pw, spot = _g(s, "call_wall"), _g(s, "put_wall"), _g(s, "spot")
        items.append(
            '<div class="skrow"><div class="skh">'
            f'<span class="sym">{_esc(_g(s, "symbol"))}</span>'
            f'<span class="{rrc}" style="font-family:var(--mono)">RR {rrt}</span>'
            f"{_shift_label(sh)}</div>"
            f'<div class="skmid">{_spark(_g(s, "rr25_trend"), w=130, h=24, color=skcol)}</div>'
            f'<div class="skf"><span class="mutv">walls</span> '
            f'{"—" if cw is None else round(float(cw))}{wdist(cw, spot)} '
            f'<span class="mutv">/</span> {"—" if pw is None else round(float(pw))}{wdist(pw, spot)} · {fr}</div></div>'
        )
    return (
        '<div class="card"><div class="lbl">Skew shift · put ↔ call rotation '
        '<span style="color:#3a4448;font-weight:400;letter-spacing:0;text-transform:none">'
        f'· most call-side first</span></div>{"".join(items)}'
        '<div class="empty" style="padding-top:8px">25Δ RR = put IV − call IV (+ puts richer / fear). '
        "A FALLING RR (▼ → calls) = demand rotating to the call side = the bullish LEAP-call tell; rising "
        "(▲ → puts) = defensive. Walls = peak gamma-OI strike. fx = fixed-strike vol offered (hold) / bid "
        "(break); the shift + fx fill on the 2nd day of history.</div></div>"
    )


def _cand_card(P: dict) -> str:
    cands = [
        s
        for s in (_g(P, "sectors", []) or [])
        if _g(s, "leap") and _g(_g(s, "leap"), "setup") == "candidate"
    ]
    if not cands:
        return (
            '<div class="card"><div class="lbl">LEAP-long candidates</div>'
            '<div class="empty">No sector clears the candidate bar right now (needs a stable/long-gamma '
            "tape, ≥2 supporting reads, and the dispersion gate open). Watch the table above.</div></div>"
        )
    items = []
    for s in cands:
        leap = _g(s, "leap", {}) or {}
        fors = "".join(
            f'<div class="r"><span class="k">+</span> {_esc(r)}</div>' for r in (_g(leap, "for") or [])
        )
        ags = "".join(
            f'<div class="r ag"><span class="k">−</span> {_esc(r)}</div>' for r in (_g(leap, "against") or [])
        )
        items.append(
            '<div class="item"><div class="hd">'
            f'<div class="s">{_esc(_g(s, "symbol"))} '
            f'<span class="mutv" style="font-size:11px;font-weight:400">#{_g(s, "rank")}</span></div>'
            f'<span class="setup candidate">candidate</span></div>{fors}{ags}</div>'
        )
    return (
        '<div class="card"><div class="lbl">LEAP-long candidates · context, not a signal</div>'
        f'<div class="cand">{"".join(items)}</div></div>'
    )


_STYLE = r"""<style>
  :root{
    --bg:#08090a; --card:#111618; --card2:#0d1214; --edge:#1c2427;
    --grn:#2fe0a6; --grn-dim:#1c8e6c; --red:#ff5d6a; --red-dim:#9e3540;
    --amb:#f4b942; --blu:#5aa9e6; --vio:#8a7fe0; --txt:#e9eef0; --mut:#6c777d;
    --mono:"SF Mono",ui-monospace,"Roboto Mono",Menlo,monospace;
  }
  *{box-sizing:border-box;margin:0;padding:0}
  body{background:var(--bg);color:var(--txt);
    font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
    -webkit-font-smoothing:antialiased;display:flex;justify-content:center;padding:16px 12px 40px}
  .app{width:100%;max-width:520px}
  .num{font-family:var(--mono);font-variant-numeric:tabular-nums;letter-spacing:-.02em}
  .top{display:flex;align-items:baseline;justify-content:space-between;padding:2px 4px 12px}
  .top h1{font-size:17px;font-weight:700;letter-spacing:.2px}
  .top .as{font-size:11px;color:var(--mut)}

  .card{background:var(--card);border:1px solid var(--edge);border-radius:16px;padding:14px 15px;margin-bottom:10px}
  .lbl{font-size:10.5px;letter-spacing:1.4px;color:var(--mut);font-weight:700;text-transform:uppercase;margin-bottom:9px}
  .gate{display:flex;align-items:center;justify-content:space-between;gap:12px}
  .gate .big{font-size:16px;font-weight:700}
  .gate .sub{font-size:11.5px;color:#9aa4a9;margin-top:2px;line-height:1.4}
  .pill{font-size:10px;font-weight:700;letter-spacing:.6px;padding:5px 10px;border-radius:20px;white-space:nowrap}
  .pill.grn{background:rgba(47,224,166,.13);color:var(--grn)}
  .pill.red{background:rgba(255,93,106,.13);color:var(--red)}
  .pill.amb{background:rgba(244,185,66,.14);color:var(--amb)}
  .metrics{display:flex;gap:14px;margin-top:12px;padding-top:11px;border-top:1px solid var(--edge)}
  .metrics div{font-size:11px;color:var(--mut)}
  .metrics b{display:block;font-size:14px;color:var(--txt);margin-top:3px;font-family:var(--mono)}

  .sparks{display:flex;gap:12px;margin-top:12px;padding-top:11px;border-top:1px solid var(--edge)}
  .sparks>div{flex:1;display:flex;flex-direction:column;gap:3px}
  .sparks span{font-size:9px;letter-spacing:.5px;color:var(--mut);text-transform:uppercase}

  table{width:100%;border-collapse:collapse;margin-top:2px}
  th{font-size:9.5px;letter-spacing:.5px;color:var(--mut);font-weight:700;text-align:right;padding:4px 5px;border-bottom:1px solid var(--edge)}
  th.l,td.l{text-align:left}
  td{font-size:12px;padding:7px 5px;border-bottom:1px solid #131a1c;font-family:var(--mono)}
  tr:last-child td{border-bottom:none}
  .sym{font-weight:700;font-family:-apple-system,sans-serif}
  .tag{font-size:9px;font-weight:700;letter-spacing:.4px;padding:2px 6px;border-radius:6px}
  .tag.stable{background:rgba(47,224,166,.13);color:var(--grn)}
  .tag.fragile{background:rgba(255,93,106,.13);color:var(--red)}
  .tag.na{background:#161d20;color:var(--mut)}
  .setup{font-size:9px;font-weight:700;letter-spacing:.4px;padding:2px 7px;border-radius:20px}
  .setup.candidate{background:rgba(47,224,166,.15);color:var(--grn)}
  .setup.watch{background:rgba(90,169,230,.14);color:var(--blu)}
  .setup.avoid{background:rgba(255,93,106,.14);color:var(--red)}
  .setup.na{background:#161d20;color:var(--mut)}
  .pos{color:var(--grn)}.neg{color:var(--red)}.mutv{color:var(--mut)}

  .how ol{margin:2px 0 8px 0;padding-left:18px}
  .how li{font-size:11.5px;color:#c7d0d3;line-height:1.55;margin-bottom:7px}
  .how li b{color:var(--txt)}

  .skrow{padding:9px 2px;border-bottom:1px solid #131a1c}
  .skrow:last-of-type{border-bottom:none}
  .skh{display:flex;align-items:center;gap:10px;font-size:13px;flex-wrap:wrap}
  .skh .sym{font-weight:700}
  .skmid{margin:5px 0 4px}
  .skf{font-size:11px;color:#9aa4a9;font-family:var(--mono)}

  .cand{margin-top:2px}
  .cand .item{background:var(--card2);border:1px solid #22333a;border-radius:11px;padding:10px 12px;margin-bottom:8px}
  .cand .hd{display:flex;align-items:center;justify-content:space-between;margin-bottom:6px}
  .cand .hd .s{font-weight:700}
  .cand .r{font-size:11px;color:#9aa4a9;line-height:1.5;margin:1px 0}
  .cand .r .k{color:var(--grn-dim);font-weight:700}
  .cand .r.ag .k{color:var(--red-dim)}
  .foot{margin-top:12px;padding:0 4px}
  .foot p{font-size:10.5px;color:#5a656a;line-height:1.6;margin-bottom:5px}
  .empty{font-size:11.5px;color:var(--mut);padding:8px 2px;line-height:1.5}
</style>"""


def _render_html_str(P: dict) -> str:
    as_of = str(_g(P, "as_of") or "").replace("T", " ")
    m = _g(P, "meta", {}) or {}
    n_sec = _g(m, "n_sectors") or len(_g(P, "sectors", []) or [])
    footmeta = (
        f'{_g(m, "n_priced", 0)}/{n_sec} sectors priced · source {_esc(_g(m, "source", "—"))} '
        f'· correlation as of {_esc(_g(_g(P, "correlation", {}) or {}, "as_of", "—"))}'
    )
    body = (
        _gate_card(P)
        + _internals_card(P)
        + _banner(P)
        + _howto_card(P)
        + _table_card(P)
        + _walls_card(P)
        + _cand_card(P)
    )
    return (
        '<!DOCTYPE html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1">\n'
        "<title>Sector Lead / Lag · Fragility</title>\n"
        f"{_STYLE}\n</head>\n<body>\n<div class=\"app\">\n"
        f'<div class="top"><h1>Sector Lead / Lag</h1><div class="as">as of {_esc(as_of)}</div></div>\n'
        f'<div class="body">{body}</div>\n'
        '<div class="foot">'
        f"<p>{footmeta}</p>"
        "<p><b>Descriptor only.</b> Lead/lag &amp; fragility read + LEAP-setup FLAGS with rationale — not "
        "a trade signal. Long gamma = dealers dampen (stable); short gamma = dealers amplify (fragile). "
        "Correlation is the go / no-go gate: low = single-sector bets diversify; high = index beta. Actual "
        "LEAP selection stays in the validated strategy layer.</p></div>\n</div>\n</body>\n</html>\n"
    )


def _render_html(payload: dict, out_path: str | None) -> str:
    html = _render_html_str(payload)
    out = (Path(out_path) if out_path else _DEFAULT_OUT).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    return str(out)


def build(*, out_path: str | None = None, settings: object = None, session: object = None) -> str:
    """Assemble the sector payload from the DB and render it into one HTML file.

    Rendered entirely server-side (no <script>) so it opens on the phone. Opens
    its own session unless one is passed. Returns the absolute path.
    """
    from trading_intel.api.sector import build_sector
    from trading_intel.config import get_settings

    settings = settings or get_settings()
    if session is not None:
        payload = build_sector(session, settings)
    else:
        from trading_intel.memory.db import make_session_factory

        with make_session_factory(settings)() as s:
            payload = build_sector(s, settings)
    return _render_html(payload, out_path)


def run(*, push: bool = True, settings: object = None) -> str:
    """Build the sector report and (optionally) push it to Telegram. Returns the path."""
    from trading_intel.config import get_settings

    settings = settings or get_settings()
    path = build(settings=settings)
    if push:
        from trading_intel.clients.telegram import TelegramClient

        sent = TelegramClient(settings).send_document(path, caption="Sector lead/lag + fragility")
        log.info("sector_report.pushed", path=path, telegram_sent=sent)
    return path


def main() -> None:
    """Manual/scheduled entrypoint: build the sector report and push it to Telegram."""
    import argparse

    parser = argparse.ArgumentParser(description="Build the sector lead/lag + fragility report.")
    parser.add_argument("--no-push", action="store_true", help="build only; do not push to Telegram")
    args = parser.parse_args()

    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ]
    )
    path = run(push=not args.no_push)
    print(f"sector report written: {path}")


if __name__ == "__main__":
    main()
