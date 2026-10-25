"""The brief's map family of sections: MAP card, its grade, and the roll-off.

Split out of ``daily_brief_render`` to keep both modules inside the project's
400-line ceiling (CLAUDE.md). ``daily_brief_render`` imports these three
section functions and stitches them into the page; nothing here knows about
the rest of the brief.
"""

from __future__ import annotations

from typing import Any

from trading_intel.synthesis.render_utils import _esc, _fmt

#: Styles for this family of sections, appended to the brief's stylesheet.
MAP_CSS = """.mapline{display:flex;flex-wrap:wrap;align-items:baseline;gap:9px;padding:9px 0;border-bottom:1px solid #eef1f6;font-size:14px}
.mapline:last-child{border-bottom:0}
.mapline .lb{flex:0 0 132px;color:#6b7684;font-size:11px;letter-spacing:.6px;text-transform:uppercase;font-weight:700}
.mapline .vv{font-weight:700;font-size:15.5px;color:#12233d;font-variant-numeric:tabular-nums}
.mapline .sub2{color:#6b7684;font-size:12.5px}
.gm-long{color:#1a8a4a;font-weight:700}.gm-short{color:#c0392b;font-weight:700}
.gm-call{color:#1a8a4a;font-weight:700}.gm-put{color:#c0392b;font-weight:700}
.odds{display:flex;gap:8px;flex-wrap:wrap}
.odds .o{flex:1 1 120px;background:#f7f9fc;border:1px solid #e2e7ee;border-radius:9px;padding:8px 11px;text-align:center}
.odds .o b{display:block;font-size:19px;font-variant-numeric:tabular-nums}
.odds .o .lab{display:block;font-size:10px;text-transform:uppercase;color:#8a93a0;letter-spacing:.5px;margin-top:2px}
.odds .o.lo b{color:#c0392b}.odds .o.mid b{color:#1a2027}.odds .o.hi b{color:#1a8a4a}
.wv{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:4px}
@media(max-width:680px){.wv{grid-template-columns:1fr}.mapline .lb{flex:1 1 100%}}
.wv .w{background:#f7f9fc;border:1px solid #e2e7ee;border-radius:9px;padding:9px 12px}
.wv .w .hd2{font-size:10.5px;text-transform:uppercase;letter-spacing:.5px;color:#6b7684;font-weight:700;margin-bottom:5px}
.wv .w .pair{font-size:15px;font-weight:700;font-variant-numeric:tabular-nums}
.wv .w .wn{display:block;color:#8a93a0;font-size:11px;margin-top:4px}"""


def _usd_signed(v: float | None) -> str:
    """Dollar magnitude WITH its sign — for net figures that can be negative.

    ``_usd`` deliberately prints magnitude only, because hedge pressure carries
    its direction in the words ("selling" / "buying"). Net gamma does not: a
    remaining book of −$1.1B must not read as $1.1B just because the cell is
    coloured red.
    """
    if v is None:
        return "—"
    return ("\u2212" if float(v) < 0 else "") + _usd(v)


def _usd(v: float | None) -> str:
    """Compact dollar magnitude: $1.4B / $820M / $95K."""
    if v is None:
        return "—"
    a = abs(float(v))
    for unit, div in (("T", 1e12), ("B", 1e9), ("M", 1e6), ("K", 1e3)):
        if a >= div:
            q = a / div
            digits = 2 if q < 10 else (1 if q < 100 else 0)
            return f"${q:,.{digits}f}{unit}"
    return f"${a:,.0f}"


def _pct0(v: float | None) -> str:
    """A 0..1 share as a whole percent."""
    if v is None:
        return "—"
    return f"{abs(float(v)) * 100:.0f}%"


