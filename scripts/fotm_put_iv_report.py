"""SPY vs far-OTM put IV — the "protection floor → reclaim at highs → correction"
tracker (re-openable HTML, inline SVG, phone-safe, no <script>).

Reproduces the @exporiskprivate "SPY vs Avg IV F-OTM Puts" chart from our own
``iv_tenor_snapshots`` (SPY 25Δ-put IV at constant-maturity 30d), marks the
FLOOR / WARN states, and tabulates what SPY did after every prior floor
episode — so the chart's n=2 anecdote becomes a running stat as history banks.

Brain = pure ``trading_intel.market.fotm_put_iv`` (unit-tested). Descriptor
only (FlashAlpha rule 4). History starts 2026-06-23 (collector birth); the two
2025 episodes on the vendor chart are NOT reconstructible (no historical chains).

Run (Windows; DATABASE_URL pointed at the NAS):
    .venv\\Scripts\\python scripts\\fotm_put_iv_report.py
    .venv\\Scripts\\python scripts\\fotm_put_iv_report.py --days 400 --floor 0.13 --reclaim 0.17
"""
from __future__ import annotations

import argparse
from datetime import date, datetime
from pathlib import Path

from sqlalchemy import create_engine, text

from trading_intel.config import get_settings
from trading_intel.market import fotm_put_iv as F

_REPO_ROOT = Path(__file__).resolve().parents[1]
_OUT = _REPO_ROOT / "reports"

BG, CARD, EDGE = "#0b0d11", "#151920", "#2a313c"
TXT, MUT = "#f2f4f7", "#aab3bf"  # high-contrast labels (Mithil: no small grey text)
SPY_C, IV_C, FLOOR_C, WARN_C, GRID = "#ffffff", "#ff4d5a", "#ff4d5a", "#ffb020", "#242a34"


def _p(v, d=1):
    return "—" if v is None else f"{v * 100:.{d}f}%"


def _sp(v, d=1):
    return "—" if v is None else f"{v * 100:+.{d}f}%"


