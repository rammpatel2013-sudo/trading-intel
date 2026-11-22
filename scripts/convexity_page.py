"""convexity_page.py - created 2026-11-22. Renders scripts/cvxmap/data/convexity.html from convexity.json
(scheduler/jobs/convexity.py). Static HTML + inline SVG (no Chart.js), phone-friendly. Display only (rule 4).

Sections: the four stock lists (cheap upside / cheap downside / rich premium / avoid selling), the cross-asset
ETF vol board (strategy map: IV rank vs IV30 - HV30; term map: IV30/IV90 vs IV rank; the table), and the paper
track record of every stored list at 5 / 10 / 20 sessions against its base rate.
Run: ``python scripts/convexity_page.py [--dir scripts/cvxmap/data]`` (NAS: run_script.sh convexity_page).
"""
from __future__ import annotations

import argparse
import html
import json
import sys
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent / "cvxmap" / "data"
INK, AX, GRN, RED, AMB, BLU = "#1F2328", "#6B7079", "#2F7D4F", "#B23A2E", "#9A6A12", "#3D63C9"
BUCKET = {
    "cheap_up": ("Cheap upside", GRN, "Calls are cheap on a stock in a rising trend. Options price less movement "
                 "than usual for this name, and the stock is beating the market. Idea space: long calls / call spreads."),
    "cheap_down": ("Cheap downside", RED, "Puts are cheap on a stock in a falling trend that is lagging the market. "
                   "Idea space: long puts / put spreads, or a cheap hedge for a related holding."),
    "rich_sell": ("Rich premium", BLU, "Options are expensive (top 20% of the year, well above recent realised moves) "
                  "on a stock that is still rising. Idea space: sell puts / put spreads below support, covered calls."),
    "avoid": ("Avoid selling", AMB, "Options are pricing LESS movement than the stock is actually making, short-dated "
              "vol is above long-dated, and the trend is down. Selling premium here is picking up nickels."),
}
QUAD = {"sell premium": GRN, "buy options": BLU, "high IV, moving - stand aside": RED, "cheap, quiet - spreads": AMB}


def esc(s) -> str:
    return html.escape("" if s is None else str(s))


def pct(v, dec: int = 0, sign: bool = False) -> str:
    if v is None:
        return "–"
    return f"{100 * v:+.{dec}f}%" if sign else f"{100 * v:.{dec}f}%"


def fx(v, fmt: str, mult: float = 1.0, suffix: str = "") -> str:
    return "–" if v is None else format(v * mult, fmt) + suffix


def vp(v) -> str:          # vol points
    return "–" if v is None else f"{100 * v:+.1f}"


def _table(b: str, rows: list) -> str:
    name, col, about = BUCKET[b]
    if not rows:
        body = '<p class="none">No names today.</p>'
    else:
        tr = []
        for p in rows:
            tr.append(f'<tr><td class="s">{esc(p["symbol"])}</td><td>{pct(p.get("iv30"))}</td>'
                      f'<td>{pct(p.get("iv_rank"))}</td><td>{vp(p.get("carry"))}</td>'
                      f'<td>{fx(p.get("ts_30_90"), ".2f")}</td>'
                      f'<td>{fx(p.get("skew_rank"), ".0f", 100, "%")}</td>'
                      f'<td>{esc(p.get("code") or "–")} / W{esc(p.get("wstage") or "–")}</td>'
                      f'<td>{fx(p.get("mrs"), "+.1f")}</td>'
                      f'<td>±{pct(p.get("move20"), 1)}</td><td>{pct(p.get("ret20"), 1, True)}</td>'
                      f'<td class="e">{esc(p.get("next_earn") or "")}</td></tr>')
        body = ('<div class="tw"><table><thead><tr><th>Name</th><th>IV30</th><th>IV rank</th><th>IV−HV10</th>'
                '<th>30/90</th><th>Skew rank</th><th>Stage d/wk</th><th>RS</th><th>20d move priced</th>'
                f'<th>Last 20d</th><th></th></tr></thead><tbody>{"".join(tr)}</tbody></table></div>')
    return f'<section class="bk" style="border-top:4px solid {col}"><h2 style="color:{col}">{esc(name)}</h2><p>{esc(about)}</p>{body}</section>'


