"""Desk brief page - phone first, reads with scripts off (2026-11-14). Pure HTML builder.

Same look as :8899/equity.html (shared CSS + bar helper). One column on a phone, two on a desktop.
Taps open ``<details>``; every number is written into the page.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from trading_intel.equity.report import CSS, _e, hbars_svg

EXTRA_CSS = """
.hero{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:14px}
.hero .t{font-size:34px}.kv{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px;margin:10px 0}
.kv div{background:var(--tint-0);border-radius:10px;padding:8px 10px}.kv .k{font-size:14px;color:var(--soft)}
.kv .v{font-size:20px;font-weight:700}ul{margin:6px 0 6px 20px;padding:0}li{margin:4px 0;font-size:17px}
table{width:100%;border-collapse:collapse;font-size:16px}td,th{padding:5px 4px;border-bottom:1px solid var(--line);
text-align:right}td:first-child,th:first-child{text-align:left}th{color:var(--soft);font-weight:600;font-size:14px}
.cov{font-size:15px}.ok{background:var(--tint-good)}.miss{background:var(--tint-bad)}a{color:var(--blue)}
@media (min-width:900px){.kv{grid-template-columns:repeat(4,minmax(0,1fr))}}
"""


def _p(x: float | None, d: int = 2) -> str:
    return "-" if x is None else f"{x:+.{d}f}%"


def _n(x: float | None, d: int = 2) -> str:
    return "-" if x is None else f"{x:.{d}f}"


def _money(x: float | None) -> str:
    if x is None:
        return "-"
    a = abs(x)
    s = (
        f"{a / 1e9:.1f}B"
        if a >= 1e9
        else f"{a / 1e6:.1f}M" if a >= 1e6 else f"{a / 1e3:.1f}K" if a >= 1e3 else f"{a:.0f}"
    )
    return ("-" if x < 0 else "+") + s


def _cls(x: float | None) -> str:
    return "" if x is None else ("up" if x >= 0 else "dn")


def _pc(x: float | None) -> str:
    return "n/a" if x is None else f"{x:.0f}%"


SHORT = {
    "Consumer Staples": "Staples",
    "Health Care & Biotech": "Health",
    "Financials": "Financials",
    "Travel, Leisure & Restaurants": "Travel",
    "Utilities & Renewables": "Utilities",
    "Tech & Communications": "Tech",
    "Industrial & Business Services": "Industrial",
    "Energy & Materials": "Energy",
    "Retail & Apparel": "Retail",
    "Housing, Building & REITs": "Housing",
}


def _r4(x: float | None) -> str:
    return "-" if x is None else f"{x:+.4f}"


def _tile(k: str, v: str, cls: str = "") -> str:
    return f'<div><div class="k">{_e(k)}</div><div class="v {cls}">{v}</div></div>'


def _name_row(r: Mapping[str, Any]) -> str:
    d = r.get("dark") or {}
    gam = r.get("st_gex")
    summ = (
        f'<div class="row"><b>{_e(r["symbol"])}</b><span class="{_cls(r.get("d1"))}">{_p(r.get("d1"))}</span></div>'
        f'<div class="note">dark {_n(d.get("w1"))} / {_n(d.get("w2"))} · options '
        f'{"n/a" if r.get("bull") is None else _pc(r.get("bull")) + " bullish"} · gamma '
        f'<span class="{_cls(gam)}">{_money(gam)}</span></div>'
    )
    body = (
        f'<table><tr><th>dark 2W / 1W / 1D</th><td>{_n(d.get("w2"))} / {_n(d.get("w1"))} / {_n(d.get("d1"))}</td></tr>'
        f'<tr><th>dark trend</th><td>{_e(d.get("trend") or "-")}</td></tr>'
        f'<tr><th>short-term gamma / $ volume</th><td>{_money(gam)} / {_r4(r.get("gex_vol"))}</td></tr>'
        f'<tr><th>1D / 5D</th><td>{_p(r.get("d1"))} / {_p(r.get("d5"))}</td></tr>'
        f'<tr><th>close in range (BSR)</th><td>{_n(r.get("bsr"), 1)}</td></tr>'
        f'<tr><th>group</th><td>{_e(r.get("group"))}</td></tr></table>'
    )
    return f'<details class="card"><summary>{summ}</summary><p>{_e(r.get("name") or "")}</p>{body}</details>'


def _top(p: Mapping[str, Any] | None, card: Mapping[str, Any] | None, hw: Mapping | None) -> str:
    if not p:
        return (
            '<div class="hero"><b>No top pick today.</b><p class="note">No two-leg name had positive short-term '
            "gamma with real options activity.</p></div>"
        )
    d = p.get("dark") or {}
    c = card or {}
    tiles = "".join(
        [
            _tile("1D / 5D", f'{_p(p.get("d1"))} / {_p(p.get("d5"))}', _cls(p.get("d1"))),
            _tile("dark 1W / 2W", f'{_n(d.get("w1"))} / {_n(d.get("w2"))}', "up"),
            _tile("options bullish", f'{p.get("bull") or 0:.0f}%', "up"),
            _tile("short-term gamma", _money(p.get("st_gex")), _cls(p.get("st_gex"))),
            _tile("gamma / $ volume", f'{p.get("gex_vol") or 0:+.4f}'),
            _tile("close in range", _n(p.get("bsr"), 1)),
            _tile("P/E (ttm)", _n(c.get("pe_ttm"), 1)),
            _tile(
                "dividend yield",
                "-" if c.get("div_yield") is None else f'{100 * c["div_yield"]:.1f}%',
            ),
        ]
    )
    q = "".join(
        f'<tr><td>{_e(x.get("period") or x.get("date"))}</td><td>{_n(x.get("eps"))}</td>'
        f'<td>{_money(x.get("revenue"))[1:]}</td><td>{_p(x.get("rev_yoy"), 1)}</td></tr>'
        for x in c.get("quarters") or []
    )
    ne = c.get("next_earnings") or {}
    fil = "".join(
        f'<li><a href="{_e(f.get("link"))}">{_e(f.get("form"))}</a> {_e(f.get("date"))}</li>'
        for f in c.get("filings") or []
    )
    news = "".join(
        f'<li><a href="{_e(n.get("url"))}">{_e(n.get("title"))}</a> <span class="note">'
        f'{_e(n.get("site"))} {_e(n.get("date"))}</span></li>'
        for n in c.get("news") or []
    )
    hold = "".join(f"<li>{_e(x)}</li>" for x in (hw or {}).get("holds", []))
    weak = "".join(f"<li>{_e(x)}</li>" for x in (hw or {}).get("weakens", []))
    return (
        f'<div class="hero"><div class="row"><span class="t">{_e(p["symbol"])}</span>'
        f'<span class="note">{_e(c.get("name") or p.get("name") or "")}</span></div>'
        f'<p class="note">Why: {_e(p.get("why"))}. {_e(p.get("group"))} · {_e(c.get("industry") or p.get("industry") or "")}'
        f' · market cap {_money(c.get("market_cap"))[1:]} · beta {_n(c.get("beta"))}</p>'
        f'<div class="kv">{tiles}</div>'
        f"<details><summary><b>Holds if / weakens if</b></summary><p><b>Holds if</b></p><ul>{hold}</ul>"
        f"<p><b>Weakens if</b></p><ul>{weak}</ul></details>"
        f'<details><summary><b>Earnings</b> · next {_e(ne.get("date") or "n/a")}'
        f'{"" if ne.get("eps_est") is None else " · est EPS " + str(ne.get("eps_est"))}</summary>'
        f"<table><tr><th>quarter</th><th>EPS</th><th>revenue</th><th>rev y/y</th></tr>{q}</table></details>"
        + (
            f"<details><summary><b>Filings + headlines</b></summary><ul>{fil}{news}</ul></details>"
            if fil or news
            else ""
        )
        + '<p class="note">Price target / rating: n/a (not served on our data plan).</p></div>'
    )


def _score(sc: Mapping[str, Any] | None) -> str:
    if not sc:
        return '<p class="note">No picks old enough to score yet.</p>'
    rows = []
    for src, roles in (sc.get("stats") or {}).items():
        for role, hs in roles.items():
            cells = "".join(
                (
                    f'<td class="{_cls(h["excess"])}">{h["excess"]:+.1f}% · {h["beat"]:.0f}% · n{h["n"]}</td>'
                    if (h := hs.get(n) or hs.get(str(n)))
                    else "<td>-</td>"
                )
                for n in (5, 10, 20)
            )
            rows.append(f"<tr><td>{_e(src)} {_e(role)}</td>{cells}</tr>")
    tbl = (
        (
            "<table><tr><th>basket</th><th>5 sessions</th><th>10</th><th>20</th></tr>"
            + "".join(rows)
            + "</table>"
            '<p class="note">vs SPY over the same window · % of picks that beat SPY · count.</p>'
        )
        if rows
        else '<p class="note">No pick has reached 5 sessions yet.</p>'
    )
    op = "".join(
        f'<tr><td>{_e(o["source"])} {_e(o["role"])} {_e(o["symbol"])}</td><td>{_e(o["date"])}</td>'
        f'<td class="{_cls(o.get("vs_spy"))}">{_p(o.get("ret"), 1)} ({_p(o.get("vs_spy"), 1)} vs SPY)</td>'
        f'<td>{o.get("sessions")}</td></tr>'
        for o in (sc.get("open") or [])[:24]
    )
    if op:
        tbl += (
            "<details><summary><b>Open picks so far</b></summary><table><tr><th>pick</th><th>brief</th>"
            f"<th>since entry</th><th>sessions</th></tr>{op}</table></details>"
        )
    return tbl


def build_html(
    p: Mapping[str, Any],
    *,
    card: Mapping | None = None,
    hw: Mapping | None = None,
    score: Mapping | None = None,
) -> str:
    cov = p.get("coverage") or {}
    chips = "".join(
        f'<span class="chip cov {"ok" if cov.get(k) else "miss"}">{lab} {"✓" if cov.get(k) else "missing"}</span>'
        for k, lab in (("finra", "dark pool"), ("options", "options split"), ("gamma", "gamma"))
    )
    t = p.get("totals") or {}
    board = p.get("scoreboard") or []
    bars = hbars_svg(
        [(SHORT.get(b["group"], b["group"][:10]), b["med1"] or 0) for b in board],
        title="median 1D by group",
    )
    groups = "".join(
        f'<details class="card"><summary><div class="row"><b>{_e(b["group"])}</b><span>{b["n"]} names</span></div>'
        f'<div class="note">avg {_p(b["avg1"])} · median {_p(b["med1"])} · {b["n2"]} above +2% · 5D {_p(b["avg5"])} · '
        f'BSR {_n(b["med_bsr"], 1)}</div></summary><p>'
        + ", ".join(f"{_e(s)} {v:+.2f}%" for s, v in b["names"])
        + "</p></details>"
        for b in board
    )
    two = "".join(_name_row(r) for r in p.get("two_leg") or [])
    two = (
        f'<div class="cards">{two}</div>'
        if two
        else (
            '<p class="note"><b>'
            + (
                "Options split not collected for this date - the dark-pool leg alone is listed below."
                if not cov.get("options")
                else "No name passed both legs."
            )
            + "</b></p>"
        )
    )
    dk = p.get("dark_only") or []
    dark_only = (
        f'<h3>Dark pool leg only ({len(dk)})</h3><div class="cards">'
        + "".join(_name_row(r) for r in dk)
        + "</div>"
        if dk
        else ""
    )
    prints = "".join(
        f'<tr><td>{_e(x["symbol"])}</td><td class="{_cls(x["d1"])}">{_p(x["d1"])}</td>'
        f'<td>{(x["volume"] or 0) / 1e6:.1f}M</td><td>{"-" if x["lev"] is None else format(x["lev"], "+g") + "x"}</td>'
        f'<td class="{_cls(x["implied"])}">{_p(x["implied"])}</td></tr>'
        for x in p.get("etf_prints") or []
    )
    lead = hbars_svg([(r["symbol"], r["d1"]) for r in p.get("leaders") or []], title="leaders 1D")
    cross = "".join(
        f'<li><b>{_e(c["industry"])}</b> ({c["n"]}): {", ".join(map(_e, c["names"]))} · avg {_p(c["avg1"])}, '
        f'5D {_p(c["avg5"])}, BSR {_n(c["avg_bsr"], 1)}</li>'
        for c in p.get("cross") or []
    )
    da = " ".join(f'<span class="chip">{_e(s)}</span>' for s in p.get("div_a") or []) or "-"
    db = " ".join(f'<span class="chip">{_e(s)}</span>' for s in p.get("div_b") or []) or "-"
    back = " ".join(
        f'<span class="chip">{_e(k)} <span class="{_cls(v)}">{_p(v)}</span></span>'
        for k, v in (p.get("backdrop") or {}).items()
    )
    return f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Desk brief {_e(p.get("date"))}</title><style>{CSS}{EXTRA_CSS}</style></head><body><main>
<h1>Desk brief · {_e(p.get("date"))}</h1>
<div>{chips}</div><p class="note">{t.get("n", 0)} names bounced (up &gt;0.5% after a down week) · avg {_p(t.get("avg1"))} ·
median {_p(t.get("med1"))} · {t.get("n2", 0)} above +2% · 5D {_p(t.get("avg5"))}</p><div>{back}</div>
<div class="two"><section><h2>Top pick</h2>{_top(p.get("top_pick"), card, hw)}</section>
<section><h2>What matters</h2><ul>{"".join(f"<li>{_e(x)}</li>" for x in p.get("matters") or [])}</ul>
<h2>Desk read</h2>{"".join(f'<p class="note">{_e(x)}</p>' for x in p.get("desk_read") or [])}</section></div>
<h2>Dark pool + options agree ({len(p.get("two_leg") or [])})</h2>
<p class="note">Dark buy ratio above 1 on 1W and 2W and rising, AND more than half the options volume bullish.</p>
{two}{dark_only}
<div class="two"><section><h3>Dark buying, options bearish</h3><div>{da}</div></section>
<section><h3>Options bullish, dark selling</h3><div>{db}</div></section></div>
<div class="two"><section><h2>Group scoreboard</h2><p class="note">Median 1D (the average can be one or two names).</p>
{bars}{groups}</section><section><h2>Leaders</h2>{lead}</section></div>
<div class="two"><section><h2>Fund prints</h2><p class="note">ETFs that passed the screen, heaviest volume first.
Implied = fund move ÷ its daily leverage.</p><table><tr><th>fund</th><th>1D</th><th>volume</th><th>lev</th><th>implied</th></tr>
{prints}</table></section><section><h2>Cross-currents</h2><ul>{cross or "<li>-</li>"}</ul></section></div>
<h2>Scorecard</h2>{_score(score)}
<p class="note">Method: project doc desk-brief-methodology-20261114. Dark ratio, 1D/5D and BSR match his definitions;
options split (all expiries, Convex) and short-term gamma (naive sign, CVForge) are ours. Descriptors, not signals.</p>
</main></body></html>"""
