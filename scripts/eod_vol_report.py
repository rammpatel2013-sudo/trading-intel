"""Generate a standalone EOD volatility report (HTML) from stored data.

A command-line analogue of an end-of-day vol dashboard: decomposition, term
structure, VVIX/VIX, implied correlation (dispersion) and the Nations indices,
rendered into one self-contained dark-theme HTML file under ``reports/``.

Reads ONLY stored data (CLAUDE.md rule 1 — no vendor calls here):
- ``vix_data``          via ``dashboard.vix_view`` helpers (VIX/VVIX/term/VRP).
- ``index_skew_daily``  directly off the ORM (SDEX/VOLI/TDEX, cor1m/cor3m,
                        vix_term_*, vvix_vix_ratio, vix_tail_hedging_score).
- the SPX day-over-day VIX decomposition via
  ``dashboard.vix_decomp_data.latest_spx_decomposition``.
- ``quotes_daily``      for the SPX headline move.

Descriptive read only — no signals are written (rule 4).

Run from repo root (venv active):
    python scripts/eod_vol_report.py            # latest stored EOD
    python scripts/eod_vol_report.py --open      # ...and open it in the browser
"""

from __future__ import annotations

import argparse
import sys
import webbrowser
from datetime import date, datetime
from pathlib import Path

import pandas as pd
from sqlalchemy import select

# Repo root on sys.path when invoked as a plain script.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from trading_intel.config import get_settings  # noqa: E402
from trading_intel.dashboard.vix_decomp_data import latest_spx_decomposition  # noqa: E402
from trading_intel.dashboard.vix_view import (  # noqa: E402
    ZONE_LOW_MAX,
    ZONE_MID_MAX,
    classify_term_structure,
    load_vix_history,
    near_term_stress,
    term_structure_from_row,
    vvix_vix_ratio,
    zone_caption,
)
from trading_intel.memory.db import make_session_factory  # noqa: E402
from trading_intel.memory.models import IndexSkewDaily, QuoteDaily  # noqa: E402
from trading_intel.synthesis.eod_knowledge import build_knowledge_blocks  # noqa: E402
from trading_intel.vol.eod_narrative import (  # noqa: E402
    deltas,
    describe,
    dispersion_phrase,
    forward_bullets,
    pctile_phrase,
    term_phrase,
)
from trading_intel.vol.vix_calendar import (  # noqa: E402
    is_market_holiday,
    next_vix_expirations,
    third_friday,
)


def _col(df, name: str) -> list:
    """Column of a DataFrame as a plain list (empty if absent)."""
    try:
        return df[name].tolist()
    except Exception:
        return []

_OUT = Path(__file__).resolve().parents[1] / "reports"

_FACTOR_LABELS = {
    "sticky_strike": "Sticky Strike (mechanical)",
    "parallel_shift": "Parallel Shift (regime/fear)",
    "put_gradient": "Put Skew (downside hedge)",
    "call_gradient": "Call Skew (upside)",
    "down_convexity": "Downside Convexity (tail)",
    "up_convexity": "Upside Convexity (lotto)",
}

