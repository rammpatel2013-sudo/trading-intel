"""Render the daily brief context dict into a self-contained HTML page.

Pure presentation: takes the plain dict produced by
``trading_intel.synthesis.daily_brief.build_brief_context`` and returns an
HTML string. No DB, no I/O — trivially unit-testable with a mock context.
Inline SVG only (no CDN charts — they don't paint in headless/artifact views);
light theme for the persisted artifact. Descriptive regime context only
(FlashAlpha rule 4).
"""

from __future__ import annotations

import html as _html
from typing import Any

# ── inline-SVG helpers ─────────────────────────────────────────────────


def _spark_points(vals: list[float | None], *, w: float = 58, h: float = 18, pad: float = 2) -> str:
    """Polyline ``points`` string for a sparkline over the value series."""
    nums = [v for v in vals if v is not None]
    if len(nums) < 2:
        return ""
    lo, hi = min(nums), max(nums)
    span = (hi - lo) or 1.0
    n = len(vals)
    step = (w - 2 * pad) / (n - 1)
    pts: list[str] = []
    for i, v in enumerate(vals):
        if v is None:
            continue
        x = pad + i * step
        y = pad + (1 - (v - lo) / span) * (h - 2 * pad)
        pts.append(f"{x:.1f},{y:.1f}")
    return " ".join(pts)


def _spark(vals: list[float | None], color: str) -> str:
    pts = _spark_points(vals)
    if not pts:
        return '<span class="cap">—</span>'
    return (
        f'<svg class="spark" width="58" height="18" aria-hidden="true">'
        f'<polyline points="{pts}" fill="none" stroke="{color}" stroke-width="1.6"/></svg>'
    )


def _direction(series: list[float | None]) -> tuple[str, str]:
    """(arrow, css_class) for the first→last drift of a numeric series."""
    nums = [v for v in series if v is not None]
    if len(nums) < 2:
        return "", "flat"
    delta = nums[-1] - nums[0]
    band = (abs(nums[0]) or 1.0) * 0.001
    if delta < -band:
        return "▼", "dn"
    if delta > band:
        return "▲", "up"
    return "▬", "flat"


def _esc(v: Any) -> str:
    return _html.escape("" if v is None else str(v))


def _fmt(v: float | None, digits: int = 0) -> str:
    if v is None:
        return "—"
    return f"{v:,.{digits}f}"


def _pct(v: float | None, digits: int = 1) -> str:
    if v is None:
        return "—"
    sign = "+" if v >= 0 else ""
    return f"{sign}{v:.{digits}f}%"


# ── section renderers ──────────────────────────────────────────────────


def _row_index(ix: dict[str, Any]) -> str:
    flip_arrow, flip_cls = _direction(ix.get("flip_series") or [])
    vf = ix.get("spot_vs_flip_pct")
    below = vf is not None and vf < 0
    pill_cls = "pill-dn" if below else "pill-up"
    pill_arrow = "▼" if below else "▲"
    regime = ix.get("regime") or ""
    reg_cls = "t-short" if "short" in regime.lower() else "t-long" if "long" in regime.lower() else "t-mid"
    flip_txt = _fmt(ix.get("flip"), 0 if (ix.get("flip") or 0) > 100 else 1)
    spot_txt = _fmt(ix.get("spot"), 0 if (ix.get("spot") or 0) > 100 else 1)
    if vf is None:
        vf_cell = '<span class="pill pill-mid">n/a</span>'
    else:
        vf_cell = f'<span class="pill {pill_cls}">{pill_arrow} {_pct(vf)}</span>'
    return (
        "<tr>"
        f'<td class="d"><span class="sym">{_esc(ix.get("symbol"))}</span> '
        f'<span class="dim">{_esc(ix.get("asof"))}</span></td>'
        f"<td>{spot_txt}</td>"
        f'<td class="d"><span class="flipval">{flip_txt}</span> '
        f'{_spark(ix.get("flip_series") or [], "#c0392b")}'
        f'<span class="cap arw-{flip_cls}">flip {flip_arrow}</span></td>'
        f'<td class="c">{vf_cell}</td>'
        f'<td class="d"><span class="tag {reg_cls}">{_esc(regime)}</span></td>'
        f'<td class="d">{_spark(ix.get("gex_series") or [], "#1f7ae0")}'
        f'<span class="cap">net GEX</span></td>'
        "</tr>"
    )


def _vix_oi_caption(vix: dict[str, Any]) -> str:
    """Peak put / call OI strikes, described by where they sit relative to spot.

    The old caption read "floor 17 · wall 20" with VIX at 14.32 — calling a
    strike 2.7 points ABOVE spot a floor. These are peak-OI strikes, not a
    bracket, so they are named for what they are.
    """
    lvl, put, call = vix.get("vix"), vix.get("floor"), vix.get("call_wall")
    bits = []
    if put is not None:
        where = "below" if (lvl is not None and put < lvl) else "above"
        bits.append(f"peak put OI {_fmt(put,0)} ({where} spot)")
    if call is not None:
        bits.append(f"peak call OI {_fmt(call,0)}")
    return " · ".join(bits) or "no VIX chain stored"