def _scatter(board: list, xk: str, yk: str, xr: tuple, yr: tuple, xlab: str, ylab: str, lines: tuple,
             W: int = 560, H: int = 360) -> str:
    pts = [r for r in board if r.get(xk) is not None and r.get(yk) is not None]
    if not pts:
        return '<p class="none">No ETF rows with both values yet.</p>'
    PL, PR, PT, PB = 44, 12, 12, 34
    iw, ih = W - PL - PR, H - PT - PB
    X = lambda v: PL + iw * (min(max(v, xr[0]), xr[1]) - xr[0]) / (xr[1] - xr[0])  # noqa: E731
    Y = lambda v: PT + ih * (1 - (min(max(v, yr[0]), yr[1]) - yr[0]) / (yr[1] - yr[0]))  # noqa: E731
    o = [f'<svg viewBox="0 0 {W} {H}" role="img" aria-label="{esc(ylab)} against {esc(xlab)}">',
         f'<rect x="{PL}" y="{PT}" width="{iw}" height="{ih}" fill="#FAFBFC" stroke="#E3E6EA"/>']
    lx, ly = lines
    if lx is not None:
        o.append(f'<line x1="{X(lx):.1f}" x2="{X(lx):.1f}" y1="{PT}" y2="{PT + ih}" stroke="#C9CDD3" stroke-dasharray="4 3"/>')
    if ly is not None:
        o.append(f'<line x1="{PL}" x2="{PL + iw}" y1="{Y(ly):.1f}" y2="{Y(ly):.1f}" stroke="#C9CDD3" stroke-dasharray="4 3"/>')
    for k in range(5):
        xv = xr[0] + (xr[1] - xr[0]) * k / 4
        yv = yr[0] + (yr[1] - yr[0]) * k / 4
        o.append(f'<text x="{X(xv):.1f}" y="{H - 16}" text-anchor="middle">{xv:.2g}</text>'
                 f'<text x="{PL - 5}" y="{Y(yv) + 4:.1f}" text-anchor="end">{yv:.2g}</text>')
    o.append(f'<text x="{PL + iw / 2:.1f}" y="{H - 2}" text-anchor="middle" class="al">{esc(xlab)}</text>'
             f'<text x="12" y="{PT + ih / 2:.1f}" text-anchor="middle" class="al" transform="rotate(-90 12 {PT + ih / 2:.1f})">{esc(ylab)}</text>')
    placed: list = []
    for r in sorted(pts, key=lambda r: r["symbol"]):
        x, y = X(r[xk]), Y(r[yk])
        col = QUAD.get(r.get("quad") or "", AX)
        o.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4" fill="{col}" fill-opacity=".8"><title>{esc(r["symbol"])}</title></circle>')
        ty = y - 6
        while any(abs(ty - py) < 10 and abs(x - px) < 30 for px, py in placed):
            ty -= 10
        placed.append((x, ty))
        o.append(f'<text x="{x + 5:.1f}" y="{ty:.1f}" class="pl">{esc(r["symbol"])}</text>')
    o.append("</svg>")
    return "".join(o)