_CSS = (
    "body{font-family:-apple-system,Segoe UI,Roboto,Arial,sans-serif;margin:0;background:#0f1115;color:#e6e8eb}"
    ".wrap{max-width:1100px;margin:0 auto;padding:22px}"
    "h1{font-size:23px;margin:0 0 2px}.sub{color:#9aa4b2;font-size:12.5px;margin:0 0 16px}"
    "h2.sec{margin:26px 0 10px;font-size:15px;border-bottom:1px solid #232833;padding-bottom:6px;color:#cbd5e1}"
    ".cards{display:flex;flex-wrap:wrap;gap:10px;margin-bottom:6px}"
    ".c{background:#171a21;border:1px solid #232833;border-left:3px solid #2a2f3a;border-radius:9px;padding:9px 13px;min-width:120px}"
    ".c b{display:block;font-size:18px}.c .lbl{color:#9aa4b2;font-size:10.5px;text-transform:uppercase;letter-spacing:.3px}"
    ".c .note{color:#7f8a9a;font-size:11px;margin-top:2px}"
    ".up{border-left-color:#e74c3c}.down{border-left-color:#2ecc71}.warn{border-left-color:#e2b13c}"
    ".read{background:#141b16;border:1px solid #1f3326;border-radius:10px;padding:13px 17px;margin:8px 0 4px}"
    ".read h3{margin:0 0 6px;font-size:13px;color:#8fd3a6}.read p{margin:6px 0;line-height:1.55;font-size:13px}"
    ".weather{display:flex;gap:8px;margin:6px 0 12px}"
    ".w{flex:1;text-align:center;background:#171a21;border:1px solid #232833;border-radius:9px;padding:8px;font-size:12px;color:#7f8a9a}"
    ".w.on{color:#e6e8eb;border-color:#3a4150;background:#1c212b}"
    ".bars{display:flex;flex-direction:column;gap:5px;margin:6px 0}"
    ".bar{display:grid;grid-template-columns:190px 1fr 64px;align-items:center;gap:8px;font-size:12px}"
    ".bar .t{color:#cbd5e1}.bar .v{text-align:right;font-variant-numeric:tabular-nums}"
    ".track{position:relative;height:14px;background:#11141a;border-radius:4px}"
    ".fill{position:absolute;top:0;bottom:0;border-radius:4px}"
    ".fill.pos{background:#e74c3c;left:50%}.fill.neg{background:#2ecc71;right:50%}"
    ".mid{position:absolute;left:50%;top:-2px;bottom:-2px;width:1px;background:#3a4150}"
    "table{width:100%;border-collapse:collapse;font-size:12.5px;margin-top:4px}"
    "th{text-align:left;color:#8b94a3;font-weight:500;padding:3px 6px;border-bottom:1px solid #232833}"
    "td{padding:3px 6px;border-bottom:1px solid #1a1e26}td.r{text-align:right;font-variant-numeric:tabular-nums}"
    ".term{display:flex;gap:6px;align-items:flex-end;margin:8px 0}"
    ".term .pt{flex:1;text-align:center}.term .pt b{display:block;font-size:15px}.term .pt span{font-size:10.5px;color:#9aa4b2}"
    ".foot{color:#5d6675;font-size:11px;margin-top:24px;text-align:center}"
    ".tag{display:inline-block;font-size:10px;padding:1px 6px;border-radius:4px;background:#232833;color:#9aa4b2;margin-left:6px}"
    ".tabnav{display:flex;flex-wrap:wrap;gap:6px;margin:4px 0 16px;border-bottom:1px solid #232833;padding-bottom:10px}"
    ".tabnav button{font:inherit;font-size:12.5px;color:#9aa4b2;background:#171a21;border:1px solid #232833;border-radius:7px;padding:7px 13px;cursor:pointer}"
    ".tabnav button:hover{color:#e6e8eb;border-color:#3a4150}"
    ".tabnav button.on{color:#0f1115;background:#6ea8fe;border-color:#6ea8fe;font-weight:600}"
    ".tab{display:none}.tab.on{display:block}"
    ".tab h2.sec:first-child{margin-top:0}"
)


# ── formatting helpers ──────────────────────────────────────────────────


def _f(v: float | None, fmt: str = "{:.2f}") -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return "—"
    return fmt.format(v)


def _pct(v: float | None) -> str:
    """A 0..1 percentile to a 0..100 string."""
    if v is None or pd.isna(v):
        return "—"
    return f"{v * 100:.0f}"


def _card(label: str, value: str, note: str = "", cls: str = "") -> str:
    note_html = f'<div class="note">{note}</div>' if note else ""
    return f'<div class="c {cls}"><div class="lbl">{label}</div><b>{value}</b>{note_html}</div>'


# ── data loaders (stored data only) ─────────────────────────────────────


def _latest_skew(session, *, days: int = 252) -> tuple[dict | None, pd.DataFrame]:
    rows = session.execute(
        select(IndexSkewDaily).order_by(IndexSkewDaily.date.desc()).limit(days)
    ).scalars().all()
    if not rows:
        return None, pd.DataFrame()
    rows = list(reversed(rows))  # oldest-first
    latest = rows[-1]
    cols = (
        "date", "cor1m", "cor1m_pctile_252d", "cor3m", "cor3m_pctile_252d",
        "sdex", "sdex_pctile_252d", "voli", "voli_pctile_252d", "tdex", "tdex_pctile_252d",
        "vix_term_9d_30d", "vix_term_3m_30d", "vvix_vix_ratio", "vix_tail_hedging_score",
        "vixeq", "vixeq_pctile_252d", "dspx", "dspx_pctile_252d", "vixeq_vix_spread",
    )
    hist = pd.DataFrame([{c: getattr(r, c) for c in cols} for r in rows])
    return {c: getattr(latest, c) for c in cols}, hist


def _spx_move(session) -> dict | None:
    rows = session.execute(
        select(QuoteDaily.date, QuoteDaily.close)
        .where(QuoteDaily.symbol == "SPX", QuoteDaily.close.is_not(None))
        .order_by(QuoteDaily.date.desc())
        .limit(2)
    ).all()
    if not rows:
        return None
    close = float(rows[0][1])
    prev = float(rows[1][1]) if len(rows) > 1 else None
    chg = (close - prev) if prev else None
    pct = (chg / prev * 100.0) if (chg is not None and prev) else None
    return {"date": rows[0][0], "close": close, "chg": chg, "pct": pct}


# ── interpretive helpers ────────────────────────────────────────────────


_WEATHER = ["CALM", "UNEASY", "STORMY", "SEVERE"]


def _weather_index(vix: float | None, nts: float | None, tail_pct: float | None) -> int:
    """0..3 storm level from VIX zone, front-end inversion and tail-hedge percentile."""
    score = 0
    if vix is not None:
        if vix >= ZONE_MID_MAX:
            score += 2
        elif vix >= ZONE_LOW_MAX:
            score += 1
    if nts is not None and nts > 1.0:  # VIX9D/VIX > 1 → front-end backwardation
        score += 1
    if tail_pct is not None and tail_pct >= 0.80:
        score += 1
    return min(score, 3)