def _index_board(ctx: dict[str, Any]) -> str:
    rows = "".join(_row_index(ix) for ix in ctx.get("indices", []))
    vix = ctx.get("vix") or {}
    vix_row = (
        "<tr>"
        '<td class="d"><span class="sym">VIX</span> <span class="dim">'
        f'{_esc(vix.get("asof"))}</span></td>'
        f'<td>{_fmt(vix.get("vix"), 2)}</td>'
        '<td class="d dim">n/a — vol index'
        f'<span class="cap">{_vix_oi_caption(vix)}</span></td>'
        '<td class="c dim">—</td>'
        f'<td class="d"><span class="tag t-mid">call-weighted OI '
        f'{_pct((vix.get("call_oi_share") or 0)*100,0)}</span></td>'
        f'<td class="d">VVIX <b>{_fmt(vix.get("vvix"),1)}</b></td>'
        "</tr>"
    )
    return f"""<h2 class="sec"><span class="n">01</span>Index Gamma Board — flip, spot-vs-flip &amp; trend</h2>
<div class="card"><table>
<thead><tr><th class="d">Index</th><th>Spot</th><th class="d">Zero-γ flip &amp; trend</th>
<th class="c">Spot vs flip</th><th class="d">Regime</th><th class="d">Net-GEX (γ) trend</th></tr></thead>
<tbody>{rows}{vix_row}</tbody></table>
<div class="legend">
<span><i class="sw" style="background:#c0392b"></i> flip trend</span>
<span><i class="sw" style="background:#1f7ae0"></i> net-GEX trend</span>
<span><i class="pill pill-up">▲</i> spot&gt;flip = long γ</span>
<span><i class="pill pill-dn">▼</i> below = short γ</span></div>
<div class="note">{_esc(ctx.get("board_note") or "")}</div></div>"""


def _declutter(ys: list[float], *, gap: float = 12.0, lo: float = 14.0, hi: float = 288.0) -> list[float]:
    """Push overlapping label y-positions apart, preserving order.

    The ladder is linearly scaled, so on a pinned day the put wall, the flip and
    spot can all land inside 12px while the call wall sits 250px away — three
    labels printed on top of each other. The LINES stay at their true y (the
    geometry must not lie); only the text is nudged, and a leader connects them.
    """
    order = sorted(range(len(ys)), key=lambda i: ys[i])
    out = list(ys)
    prev = -1e9
    for i in order:
        v = max(out[i], prev + gap, lo)
        out[i] = v
        prev = v
    # If the nudging ran past the bottom, pull the whole stack back up.
    over = max(out, default=0) - hi
    if over > 0:
        shift = min(over, min(out, default=0) - lo)
        if shift > 0:
            out = [v - shift for v in out]
    return out


def _doc_ladder_svg(doc: dict[str, Any]) -> str:
    """Vertical SPX level ladder scaled to the day's levels (pure SVG)."""
    flip = doc.get("flip")
    spot = doc.get("spot")
    cw = doc.get("call_wall")
    em_hi, em_lo = doc.get("em_hi"), doc.get("em_lo")
    pw = doc.get("put_wall")
    pts = [p for p in (flip, spot, cw, em_hi, em_lo, pw) if p]
    if len(pts) < 2 or flip is None or spot is None:
        return '<div class="note dim">Level ladder unavailable — need flip + spot.</div>'
    top = max(pts) * 1.004
    bot = min(pts) * 0.996
    rng = (top - bot) or 1.0
    H, y0, y1 = 300.0, 18.0, 282.0

    def y(price: float) -> float:
        return y0 + (top - price) / rng * (y1 - y0)

    # (price, colour, width, dash, label, label-colour) in draw order.
    items: list[tuple[float, str, float, str, str, str]] = []
    if cw:
        items.append((cw, "#2e9e5b", 1.4, "", f"call wall {cw:,.0f} · resistance", "#2e9e5b"))
    if pw and not (cw and abs(pw - cw) / (cw or 1) <= 0.002):
        items.append((pw, "#8a93a0", 1.0, "3 2", f"put wall {pw:,.0f}", "#8a93a0"))
    items.append((flip, "#0e8f9c", 2.4, "7 3", f"◄ ZERO-γ FLIP {flip:,.0f}", "#0e8f9c"))
    items.append((spot, "#111", 1.6, "", f"● SPOT {spot:,.0f}", "#111"))
    if em_hi:
        items.append((em_hi, "#8e6fd0", 1.0, "2 3", f"EM+ {em_hi:,.0f}", "#8e6fd0"))
    if em_lo:
        items.append((em_lo, "#8e6fd0", 1.0, "2 3", f"EM− {em_lo:,.0f}", "#8e6fd0"))

    true_y = [y(p) for p, *_ in items]
    lab_y = _declutter(true_y)

    parts = ['<svg class="ladder" viewBox="0 0 540 300" role="img" aria-label="SPX level ladder">']
    if cw:
        parts.append(
            f'<rect x="60" y="{y(cw):.1f}" width="300" height="{max(2,y(cw*0.995)-y(cw)):.1f}" '
            f'fill="#2e9e5b" opacity="0.10"/>'
        )
    parts.append(
        f'<rect x="60" y="{y(flip):.1f}" width="300" height="{y1-y(flip):.1f}" '
        f'fill="#c0392b" opacity="0.05"/>'
    )
    parts.append(f'<line x1="60" y1="{y0}" x2="60" y2="{y1}" stroke="#d5dbe3" stroke-width="1"/>')
    for (price, color, wide, dash, label, lc), ty, ly in zip(items, true_y, lab_y):
        d = f' stroke-dasharray="{dash}"' if dash else ""
        parts.append(
            f'<line x1="60" y1="{ty:.1f}" x2="360" y2="{ty:.1f}" stroke="{color}" '
            f'stroke-width="{wide}"{d}/>'
        )
        if abs(ly - ty) > 1.5:  # leader from the line to its nudged label
            parts.append(
                f'<path d="M360 {ty:.1f} L364 {ty:.1f} L364 {ly:.1f}" fill="none" '
                f'stroke="{color}" stroke-width="0.7" opacity="0.55"/>'
            )
        parts.append(
            f'<text x="368" y="{ly+3:.1f}" font-size="10.5" fill="{lc}">{_esc(label)}</text>'
            f'<text x="54" y="{ty+3:.1f}" font-size="9.5" fill="#8a93a0" text-anchor="end">'
            f'{price:,.0f}</text>'
        )
    parts.append("</svg>")
    return f'<div class="ladderwrap">{"".join(parts)}</div>'


