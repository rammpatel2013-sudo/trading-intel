"""SPX confluence read-out — nightly EOD report (JSON + standalone HTML).

Reads banked tables only (``trading_intel.api.confluence``), runs the pure engine
(``trading_intel.market.confluence*``) and writes

    reports/confluence/confluence_YYYYMMDD.json
    reports/confluence/SPX_confluence_YYYY-MM-DD.html

Dark house style, large high-contrast text, inline SVG only (no <script>), so it opens
in a browser or on a phone. No Telegram send. Positioning read, not a trade signal
(rule 4).

Run:
    python scripts/confluence_report.py                     # latest SPX book
    python scripts/confluence_report.py --date 2026-11-06   # rebuild a past day
    python scripts/confluence_report.py --no-html
    python scripts/confluence_report.py --fixture inputs.json   # offline inputs (tests)

Also writes scripts/cvxmap/data/confluence.json (the cockpit Levels tab; served by
cvxmap-serve like tenor.json) on the nightly run. A --date rebuild of a past day does not
touch it unless --cockpit-out is given.
"""

from __future__ import annotations

import argparse
import html
import json
from datetime import date
from pathlib import Path

_BG, _PANEL, _TXT, _MUT, _LINE = "#0f1216", "#1a2029", "#f2f5f8", "#c9d3df", "#3a4656"
_SPOT = "#ffffff"
LAYER_COLOR = {"greeks": "#ffb454", "oi": "#4ea1ff", "vix": "#d49bff", "price": "#3ee089",
               "crowd": "#ff6b8b"}
LAYER_EXPLAIN = {
    "greeks": "Where the options book is shaped: the strike with the most vomma (vega most "
              "sensitive to a vol move), the spots where dealer gamma bends hardest (speed), "
              "and where naive dealer gamma (calls +, puts −) crosses zero. Buckets: ≤7 days, "
              "8–30 days, whole 0–45 day book.",
    "oi": "Strikes holding the most put and call open interest (all expiries up to 45 days, "
          "and the ≤7-day front), with the day-over-day change in OI. Big OI = many open "
          "contracts whose hedging touches that price.",
    "vix": "The biggest VIX option strikes for the next two real VIX expiries, turned into SPX "
           "levels: SPX % move = −(VIX strike − VIX now) ÷ points-per-1%. Default 2.0 VIX "
           "points per 1% SPX drop; the measured recent beta is shown alongside.",
    "price": "Price memory: 20/50/100/200-day averages, swing highs/lows (5 bars each side, "
             "last 250 bars), last month's high/low, and anchored VWAP (SPY volume scaled to SPX).",
    "crowd": "Crowded strikes: the 22- and 25-delta put/call strikes of the front week and the "
             "next monthly (where standard hedges and overwrites cluster), plus strikes of the "
             "largest index block structures from the last 3 sessions.",
}


def _f(v: float | None, fmt: str = "{:,.0f}") -> str:
    return "—" if v is None else fmt.format(v)


def _esc(s: object) -> str:
    return html.escape(str(s))