def _factor_bars(decomp) -> str:
    items = list(decomp.factors.items())
    mx = max((abs(v) for _, v in items), default=1.0) or 1.0
    out = []
    for key, val in items:
        width = min(abs(val) / mx * 50.0, 50.0)
        side = "pos" if val >= 0 else "neg"
        style = f"width:{width:.1f}%;left:50%" if val >= 0 else f"width:{width:.1f}%;right:50%;left:auto"
        out.append(
            f'<div class="bar"><div class="t">{_FACTOR_LABELS[key]}</div>'
            f'<div class="track"><div class="mid"></div>'
            f'<div class="fill {side}" style="{style}"></div></div>'
            f'<div class="v">{val:+.2f}</div></div>'
        )
    return '<div class="bars">' + "".join(out) + "</div>"


# ── section builders ────────────────────────────────────────────────────


def _summary_section(spx, vix_row, skew, nts, hist, catalyst) -> str:
    vix = vix_row.get("vix") if vix_row else None
    vvix = vix_row.get("vvix") if vix_row else None
    cor1m = skew.get("cor1m") if skew else None
    cor3m = skew.get("cor3m") if skew else None
    tail_pct = skew.get("tdex_pctile_252d") if skew else None
    widx = _weather_index(vix, nts, tail_pct)

    weather = "".join(
        f'<div class="w {"on" if i == widx else ""}">{name}</div>'
        for i, name in enumerate(_WEATHER)
    )

    cards = ""
    if spx:
        cls = "up" if (spx["pct"] or 0) >= 0 else "down"
        cards += _card("S&P 500 · SPX", _f(spx["close"], "{:,.2f}"),
                       _f(spx["pct"], "{:+.2f}") + "%", cls)
    cards += _card("VIX · 30d implied", _f(vix), zone_caption(vix)[:42] if vix else "",
                   "up" if (vix or 0) >= ZONE_LOW_MAX else "down")
    cards += _card("VVIX · vol-of-vol", _f(vvix, "{:.1f}"))
    cards += _card("COR1M · correlation", _f(cor1m, "{:.2f}"), "dispersion gauge")
    cards += _card("VIX9D/VIX", _f(nts, "{:.2f}"),
                   "front inverted" if (nts or 0) > 1 else "normal slope",
                   "warn" if (nts or 0) > 1 else "")

    # Plain-language move read (day-over-day + week-over-week).
    voli_series = _col(hist, "voli")
    spread_series = _col(hist, "vixeq_vix_spread")
    vol_falling = (deltas(voli_series).dod or 0) < 0
    lines = [
        describe("ATM IV (VolDex)", voli_series, dp=2),
        describe("SkewDex", _col(hist, "sdex"), dp=2),
        describe("TailDex", _col(hist, "tdex"), dp=2),
        describe("COR1M", _col(hist, "cor1m"), dp=2),
    ]
    read_p = " ".join(x for x in lines if "no data" not in x)
    if not read_p:
        read_p = "Not enough stored history yet for day-over-day commentary."

    ctx = {
        "vix": vix, "vix9d": vix_row.get("vix9d") if vix_row else None,
        "tail_pctile": tail_pct, "cor1m": cor1m, "cor3m": cor3m,
        "spread_dod": deltas(spread_series).dod, "vol_falling": vol_falling,
        "catalyst": catalyst,
    }
    bullets = "".join(f"<li>{b}</li>" for b in forward_bullets(ctx))

    return (
        '<h2 class="sec">Summary — today\'s market weather</h2>'
        f'<div class="weather">{weather}</div>'
        f'<div class="cards">{cards}</div>'
        f'<div class="read"><h3>In plain language</h3><p>{read_p}</p></div>'
        f'<div class="read"><h3>What to expect — next day / next week</h3>'
        f'<ul style="margin:0;padding-left:18px;line-height:1.5;font-size:13px">{bullets}</ul></div>'
    )


def _decomp_section(result) -> str:
    if result is None or result.decomposition is None:
        avail = result.days_available if result else 0
        return (
            '<h2 class="sec">Decomposition — mechanical vs. true fear</h2>'
            '<div class="read"><p>Decomposition needs two consecutive SPX '
            f'oi_chain_eod snapshots; have {avail}. It lights up after the next '
            'EOD chain run.</p></div>'
        )
    d = result.decomposition
    total = sum(d.factors.values())
    bars = _factor_bars(d)
    return (
        '<h2 class="sec">Decomposition — mechanical vs. true fear'
        f'<span class="tag">{result.prior:%b %d} → {result.as_of:%b %d}</span></h2>'
        f"{bars}"
        f'<div class="read"><h3>The read</h3>'
        f'<p>Total {total:+.2f} vol pts. Dominant factor: '
        f'<b>{_FACTOR_LABELS[d.dominant]}</b>. {d.regime_read()}.</p>'
        f'<p>Positive = vol bid (fear); negative = vol offered (relief). A crush '
        f'that is mostly <i>sticky strike</i> is mechanical; one driven by '
        f'<i>parallel shift / put gradient / downside convexity</i> is genuine. '
        f'Watch downside convexity going positive on an up day — that is crash '
        f'protection being bid into a rally.</p></div>'
    )