_GX_COLOR = {
    "quiet_unwind": "#d1495b",
    "confirmed": "#e08a1e",
    "gex_drop": "#e08a1e",
    "rebuild": "#2f9e6f",
    "base": "#93a0b3",
}
_GX_LABEL = {
    "quiet_unwind": "QUIET UNWIND",
    "confirmed": "CONFIRMED DROP",
    "gex_drop": "GEX DROP",
    "rebuild": "REBUILD",
    "base": "BASE",
}


def _gex_transition_html(ctx: dict[str, Any]) -> str:
    """Dealer-gamma "quiet unwind" state block for the Doc section (self-contained)."""
    gx = ctx.get("gex_transition")
    if not gx:
        return ""
    st = gx.get("state") or "base"
    col = _GX_COLOR.get(st, "#93a0b3")
    lab = _GX_LABEL.get(st, "BASE")
    z, div, ng, over = gx.get("d_gex_z"), gx.get("d_iv_pt"), gx.get("net_gex"), gx.get("over_pct")
    zt = f"{z:+.1f}σ" if isinstance(z, (int, float)) else "—"
    dvt = f"{div:+.2f}pt" if isinstance(div, (int, float)) else "—"
    ngt = f"{ng:.0f}" if isinstance(ng, (int, float)) else "—"
    ovt = f"+{over:.1f}% &gt; flip" if isinstance(over, (int, float)) else ""
    cells = ""
    for c in gx.get("strip", []):
        cc = _GX_COLOR.get(c.get("state"), "#93a0b3")
        g = c.get("gex")
        gt = f"{g:.0f}" if isinstance(g, (int, float)) else "—"
        cells += (
            f'<div style="flex:1;text-align:center;border:1px solid #e6e9ef;border-top:3px solid {cc};'
            f'border-radius:6px;padding:4px 2px;min-width:0">'
            f'<div style="font-size:8.5px;color:#9aa3b2">{_esc(c.get("d"))}</div>'
            f'<div style="font-size:11px;font-weight:800;color:{cc}">{gt}</div></div>'
        )
    if gx.get("firing"):
        read = (
            "Fast GEX drop with IV pinned — the quiet unwind the backtest flags bearish."
            if st == "quiet_unwind"
            else "Fast GEX move — watch closely (IV confirming or ambiguous)."
        )
    else:
        read = (
            "No quiet-unwind. Slow bleed = noise; watch for a fast net-GEX drop "
            "(ΔGEX ≤ −1.5σ) while IV stays pinned."
        )
    return (
        f'<div style="margin-top:10px;border:1px solid #e6e9ef;border-left:4px solid {col};'
        f'border-radius:8px;padding:9px 11px;background:#fafbfc">'
        f'<div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">'
        f'<b style="color:{col};font-size:13px">GEX transition: {_esc(lab)}</b>'
        f'<span style="font-size:9px;font-weight:800;color:#fff;background:#2f6df0;border-radius:4px;'
        f'padding:1px 5px">NEW</span>'
        f'<span style="margin-left:auto;font-size:11px;color:#55607a">ΔGEX <b>{zt}</b> · '
        f'ΔIV <b>{dvt}</b> · net <b>{ngt}</b> {("· " + ovt) if ovt else ""}</span></div>'
        f'<div style="display:flex;gap:3px;margin-top:7px">{cells}</div>'
        f'<div style="font-size:11.5px;color:#55607a;margin-top:6px">{read}</div></div>'
    )


def _vol_skew_chips(ctx: dict[str, Any]) -> str:
    """Compact skew / dispersion chips for the vol section."""
    vs = ctx.get("vol_skew")
    if not vs:
        return ""

    def _pctile(x: Any) -> str:
        return f"{int(round(x * 100))}%ile" if isinstance(x, (int, float)) else "—"

    parts = []
    if vs.get("rr_pctile") is not None:
        parts.append(f'<div class="chip"><span class="lab">25Δ skew</span><b>{_pctile(vs.get("rr_pctile"))}</b></div>')
    if vs.get("cor1m") is not None:
        parts.append(f'<div class="chip"><span class="lab">Impl corr 1M</span><b>{_fmt(vs.get("cor1m"),1)}</b></div>')
    if vs.get("vvix_vix") is not None:
        parts.append(f'<div class="chip"><span class="lab">VVIX/VIX</span><b>{_fmt(vs.get("vvix_vix"),2)}</b></div>')
    if vs.get("dspx") is not None:
        parts.append(f'<div class="chip"><span class="lab">DSPX</span><b>{_fmt(vs.get("dspx"),1)}</b></div>')
    return "".join(parts)


_STALE_BADGE = (
    '<span style="font-size:9.5px;font-weight:800;color:#fff;background:#c0392b;'
    'border-radius:4px;padding:1px 6px;margin-left:7px;letter-spacing:.4px">{}</span>'
)


