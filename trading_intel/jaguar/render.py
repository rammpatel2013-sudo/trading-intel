"""Pure renderer for the Jaguar daily brief — the approved v4 layout.

Takes the assembled ``brief`` dict (from :mod:`jaguar.brief`) and emits a self-contained
HTML document: his trades & the flow he's following (his read → our tape → a defined-risk
⚡ structure), his thinking, S&P breadth + 5-day trend, what changed, and the macro
through-line. No I/O, no market calls — just dict-in / HTML-out, so it is unit-tested.
Descriptive relay + our own analysis, never an automated signal or advice (rule 4).
"""

from __future__ import annotations

import html as _html
from typing import Any

_CSS = """
  body{font:14px/1.62 -apple-system,Segoe UI,Roboto,Arial,sans-serif;color:#1a2027;background:#f4f6f9;margin:0;padding:20px 14px 46px}
  .wrap{max-width:920px;margin:0 auto}
  .banner{background:#e5f4ea;border:1px solid #a8d8ba;color:#1c6b3a;font-size:12px;padding:7px 11px;border-radius:8px;margin-bottom:10px}
  .caveat{background:#fbfcfe;border:1px solid #dbe3ef;color:#5b6673;font-size:11.5px;padding:6px 11px;border-radius:8px;margin-bottom:16px}
  h1{font-size:21px;color:#12233d;margin:0 0 2px}
  .sub{color:#5b6673;font-size:12.5px;margin-bottom:16px}
  .card{background:#fff;border:1px solid #e2e7ee;border-radius:11px;padding:15px 17px;margin-bottom:14px}
  .card h2{font-size:11px;letter-spacing:.6px;text-transform:uppercase;color:#8a93a0;margin:0 0 12px}
  .trade{border:1px solid #eef1f6;border-radius:9px;padding:11px 13px;margin-bottom:13px}
  .trade:last-child{margin-bottom:0}
  .trade .tk{font-size:15.5px;font-weight:800;color:#12233d}
  .trade .tag{font-size:10px;padding:1px 7px;border-radius:9px;background:#e5edff;color:#274690;vertical-align:middle;margin-left:4px}
  .trade .tag.er{background:#fdeede;color:#a5601a}.trade .tag.new{background:#e5f4ea;color:#1c6b3a}
  .trade .flow{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12px;background:#f2f5fa;border-radius:6px;padding:6px 9px;margin:7px 0;color:#33415a}
  .lbl{font-size:10px;font-weight:700;letter-spacing:.5px;text-transform:uppercase;color:#8a93a0;margin:9px 0 2px}
  .him{color:#2b3440}
  .ours{color:#274690;background:#f6f9fe;border-left:3px solid #4f79c7;border-radius:0 6px 6px 0;padding:6px 10px;margin-top:3px;font-size:13px}
  .synth{color:#7a4a12;background:#fffaf1;border-left:3px solid #d99a3a;border-radius:0 6px 6px 0;padding:7px 10px;margin-top:6px;font-size:13px}
  .synth b,.ours b{font-weight:700}
  .rr{display:flex;gap:14px;flex-wrap:wrap;margin-top:5px;font-family:ui-monospace,monospace;font-size:11.5px}
  .rr .risk{color:#c0392b}.rr .tgt{color:#1f9254}.rr .be{color:#5b6673}
  .think p{margin:0 0 9px;color:#26313d}.think p:last-child{margin-bottom:0}
  .think .h{font-weight:700;color:#12233d}
  .moat{columns:2;font-size:12.5px;color:#3a4653;margin:2px 0 0;padding-left:16px}
  table.bd{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums;font-size:13px}
  table.bd td{padding:6px 8px;border-bottom:1px solid #f1f4f8}table.bd tr:last-child td{border-bottom:0}
  table.bd td:first-child{color:#5b6673}table.bd td:last-child{text-align:right;font-weight:700;font-family:ui-monospace,monospace}
  .idx{display:flex;gap:16px;flex-wrap:wrap;margin-bottom:8px;font-size:13px;font-family:ui-monospace,monospace}
  .g{color:#1f9254;font-weight:700}.r{color:#c0392b;font-weight:700}.mut{color:#8a93a0}
  .chg{padding:4px 0;border-bottom:1px solid #f1f4f8;font-size:13px}.chg:last-child{border-bottom:0}
  .macro{color:#5b6673;font-size:12.5px}.macro div{padding:2px 0}
  .foot{color:#8a93a0;font-size:11.5px;margin-top:8px;line-height:1.5}
  a{color:#274690}
"""

_CAVEAT = (
    "⚠️ The ⚡ structures are illustrative, defined-risk ways to express each thesis — "
    "for your own evaluation, not financial advice. Strikes are real (from Fahad's flow); "
    "debits/returns price live from our chain. You size and decide."
)


def _esc(s: Any) -> str:
    return _html.escape(str(s if s is not None else ""))


def _links(pairs: list) -> str:
    return " · ".join(f'<a href="{_esc(u)}">{_esc(t)}</a>' for t, u in (pairs or []))


