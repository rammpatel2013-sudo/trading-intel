"""HTML for the daily brief's tape pillar (2026-11-13 overnight). Plain words, big marks."""
from __future__ import annotations

from typing import Any

from trading_intel.synthesis.render_utils import _esc, _fmt

_MARK = {"yes": ("✔", "#16a34a"), "no": ("✘", "#dc2626"), "no data": ("–", "#94a3b8")}
_POSTURE = {"press": "#16a34a", "selective": "#d97706", "stand aside": "#dc2626"}


def _pct(x: float | None, nd: int = 0) -> str:
    return "–" if x is None else f"{x * 100:.{nd}f}%"


def _yes(v: bool | None) -> str:
    return '<b style="color:#16a34a">▲</b>' if v else ('<b style="color:#dc2626">▼</b>' if v is False else "–")


def tape_section(ctx: dict[str, Any]) -> str:
    t = ctx.get("tape") or {}
    if not t:
        return ""
    p = t.get("posture") or {}
    word = p.get("word") or "selective"
    col = _POSTURE.get(word, "#334155")
    why = "; ".join(p.get("why") or []) or "mixed signals"
    rows = "".join(
        f'<tr><td style="font-size:18px;color:{_MARK[b["state"]][1]};width:28px">{_MARK[b["state"]][0]}</td>'
        f'<td><b>{_esc(b["item"])}</b><div class="note" style="margin:2px 0 0">{_esc(b["detail"])}</div></td></tr>'
        for b in t.get("checklist") or [])
    w = t.get("washout") or {}
    witems = ", ".join(f"{_esc(k)} {('–' if v is None else _fmt(v, 1))}" for k, v in (w.get("items") or {}).items())
    ftd = t.get("ftd") or {}
    sb = t.get("stockbee") or {}
    sh = t.get("shock") or {}
    fb = t.get("full_book") or {}
    board = "".join(
        f'<tr><td><b>{_esc(r["symbol"])}</b></td><td>{_fmt(r["close"], 2)}</td>'
        f'<td style="color:{"#16a34a" if r["chg"] >= 0 else "#dc2626"}">{r["chg"]:+.2f}%</td>'
        f'<td>{_yes(r.get("above_10"))}</td><td>{_yes(r.get("above_21"))}</td>'
        f'<td>{_yes(r.get("above_50"))}</td><td>{_yes(r.get("above_200"))}</td><td>{r["off_high"]:.1f}%</td></tr>'
        for r in t.get("board") or [])
    shock = ""
    if sh.get("n"):
        shock = (f'<div class="note"><b>After a VIX jump of +30% or more in a day</b> ({sh["n"]} times in our data, '
                 f'last {_esc(sh.get("last"))}): SPX 20 days later averaged {_pct(sh.get("shock_20d"), 1)} '
                 f'(up {_pct(sh.get("shock_20d_up"))} of the time) vs {_pct(sh.get("base_20d"), 1)} on any day. '
                 f'Small sample - read as a hint, not a rule.</div>')
    fbl = ""
    if fb:
        fbl = (f'<div class="note"><b>Dealer gamma, full book (all expiries):</b> {_esc(fb.get("label"))} '
               f'at {_fmt(fb.get("spot"), 0)}, zero-gamma flip {_fmt(fb.get("flip"), 0)} '
               f'(book of {_esc(fb.get("as_of"))}). The front-3-expiry line above can disagree on ~1 day in 6; '
               f'this one matched next-day move size better in our test.</div>')
    return f"""<h2 class="sec"><span class="n">T</span>Tape pillar - is it a bottom?</h2>
<div class="card">
<div style="font-size:22px;font-weight:800;color:{col};text-transform:uppercase">Posture: {_esc(word)}</div>
<div class="note">Why: {_esc(why)}. Press = add risk; selective = only the best setups; stand aside = protect.</div>
<div style="font-size:16px;margin-top:8px"><b>{_esc(t.get("vol_read") or "")}</b></div>
<div style="font-size:17px;margin:12px 0 6px"><b>Bottom checklist: {t.get("checked", 0)} of {t.get("checkable", 0)}</b>
<span class="note"> (items marked – need data we don't collect, or are manual)</span></div>
<table style="width:100%;border-collapse:collapse;font-size:14px">{rows}</table>
<div class="note"><b>Washout score {_fmt(w.get("score"), 1)} / 6</b> ({witems}). Lows without a washout get retested more often.</div>
<div class="note"><b>Rally state:</b> {_esc(ftd.get("state") or "–")}{f" - day {ftd['attempt_day']}" if ftd.get("attempt_day") else ""}.
A follow-through fails if SPX closes below the rally low ({_fmt(ftd.get("low"), 0)}).</div>
{shock}{fbl}
</div>
<div class="card"><b style="font-size:15px">Breadth (S&amp;P 500 names, Stockbee style) - {_esc(sb.get("as_of") or "")}</b>
<div class="lvl" style="margin-top:8px">
<div class="chip"><span class="lab">Up 4%+ / down 4%+</span><b>{sb.get("up4", "–")} / {sb.get("down4", "–")}</b></div>
<div class="chip"><span class="lab">Up / down 25% in a quarter</span><b>{sb.get("up25q", "–")} / {sb.get("down25q", "–")}</b></div>
<div class="chip"><span class="lab">Above 40 / 50-day</span><b>{sb.get("pct_above_40", "–")}% / {sb.get("pct_above_50", "–")}%</b></div>
<div class="chip"><span class="lab">Up-volume share</span><b>{_pct(sb.get("up_share"))}</b></div>
<div class="chip"><span class="lab">New highs / lows</span><b>{sb.get("new_highs", "–")} / {sb.get("new_lows", "–")}</b></div>
<div class="chip"><span class="lab">Setups: tight bases / pullbacks</span><b>{sb.get("setups_base", "–")} / {sb.get("setups_pullback", "–")}</b></div>
</div><div class="note">Many 4% up days and many setups = a healthy tape; many 4% down days = sellers in charge.</div></div>
<div class="card"><b style="font-size:15px">Price board - above (▲) or below (▼) each average</b>
<table style="width:100%;border-collapse:collapse;font-size:14px;margin-top:6px">
<tr style="color:#64748b"><td></td><td>Close</td><td>Day</td><td>10d</td><td>21d</td><td>50d</td><td>200d</td><td>Off high</td></tr>
{board}</table></div>"""