def _doc_section(ctx: dict[str, Any]) -> str:
    doc = ctx.get("doc") or {}
    age = doc.get("letter_age") or {}
    fresh = bool(age.get("fresh"))
    exp = doc.get("expectation") or "Doc's daily read will appear here once the letter body is stored."
    src = doc.get("expectation_src") or ""

    # The header used to read "Doc McGraw — levels & what he expects today" and
    # "Doc's read into today" regardless of how old the stored letter was, so an
    # Aug 13 daily plan was published as today's expectation. The heading now
    # follows the data: Doc's words when they are current, our own read when
    # they are not, with the stale letter still quoted but clearly dated.
    if fresh:
        head = "Doc McGraw — levels &amp; what he expects today"
        hd = f"Doc's read into today · {_esc(src)}"
        badge = ""
    else:
        head = "SPX levels — our read (no current Doc letter)"
        hd = f"Our data-driven read · {_esc(src)}"
        badge = _STALE_BADGE.format(_esc((age.get("label") or "stale").upper()))

    stale_note = ""
    if not fresh and doc.get("stale_quote"):
        stale_note = (
            '<div class="flag"><b>Last stored Doc letter '
            f'({_esc(age.get("as_of") or "undated")}, {_esc(age.get("label") or "stale")}){badge}</b> — '
            "kept for context only; its levels and scenarios are withheld from the "
            f'triggers and level chips above.<br><span class="dim">{_esc(doc.get("stale_quote"))}</span></div>'
        )

    walls_caveat = ""
    if doc.get("walls_stale"):
        walls_caveat = (
            '<div class="flag"><b>Wall levels stale:</b> newest stored index chain is '
            f'{_esc(doc.get("walls_as_of") or "unknown")}. Run <code>index_walls_am</code> to '
            "refresh strike-by-strike; flip, regime and EM rails are current.</div>"
        )

    r16 = ""
    if doc.get("r16_lo") and doc.get("r16_hi"):
        r16 = (
            f'<div class="chip"><span class="lab">EM · Rule-of-16</span>'
            f'<b>{_fmt(doc.get("r16_lo"),0)} – {_fmt(doc.get("r16_hi"),0)}</b></div>'
        )
    straddle_rail = ""
    if doc.get("em_lo") and doc.get("em_hi"):
        straddle_rail = (
            f'<div class="chip"><span class="lab">EM · straddle</span>'
            f'<b>{_fmt(doc.get("em_lo"),0)} – {_fmt(doc.get("em_hi"),0)}</b></div>'
        )
    wall_lab = "Call wall" + (" (stale)" if doc.get("walls_stale") else "")
    return f"""<h2 class="sec"><span class="n">04</span>{head}</h2>
<div class="card">
<div class="expect"><div class="hd">{hd}</div>{_esc(exp)}</div>
{_doc_ladder_svg(doc)}
<div class="lvl">
<div class="chip"><span class="lab">Zero-γ flip</span><b>{_fmt(doc.get("flip"),0)}</b></div>
<div class="chip"><span class="lab">Spot</span><b>{_fmt(doc.get("spot"),0)}</b></div>
<div class="chip"><span class="lab">{wall_lab}</span><b>{_fmt(doc.get("call_wall"),0)}</b></div>
{straddle_rail}{r16}</div>
{_gex_transition_html(ctx)}
{stale_note}{walls_caveat}</div>"""


def _pos_bar(pos_pct: float | None, status: str) -> str:
    """Horizontal bar showing where current spot sits between lower/upper rails."""
    p = max(0.0, min(100.0, pos_pct if pos_pct is not None else 50.0))
    x = 3 + (p / 100.0) * 144
    s = status.lower()
    color = "#c0392b" if ("below" in s or "lower" in s) else "#1a8a4a" if ("above" in s or "upper" in s) else "#b26a00"
    return (
        '<svg width="150" height="14" aria-hidden="true">'
        '<line x1="3" y1="7" x2="147" y2="7" stroke="#d5dbe3" stroke-width="3"/>'
        f'<circle cx="{x:.1f}" cy="7" r="4.5" fill="{color}"/></svg>'
    )


def _em_section(ctx: dict[str, Any]) -> str:
    em = ctx.get("em_levels") or {}
    rows = em.get("rows") or []
    if not rows:
        return ""
    cur = em.get("current_spot")
    body = ""
    for r in rows:
        st = r.get("status") or ""
        s = st.lower()
        scls = "arw-dn" if ("below" in s or "lower" in s) else "arw-up" if ("above" in s or "upper" in s) else "arw-flat"
        body += (
            f'<tr><td class="d sym">{_esc(r.get("tenor"))} <span class="dim">{_esc(r.get("iv_label"))}</span></td>'
            f'<td class="d dim">{_esc(r.get("anchor_date"))} @ {_fmt(r.get("anchor_spot"), 0)}</td>'
            f'<td>{_fmt(r.get("lower"), 0)}</td>'
            f'<td>±{_fmt(r.get("em_pct"), 2)}%</td>'
            f'<td>{_fmt(r.get("upper"), 0)}</td>'
            f'<td class="d">{_pos_bar(r.get("pos_pct"), st)}</td>'
            f'<td class="d {scls}">{_esc(st)}</td></tr>'
        )
    return f"""<h2 class="sec"><span class="n">05</span>Expected-move rails — anchored at period open</h2>
<div class="card">
<div class="note" style="margin-top:0">Current spot <b>{_fmt(cur, 0)}</b> — the index board's SPX, the same figure used everywhere above. <b>Anchors</b> are SPY closes ×10 ({_esc(em.get("as_of"))}), which run a few points under cash SPX (a persistent index-vs-ETF basis), so an anchor will not match the board's spot for the same date — SPY is the maintained daily series and the rails only need a consistent origin. Quarterly / Monthly / Weekly rails are <b>fixed</b> at each period's opening spot × that period's implied move; only <b>Daily</b> re-anchors.</div>
<table><thead><tr><th class="d">Horizon</th><th class="d">Anchored</th><th>Lower</th><th>EM</th><th>Upper</th>
<th class="d">Spot in range</th><th class="d">Read</th></tr></thead>
<tbody>{body}</tbody></table></div>"""