def _map_section(ctx: dict[str, Any]) -> str:
    """The map, in plain English.

    No Greek letters, no vendor field names, no percentages we can't stand
    behind. Same numbers underneath; the reader shouldn't need the vocabulary,
    and anyone who wants the workings asks the MCP (Mithil, 2026-10-25).

    Colour code is his: calls green, puts red, dealers-cushioning green,
    dealers-amplifying red, caution amber.
    """
    m = ctx.get("map_block") or {}
    if not m:
        return ""
    spot = m.get("spot")
    reg = m.get("regime") or {}
    long_g = reg.get("long_gamma")
    rcls = "gm-long" if long_g else "gm-short"
    dot = "\U0001F7E2" if long_g else "\U0001F534"
    headline = (
        "Dealers cushion moves here" if long_g else "Dealers amplify moves here"
    )

    switches = [s for s in (m.get("switches") or []) if s.get("value")]
    if switches:
        main = switches[0]
        where = "below" if not main.get("above") else "above"
        other = "above it they cushion instead" if where == "below" else "below it they amplify instead"
        sw_txt = f"Price is {where} the {_fmt(main['value'], 0)} switch — {other}."
        if len(switches) > 1 and reg.get("between_switches"):
            sw_txt += f" It sits between that and {_fmt(switches[1]['value'], 0)}, where the day often stays."
    else:
        sw_txt = "No switch level available today."

    wv = ""
    for w in m.get("wall_views") or []:
        tag = ' <span class="pill pill-mid">thin</span>' if w.get("stale") else ""
        wv += (
            f'<div class="w"><div class="hd2">{_esc(w["label"])}{tag}</div>'
            f'<div class="pair"><span class="gm-put">{_fmt(w.get("put_wall"), 0)}</span>'
            f'&nbsp; floor&nbsp;&middot;&nbsp;cap &nbsp;'
            f'<span class="gm-call">{_fmt(w.get("call_wall"), 0)}</span></div>'
            f'<span class="wn">{_esc(w.get("note") or "")}</span></div>'
        )
    heaviest = m.get("heaviest")
    heavy_txt = (
        f'<div class="sub2" style="margin-top:7px">Heaviest pile of all: '
        f'<b>{_fmt(heaviest, 0)}</b></div>'
        if heaviest
        else ""
    )

    # --- who traded ----------------------------------------------------
    f = m.get("flow") or {}
    if f.get("found"):
        steady = f.get("steadying")
        fdot = "\U0001F7E2" if steady else "\U0001F534"
        fcls = "gm-long" if steady else "gm-short"
        word = "Mostly sellers" if steady else "Mostly buyers"
        effect = (
            "That hands dealers more inventory, which tends to steady price."
            if steady
            else "That leaves dealers holding less, which tends to loosen price."
        )
        at = " &middot; ".join(
            f'{_fmt(d["strike"], 0)} was {_esc(d["customers"])}'
            for d in (f.get("at_levels") or [])
        )
        busiest = " &middot; ".join(
            f'{_fmt(d["strike"], 0)} {_esc(d["customers"])}'
            for d in (f.get("busiest") or [])[:3]
        )
        extra = f" At the levels: {at}." if at else ""
        flow_row = (
            f'<div class="mapline"><span class="lb">Who traded</span>'
            f'<span class="vv {fcls}">{fdot} {word}</span>'
            f'<span class="sub2">{effect}{extra}'
            + (f' Busiest: {busiest}.' if busiest else "")
            + "</span></div>"
        )
    else:
        flow_row = (
            '<div class="mapline"><span class="lb">Who traded</span>'
            '<span class="vv dim">\u26AA Not yet</span>'
            '<span class="sub2">Buy/sell detail starts from the 2026-10-26 session.</span></div>'
        )

    # --- likely close ---------------------------------------------------
    cone_bits = []
    for c in m.get("cones") or []:
        lo, hi = c.get("lower"), c.get("upper")
        if lo is not None and hi is not None:
            cone_bits.append(f'<b>{c["band_pct"]}%</b> {_fmt(lo, 0)}&ndash;{_fmt(hi, 0)}')
    cones_html = " &nbsp;&middot;&nbsp; ".join(cone_bits) or '<span class="dim">not available</span>'

    # --- if it moves ----------------------------------------------------
    h = m.get("hedge") or {}
    drop, rally, ratio = h.get("sell_on_drop"), h.get("buy_on_rally"), h.get("ratio")
    if drop is not None:
        hedge_html = (
            f'<span class="gm-put">1% down &rarr; dealers sell about {_usd(drop)}</span>'
            f' &nbsp;&middot;&nbsp; <span class="gm-call">1% up &rarr; they buy about {_usd(rally)}</span>'
            + (f' <span class="dim">(down pushes about {ratio:.1f}&times; harder)</span>' if ratio else "")
        )
    else:
        hedge_html = '<span class="dim">not available</span>'

    # --- how solid ------------------------------------------------------
    frag = m.get("fragility") or {}
    firm = frag.get("firm")
    if firm is None:
        sdot, scls, sword = "\u26AA", "dim", "Can't tell"
    elif firm:
        sdot, scls, sword = "\U0001F7E2", "gm-long", "Solid"
    else:
        sdot, scls, sword = "\U0001F7E1", "warn", "Could shift"
    legs = [l for l in (frag.get("legs") or []) if l.get("move") is not None]
    if legs:
        worst = max(legs, key=lambda l: l["move"])
        what = "switch" if worst["name"] == "flip" else "floor and cap"
        solid_txt = (
            f'A small move in volatility slides the {what} about '
            f'{_fmt(worst["move"], 0)} points.'
        )
        if frag.get("calibrating"):
            solid_txt += f' Still learning what counts as a lot — {frag.get("graded_sessions") or 0} days in.'
    else:
        solid_txt = "Not enough chain to test."

    age = m.get("chain_age_days")
    stale_flag = (
        f'<div class="flag">These positions are {age} day(s) old, so they describe '
        "that session's book rather than this morning's.</div>"
        if m.get("chain_stale")
        else ""
    )

    return f"""<h2 class="sec"><span class="n">00a</span>The map &mdash; {_esc(m.get("symbol"))} {_fmt(spot, 0)}</h2>
<div class="card">
<div class="mapline"><span class="lb">Regime</span>
<span class="vv">{dot} <span class="{rcls}">{headline}</span></span>
<span class="sub2">{_esc(sw_txt)}</span></div>

<div class="mapline"><span class="lb">Floor &amp; cap</span>
<div style="flex:1 1 340px"><div class="wv">{wv}</div>{heavy_txt}</div></div>

{flow_row}

<div class="mapline"><span class="lb">Likely close</span>
<span class="sub2" style="font-size:13.5px">{cones_html}</span></div>

<div class="mapline"><span class="lb">If it moves</span>
<span class="sub2" style="font-size:13.5px">{hedge_html}</span></div>

<div class="mapline"><span class="lb">How solid</span>
<span class="vv {scls}">{sdot} {sword}</span>
<span class="sub2">{_esc(solid_txt)}</span></div>
{stale_flag}
<div class="note">{_esc(m.get("caveat") or "")}</div></div>"""