def _board(board: list) -> str:
    if not board:
        return ""
    smap = _scatter([{**r, "vrp_pts": None if r.get("vrp") is None else 100 * r["vrp"]} for r in board],
                    "iv_rank", "vrp_pts", (0, 1), (-15, 15), "IV rank (0 = cheapest of the year, 1 = richest)",
                    "IV30 − HV30, vol pts", (0.5, 0))
    tmap = _scatter(board, "ts_30_90", "iv_rank", (0.8, 1.2), (0, 1), "IV30 ÷ IV90 (above 1 = short-dated stress)",
                    "IV rank", (1.0, 0.5))
    keyh = "".join(f'<span><i style="background:{c}"></i>{esc(k)}</span>' for k, c in QUAD.items())
    tr = []
    for r in sorted(board, key=lambda r: (r["grp"], r["symbol"])):
        q = r.get("quad")
        tr.append(f'<tr><td class="s">{esc(r["symbol"])}</td><td>{esc(r["grp"])}</td><td>{pct(r.get("iv30"))}</td>'
                  f'<td>{pct(r.get("hv30"))}</td><td>{vp(r.get("vrp"))}</td><td>{pct(r.get("iv_rank"))}</td>'
                  f'<td>{fx(r.get("ts_30_90"), ".2f")}</td>'
                  f'<td>{fx(r.get("z20"), "+.1f", 1, "σ")}</td>'
                  f'<td><span class="q" style="color:{QUAD.get(q or "", AX)}">{esc(q or "–")}</span>'
                  f'{"" if r.get("iv_src") != "hv20" else " <em>(no option IV: HV20 stand-in)</em>"}</td></tr>')
    return (f'<section><h2>Cross-asset vol board</h2><p>Same read for the ETF grid (stocks, bonds, commodities, world, '
            f'sectors, FX, vol). <b>VRP</b> = IV30 minus 30-day realised vol: above 0 = options cost more than the moves. '
            f'<b>IV rank</b> = where IV30 sits in its own year.</p><div class="key">{keyh}</div>'
            f'<div class="two"><figure><figcaption>Strategy map</figcaption>{smap}</figure>'
            f'<figure><figcaption>Term structure vs IV rank</figcaption>{tmap}</figure></div>'
            f'<div class="tw"><table><thead><tr><th>ETF</th><th>Group</th><th>IV30</th><th>HV30</th><th>VRP</th>'
            f'<th>IV rank</th><th>30/90</th><th>20d move</th><th>Quadrant</th></tr></thead><tbody>{"".join(tr)}</tbody>'
            f'</table></div></section>')


def _track(t: dict) -> str:
    rows = t.get("rows") or []
    if not any(r.get("n") for r in rows):
        return ('<section><h2>Track record</h2><p class="none">Not scored yet: the first lists need 5 sessions of price '
                'history before they can be checked. Every list is stored, so this fills in by itself.</p></section>')
    tr = []
    for r in rows:
        name, col, _ = BUCKET[r["bucket"]]
        hit, base = r.get("hit"), r.get("base")
        edge = None if hit is None else hit - base
        ec = AX if edge is None else (GRN if edge > 0.05 else RED if edge < -0.05 else AX)
        tr.append(f'<tr><td style="color:{col}">{esc(name)}</td><td>{r["h"]}</td><td>{r["n"]}</td><td>{pct(hit)}</td>'
                  f'<td>{pct(base)}</td><td style="color:{ec}">{fx(edge, "+.0f", 100, " pts")}</td>'
                  f'<td>{fx(r.get("rv_iv"), ".2f")}</td></tr>')
    return (f'<section><h2>Track record</h2><p>{t.get("lists", 0)} stored lists, {t.get("dates_scored", 0)} with enough '
            f'history. <b>Hit</b>: cheap upside = touched +1σ (the move IV priced for that horizon); cheap downside = '
            f'touched −1σ; rich premium = closed above −1σ (a −1σ short put kept); avoid = moved more than 1σ. '
            f'<b>Base</b> = what a fairly priced option gives. <b>Realised ÷ IV</b> above 1 = options were cheap.</p>'
            f'<div class="tw"><table><thead><tr><th>List</th><th>Sessions</th><th>Picks</th><th>Hit</th><th>Base</th>'
            f'<th>Edge</th><th>Realised ÷ IV</th></tr></thead><tbody>{"".join(tr)}</tbody></table></div></section>')