def _vol_section(ctx: dict[str, Any]) -> str:
    v = ctx.get("vix") or {}
    return f"""<h2 class="sec"><span class="n">06</span>Volatility state</h2>
<div class="card"><div class="lvl">
<div class="chip"><span class="lab">VIX</span><b>{_fmt(v.get("vix"),2)}</b></div>
<div class="chip"><span class="lab">VVIX</span><b>{_fmt(v.get("vvix"),1)}</b></div>
<div class="chip"><span class="lab">VIX9d / 3m</span><b>{_fmt(v.get("vix9d"),1)} / {_fmt(v.get("vix3m"),1)}</b></div>
<div class="chip"><span class="lab">Term 9d−3m</span><b>{_fmt(v.get("term"),2)}</b></div>
<div class="chip"><span class="lab">VRP</span><b>{_fmt(v.get("vrp"),1)}</b></div>
<div class="chip"><span class="lab">Vega zone</span><b>{_esc(v.get("vega_zone"))}</b></div>
{_vol_skew_chips(ctx)}
</div><div class="note">{_esc(ctx.get("vol_note") or "")}</div></div>"""


def _letters_section(ctx: dict[str, Any]) -> str:
    cards = ""
    for lt in ctx.get("letters", []):
        fresh = bool(lt.get("fresh"))
        age = lt.get("age_label") or ""
        badge = (
            f'<span class="dim" style="font-size:10.5px"> · {_esc(age)}</span>' if fresh
            else _STALE_BADGE.format(_esc(age.upper() or "STALE"))
        )
        cards += (
            f'<div class="card"><div class="quote"><span class="src">{_esc(lt.get("src"))}</span>'
            f'{badge} — {_esc(lt.get("text"))}</div></div>'
        )
    tags = ctx.get("fresh_tags") or []
    tagline = ""
    if tags:
        tagline = '<div class="note">Fresh tags today: ' + ", ".join(_esc(t) for t in tags[:12]) + "</div>"
    body = cards or '<div class="card note dim">No letter commentary stored yet.</div>'
    return (
        '<h2 class="sec"><span class="n">07</span>Letters — market-structure commentary</h2>'
        f'<div class="two">{body}</div>{tagline}'
    )


def _tracker_section(ctx: dict[str, Any]) -> str:
    rows = ""
    mentions = 0
    for t in ctx.get("tracker", []):
        d = (t.get("dir") or "").lower()
        dcls = "neg" if "bear" in d else "pos" if "bull" in d else "dim"
        if t.get("mention_only"):
            mentions += 1
        scls = "dim" if t.get("mention_only") else "pos"
        rows += (
            f'<tr><td class="d src">{_esc(t.get("src"))}</td>'
            f'<td class="d sym">{_esc(t.get("ticker"))}</td>'
            f'<td class="d {dcls}">{_esc(t.get("dir"))}</td>'
            f'<td class="d">{_esc(t.get("note"))}</td>'
            f'<td class="d {scls}">{_esc(t.get("status"))}</td></tr>'
        )
    if not rows:
        return (
            '<h2 class="sec"><span class="n">08</span>Trade tracker — letters</h2>'
            '<div class="card"><div class="note dim">No letter ideas cleared the '
            "direction / subject checks today.</div></div>"
        )
    note = (
        "<b>thesis</b> = the letter argued a view on the name; <b>named only</b> = the "
        "letter mentioned it without one, so no direction is shown. Rows whose tagged "
        "direction contradicts their own text, or whose text is about a different "
        "company, are dropped rather than published."
    )
    return f"""<h2 class="sec"><span class="n">08</span>Trade tracker — letters</h2>
<div class="card"><table><thead><tr><th class="d">Src</th><th class="d">Ticker</th>
<th class="d">Dir</th><th class="d">Note</th><th class="d">Basis</th></tr></thead>
<tbody>{rows}</tbody></table><div class="note">{note}</div></div>"""


def _learned_section(ctx: dict[str, Any]) -> str:
    items = ""
    for r in ctx.get("learned", []):
        themes = ", ".join(_esc(t) for t in (r.get("themes") or [])[:3])
        items += (
            f'<li><b>{_esc(r.get("symbol"))}</b> '
            f'<span class="dim">[{themes}]</span> — {_esc((r.get("rationale") or "")[:180])}</li>'
        )
    body = items or '<li class="dim">No fresh names ingested.</li>'
    n = ctx.get("learned_total")
    hdr = f"{n} entries ingested" if n is not None else "Fresh watchlist adds"
    return f"""<h2 class="sec"><span class="n">09</span>What I learned today</h2>
<div class="card"><div class="note" style="margin-top:0"><b>{hdr}</b> — top clean signals (junk filtered):</div>
<ul class="clean">{body}</ul></div>"""


def _crosscheck_section(ctx: dict[str, Any]) -> str:
    rows = ""
    for c in ctx.get("crosschecks", []):
        rows += (
            f'<tr><td class="d">{_esc(c.get("claim"))}</td>'
            f'<td class="d dim">{_esc(c.get("source"))}</td>'
            f'<td class="d">{_esc(c.get("our"))}</td>'
            f'<td class="d verdict {_esc(c.get("cls"))}">{_esc(c.get("verdict"))}</td></tr>'
        )
    if not rows:
        return ""
    return f"""<h2 class="sec"><span class="n">10</span>Cross-checks — letters vs. our tape</h2>
<div class="card"><table><thead><tr><th class="d">Claim</th><th class="d">Source</th>
<th class="d">Our data</th><th class="d">Verdict</th></tr></thead><tbody>{rows}</tbody></table></div>"""