# ── ladder chart ─────────────────────────────────────────────────────────────
def ladder_svg(r: dict, *, span: float = 0.075, w: int = 980, h: int = 760) -> str:
    """Zones as bands on a vertical price axis; each agreeing layer is a coloured mark."""
    spot = float(r["spot"])
    lo, hi = spot * (1 - span), spot * (1 + span)
    zones = [z for z in r["zones"] if z["n_layers"] >= 2 and lo <= z["centre"] <= hi]
    pad_t, pad_b, x0 = 24, 24, 110
    cols = {lay: x0 + 40 + i * 70 for i, lay in enumerate(LAYER_COLOR)}
    xl = x0 + 40 + 5 * 70

    def y(p: float) -> float:
        return pad_t + (hi - p) / (hi - lo) * (h - pad_t - pad_b)

    o = [f'<svg viewBox="0 0 {w} {h}" width="100%" role="img" aria-label="Confluence ladder">',
         f'<rect x="0" y="0" width="{w}" height="{h}" fill="{_PANEL}"/>']
    step = 50 if hi - lo < 1400 else 100
    p = (int(lo) // step + 1) * step
    while p < hi:
        yy = y(p)
        o.append(f'<line x1="{x0}" x2="{w - 10}" y1="{yy:.1f}" y2="{yy:.1f}" stroke="{_LINE}" '
                 f'stroke-width="0.6"/><text x="{x0 - 10}" y="{yy + 6:.1f}" fill="{_MUT}" '
                 f'font-size="17" text-anchor="end">{p:,}</text>')
        p += step
    for lay, cx in cols.items():
        o.append(f'<text x="{cx}" y="{h - 4}" fill="{LAYER_COLOR[lay]}" font-size="15" '
                 f'font-weight="700" text-anchor="middle">{_esc(lay.upper())}</text>')
    # label rows: push labels apart so two close zones never print on top of each other
    label_y: dict[int, float] = {}
    last = -1e9
    for i in sorted(range(len(zones)), key=lambda i: -zones[i]["centre"]):
        yy = max(y(zones[i]["centre"]) + 6, last + 22)
        label_y[i] = last = yy
    for zi, z in enumerate(zones):
        y1, y2 = y(z["high"]), y(z["low"])
        hgt = max(y2 - y1, 6.0)
        fill = "#ffd54a" if z["key"] else "#7f8ea3"
        o.append(f'<rect x="{x0}" y="{(y1 + y2) / 2 - hgt / 2:.1f}" width="{w - x0 - 10}" '
                 f'height="{hgt:.1f}" fill="{fill}" opacity="{0.22 if z["key"] else 0.12}"/>')
        yc = y(z["centre"])
        for lay in z["layers"]:
            o.append(f'<circle cx="{cols[lay]}" cy="{yc:.1f}" r="11" fill="{LAYER_COLOR[lay]}"/>')
        tag = "KEY " if z["key"] else ""
        dist = z["centre"] - spot
        ly = label_y[zi]
        if abs(ly - (yc + 6)) > 2:
            o.append(f'<line x1="{xl - 4}" x2="{xl + 6}" y1="{yc:.1f}" y2="{ly - 6:.1f}" '
                     f'stroke="{_MUT}" stroke-width="1"/>')
        o.append(f'<text x="{xl + 10}" y="{ly:.1f}" fill="{_TXT if z["key"] else _MUT}" '
                 f'font-size="18" font-weight="{700 if z["key"] else 400}">{tag}{z["centre"]:,.0f} '
                 f'({z["n_layers"]} layers, {dist:+,.0f} pts)</text>')
    gx = cols["greeks"]
    for x in r.get("info_levels", []):
        if lo <= x["level"] <= hi:
            yi = y(x["level"])
            o.append(f'<circle cx="{gx}" cy="{yi:.1f}" r="10" fill="none" '
                     f'stroke="{LAYER_COLOR["greeks"]}" stroke-width="3" stroke-dasharray="4 3"/>'
                     f'<text x="{gx - 18}" y="{yi + 5:.1f}" fill="{LAYER_COLOR["greeks"]}" '
                     f'font-size="14" text-anchor="end">{x["level"]:,.0f} info</text>')
    ys = y(spot)
    o.append(f'<line x1="{x0}" x2="{w - 10}" y1="{ys:.1f}" y2="{ys:.1f}" stroke="{_SPOT}" '
             f'stroke-width="2.5" stroke-dasharray="8 5"/><text x="{w - 14}" y="{ys - 8:.1f}" '
             f'fill="{_SPOT}" font-size="18" font-weight="700" text-anchor="end">SPX {spot:,.2f}</text>')
    o.append("</svg>")
    return "".join(o)


# ── tables ───────────────────────────────────────────────────────────────────
def _chips(layers: list[str]) -> str:
    return " ".join(f'<span class="chip" style="background:{LAYER_COLOR[x]}">{_esc(x)}</span>'
                    for x in layers)


def _roc_text(z: dict) -> str:
    rc = z.get("roc") or {}
    if not rc:
        return "no prior read-out"
    if not rc.get("matched"):
        return "new since yesterday"
    parts = [f"centre {rc['centre_shift']:+,.0f} pts"]
    if rc.get("layer_change"):
        parts.append(f"layers {rc['layer_change']:+d}")
    if rc.get("oi_change_delta") is not None:
        parts.append(f"OI chg Δ {rc['oi_change_delta']:+,.0f}")
    if rc.get("distance_prior") is not None and rc.get("distance_now") is not None:
        parts.append(f"distance {rc['distance_prior']:+,.0f} → {rc['distance_now']:+,.0f}")
    return "; ".join(parts)


def _meaning(z: dict, spot: float) -> str:
    side = "below" if z["centre"] < spot else "above"
    names = ", ".join(z["layers"])
    if z["n_layers"] < 2:
        s = (f"Only {z['n_layers']} counted view ({names}) here, {abs(z['centre'] - spot):,.0f} pts "
             f"{side} spot; listed because a vol-shock peak (info) sits nearby.")
    else:
        s = (f"{z['n_layers']} independent views ({names}) point at the same "
             f"{z['high'] - z['low']:,.0f}-point band, {abs(z['centre'] - spot):,.0f} pts "
             f"{side} spot.")
    if z.get("info_levels"):
        s += (" The vol-shock peak shown here is where a single option's vega reacts most to "
              "a vol move; it reflects spot, vol and time only, so it is not counted.")
    if z.get("oi_change"):
        s += f" OI at its strikes changed {z['oi_change']:+,.0f} contracts on the day."
    return s


def zones_table(r: dict) -> str:
    spot = float(r["spot"])
    rows = [z for z in r["zones"] if z["n_layers"] >= 2 or z.get("info_levels")]
    rows.sort(key=lambda z: (-z["n_layers"], abs(z["centre"] - spot)))
    body = "".join(
        f'<tr class="{"key" if z["key"] else ""}"><td><b>{z["centre"]:,.0f}</b></td>'
        f'<td>{z["low"]:,.0f}–{z["high"]:,.0f}</td><td>{_chips(z["layers"])}{_info(z)}</td>'
        f'<td>{z["centre"] - spot:+,.0f}</td><td>{_esc(_roc_text(z))}</td>'
        f'<td class="mean">{_esc(_meaning(z, spot))}</td></tr>' for z in rows)
    return ("<table><thead><tr><th>Centre</th><th>Range</th><th>Layers that agree</th>"
            "<th>To spot</th><th>Change since yesterday</th><th>What this means</th></tr></thead>"
            f"<tbody>{body}</tbody></table>")


def _info(z: dict) -> str:
    """Info-only levels attached to a zone: shown, not counted in its layers."""
    infos = z.get("info_levels") or []
    if not infos:
        return ""
    txt = ", ".join(f'{x["level"]:,.0f}' for x in infos)
    return f'<div class="info">vol-shock peak (info): {_esc(txt)} — not counted</div>'


def layers_html(r: dict) -> str:
    out = []
    for lay, items in r["layers"].items():
        rows = "".join(f'<tr><td><b>{x["level"]:,.1f}</b></td><td>{_esc(x["label"])}</td>'
                       f'<td>{"info only" if x.get("info") else format(x.get("strength", 1), ".2f")}'
                       f'</td></tr>' for x in sorted(items, key=lambda x: -x["level"]))
        extra = ""
        if lay == "greeks":
            extra = f'<p class="info">{_esc(r.get("info_note", ""))}</p>'
        if lay == "vix":
            extra = (f'<p>VIX now {_f(r.get("vix_spot"), "{:.2f}")}. Mapping uses '
                     f'{r.get("beta_pts_default", 2.0):.1f} VIX pts per 1% SPX drop; measured '
                     f'21-day beta: {_f(r.get("beta_pts_measured"), "{:.2f}")} pts per 1% '
                     f'(levels with it are listed in the JSON as level_measured_beta).</p>')
        out.append(f'<details open><summary style="color:{LAYER_COLOR[lay]}">{_esc(lay.upper())} '
                   f'— {len(items)} levels</summary><p>{_esc(LAYER_EXPLAIN[lay])}</p>{extra}'
                   f'<table><thead><tr><th>SPX level</th><th>What it is</th><th>Weight</th></tr>'
                   f'</thead><tbody>{rows or "<tr><td colspan=3>none</td></tr>"}</tbody></table>'
                   f'</details>')
    return "".join(out)


def track_html(score: dict | None) -> str:
    if not score:
        return "<p>No track record yet. Run <code>confluence_score.py --backfill</code>.</p>"
    from trading_intel.market.confluence_score import headline

    rows = []
    for hz, g in sorted(score.get("horizons", {}).items(), key=lambda kv: int(kv[0])):
        for name, lab in (("key", "Key zones (3+ layers)"), ("multi2", "2-layer zones"),
                          ("single", "Single-layer levels")):
            s = g[name]
            rows.append(f'<tr><td>{hz} session{"s" if hz != "1" else ""}</td><td>{lab}</td>'
                        f'<td>{s["n"]}</td><td>{_f((s["touch_rate"] or 0) * 100 if s["touch_rate"] is not None else None, "{:.0f}%")}</td>'
                        f'<td>{_f((s["hold_rate"] or 0) * 100 if s["hold_rate"] is not None else None, "{:.0f}%")}</td></tr>')
    return (f'<p class="big">{_esc(headline(score))}</p>'
            "<p>Touched = SPX traded into the zone within the window. Held = touched, and the "
            "last close of the window was back on the side it started from. Hold rate is "
            "held ÷ touched.</p><table><thead><tr><th>Window</th><th>Group</th><th>n</th>"
            f"<th>Touch rate</th><th>Hold rate</th></tr></thead><tbody>{''.join(rows)}</tbody></table>")


def render(r: dict, score: dict | None = None) -> str:
    spot = float(r["spot"])
    keys = [z for z in r["zones"] if z["key"]]
    above = min((z for z in keys if z["centre"] > spot), key=lambda z: z["centre"], default=None)
    below = max((z for z in keys if z["centre"] < spot), key=lambda z: z["centre"], default=None)
    gz = [x["level"] for x in r["layers"].get("greeks", []) if x.get("kind") == "gamma_zero"
          and x.get("bucket") == "0-45d"]
    kpis = [
        ("SPX spot", f"{spot:,.2f}", f"book as of {r['as_of']}"),
        ("Key zones", str(len(keys)), f"3+ of 5 layers within {r['window']:g} pts"),
        ("Nearest key zone above", _f(above["centre"]) if above else "—",
         f"{above['centre'] - spot:+,.0f} pts" if above else "none in range"),
        ("Nearest key zone below", _f(below["centre"]) if below else "—",
         f"{below['centre'] - spot:+,.0f} pts" if below else "none in range"),
        ("Dealer gamma zero (0–45d)", _f(gz[0]) if gz else "—", "naive book, calls + / puts −"),
    ]
    kpi = "".join(f'<div class="kpi"><div class="kl">{_esc(a)}</div><div class="kv">{_esc(b)}</div>'
                  f'<div class="ks">{_esc(c)}</div></div>' for a, b, c in kpis)
    gaps = "".join(f"<li>{_esc(g)}</li>" for g in r.get("gaps", []))
    prior = r.get("prior_as_of") or "none stored"
    legend = " ".join(f'<span class="chip" style="background:{c}">{_esc(k)}</span>'
                      for k, c in LAYER_COLOR.items())
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>SPX Confluence {r['as_of']}</title>
<style>
body{{background:{_BG};color:{_TXT};font:18px/1.55 -apple-system,Segoe UI,Roboto,Arial,sans-serif;margin:0}}
.wrap{{max-width:1180px;margin:0 auto;padding:20px 16px}} h1{{font-size:30px;margin:0 0 6px}}
h2{{font-size:24px;margin:30px 0 10px;color:#fff;border-bottom:2px solid {_LINE};padding-bottom:6px}}
p,li{{color:{_TXT}}} .sub{{color:{_MUT};font-size:18px}} .big{{font-size:21px;font-weight:700}}
.warn{{border-left:6px solid #ffd54a;background:{_PANEL};padding:12px 16px;border-radius:6px;font-size:19px}}
.gaps{{border-left:6px solid #ff8a65;background:{_PANEL};padding:10px 16px;border-radius:6px}}
.kpis{{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:10px;margin:16px 0}}
.kpi{{background:{_PANEL};border-radius:8px;padding:12px}} .kl{{color:{_MUT};font-size:16px;font-weight:600}}
.kv{{font-size:28px;font-weight:800}} .ks{{color:{_MUT};font-size:16px}}
table{{border-collapse:collapse;width:100%;margin:8px 0;font-size:17px}}
th,td{{border-bottom:1px solid {_LINE};padding:8px 8px;text-align:left;vertical-align:top}}
th{{color:#fff;background:#232b36}} tr.key td{{background:#2a2614}} td.mean{{min-width:260px}}
.chip{{display:inline-block;color:#0b0d10;font-weight:800;font-size:15px;border-radius:5px;padding:1px 8px;margin:2px 2px}}
details{{background:{_PANEL};border-radius:8px;padding:10px 14px;margin:10px 0}}
summary{{font-size:20px;font-weight:800;cursor:pointer}} code{{color:#ffd54a}}
.scroll{{overflow-x:auto}} .info{{color:#ffcf8a;font-size:17px}}
</style></head><body><div class="wrap">
<h1>SPX confluence read-out · {r['as_of']}</h1>
<div class="sub">Five independent ways of finding price levels, merged where they agree. Compared with: {_esc(prior)}.</div>
<p class="warn"><b>{_esc(r.get('disclaimer', 'Positioning read, not a trade signal.'))}</b> A zone says where several
descriptions of the options book and of price history point at the same place. It does not say which way price
goes, or whether a level holds — see the track record below before leaning on it.</p>
<div class="kpis">{kpi}</div>
<h2>Ladder: zones vs spot</h2>
<p>Each band is a zone (yellow = KEY, 3+ layers agree; grey = 2 layers). The coloured dots show which layers
agree at that price. A hollow dashed orange ring = vol-shock peak (info only, not counted). Dashed white line =
SPX close. Range shown: ±7.5% of spot. {legend}</p>
<p class="info">{_esc(r.get('info_note', ''))}</p>
<div class="scroll">{ladder_svg(r)}</div>
<h2>Zones (2+ layers), strongest first</h2>
<p>Centre = strength-weighted middle of the levels in the zone; range = lowest to highest level in it; "To spot" =
centre minus SPX close (negative = below). "Change since yesterday" compares with the nearest zone in the prior
read-out.</p><div class="scroll">{zones_table(r)}</div>
<h2>Track record so far</h2>{track_html(score)}
<h2>Every layer's raw levels</h2>{layers_html(r)}
<h2>Data gaps</h2><div class="gaps"><ul>{gaps or '<li>none</li>'}</ul></div>
<p class="sub">Sources (banked tables, no vendor calls): oi_chain_eod (SPX book), greeks_snapshots (spot),
quotes_daily (SPX/SPY bars), vix_data, vix_options_chain, vol_surface_cm, tas_prints (convex_index).
MCP: get_confluence. Greek maths: Black-Scholes r=q=0, calendar time, each strike's own IV.
{_esc(r.get('disclaimer', ''))}</p>
</div></body></html>"""


# ── build ────────────────────────────────────────────────────────────────────
def _paths(day: date, out_dir: Path) -> tuple[Path, Path]:
    return (out_dir / f"confluence_{day.strftime('%Y%m%d')}.json",
            out_dir / f"SPX_confluence_{day.isoformat()}.html")


def _score(out_dir: Path) -> dict | None:
    try:
        return json.loads((out_dir / "score.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def write_html(r: dict, out_dir: Path) -> Path:
    _, hp = _paths(date.fromisoformat(r["as_of"]), out_dir)
    hp.parent.mkdir(parents=True, exist_ok=True)
    hp.write_text(render(r, _score(out_dir)), encoding="utf-8")
    return hp


COCKPIT_JSON = Path(__file__).resolve().parent / "cvxmap" / "data" / "confluence.json"


def write_cockpit(r: dict, out_dir: Path, bars: list, path: Path | None = None) -> Path:
    """scripts/cvxmap/data/confluence.json for the cockpit Levels tab (served like tenor.json)."""
    from datetime import datetime

    from trading_intel.api import confluence as api
    from trading_intel.market.confluence_build import cockpit_payload

    prior = api.prior_readout(date.fromisoformat(r["as_of"]), out_dir)
    obj = cockpit_payload(r, prior=prior, score=_score(out_dir), bars=bars,
                          generated=datetime.now().replace(microsecond=0).isoformat())
    p = path or COCKPIT_JSON
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, default=str), encoding="utf-8")
    return p


def build(day: date | None = None, *, out_dir: Path | None = None, html_out: bool = True,
          window: float | None = None, session: object = None, fixture: str | None = None,
          cockpit: Path | bool | None = None) -> dict:
    from trading_intel.api import confluence as api
    from trading_intel.market.confluence import DEFAULT_WINDOW

    out = out_dir or api.OUT_DIR
    win = window or DEFAULT_WINDOW
    bars: list = []
    if fixture:
        from trading_intel.market.confluence_build import compute_readout, inputs_from_json

        inp = inputs_from_json(json.loads(Path(fixture).read_text(encoding="utf-8")))
        r = compute_readout(inp, window=win, prior=api.prior_readout(inp.as_of, out))
        bars = inp.spx_bars
    elif session is not None:
        r = api.compute_for_day(session, day, window=win, out_dir=out)
        if r:
            bars = api._bars(session, api.SYMBOL, date.fromisoformat(r["as_of"]), n=80)
    else:
        from trading_intel.config import get_settings
        from trading_intel.memory.db import make_session_factory

        with make_session_factory(get_settings())() as s:
            r = api.compute_for_day(s, day, window=win, out_dir=out)
            if r:
                bars = api._bars(s, api.SYMBOL, date.fromisoformat(r["as_of"]), n=80)
    if r is None:
        raise SystemExit("no SPX book / spot for that day — nothing written")
    jp = api.write_json(r, out)
    paths = {"json": str(jp)}
    if html_out:
        paths["html"] = str(write_html(r, out))
    if cockpit is not False:
        cp = cockpit if isinstance(cockpit, Path) else None
        if cp is not None or day is None or fixture:
            # a --date rebuild of a past day must not overwrite the live cockpit file
            paths["cockpit"] = str(write_cockpit(r, out, bars, cp))
    return {"readout": r, "paths": paths}


def main() -> None:
    ap = argparse.ArgumentParser(description="SPX confluence read-out (JSON + HTML).")
    ap.add_argument("--date", help="YYYY-MM-DD to rebuild (default: latest SPX book)")
    ap.add_argument("--no-html", action="store_true")
    ap.add_argument("--window", type=float, default=None, help="merge window, SPX points")
    ap.add_argument("--out-dir", default=None)
    ap.add_argument("--fixture", default=None, help="offline inputs JSON instead of the DB")
    ap.add_argument("--cockpit-out", default=None, help="cockpit JSON path (default cvxmap/data)")
    ap.add_argument("--no-cockpit", action="store_true", help="do not write the cockpit JSON")
    a = ap.parse_args()
    ck: Path | bool | None = False if a.no_cockpit else (Path(a.cockpit_out) if a.cockpit_out else None)
    res = build(date.fromisoformat(a.date) if a.date else None,
                out_dir=Path(a.out_dir) if a.out_dir else None, html_out=not a.no_html,
                window=a.window, fixture=a.fixture, cockpit=ck)
    r = res["readout"]
    print(f"confluence {r['as_of']}: {len(r['key_zones'])} key zones; " + ", ".join(
        f"{z['centre']:,.0f}({z['n_layers']})" for z in r["key_zones"]))
    for k, v in res["paths"].items():
        print(f"{k}: {v}")


if __name__ == "__main__":
    main()