CSS = (f"body{{margin:0;font:15px/1.45 system-ui,sans-serif;color:{INK};background:#F4F5F7}}"
       "header{display:flex;gap:14px;align-items:baseline;flex-wrap:wrap;padding:14px 18px;background:#fff;border-bottom:1px solid #E3E6EA}"
       f"h1{{margin:0;font-size:22px}} .asof{{color:{AX}}} header a{{color:{BLU};margin-left:auto}}"
       "main{max-width:1200px;margin:0 auto;padding:12px}"
       "section{background:#fff;border-radius:8px;padding:12px 14px;margin:12px 0;box-shadow:0 1px 2px rgba(0,0,0,.05)}"
       f"h2{{margin:0 0 4px;font-size:18px}} section>p{{margin:0 0 8px;color:{AX}}} .none{{color:{AX};font-style:italic}}"
       ".grid{display:grid;grid-template-columns:1fr 1fr;gap:0 12px} .grid section{margin:6px 0}"
       ".tw{overflow-x:auto} table{border-collapse:collapse;width:100%;font-size:14px;font-variant-numeric:tabular-nums}"
       "th,td{padding:4px 7px;text-align:right;white-space:nowrap;border-bottom:1px solid #EEF0F2}"
       f"th{{color:{AX};font-weight:600}} td.s,th:first-child{{text-align:left;font-weight:700}} td.e{{color:{AX};font-size:12px}}"
       f".two{{display:grid;grid-template-columns:1fr 1fr;gap:12px}} figure{{margin:0}} figcaption{{font-weight:600;margin:4px 0}}"
       f"svg{{width:100%;height:auto}} svg text{{font:11px system-ui,sans-serif;fill:{AX}}} svg .pl{{font-size:10px;fill:{INK}}}"
       f"svg .al{{font-size:11px;fill:{INK}}} .key{{display:flex;gap:12px;flex-wrap:wrap;font-size:13px;margin:4px 0}}"
       ".key i{display:inline-block;width:10px;height:10px;border-radius:5px;margin-right:4px} .q{font-weight:600}"
       f".rules{{font-size:13px;color:{AX}}}"
       "@media(max-width:820px){.grid,.two{grid-template-columns:1fr}}")


def page(o: dict) -> str:
    picks = o.get("picks") or {}
    c = o.get("counts") or {}
    sk = c.get("skipped") or {}
    lists = "".join(_table(b, picks.get(b) or []) for b in BUCKET)
    rules = (f'<p class="rules">Stocks: S&amp;P 1500 names with a real option IV (names on the HV20 stand-in are left out: '
             f'{sk.get("no_real_iv", 0)}), $ option premium ≥ ${(o.get("rules") or {}).get("min_prem", 5e5) / 1e6:.1f}M '
             f'({sk.get("illiquid", 0)} too thin), no earnings in the next {(o.get("rules") or {}).get("earn_days", 25)} days '
             f'({sk.get("earnings", 0)} left out). Matches before the top-{(o.get("rules") or {}).get("top_n", 15)} cut: '
             + ", ".join(f'{BUCKET[b][0].lower()} {c.get(b, 0)}' for b in BUCKET) +
             f'. Stages as of {esc(o.get("asof_stage"))}. Descriptors, not trade signals: size, strikes and exits are yours.</p>')
    return (f'<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">'
            f'<title>Convexity</title><!-- convexity.html - written by scripts/convexity_page.py ({esc(o.get("built"))}); display only (rule 4). -->'
            f'<style>{CSS}</style></head><body><header><h1>Convexity</h1><span class="asof">options data of {esc(o.get("asof"))}</span>'
            f'<a href="risk.html">Risk page</a></header><main>'
            f'<section><p>Where options look mispriced against the stock&rsquo;s own trend. Each name lands in at most one list. '
            f'<b>IV rank</b> = IV30 in its own year (0&ndash;100%). <b>IV&minus;HV10</b> = vol points options charge over the last '
            f'10 days&rsquo; realised moves. <b>30/90</b> above 1 = short-dated options dearer than long. <b>Stage d/wk</b> = '
            f'daily code / weekly 30-week stage. <b>RS</b> = Mansfield strength vs SPY (above 0 = beating it).</p>{rules}</section>'
            f'<div class="grid">{lists}</div>{_board(o.get("board") or [])}{_track(o.get("track") or {})}'
            f'</main></body></html>')


def write(data_dir: Path = DATA_DIR) -> dict | None:
    try:
        o = json.loads((data_dir / "convexity.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    tmp = data_dir / "convexity.html.tmp"
    tmp.write_text(page(o), encoding="utf-8", newline="\n")
    tmp.replace(data_dir / "convexity.html")
    return o


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dir", type=Path, default=DATA_DIR)
    a = ap.parse_args()
    o = write(a.dir)
    print("convexity.html: no convexity.json" if o is None else f"convexity.html: asof {o.get('asof')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