def _session_banner(ctx: dict[str, Any]) -> str:
    """Name the session the numbers describe, and say so loudly when closed.

    The 2026-09-07 edition called itself a "pre-open daily brief" on Labor Day
    and stamped its board rows 2026-09-06, a Sunday.
    """
    sd = ctx.get("session_date")
    if not sd:
        return ""
    if ctx.get("is_market_open_today"):
        return f'<div class="sub dim">Data through the <b>{_esc(sd)}</b> close.</div>'
    return (
        '<div class="flag" style="margin:8px 0 0"><b>US equity market closed today.</b> '
        f'Every number below is the <b>{_esc(sd)}</b> session — nothing has re-priced since.</div>'
    )


def _recap_html(ctx: dict[str, Any]) -> str:
    rc = ctx.get("recap") or {}
    recap, outlook = rc.get("recap"), rc.get("outlook")
    if not (recap or outlook):
        return ""
    parts = []
    if recap:
        parts.append(f"<b>Last session:</b> {_esc(recap)}")
    if outlook:
        # Labelled by provenance, not by wishful thinking. This block used to say
        # "Today:" over whatever text _doc_block returned, which on a stale run
        # was a three-week-old letter pasted in raw.
        lead = "<b>Into today:</b>" if rc.get("outlook_fresh") else "<b>Our read:</b>"
        src = rc.get("outlook_src")
        tail = f' <span class="dim">({_esc(src)})</span>' if src else ""
        parts.append(f"{lead} {_esc(outlook)}{tail}")
    return f'<div class="recap">{"<br>".join(parts)}</div>'


def _mag7_section(ctx: dict[str, Any]) -> str:
    rows = ctx.get("mag7") or []
    body = ""
    any_found = False
    for r in rows:
        if not r.get("found"):
            body += (
                f'<tr><td class="d sym">{_esc(r.get("symbol"))}</td>'
                '<td colspan="4" class="dim">no data</td></tr>'
            )
            continue
        any_found = True
        vf = r.get("vs_flip")
        vcls = "neg" if (vf or 0) < 0 else "pos"
        reg = r.get("regime") or ""
        rcls = "t-short" if "short" in reg.lower() else "t-long" if "long" in reg.lower() else "t-mid"
        iv = r.get("atm_iv")
        body += (
            f'<tr><td class="d sym">{_esc(r.get("symbol"))}</td>'
            f'<td>{_fmt(r.get("spot"), 2)}</td>'
            f'<td class="{vcls}">{_pct(vf)}</td>'
            f'<td class="d"><span class="tag {rcls}">{_esc(reg.split(" (")[0])}</span></td>'
            f'<td>{_fmt(iv * 100, 1) if iv is not None else "—"}%</td></tr>'
        )
    if not any_found:
        return ""
    return f"""<h2 class="sec"><span class="n">02</span>Mag7 — the index drivers</h2>
<div class="card"><table><thead><tr><th class="d">Name</th><th>Spot</th><th>vs flip</th>
<th class="d">Regime</th><th>ATM IV</th></tr></thead><tbody>{body}</tbody></table>
<div class="note">The mega-caps that move the index. vs-flip green = above the gamma flip (long-γ), red = below (short-γ).</div></div>"""


def _flows_section(ctx: dict[str, Any]) -> str:
    rows = ctx.get("flows") or []
    if not rows:
        return (
            '<h2 class="sec"><span class="n">03</span>Top option flow — biggest names</h2>'
            '<div class="card"><div class="note dim">No flow roll-up yet '
            "(populates once tas_daily_rollup has run).</div></div>"
        )
    body = ""
    conflicts = 0
    for r in rows:
        lbl = r.get("label") or ""
        lcls = "pos" if lbl == "accumulation" else "neg" if lbl == "distribution" else "dim"
        nd = r.get("net_delta") or 0.0
        mark = ""
        if r.get("tilt_conflict"):
            conflicts += 1
            mark = ' <span class="dim" title="label disagrees with buy-tilt">⚠︎</span>'
        obs = r.get("days_observed")
        obs_cell = f'{_fmt(obs, 0)}/5' if obs is not None else "—"
        body += (
            f'<tr><td class="d sym">{_esc(r.get("root"))}</td>'
            f'<td>${_fmt((r.get("premium") or 0.0) / 1e6, 1)}M</td>'
            f'<td class="{"pos" if nd >= 0 else "neg"}">${_fmt(nd / 1e6, 1)}M</td>'
            f'<td class="c dim">{obs_cell}</td>'
            f'<td class="d {lcls}">{_esc(lbl)}{mark}</td>'
            f'<td>{_fmt(r.get("score"), 0)}</td></tr>'
        )
    note = (
        "Ranked by <b>premium traded</b> over the last 5 sessions (our own tape). "
        "<b>Premium</b> is dollars of option premium; <b>Net Δ$</b> is delta-notional — "
        "different units, so Net Δ$ legitimately exceeds premium. "
        "<b>Days</b> = sessions with qualifying prints out of 5."
    )
    if conflicts:
        note += (
            f" ⚠︎ marks {conflicts} row(s) whose read disagrees with their own "
            "buy-tilt — treat the label as unconfirmed."
        )
    return f"""<h2 class="sec"><span class="n">03</span>Top option flow — biggest names (5-day)</h2>
<div class="card"><table><thead><tr><th class="d">Name</th><th>Premium</th><th>Net &Delta;$</th>
<th class="c">Days</th><th class="d">Read</th><th>Score</th></tr></thead><tbody>{body}</tbody></table>
<div class="note">{note}</div></div>"""