def _term_section(vix_row, vix_hist) -> str:
    term = term_structure_from_row(vix_row)
    if term.empty:
        return '<h2 class="sec">Term Structure</h2><div class="read"><p>No term data.</p></div>'
    shape = classify_term_structure(term)
    pts = "".join(
        f'<div class="pt"><b>{_f(r.level, "{:.2f}")}</b><span>{r.tenor}</span></div>'
        for r in term.itertuples()
    )
    vix9d = vix_row.get("vix9d") if vix_row else None
    vix = vix_row.get("vix") if vix_row else None
    vix3m = vix_row.get("vix3m") if vix_row else None
    moves = " ".join(
        x for x in (
            describe("VIX", _col(vix_hist, "vix"), dp=2),
            describe("VIX9D", _col(vix_hist, "vix9d"), dp=2),
        ) if "no data" not in x
    )
    return (
        f'<h2 class="sec">Term Structure<span class="tag">{shape or "—"}</span></h2>'
        f'<div class="term">{pts}</div>'
        f'{_term_charts(vix_row, vix_hist)}'
        f'<div class="read"><h3>The read</h3><p>{term_phrase(vix9d, vix, vix3m)}</p>'
        f'<p>{moves}</p></div>'
    )


def _vvix_section(vix_row, skew, hist, vix_hist) -> str:
    vvix = vix_row.get("vvix") if vix_row else None
    vix = vix_row.get("vix") if vix_row else None
    ratio = vvix_vix_ratio(vvix, vix)
    cards = (
        _card("VVIX/VIX", _f(ratio, "{:.2f}"), "elevated = latent fragility")
        + _card("VVIX", _f(vvix, "{:.1f}"))
        + _card("VolDex", _f(skew.get("voli") if skew else None, "{:.2f}"),
                f'pctile {_pct(skew.get("voli_pctile_252d") if skew else None)}')
        + _card("SkewDex", _f(skew.get("sdex") if skew else None, "{:.2f}"),
                f'pctile {_pct(skew.get("sdex_pctile_252d") if skew else None)}')
        + _card("TailDex", _f(skew.get("tdex") if skew else None, "{:.2f}"),
                f'pctile {_pct(skew.get("tdex_pctile_252d") if skew else None)}')
    )
    moves = " ".join(
        x for x in (
            describe("VVIX", _col(vix_hist, "vvix"), dp=1),
            describe("VolDex", _col(hist, "voli"), dp=2),
            describe("TailDex", _col(hist, "tdex"), dp=2),
        ) if "no data" not in x
    )
    nations = (
        "The Nations complex (VolDex = ATM IV, SkewDex = put-vs-ATM, TailDex = "
        "deep-OTM crash bid) read together shows whether hedging is building or "
        "exhaling, and which part of the curve."
    )
    return (
        '<h2 class="sec">VVIX / VIX &amp; Nations indices</h2>'
        f'<div class="cards">{cards}</div>'
        f'{_vvix_charts(vix_hist)}'
        f'<div class="read"><h3>The read</h3><p>{moves}</p><p>{nations}</p></div>'
    )


def _cor_section(skew, hist) -> str:
    if not skew:
        return '<h2 class="sec">COR1M Map — implied correlation</h2><div class="read"><p>No correlation data.</p></div>'
    cor1m = skew.get("cor1m")
    cor3m = skew.get("cor3m")
    slope = (cor1m - cor3m) if (cor1m is not None and cor3m is not None) else None
    pct = skew.get("cor1m_pctile_252d")
    vixeq = skew.get("vixeq")
    dspx = skew.get("dspx")
    spread = skew.get("vixeq_vix_spread")
    spread_dod = deltas(_col(hist, "vixeq_vix_spread")).dod
    cards = (
        _card("COR1M", _f(cor1m, "{:.2f}"), f"pctile {_pct(pct)}",
              "warn" if (pct or 0) >= 0.5 else "")
        + _card("COR3M", _f(cor3m, "{:.2f}"))
        + _card("1M−3M slope", _f(slope, "{:+.2f}"),
                "near-term corr stress" if (slope or 0) > 0 else "normal",
                "warn" if (slope or 0) > 0 else "")
        + _card("VIXEQ", _f(vixeq, "{:.2f}"), "single-stock vol")
        + _card("VIXEQ−VIX", _f(spread, "{:.2f}"), "dispersion spread",
                "warn" if (spread_dod or 0) > 0 else "")
        + _card("DSPX", _f(dspx, "{:.2f}"), "dispersion index")
    )
    narrative = dispersion_phrase(
        cor1m=cor1m, cor3m=cor3m, vixeq=vixeq, vix=(vixeq - spread) if (vixeq is not None and spread is not None) else None,
        spread=spread, spread_dod=spread_dod, cor1m_pctile=pct,
    )
    return (
        '<h2 class="sec">COR1M Map — implied correlation &amp; dispersion</h2>'
        f'<div class="cards">{cards}</div>{_cor_charts(hist)}'
        f'<div class="read"><h3>The read</h3><p>{narrative}</p>'
        '<p>VIXEQ is the single-stock leg (DSPX² = VIXEQ² − VIX²). When VIXEQ stays '
        'flat while VIX and COR1M whip around, the move is a positioning/correlation '
        'event, not a fundamental one — and a re-widening VIXEQ−VIX spread as vol '
        'falls is dispersion desks reloading the same trade.</p></div>'
    )