def _chart(pts, *, floor_iv, reclaim_iv, w=560, h=330):
    """Dual-axis: SPY (left, white) vs put IV (right, red); floor bars + WARN markers."""
    ml, mr, mt, mb = 60, 66, 22, 34
    pw, ph = w - ml - mr, h - mt - mb
    n = len(pts)
    if n < 2:
        return f'<svg viewBox="0 0 {w} {h}" width="100%"><rect width="{w}" height="{h}" fill="{CARD}"/><text x="{w/2}" y="{h/2}" fill="{TXT}" font-size="16" text-anchor="middle">no data</text></svg>'
    spots = [p.spot for p in pts]
    ivs = [p.iv for p in pts]
    s_lo, s_hi = min(spots), max(spots)
    s_pad = (s_hi - s_lo) * 0.08 or 1
    s_lo, s_hi = s_lo - s_pad, s_hi + s_pad
    # IV axis: give the price line the top ~55% and IV the bottom ~60% (overlap like the vendor chart)
    i_lo = min(min(ivs), floor_iv) * 0.85
    i_hi = max(max(ivs), reclaim_iv) * 1.9

    def sx(i):
        return ml + pw * i / (n - 1)

    def sy_s(v):
        return mt + ph * 0.55 * (s_hi - v) / (s_hi - s_lo)

    def sy_i(v):
        return mt + ph * (i_hi - v) / (i_hi - i_lo)

    parts = [f'<rect width="{w}" height="{h}" fill="{CARD}" rx="10"/>']
    # floor-day shading
    for i, p in enumerate(pts):
        if p.state == "FLOOR":
            x0 = sx(i) - (pw / (n - 1)) / 2
            parts.append(f'<rect x="{x0:.1f}" y="{mt}" width="{max(2.0, pw/(n-1)):.1f}" height="{ph}" fill="{FLOOR_C}" opacity="0.28"/>')
    # grid + right axis (IV)
    for tv in (floor_iv, reclaim_iv, 0.25, 0.35):
        if tv > i_hi:
            continue
        yy = sy_i(tv)
        dash = ' stroke-dasharray="6,5"' if tv in (floor_iv, reclaim_iv) else ""
        col = FLOOR_C if tv == floor_iv else (WARN_C if tv == reclaim_iv else GRID)
        parts.append(f'<line x1="{ml}" y1="{yy:.1f}" x2="{w-mr}" y2="{yy:.1f}" stroke="{col}" stroke-width="1"{dash} opacity="0.9"/>')
        parts.append(f'<text x="{w-mr+6}" y="{yy+5:.1f}" fill="{col if tv in (floor_iv, reclaim_iv) else MUT}" font-size="16" font-weight="800">{_p(tv, 0)}</text>')
    # left axis (SPY)
    for k in range(4):
        v = s_lo + (s_hi - s_lo) * k / 3
        yy = sy_s(v)
        parts.append(f'<text x="{ml-8}" y="{yy+5:.1f}" fill="{MUT}" font-size="16" text-anchor="end">{v:.0f}</text>')
    # IV line
    parts.append('<polyline fill="none" stroke="%s" stroke-width="2.2" points="%s"/>' % (
        IV_C, " ".join(f"{sx(i):.1f},{sy_i(p.iv):.1f}" for i, p in enumerate(pts))))
    # SPY line
    parts.append('<polyline fill="none" stroke="%s" stroke-width="2" points="%s"/>' % (
        SPY_C, " ".join(f"{sx(i):.1f},{sy_s(p.spot):.1f}" for i, p in enumerate(pts))))
    # WARN markers on the SPY line
    for i, p in enumerate(pts):
        if p.state == "WARN":
            parts.append(f'<circle cx="{sx(i):.1f}" cy="{sy_s(p.spot):.1f}" r="4.5" fill="{WARN_C}" stroke="{BG}" stroke-width="1.2"/>')
    # last-value tags (right side, nudged off the axis labels)
    last = pts[-1]
    y_iv = sy_i(last.iv)
    for tv in (floor_iv, reclaim_iv, 0.25, 0.35):
        if tv <= i_hi and abs(sy_i(tv) - y_iv) < 16:
            y_iv = sy_i(tv) - 18 if y_iv <= sy_i(tv) else sy_i(tv) + 18
    parts.append(f'<text x="{w-mr+6}" y="{y_iv+5:.1f}" fill="{IV_C}" font-size="17" font-weight="800">{_p(last.iv)}</text>')
    parts.append(f'<text x="{w-mr+6}" y="{sy_s(last.spot)+5:.1f}" fill="{SPY_C}" font-size="17" font-weight="800">{last.spot:.0f}</text>')
    # x labels: first, mid, last
    for i in (0, n // 2, n - 1):
        anchor = "start" if i == 0 else ("end" if i == n - 1 else "middle")
        parts.append(f'<text x="{sx(i):.1f}" y="{h-10}" fill="{MUT}" font-size="16" text-anchor="{anchor}">{pts[i].ts:%b %d}</text>')
    return f'<svg viewBox="0 0 {w} {h}" width="100%" xmlns="http://www.w3.org/2000/svg">{"".join(parts)}</svg>'


def _state_pill(state: str) -> str:
    col = {"FLOOR": FLOOR_C, "WARN": WARN_C, "NEUTRAL": MUT, "NO DATA": MUT}.get(state, MUT)
    return f'<span class="pill" style="background:{col}22;color:{col};border:1px solid {col}">{state}</span>'


def _header(read: dict) -> str:
    if read.get("state") == "NO DATA":
        return '<div class="card"><div class="big">NO DATA</div></div>'
    off = read.get("pct_off_high")
    return (
        '<div class="card">'
        f'<div class="row"><div class="big">{_state_pill(read["state"])}</div>'
        f'<div class="kv"><span>put IV</span><b style="color:{IV_C}">{_p(read["iv"])}</b></div>'
        f'<div class="kv"><span>SPY</span><b>{read["spot"]:.2f}</b></div>'
        f'<div class="kv"><span>off 60d high</span><b>{_sp(off, 2)}</b></div></div>'
        f'<div class="readline">{read["read"]}.</div>'
        '<div class="row sub">'
        f'<div class="kv"><span>last floor</span><b>{read.get("last_floor_date") or "—"} @ {_p(read.get("last_floor_iv"))}</b></div>'
        f'<div class="kv"><span>sessions since</span><b>{read.get("days_since_floor") if read.get("days_since_floor") is not None else "—"}</b></div>'
        f'<div class="kv"><span>IV vs 17% line</span><b>{_sp(read.get("iv_vs_reclaim"), 1)}</b></div>'
        f'<div class="kv"><span>WARN run</span><b>{read.get("warn_run_days", 0)}d</b></div>'
        "</div></div>"
    )


def _pair(a, b):
    def one(v):
        if v is None:
            return '<span class="mut">n/a</span>'
        return f'<span class="{"pos" if v >= 0 else "neg"}">{_sp(v)}</span>'
    return f"<td>{one(a)}<br>{one(b)}</td>"


def _table(clusters) -> str:
    if not clusters:
        return '<div class="card"><div class="lbl">Floor episodes</div><div class="mut">none in window</div></div>'
    rows = []
    for c in reversed(clusters):
        warn = f"{c.warn_date:%m-%d}<br>+{c.days_to_warn}d" if c.warn_date else '<span class="mut">none<br>yet</span>'
        dd = '<span class="mut">n/a</span>' if c.max_dd_30d is None else f'<span class="{"pos" if c.max_dd_30d >= 0 else "neg"}">{_sp(c.max_dd_30d)}</span>'
        rows.append(
            "<tr>"
            f"<td class='l'>{c.start:%m-%d}<br>→{c.end:%m-%d}</td>"
            f"<td>{c.n_touches}<br><span style='color:{IV_C}'>{_p(c.min_iv)}</span></td>"
            f"<td>{c.spot_at_floor:.0f}</td>"
            f"<td class='l'>{warn}</td>"
            f"{_pair(c.fwd.get('floor_16d'), c.fwd.get('floor_20d'))}"
            f"{_pair(c.fwd.get('warn_16d'), c.fwd.get('warn_20d'))}"
            f"<td>{dd}</td>"
            "</tr>"
        )
    return (
        '<div class="card"><div class="lbl">Floor episodes → what SPY did next</div>'
        "<table><thead><tr><th class='l'>floor</th><th>touches<br>min IV</th><th>SPY</th>"
        "<th class='l'>WARN</th><th>+16d<br>+20d</th><th>WARN<br>+16/20d</th><th>worst<br>30d</th>"
        f"</tr></thead><tbody>{''.join(rows)}</tbody></table>"
        '<div class="note">+16d/+20d = SPY return 16/20 sessions after the LAST floor day (the vendor chart\'s +2.7%/16d and +3.4%/20d). '
        "WARN +16/20d = the same, measured from the first WARN day. worst 30d = lowest close in the 30 sessions after the floor. n/a = not enough history yet.</div></div>"
    )


def _page(body: str, *, generated: str, n: int, params: dict, first: str, last: str) -> str:
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>SPY vs F-OTM put IV</title><style>
body{{background:{BG};color:{TXT};font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;margin:0;padding:16px 12px 40px;display:flex;justify-content:center}}
.app{{width:100%;max-width:820px}}
h1{{font-size:22px;margin:0 0 4px}}.sub{{color:{MUT};font-size:15px;margin:0 0 14px}}
.card{{background:{CARD};border:1px solid {EDGE};border-radius:14px;padding:14px 16px;margin-bottom:12px}}
.lbl{{font-size:13px;letter-spacing:1.2px;color:{MUT};font-weight:800;text-transform:uppercase;margin-bottom:10px}}
.row{{display:flex;flex-wrap:wrap;gap:18px;align-items:center}}.row.sub{{margin-top:12px;padding-top:10px;border-top:1px solid {EDGE}}}
.big{{font-size:20px;font-weight:800}}
.pill{{font-size:16px;font-weight:800;letter-spacing:1px;padding:6px 14px;border-radius:20px}}
.kv span{{display:block;font-size:13px;color:{MUT};font-weight:700}}.kv b{{font-size:19px;font-family:ui-monospace,Menlo,monospace}}
.readline{{font-size:17px;line-height:1.45;margin-top:12px}}
table{{width:100%;border-collapse:collapse}}th{{font-size:13px;color:{MUT};text-align:right;padding:6px 6px;border-bottom:1px solid {EDGE};font-weight:800}}
td{{font-size:14px;line-height:1.35;padding:8px 5px;white-space:nowrap;text-align:right;border-bottom:1px solid #1b2029;font-family:ui-monospace,Menlo,monospace}}
th.l,td.l{{text-align:left}}.pos{{color:#2fe0a6}}.neg{{color:{IV_C}}}.mut{{color:{MUT}}}
.note{{color:{MUT};font-size:14px;line-height:1.5;margin-top:10px}}
.legend{{font-size:14px;margin:0 0 8px}}.legend i{{display:inline-block;width:14px;height:4px;margin:0 6px 3px 0;vertical-align:middle}}
</style></head><body><div class="app">
<h1>SPY vs far-OTM put IV</h1>
<p class="sub">SPY 25Δ-put IV, constant-maturity 30d &middot; {first} → {last} ({n} sessions) &middot; floor ≤ {_p(params["floor_iv"],0)} &middot; WARN = IV ≥ {_p(params["reclaim_iv"],0)} with SPY within {_p(params["near_high_pct"],0)} of its {params["high_lookback"]}d high &middot; generated {generated}</p>
{body}
<p class="note"><b>Reading it:</b> red bars = floor days (max complacency); amber dots = WARN days (protection bid while the index sits at highs). The vendor chart's pattern: floor → IV reclaims ~17% with SPY still near highs → correction a few weeks later; both prior cases (Jan-25, Dec-25) went HIGHER first for 16–20 sessions. Our history starts 2026-06-23, so those two are not in the table. Descriptor only, not a signal (FlashAlpha rule 4). Rows with &lt;{F.MIN_EXPIRIES} listed expiries are dropped as bad pulls.</p>
</div></body></html>"""


def _fetch(days: int, *, settings=None) -> list[dict]:
    eng = create_engine((settings or get_settings()).DATABASE_URL)
    with eng.connect() as cx:
        rows = cx.execute(text(
            "SELECT ts, iv_put_25d, spot, n_expiries FROM iv_tenor_snapshots "
            "WHERE symbol='SPY' AND tenor_dte=30 AND ts >= now() - (:d || ' days')::interval ORDER BY ts ASC"),
            {"d": days}).mappings().all()
    return [dict(r) for r in rows]


def build(*, days: int = 400, floor_iv: float = F.FLOOR_IV, reclaim_iv: float = F.RECLAIM_IV,
          near_high_pct: float = F.NEAR_HIGH_PCT, out=None, settings=None) -> Path:
    rows = _fetch(days, settings=settings)
    v = F.build_view(rows, floor_iv=floor_iv, reclaim_iv=reclaim_iv, near_high_pct=near_high_pct)
    pts = v["points"]
    legend = (f'<div class="legend"><i style="background:{SPY_C}"></i>SPY &nbsp; <i style="background:{IV_C}"></i>25Δ put IV 30d '
              f'&nbsp; <i style="background:{FLOOR_C};opacity:.5;height:12px"></i>floor day &nbsp; <i style="background:{WARN_C};height:10px;width:10px;border-radius:5px"></i>WARN day</div>')
    body = (_header(v["read"])
            + f'<div class="card">{legend}{_chart(pts, floor_iv=floor_iv, reclaim_iv=reclaim_iv)}</div>'
            + _table(v["clusters"]))
    first = pts[0].ts.isoformat() if pts else "—"
    last = pts[-1].ts.isoformat() if pts else "—"
    out = Path(out) if out else (_OUT / f"fotm_put_iv_{date.today():%Y-%m-%d}.html")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(_page(body, generated=datetime.now().strftime("%Y-%m-%d %H:%M"), n=len(pts),
                         params=v["params"], first=first, last=last), encoding="utf-8")
    print(f"Wrote {out} ({len(pts)} sessions, {len(v['clusters'])} floor episodes, state={v['read'].get('state')})")
    return out


def main():
    p = argparse.ArgumentParser(description="SPY vs far-OTM put-IV floor tracker (HTML).")
    p.add_argument("--days", type=int, default=400)
    p.add_argument("--floor", type=float, default=F.FLOOR_IV)
    p.add_argument("--reclaim", type=float, default=F.RECLAIM_IV)
    p.add_argument("--near-high", type=float, default=F.NEAR_HIGH_PCT)
    p.add_argument("--out", default=None)
    a = p.parse_args()
    build(days=a.days, floor_iv=a.floor, reclaim_iv=a.reclaim, near_high_pct=a.near_high, out=a.out)


if __name__ == "__main__":
    main()