_CSS = """*{box-sizing:border-box}body{margin:0;background:#f4f6f9;color:#1a2027;
font:15px/1.5 -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Arial,sans-serif;padding:22px 14px 54px}
.wrap{max-width:900px;margin:0 auto}h1{font-size:21px;margin:0 0 2px;color:#12233d}
.sub{color:#5b6673;font-size:13px;margin:5px 0 4px}.sub b{color:#b26a00}
.through{background:#eef4fd;border-left:3px solid #1f7ae0;border-radius:0 8px 8px 0;padding:10px 14px;margin:14px 0 14px;font-size:13.5px;color:#22303f}
.recap{background:#fff;border:1px solid #e2e7ee;border-left:3px solid #6f4bc0;border-radius:0 8px 8px 0;padding:11px 15px;margin:0 0 20px;font-size:13.5px;color:#33404f;line-height:1.6}
h2.sec{font-size:12px;letter-spacing:1.3px;text-transform:uppercase;color:#0e6b76;margin:24px 0 11px;font-weight:700;border-bottom:1px solid #dfe4ea;padding-bottom:6px}
h2.sec .n{color:#9aa5b1;margin-right:8px}
.card{background:#fff;border:1px solid #e2e7ee;border-radius:11px;padding:14px 16px;margin-bottom:13px}
table{width:100%;border-collapse:collapse;font-size:13px;font-variant-numeric:tabular-nums}
th,td{text-align:right;padding:7px 9px;border-bottom:1px solid #eef1f6}
th{color:#7a8695;font-weight:600;font-size:10.5px;letter-spacing:.4px;text-transform:uppercase}
td.d,th.d{text-align:left}td.c,th.c{text-align:center}
.sym{font-weight:700}.pos{color:#1a8a4a}.neg{color:#c0392b}.dim{color:#8a93a0}
.tag{display:inline-block;font-size:10.5px;font-weight:700;padding:2px 7px;border-radius:20px}
.t-long{background:#e5f5ec;color:#1a8a4a}.t-short{background:#fdecea;color:#c0392b}.t-mid{background:#fbf1dc;color:#b26a00}
.pill{display:inline-block;font-size:11px;font-weight:700;padding:3px 8px;border-radius:7px}
.pill-up{background:#e5f5ec;color:#1a8a4a}.pill-dn{background:#fdecea;color:#c0392b}.pill-mid{background:#fbf1dc;color:#b26a00}
.flipval{font-weight:700}.cap{display:block;font-size:9.5px;color:#9aa5b1;margin-top:1px}
.arw-dn{color:#c0392b}.arw-up{color:#1a8a4a}.arw-flat{color:#9aa5b1}
.spark{vertical-align:middle}.sw{width:14px;height:3px;border-radius:2px;display:inline-block;vertical-align:middle}
.legend{display:flex;flex-wrap:wrap;gap:14px;margin-top:9px;font-size:11.5px;color:#5b6673;align-items:center}
.note{color:#5b6673;font-size:12.5px;margin-top:9px}.note b{color:#1a2027}
.two{display:grid;grid-template-columns:1fr 1fr;gap:12px}@media(max-width:680px){.two{grid-template-columns:1fr}}
.lvl{display:flex;flex-wrap:wrap;gap:8px;margin-top:10px}
.lvl .chip{background:#f7f9fc;border:1px solid #e2e7ee;border-radius:8px;padding:7px 11px;font-size:12.5px}
.lvl .chip b{font-size:15px}.lvl .chip .lab{display:block;color:#9aa5b1;font-size:10px;text-transform:uppercase;margin-bottom:2px}
.quote{border-left:2px solid #8e6fd0;padding:3px 0 3px 11px;color:#33404f;font-size:13px}
.src{color:#6f4bc0;font-weight:700;font-size:11.5px}
.expect{background:#f3effc;border:1px solid #ddd0f5;border-radius:10px;padding:11px 14px;font-size:13.5px;color:#2b2340}
.expect .hd{font-size:11px;letter-spacing:.6px;text-transform:uppercase;color:#6f4bc0;font-weight:700;margin-bottom:5px}
ul.clean{margin:6px 0 0;padding-left:18px;font-size:13px}ul.clean li{margin:5px 0}
.verdict{font-weight:700}.ok{color:#1a8a4a}.warn{color:#b26a00}.bad{color:#c0392b}
.flag{background:#fbf6e9;border:1px dashed #e3c98a;border-radius:8px;padding:9px 12px;font-size:12.5px;color:#7a5b12;margin-top:10px}
.ladderwrap{width:100%;overflow-x:auto}svg.ladder{display:block;margin:6px auto;max-width:540px;width:100%;height:auto}
.foot{color:#9aa5b1;font-size:11.5px;margin-top:24px;text-align:center;line-height:1.7;border-top:1px solid #e2e7ee;padding-top:14px}
.mrmeta{margin:11px 0 6px;font-size:13px}.mrmeta .path{color:#33404f;margin-left:9px}
.trigs{margin-top:12px}.trigs .hd,.overlay .hd{font-size:10.5px;text-transform:uppercase;letter-spacing:.6px;color:#6b7684;font-weight:700;margin-bottom:6px}
.overlay{margin-top:13px}.overlay th.l,.overlay td.l{text-align:left}
.quote .src{color:#9aa5b1;font-size:11px;font-weight:400}"""