def _next_opex(d: date) -> date:
    """Next standard equity OPEX (third Friday), rolled back if it's a holiday."""
    y, m = d.year, d.month
    for _ in range(3):
        friday = third_friday(y, m)
        opex = friday
        while is_market_holiday(opex):
            opex = opex.fromordinal(opex.toordinal() - 1)
        if opex >= d:
            return opex
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return third_friday(d.year, d.month)


def _rabbit_section(as_of: date, vix_row, skew) -> str:
    vix_exp = next_vix_expirations(as_of, 1)
    next_vix = vix_exp[0] if vix_exp else None
    opex = _next_opex(as_of)
    nts = near_term_stress(vix_row.get("vix9d"), vix_row.get("vix")) if vix_row else None
    tail_pct = skew.get("tdex_pctile_252d") if skew else None

    rows = ""
    if next_vix:
        rows += (
            f"<tr><td>Next VIX expiration</td><td class='r'>{next_vix:%a %b %d, %Y}</td>"
            f"<td class='r'>{(next_vix - as_of).days}d</td></tr>"
        )
    rows += (
        f"<tr><td>Next equity OPEX (3rd Fri, rolled)</td><td class='r'>{opex:%a %b %d, %Y}</td>"
        f"<td class='r'>{(opex - as_of).days}d</td></tr>"
    )

    front = (
        "front still inverted (VIX9D &gt; VIX) — unspent crush fuel"
        if (nts or 0) > 1
        else "front in normal slope — no inversion fuel left"
    )
    tail_txt = (
        "tail hedges washed out (cheap) — protection is inexpensive into the catalyst"
        if (tail_pct is not None and tail_pct <= 0.20)
        else "tail bid present — hedges not cheap"
    )

    return (
        '<h2 class="sec">🐰 Rabbit Hole — the deeper threads</h2>'
        '<table><thead><tr><th>Event horizon</th><th class="r">Date</th><th class="r">Away</th></tr></thead>'
        f'<tbody>{rows}</tbody></table>'
        '<div class="read"><h3>The reversion clock &amp; what to watch</h3>'
        f'<p>The dated catalyst is the OPEX/VIX-expiration cluster above — when the '
        f'biggest gamma open interest rolls off and the board resets. Until then the '
        f'tape pins to the gamma map; after it, moves get freer.</p>'
        f'<p>Front-end read: {front}. Tail read: {tail_txt}.</p>'
        f'<p><i>Not tracked yet:</i> equity breadth (new highs/lows, % above 20-day) '
        f'and the VIXEQ−VIX dispersion spread — Doc leans on those here; they would be '
        f'the natural next data adds.</p></div>'
    )


def _sparkline(series: list[float], *, w: int = 720, h: int = 60) -> str:
    vals = [v for v in series if v is not None and not pd.isna(v)]
    if len(vals) < 2:
        return ""
    lo, hi = min(vals), max(vals)
    rng = (hi - lo) or 1.0
    n = len(vals)
    pts = " ".join(
        f"{i / (n - 1) * w:.1f},{h - (v - lo) / rng * h:.1f}" for i, v in enumerate(vals)
    )
    return (
        f'<svg viewBox="0 0 {w} {h}" width="100%" height="{h}" preserveAspectRatio="none" '
        f'style="margin:6px 0">'
        f'<polyline points="{pts}" fill="none" stroke="#6ea8fe" stroke-width="1.5"/></svg>'
    )


def _ok(v) -> bool:
    return v is not None and not (isinstance(v, float) and pd.isna(v))