def _map_grade_html(ctx: dict[str, Any]) -> str:
    """"Yesterday" plus, on Mondays, the scorecard — levels only, no odds.

    The three-ways percentages left the brief until Breeden-Litzenberger can
    be computed from banked mid prices, so there is nothing to score a "the
    map leaned X%" verdict against. What survives is what the levels actually
    did, which needs no probability at all.
    """
    from trading_intel.market.grading import scorecard

    rows = ctx.get("map_history") or []
    if not rows:
        return ""

    last = rows[0]
    bucket = last.get("bucket")
    if bucket == "between":
        dot, cls, said = "\U0001F7E2", "ok", "Closed between the floor and the cap"
    elif bucket == "below":
        dot, cls, said = "\U0001F534", "bad", f'Closed below the {_fmt(last.get("floor"), 0)} floor'
    elif bucket == "above":
        dot, cls, said = "\U0001F7E2", "ok", f'Closed above the {_fmt(last.get("cap"), 0)} cap'
    else:
        dot, cls, said = "\u26AA", "dim", "Not graded"

    notes: list[str] = []
    if last.get("floor_touched"):
        notes.append("the floor was tested and held" if last.get("floor_held") else "the floor gave way")
    if last.get("cap_touched"):
        notes.append("the cap was tested and held" if last.get("cap_held") else "the cap was taken out")
    if not notes:
        notes.append("neither level was reached")
    if last.get("inside_range70") is not None:
        notes.append(
            "inside the likely range" if last["inside_range70"] else "outside the likely range"
        )

    d = last.get("date")
    when = d.strftime("%b %d") if hasattr(d, "strftime") else _esc(d)
    yest = (
        f'<div class="mapline"><span class="lb">Yesterday</span>'
        f'<span class="vv">{dot} <span class="{cls}">{_esc(when)}</span></span>'
        f'<span class="sub2">{_esc(said)} &mdash; {_esc(", ".join(notes))}.</span></div>'
    )

    board = ""
    if ctx.get("is_monday") and len(rows) >= 5:
        c = scorecard(rows)
        b = c["buckets"]
        rate = c.get("range70_rate")
        if rate is None:
            cal_cls, cal_txt = "dim", "not enough graded days yet"
        else:
            gap = rate - c["range70_expected"]
            cal_cls = "ok" if abs(gap) <= 0.10 else "warn"
            cal_txt = f"{_pct0(rate)} of closes landed inside it, against a target of 70%"
            if abs(gap) > 0.10:
                cal_txt += " — the range looks too " + ("wide" if gap > 0 else "narrow")
        soft, firm_ = c["soft"], c["firm"]
        if soft["rate"] is not None and firm_["rate"] is not None:
            frag_txt = (
                f'days flagged shaky broke {_pct0(soft["rate"])} of the time '
                f'({soft["n"]} days) against {_pct0(firm_["rate"])} for solid ones ({firm_["n"]})'
            )
        else:
            frag_txt = f'not enough graded days yet (shaky {soft["n"]} / solid {firm_["n"]})'
        verdict = c.get("fragility_verdict")
        board = f"""<h2 class="sec"><span class="n">00d</span>How the map has been doing &mdash; last {c["n"]} sessions</h2>
<div class="card">
<div class="mapline"><span class="lb">Where it closed</span>
<span class="sub2" style="font-size:13.5px"><span class="gm-put">below the floor {b["below"]}</span>
&nbsp;&middot;&nbsp; in between {b["between"]} &nbsp;&middot;&nbsp;
<span class="gm-call">above the cap {b["above"]}</span></span></div>
<div class="mapline"><span class="lb">Likely range</span>
<span class="sub2 {cal_cls}" style="font-size:13.5px">{cal_txt}</span></div>
<div class="mapline"><span class="lb">Levels tested</span>
<span class="sub2" style="font-size:13.5px">floor held {c["floor_holds"]} of {c["floor_tests"]}
&nbsp;&middot;&nbsp; cap held {c["cap_holds"]} of {c["cap_tests"]}</span></div>
<div class="mapline"><span class="lb">Shaky vs solid</span>
<span class="sub2" style="font-size:13.5px">{frag_txt}{(" &mdash; <b>" + _esc(verdict) + "</b>") if verdict else ""}</span></div>
<div class="note">That last row is the how-solid flag checking itself. If shaky days
keep failing to break more often than solid ones, the flag gets dropped.</div></div>"""

    return f'<div class="card" style="margin-top:-6px">{yest}</div>{board}'