#: The extractor emits ``key_support`` / ``key_resistance`` / ``expected_move``;
#: this table used to look up ``gamma_flip`` / ``call_wall`` / ``put_wall`` only,
#: so Doc's column was permanently "—" even when he had stated levels — and the
#: "ours" side was hard-coded to None for both walls. Map both vocabularies.
_LEVEL_ALIASES: tuple[tuple[str, tuple[str, ...], str], ...] = (
    ("Gamma flip", ("gamma_flip", "flip", "zero_gamma"), "gex_flip"),
    ("Resistance / call wall", ("call_wall", "key_resistance", "resistance"), "call_wall"),
    ("Support / put wall", ("put_wall", "key_support", "support"), "put_wall"),
)


def _stated_vs_ours(ctx: dict[str, Any], mech: dict[str, Any]) -> str:
    """Doc's stated levels vs our computed ones — the cross-check overlay.

    Suppressed entirely when the stored letter is stale: comparing today's flip
    against three-week-old stated levels invites reading the gap as a signal.
    """
    ns = ctx.get("newsletter") or {}
    doc_src = (ns.get("sources") or {}).get("DOC") or {}
    if not (ctx.get("doc") or {}).get("letter_age", {}).get("fresh"):
        return ""
    stated = {
        (lv.get("name") or "").lower(): lv.get("value")
        for lv in (doc_src.get("levels") or [])
    }
    ours_src = {
        "gex_flip": mech.get("gex_flip"),
        "call_wall": (ctx.get("doc") or {}).get("call_wall"),
        "put_wall": (ctx.get("doc") or {}).get("put_wall"),
    }
    pairs = []
    for label, aliases, our_key in _LEVEL_ALIASES:
        said = next((stated[a] for a in aliases if stated.get(a) is not None), None)
        ours = ours_src.get(our_key)
        if said is not None or ours is not None:
            pairs.append((label, ours, said))
    if not pairs:
        return ""
    rows = "".join(
        f'<tr><td class="l">{lab}</td><td>{_fmt(o, 0) if o is not None else "—"}</td>'
        f'<td>{_fmt(sd, 0) if sd is not None else "—"}</td>'
        f'<td class="dim">{_fmt(abs(o - sd), 0) if (o is not None and sd is not None) else "—"}</td></tr>'
        for (lab, o, sd) in pairs
    )
    asof = _esc(doc_src.get("as_of") or "")
    return (
        f'<div class="overlay"><div class="hd">Stated vs ours{f" · Doc {asof}" if asof else ""}</div>'
        '<table><thead><tr><th class="l">level</th><th>ours</th><th>Doc</th>'
        '<th>gap</th></tr></thead>'
        f"<tbody>{rows}</tbody></table></div>"
    )


def _market_read_section(ctx: dict[str, Any]) -> str:
    """The fused synthesis read — path, levels, triggers, confluence, narrative."""
    mr = ctx.get("market_read") or {}
    if not mr:
        return ""
    mech = mr.get("mechanics") or {}
    confl = mr.get("confluence") or {}
    score = confl.get("score") or "—"
    bg, fg = ("#e6f6ee", "#0b7a43") if "constructive" in score else (
        ("#fdecec", "#b3261e") if "defensive" in score else ("#fbf3e2", "#8a5a12")
    )
    chips = "".join(
        f'<div class="chip"><span class="lab">{_esc(lv.get("name"))}</span>'
        f'<b>{_fmt(lv.get("value"), 0)}</b></div>'
        for lv in (mr.get("levels") or [])
    )
    flags = "".join(f'<div class="flag">{_esc(f)}</div>' for f in (mr.get("cross_pillar_flags") or []))
    trigs = "".join(
        f'<div class="quote"><b>{_esc(t.get("trigger"))}</b> → {_esc(t.get("consequence") or "")}'
        f' <span class="src">{_esc(t.get("source"))}</span></div>'
        for t in (mr.get("triggers") or [])[:6]
    )
    overlay = _stated_vs_ours(ctx, mech)
    return (
        '<h2 class="sec"><span class="n">00</span>Market read — the fused board</h2>'
        '<div class="card">'
        f'<div class="through">{_esc(mr.get("narrative") or "")}</div>'
        f'<div class="mrmeta"><span class="pill" style="background:{bg};color:{fg}">{_esc(score)}</span>'
        f'<span class="path">{_esc(mr.get("path") or "")}</span></div>'
        + (f'<div class="lvl">{chips}</div>' if chips else "")
        + overlay
        + (f'<div class="trigs"><div class="hd">Triggers to watch</div>{trigs}</div>' if trigs else "")
        + flags
        + "</div>"
    )


def render_html(ctx: dict[str, Any]) -> str:
    """Render the full daily-brief HTML from the context dict."""
    through = _esc(ctx.get("through_line") or "")
    body = (
        _market_read_section(ctx)
        + _index_board(ctx)
        + _mag7_section(ctx)
        + _flows_section(ctx)
        + _doc_section(ctx)
        + _em_section(ctx)
        + _vol_section(ctx)
        + _letters_section(ctx)
        + _tracker_section(ctx)
        + _learned_section(ctx)
        + _crosscheck_section(ctx)
    )
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Trading-Intel Daily — {_esc(ctx.get('as_of'))}</title>
<style>{_CSS}</style></head><body><div class="wrap">
<h1>📈 Trading-Intel Daily</h1>
<div class="sub">{_esc(ctx.get('as_of'))} · {_esc(ctx.get('subtitle') or '')}</div>
{_session_banner(ctx)}
<div class="through"><b>Through-line:</b> {through}</div>
{_recap_html(ctx)}
{body}
<div class="foot">Descriptive regime context only — <b>not trading signals</b> (FlashAlpha rule 4).<br>
Generated by the letters pipeline · {_esc(ctx.get('provenance') or '')}</div>
</div></body></html>"""