def _linechart(series, *, x_labels=None, w: int = 760, h: int = 175,
               title: str = "", y_fmt: str = "{:.1f}") -> str:
    """Self-contained multi-series SVG line chart (no JS/CDN — keeps the report a
    standalone doc). ``series`` = [(name, values_oldest_first, color), ...];
    values may carry None/NaN gaps. ``x_labels`` (len == series length) labels
    the x axis. Returns "" when there is not enough data to plot."""
    allv = [v for _, vals, _ in series for v in vals if _ok(v)]
    if len(allv) < 2:
        return ""
    n = max((len(vals) for _, vals, _ in series), default=0)
    if n < 2:
        return ""
    lo, hi = min(allv), max(allv)
    if hi == lo:
        hi = lo + 1.0
    pad = (hi - lo) * 0.08
    lo -= pad
    hi += pad
    rng = hi - lo or 1.0
    padL, padR, padT, padB = 46, 12, (24 if title else 8), 22
    plotw, ploth = w - padL - padR, h - padT - padB

    def X(i):
        return padL + (i / (n - 1)) * plotw

    def Y(v):
        return padT + (1 - (v - lo) / rng) * ploth

    grid = []
    for t in range(4):
        yv = lo + rng * t / 3
        yy = Y(yv)
        grid.append(f'<line x1="{padL}" y1="{yy:.1f}" x2="{w - padR}" y2="{yy:.1f}" stroke="#232833" stroke-width="1"/>')
        grid.append(f'<text x="{padL - 6}" y="{yy + 3:.1f}" text-anchor="end" font-size="9" fill="#7f8a9a">{y_fmt.format(yv)}</text>')
    xlab = []
    if x_labels and len(x_labels) == n:
        step = max(1, n // 6)
        for i in range(0, n, step):
            xlab.append(f'<text x="{X(i):.1f}" y="{h - 6}" text-anchor="middle" font-size="9" fill="#7f8a9a">{x_labels[i]}</text>')
    polys, legend = [], []
    for name, vals, color in series:
        pts = [f"{X(i):.1f},{Y(v):.1f}" for i, v in enumerate(vals) if _ok(v)]
        if len(pts) >= 2:
            polys.append(f'<polyline points="{" ".join(pts)}" fill="none" stroke="{color}" stroke-width="1.7"/>')
            idxs = [i for i, v in enumerate(vals) if _ok(v)]
            li = idxs[-1]
            polys.append(f'<circle cx="{X(li):.1f}" cy="{Y(vals[li]):.1f}" r="2.5" fill="{color}"/>')
        legend.append(f'<span style="color:{color};font-size:11px;margin-right:14px">&#9632; {name}</span>')
    title_html = f'<text x="{padL}" y="15" font-size="11" fill="#cbd5e1">{title}</text>' if title else ""
    svg = (f'<svg viewBox="0 0 {w} {h}" width="100%" height="{h}" style="margin:6px 0">'
           f'{"".join(grid)}{title_html}{"".join(xlab)}{"".join(polys)}</svg>')
    leg = f'<div style="margin:0 0 6px">{"".join(legend)}</div>' if len(series) > 1 else ""
    return svg + leg


def _dates(df) -> list:
    return [str(d)[5:10] for d in _col(df, "date")]


def _curve_at(df, idx) -> list:
    try:
        r = df.iloc[idx]
        return [r.get("vix9d"), r.get("vix"), r.get("vix3m"), r.get("vix6m")]
    except Exception:
        return [None, None, None, None]


def _term_charts(vix_row, vix_hist) -> str:
    if vix_hist is None or getattr(vix_hist, "empty", True):
        return ""
    n = len(vix_hist)
    today = _curve_at(vix_hist, -1)
    yday = _curve_at(vix_hist, -2) if n >= 2 else [None] * 4
    wk = _curve_at(vix_hist, -6) if n >= 6 else (_curve_at(vix_hist, 0) if n >= 2 else [None] * 4)
    overlay = _linechart(
        [("Today", today, "#6ea8fe"), ("Yesterday", yday, "#e2b13c"), ("Last week", wk, "#8a92a3")],
        x_labels=["9D", "30D", "3M", "6M"],
        title="Term structure — today vs yesterday vs last week", y_fmt="{:.1f}",
    )
    hist = _linechart(
        [("VIX", _col(vix_hist, "vix"), "#6ea8fe"), ("VIX9D", _col(vix_hist, "vix9d"), "#e2b13c"),
         ("VIX3M", _col(vix_hist, "vix3m"), "#2ecc71")],
        x_labels=_dates(vix_hist), title="VIX complex history", y_fmt="{:.1f}",
    )
    return overlay + hist


def _vvix_charts(vix_hist) -> str:
    if vix_hist is None or getattr(vix_hist, "empty", True):
        return ""
    labels = _dates(vix_hist)
    vvix = _col(vix_hist, "vvix")
    vix = _col(vix_hist, "vix")
    ratio = [(a / b) if (_ok(a) and _ok(b) and b) else None for a, b in zip(vvix, vix)]
    c1 = _linechart([("VVIX", vvix, "#8a92a3")], x_labels=labels, title="VVIX history", y_fmt="{:.0f}")
    c2 = _linechart([("VVIX/VIX", ratio, "#6ea8fe")], x_labels=labels, title="VVIX / VIX ratio", y_fmt="{:.2f}")
    return c1 + c2


def _cor_charts(hist) -> str:
    if hist is None or getattr(hist, "empty", True):
        return ""
    labels = _dates(hist)
    c1 = _linechart(
        [("COR1M", _col(hist, "cor1m"), "#6ea8fe"), ("COR3M", _col(hist, "cor3m"), "#2ecc71")],
        x_labels=labels, title="Implied correlation (COR1M vs COR3M)", y_fmt="{:.2f}",
    )
    c2 = _linechart([("VIXEQ-VIX spread", _col(hist, "vixeq_vix_spread"), "#e2b13c")],
                    x_labels=labels, title="Dispersion spread (VIXEQ - VIX)", y_fmt="{:.2f}")
    return c1 + c2



# ── assembly ────────────────────────────────────────────────────────────


def _tab_figures(spx, vix_row, skew, nts, decomp, hist, vix_hist) -> dict[str, str]:
    """Per-tab figures text (with d/d + w/w context) fed to the knowledge LLM.

    Uses ``describe()`` so each metric carries its day-over-day and week-over-week
    move, giving the model the comparison it needs to interpret rather than just
    restate the level.
    """
    vr = vix_row or {}
    sk = skew or {}
    ratio = vvix_vix_ratio(vr.get("vvix"), vr.get("vix"))
    cor1m, cor3m = sk.get("cor1m"), sk.get("cor3m")
    slope = (cor1m - cor3m) if (cor1m is not None and cor3m is not None) else None

    vix_d = describe("VIX", _col(vix_hist, "vix"))
    vix9d_d = describe("VIX9D", _col(vix_hist, "vix9d"))
    vvix_d = describe("VVIX", _col(vix_hist, "vvix"), dp=1)
    voli_d = describe("VolDex", _col(hist, "voli"))
    sdex_d = describe("SkewDex", _col(hist, "sdex"))
    tdex_d = describe("TailDex", _col(hist, "tdex"))
    cor_d = describe("COR1M", _col(hist, "cor1m"))
    spread_d = describe("VIXEQ-VIX spread", _col(hist, "vixeq_vix_spread"))

    decomp_txt = "Decomposition not available (needs two consecutive SPX EOD chains)."
    if decomp is not None and getattr(decomp, "decomposition", None) is not None:
        d = decomp.decomposition
        decomp_txt = (
            f"Dominant factor {_FACTOR_LABELS.get(d.dominant, d.dominant)}; "
            f"total {sum(d.factors.values()):+.2f} vol pts (positive = vol bid/fear, "
            f"negative = vol offered/relief). {d.regime_read()}."
        )

    spx_txt = (
        f"SPX {_f(spx['close'], '{:,.2f}')} ({_f(spx['pct'], '{:+.2f}')}%). "
        if spx else ""
    )
    return {
        "summary": (
            f"{spx_txt}{vix_d} {vvix_d} VVIX/VIX {_f(ratio)}; VIX9D/VIX {_f(nts)} "
            f"({'front inverted' if (nts or 0) > 1 else 'normal slope'}). "
            f"{voli_d} {sdex_d} {tdex_d} {cor_d} "
            f"TailDex pctile {_pct(sk.get('tdex_pctile_252d'))}."
        ),
        "decomp": decomp_txt,
        "term": (
            f"{vix9d_d} {vix_d} VIX3M {_f(vr.get('vix3m'))}, VIX6M {_f(vr.get('vix6m'))}. "
            f"VIX9D/VIX {_f(nts)} ({'front inverted' if (nts or 0) > 1 else 'normal slope'}); "
            f"30D-vs-3M {'contango' if (vr.get('vix3m') or 0) > (vr.get('vix') or 0) else 'inverted'}."
        ),
        "vvix": (
            f"{vvix_d} VVIX/VIX {_f(ratio)}. {voli_d} (pctile {_pct(sk.get('voli_pctile_252d'))}); "
            f"{sdex_d} (pctile {_pct(sk.get('sdex_pctile_252d'))}); "
            f"{tdex_d} (pctile {_pct(sk.get('tdex_pctile_252d'))})."
        ),
        "rabbit": (
            f"VIX9D/VIX {_f(nts)} "
            f"({'front still inverted — unspent crush fuel' if (nts or 0) > 1 else 'normal front slope'}); "
            f"TailDex pctile {_pct(sk.get('tdex_pctile_252d'))} "
            f"({'tail hedges washed out / cheap' if (sk.get('tdex_pctile_252d') or 1) <= 0.20 else 'tail bid present'})."
        ),
        "cor": (
            f"{cor_d} (pctile {_pct(sk.get('cor1m_pctile_252d'))}); COR3M {_f(cor3m)}; "
            f"1M-3M slope {_f(slope, '{:+.2f}')}. {spread_d} "
            f"VIXEQ {_f(sk.get('vixeq'))}, DSPX {_f(sk.get('dspx'))}."
        ),
    }


def build(*, days: int = 252, llm=None, settings=None) -> str:
    """Assemble the EOD vol report from stored data.

    When an ``llm`` (an ``LLMProvider``) is supplied, each tab also gets a
    "Knowledge read" note grounded in the desk knowledge base (pgvector +
    local Ollama, per CLAUDE.md rule 7). The note degrades silently if the LLM
    or retrieval is unavailable, so the deterministic report always renders.
    """
    settings = settings or get_settings()
    factory = make_session_factory(settings)
    with factory() as session:
        skew, hist = _latest_skew(session, days=days)
        spx = _spx_move(session)
        vix_hist = load_vix_history(session, days=days)
        decomp = latest_spx_decomposition(session)

    vix_row = vix_hist.iloc[-1].to_dict() if not vix_hist.empty else None
    nts = near_term_stress(vix_row.get("vix9d"), vix_row.get("vix")) if vix_row else None

    as_of = (skew["date"] if skew else None) or (spx["date"] if spx else None) or date.today()

    # Nearest dated catalyst (VIX expiration vs equity OPEX) for the forward read.
    _vix_exp = next_vix_expirations(as_of, 1)
    _opex = _next_opex(as_of)
    _cands = [("VIX expiration", _vix_exp[0]) if _vix_exp else None, (f"{_opex:%b} OPEX", _opex)]
    _cands = [c for c in _cands if c]
    _soon = min(_cands, key=lambda c: c[1]) if _cands else None
    catalyst = (_soon[0], (_soon[1] - as_of).days) if _soon else None

    tabs = [
        ("summary", "Summary", _summary_section(spx, vix_row, skew, nts, hist, catalyst)),
        ("decomp", "Decomposition", _decomp_section(decomp)),
        ("term", "Term Structure", _term_section(vix_row, vix_hist)),
        ("vvix", "VVIX / VIX", _vvix_section(vix_row, skew, hist, vix_hist)),
        ("rabbit", "🐰 Rabbit Hole", _rabbit_section(as_of, vix_row, skew)),
        ("cor", "COR1M Map", _cor_section(skew, hist)),
    ]

    # Knowledge-grounded per-tab notes (local Ollama; rule 7). Appended below the
    # deterministic content of each tab. Degrades to nothing if llm is None or
    # retrieval/the model is unavailable.
    if llm is not None:
        figures = _tab_figures(spx, vix_row, skew, nts, decomp, hist, vix_hist)
        try:
            with factory() as session:
                blocks = build_knowledge_blocks(
                    session, llm, settings, as_of=str(as_of), figures=figures
                )
        except Exception:  # never let the knowledge layer break the report
            blocks = {}
        if blocks:
            tabs = [(tid, label, html + blocks.get(tid, "")) for tid, label, html in tabs]

    nav = "".join(
        f'<button class="{"on" if i == 0 else ""}" onclick="showTab(\'{tid}\',this)">{label}</button>'
        for i, (tid, label, _) in enumerate(tabs)
    )
    panels = "".join(
        f'<section id="{tid}" class="tab {"on" if i == 0 else ""}">{html}</section>'
        for i, (tid, _, html) in enumerate(tabs)
    )
    script = (
        "<script>function showTab(id,btn){"
        "document.querySelectorAll('.tab').forEach(function(t){t.classList.remove('on')});"
        "document.querySelectorAll('.tabnav button').forEach(function(b){b.classList.remove('on')});"
        "document.getElementById(id).classList.add('on');btn.classList.add('on');}</script>"
    )

    page = (
        f'<!doctype html><html><head><meta charset="utf-8">'
        f'<title>EOD Vol Report — {as_of}</title><style>{_CSS}</style></head>'
        f'<body><div class="wrap">'
        f'<h1>EOD Volatility Report</h1>'
        f'<p class="sub">trading-intel · close of {as_of} · generated {datetime.now():%Y-%m-%d %H:%M} '
        f'· descriptive read only (FlashAlpha rule 4)</p>'
        f'<div class="tabnav">{nav}</div>'
        f'{panels}'
        f'<p class="foot">Stored-data report. Series may differ in freshness; each tab is stamped with its own as-of where it matters.</p>'
        f'{script}'
        f'</div></body></html>'
    )

    _OUT.mkdir(parents=True, exist_ok=True)
    path = _OUT / f"eod_vol_{as_of}.html"
    path.write_text(page, encoding="utf-8")
    return str(path)


def main() -> None:
    p = argparse.ArgumentParser(description="Generate an EOD volatility report (HTML) from stored data.")
    p.add_argument("--days", type=int, default=252, help="history depth for percentiles/sparklines")
    p.add_argument("--open", action="store_true", help="open the report in the default browser")
    p.add_argument("--no-knowledge", action="store_true",
                   help="skip the knowledge-grounded per-tab notes (no LLM/Ollama)")
    args = p.parse_args()

    llm = None
    if not args.no_knowledge:
        try:  # local Ollama; degrades silently inside build() if unreachable
            from trading_intel.synthesis.llm import OllamaProvider

            llm = OllamaProvider(get_settings())
        except Exception:
            llm = None

    path = build(days=args.days, llm=llm)
    print("wrote", path)
    if args.open:
        webbrowser.open(Path(path).as_uri())


if __name__ == "__main__":
    main()