def _structure_html(st: dict[str, Any]) -> str:
    if not st:
        return ""
    risk = (
        f"MAX RISK ≈ ${st['max_risk']:,.0f} (the debit, capped)"
        if st.get("max_risk") is not None
        else "MAX RISK = the debit (capped) · live-priced from our chain"
    )
    tgt = (
        f"TARGET ~+{st['target_pct'] * 100:.0f}% on risk if the thesis hits"
        if st.get("target_pct") is not None
        else "TARGET fills from our chain"
    )
    be = f"B/E ~${st['breakeven']:.2f}" if st.get("breakeven") is not None else ""
    rr = f'<span class="risk">{_esc(risk)}</span><span class="tgt">{_esc(tgt)}</span>'
    if be:
        rr += f'<span class="be">{_esc(be)}</span>'
    return (
        f'<div class="synth">⚡ <b>Combined — {_esc(st.get("label", "one structure"))}.</b> '
        f'{_esc(st.get("note", ""))}<div class="rr">{rr}</div></div>'
    )


def _trade_html(t: dict[str, Any]) -> str:
    tag = ""
    if t.get("tag"):
        tag = f'<span class="tag {_esc(t.get("tag_kind", ""))}">{_esc(t["tag"])}</span>'
    parts = [f'<div class="tk">{_esc(t["ticker"])} · {_esc(t.get("name", ""))} {tag}</div>']
    if t.get("flow"):
        parts.append(f'<div class="flow">{_esc(t["flow"])}</div>')
    if t.get("him"):
        parts.append(f'<div class="lbl">Fahad\'s read</div><div class="him">{_esc(t["him"])}</div>')
    if t.get("links"):
        parts.append(f'<div class="him" style="margin-top:4px">{_links(t["links"])}</div>')
    if t.get("ours"):
        parts.append(f'<div class="ours">⊕ <b>Our tape:</b> {_esc(t["ours"])}</div>')
    parts.append(_structure_html(t.get("structure") or {}))
    return f'<div class="trade">{"".join(parts)}</div>'


def _breadth_html(b: dict[str, Any]) -> str:
    if not b:
        return ""
    idx = "".join(
        f'<span>{_esc(name)} <span class="{cls}">{_esc(val)}</span></span>'
        for name, val, cls in (b.get("index") or [])
    )
    rows = "".join(
        f"<tr><td>{_esc(label)}</td><td>{_esc(now)} "
        f'<span class="mut" style="font-weight:400">{_esc(trend)}</span></td></tr>'
        for label, now, trend in (b.get("rows") or [])
    )
    read = (
        f'<div class="ours" style="margin-top:9px">📉 <b>Breadth read:</b> {_esc(b["read"])}</div>'
        if b.get("read")
        else ""
    )
    foot = f'<div class="foot">{_esc(b.get("foot", ""))}</div>'
    return (
        '<div class="card"><h2>03 · Market breadth &amp; 5-day trend '
        '<span class="mut">— S&amp;P 500-wide (broad feed)</span></h2>'
        f'<div class="idx">{idx}</div><table class="bd">{rows}</table>{read}{foot}</div>'
    )


def build_html(brief: dict[str, Any]) -> str:
    """Render the assembled brief dict into the self-contained daily-brief HTML."""
    as_of = _esc(brief.get("as_of", ""))
    trades = "".join(_trade_html(t) for t in (brief.get("trades") or []))
    smaller = (
        f'<div class="chg" style="margin-top:4px"><b>Also flagged (smaller):</b> '
        f'{_esc(brief["smaller"])}</div>'
        if brief.get("smaller")
        else ""
    )
    th = brief.get("thinking") or {}
    moat = "".join(f"<li>{_esc(x)}</li>" for x in (th.get("moat") or []))
    thinking = (
        '<div class="card"><h2>02 · What Fahad\'s thinking '
        '<span class="mut">— big picture &amp; the small stuff</span></h2><div class="think">'
        f'<p><span class="h">Big picture.</span> {_esc(th.get("big_picture", ""))}</p>'
        f'<p><span class="h">Small / tactical.</span> {_esc(th.get("tactical", ""))}</p>'
        + (f'<ul class="moat">{moat}</ul>' if moat else "")
        + (f'<p style="margin-top:8px">{_esc(th.get("extra"))}</p>' if th.get("extra") else "")
        + "</div></div>"
    )
    changed = "".join(
        f'<div class="chg"><b>{_esc(k)}:</b> {_esc(v)}</div>'
        for k, v in (brief.get("changed") or [])
    )
    macro = (
        '<div class="card"><h2>05 · Macro &amp; the through-line</h2>'
        f'<div class="macro"><div>{_esc(brief.get("macro_facts", ""))}</div></div>'
        + (
            f'<div class="ours" style="margin-top:9px">🧭 <b>The read:</b> {_esc(brief["macro_read"])}</div>'
            if brief.get("macro_read")
            else ""
        )
        + f'<div class="foot">{_esc(brief.get("foot", ""))}</div></div>'
    )
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<title>Jaguar Daily — {as_of}</title><style>{_CSS}</style></head><body><div class="wrap">'
        f'<div class="banner">{_esc(brief.get("banner", "Jaguar Daily"))}</div>'
        f'<div class="caveat">{_CAVEAT}</div>'
        f"<h1>🐆 Jaguar Daily — {as_of}</h1>"
        f'<div class="sub">{_esc(brief.get("sub", ""))}</div>'
        '<div class="card"><h2>01 · Trades &amp; the flow he\'s following</h2>'
        f"{trades}{smaller}</div>"
        f"{thinking}{_breadth_html(brief.get('breadth') or {})}"
        + (f'<div class="card"><h2>04 · What changed</h2>{changed}</div>' if changed else "")
        + f"{macro}</div></body></html>"
    )