def _rolloff_html(ctx: dict[str, Any]) -> str:
    """Monday / OpEx-day gamma roll-off: what expires this week and what survives.

    The step-down is why a pin loosens as the week runs out, and the
    concentration line is the one @GammaGirlLab reads as "the grip tightens
    into that Friday, then releases".
    """
    r = ctx.get("rolloff") or {}
    if not r.get("found") or not r.get("steps"):
        return ""

    gross = r.get("gross") or 0
    rows = ""
    for st in r["steps"]:
        when = "today" if st["dte"] == 0 else f'{st["dte"]}d'
        gcls = "arw-up" if (st.get("remaining_after") or 0) > 0 else "arw-dn"
        rows += (
            f'<tr><td class="d">{_esc(st["expiration"])}'
            f' <span class="dim">({_esc(when)})</span></td>'
            f'<td>{_pct0(st.get("share"))}</td>'
            f'<td class="{gcls}">{_usd_signed(st.get("remaining_after"))}</td>'
            f'<td class="d dim">still in play after it settles'
            f'{" — and the book flips the other way" if _flips(r.get("total"), st.get("remaining_after")) else ""}</td></tr>'
        )

    big = r.get("biggest") or {}
    big_line = (
        f'Most of it sits on <b>{_esc(big.get("expiration"))}</b> at '
        f'&mdash; {_pct0(big.get("share"))} of the whole book'
        + ("" if big.get("is_this_week") else " — and that is NOT this week, so the "
           "grip stays on past Friday")
        if big
        else ""
    )

    today_exp = r.get("expiring_today")
    opex = (
        f'<div class="flag"><b>Monthly OpEx.</b> {_pct0(today_exp.get("share"))} '
        f"of the hedging settles on this morning's prints; {_usd_signed(today_exp.get('remaining_after'))} "
        "is what's left afterwards.</div>"
        if r.get("is_opex") and today_exp
        else ""
    )

    return f"""<h2 class="sec"><span class="n">00e</span>Gamma roll-off &mdash; week to {_esc(r.get("week_end"))}</h2>
<div class="card">
<div class="mapline"><span class="lb">This week</span>
<span class="vv">{_pct0(r.get("week_share"))}</span>
<span class="sub2">of the hedging expires by Friday ({_usd(gross)} in all)</span></div>
<table><thead><tr><th class="d">Expiry</th><th>Share</th><th>Hedging left</th>
<th class="d">&nbsp;</th></tr></thead><tbody>{rows}</tbody></table>
<div class="note">{big_line}</div>
{opex}
<div class="note">How much of the week's hedging survives each expiry. It thins out as
the week runs, which is why levels tend to hold less firmly by Friday.</div></div>"""


def _flips(total: float | None, remaining: float | None) -> bool:
    """Does the net book change sign once this expiry is gone?"""
    if total is None or remaining is None:
        return False
    return (total > 0) != (remaining > 0) and abs(remaining) > 0
