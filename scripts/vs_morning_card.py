#!/usr/bin/env python
# ruff: noqa: E501, N806, RUF100
"""vs_morning_card.py - created 2026-11-13 for Mithil (VS analysis plan, step 7).

The VolSignals-style morning card as ONE picture on Telegram (plus the HTML in reports/vs_card/):
  - the whole-book gamma profile (Full / Ex-0DTE / tape-signed) with each flip and the percentile,
  - the ES hedge table, the close odds bar, charm at price + the red flag (all from profile_SPX.json,
    written by vs_profile_feed.py a few minutes earlier),
  - the largest trades of the last session from the tape, legs of one order read together as a spread
    (same leg_group, else same millisecond), opening when open interest rose by at least half the size,
  - Balance / Tests: the biggest dealer-long strike near price and the biggest dealer-short strike above and
    below (within 2 straddles), from the tape-signed book when there is one, else the naive book.
Caption = two plain lines. Descriptor, not a signal (rule 4).

Run on the NAS (DSM Mon-Fri 08:50 = morning card, 17:40 = night-before card):
    sudo bash /var/services/homes/drmithil/trading-intel/scripts/nas/run_script.sh vs_morning_card
    ... vs_morning_card --no-telegram     (write the files only)
"""
from __future__ import annotations

import argparse
import html
import json
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
for p in (str(REPO), str(HERE), str(HERE / "cvxmap")):
    if p not in sys.path:
        sys.path.insert(0, p)

PROFILE = HERE / "cvxmap" / "data" / "profile_SPX.json"
OUT_DIR = REPO / "reports" / "vs_card"
E = html.escape


# ---------------------------------------------------------------- pure logic (tests/test_vs_morning_card.py)
def group_orders(prints: list[dict], sides: list[int]) -> list[dict]:
    """Prints -> orders: legs with the same leg_group (else the same millisecond and root) are one order.
    sides[i] = customer side of print i (+1 bought, -1 sold, 0 unknown). Returns orders with legs
    [(expiry, strike, cp, size, side)], size (contracts of the smallest leg), premium ($) and a name."""
    by: dict[Any, list[int]] = defaultdict(list)
    for i, r in enumerate(prints):
        key = ("g", str(r["leg_group"])) if r.get("leg_group") else ("t", str(r.get("ts")), r.get("root"))
        by[key].append(i)
    out = []
    for idx in by.values():
        legs = defaultdict(lambda: [0, 0.0, 0])   # (exp, k, cp) -> [size, premium, side votes]
        for i in idx:
            r = prints[i]
            if r.get("strike") is None or r.get("cp") not in ("C", "P"):
                continue
            k = (str(r["expiry"]), float(r["strike"]), r["cp"])
            n = int(r.get("size") or 0)
            legs[k][0] += n
            legs[k][1] += n * float(r.get("price") or 0) * 100.0
            legs[k][2] += sides[i] * n
        if not legs:
            continue
        L = [(e, k, c, v[0], (1 if v[2] > 0 else -1 if v[2] < 0 else 0)) for (e, k, c), v in sorted(legs.items())]
        size = min(x[3] for x in L)
        prem = sum(v[1] for v in legs.values())
        out.append({"legs": L, "size": size, "premium": prem, "name": order_name(L)})
    return sorted(out, key=lambda o: o["premium"], reverse=True)


def order_name(L: list[tuple]) -> str:
    """'7,690/7,790 call spread' / '7,500 put' / '7,450/7,650 strangle' / '3-leg order'."""
    f = lambda k: f"{k:,.0f}"  # noqa: E731
    if len(L) == 1:
        return f"{f(L[0][1])} {'call' if L[0][2] == 'C' else 'put'}"
    if len(L) == 2:
        (_, k1, c1, *_), (_, k2, c2, *_) = L
        if c1 == c2:
            return f"{f(min(k1, k2))}/{f(max(k1, k2))} {'call' if c1 == 'C' else 'put'} spread"
        return f"{f(min(k1, k2))}/{f(max(k1, k2))} {'straddle' if k1 == k2 else 'strangle / risk reversal'}"
    return f"{len(L)}-leg order"


def order_side(o: dict) -> str:
    """For a vertical: the side of the leg nearer the money decides (bought = customer paid)."""
    sides = [x[4] for x in o["legs"]]
    if all(s == 0 for s in sides):
        return "side unknown"
    s = sides[0] if len(sides) == 1 else (sides[0] or -sides[1])
    return "customer bought" if s > 0 else "customer sold" if s < 0 else "side unknown"


def balance_tests(per_strike: dict[float, float], spot: float, band: float) -> dict[str, Any]:
    """per_strike = dealer gamma at price per strike (+ = dealers long). Balance = the biggest long strike
    within the band, up / down test = the biggest short strike above / below within the band."""
    near = {k: v for k, v in per_strike.items() if abs(k - spot) <= band}
    lon = {k: v for k, v in near.items() if v > 0}
    up = {k: v for k, v in near.items() if v < 0 and k > spot}
    dn = {k: v for k, v in near.items() if v < 0 and k < spot}
    pick = lambda d, f: f(d, key=d.get) if d else None  # noqa: E731
    return {"balance": pick(lon, max), "up_test": pick(up, min), "down_test": pick(dn, min), "band": round(band, 1)}


def caption(P: dict) -> str:
    """Two plain lines."""
    S, f = P["spot"], lambda v: f"{v:,.0f}"  # noqa: E731
    full = P["at_spot"]["full"]
    fl = P["flips"]["full"]["nearest"]
    ln1 = (f"SPX {f(S)}: dealers {'LONG' if full > 0 else 'SHORT'} gamma ({P['pct']['full'] or '–'} pct) - "
           f"{'moves get absorbed' if full > 0 else 'moves get chased'}; flip {f(fl) if fl else 'none within 4%'}.")
    h = {r["move_pct"]: r for r in P.get("hedge", [])}
    dn, up = h.get(-1.0), h.get(1.0)
    rf = (P.get("charm") or {}).get("red_flag", {}).get("flag")
    ln2 = ""
    if dn and up:
        ln2 = (f"-1%: dealers {'buy' if dn['es'] > 0 else 'sell'} · +1%: dealers {'buy' if up['es'] > 0 else 'sell'}"
               f"{' · RED FLAG: mixed charm near price' if rf else ''}.")
    return ln1 + ("\n" + ln2 if ln2 else "")


def svg_profile(P: dict, w: int = 1060, h: int = 300) -> str:
    G, S = P["grid"], P["spot"]
    xs = np.arange(G["n"]) * G["step"] + G["lo"]
    m = (xs >= S * 0.975) & (xs <= S * 1.025)
    xs = xs[m]
    lines = [("full", "#3fd0ff", ""), ("ex0", "#ffa64d", "10,6")]
    data = {k: np.array(P[k])[m] for k, *_ in lines}
    sg = P.get("signed") or {}
    if sg.get("ok"):
        data["signed"] = np.array(sg["line"])[m]
        lines.append(("signed", "#3ddc84", "3,5"))
    lo, hi = min(0.0, *(v.min() for v in data.values())), max(0.0, *(v.max() for v in data.values()))
    pad = (hi - lo) * 0.08 or 1.0
    lo, hi = lo - pad, hi + pad
    L, R, T, B = 60, 20, 14, 30
    X = lambda v: L + (v - xs[0]) / (xs[-1] - xs[0]) * (w - L - R)  # noqa: E731
    Y = lambda v: T + (hi - v) / (hi - lo) * (h - T - B)  # noqa: E731
    out = [f'<svg viewBox="0 0 {w} {h}" width="{w}" height="{h}" xmlns="http://www.w3.org/2000/svg" font-family="system-ui,sans-serif">',
           f'<line x1="{L}" x2="{w - R}" y1="{Y(0):.1f}" y2="{Y(0):.1f}" stroke="#fff" stroke-dasharray="4,4" stroke-width="1.5"/>']
    step = next((s for s in (10, 25, 50, 100, 200) if (xs[-1] - xs[0]) / s <= 10), 200)
    v = np.ceil(xs[0] / step) * step
    while v <= xs[-1]:
        out.append(f'<line x1="{X(v):.1f}" x2="{X(v):.1f}" y1="{T}" y2="{h - B}" stroke="rgba(255,255,255,.15)"/>'
                   f'<text x="{X(v):.1f}" y="{h - 9}" fill="#e8edf2" font-size="15" text-anchor="middle">{v:,.0f}</text>')
        v += step
    for k, col, dash in lines:
        pts = " ".join(f"{X(x):.1f},{Y(y):.1f}" for x, y in zip(xs, data[k], strict=True))
        out.append(f'<polyline points="{pts}" fill="none" stroke="rgba(0,0,0,.7)" stroke-width="6"/>'
                   f'<polyline points="{pts}" fill="none" stroke="{col}" stroke-width="3" stroke-dasharray="{dash}"/>')
    out.append(f'<line x1="{X(S):.1f}" x2="{X(S):.1f}" y1="{T}" y2="{h - B}" stroke="#fff" stroke-width="2"/>'
               f'<text x="{X(S) + 6:.1f}" y="{T + 16}" fill="#fff" font-size="16" font-weight="700">SPX {S:,.0f}</text>')
    flips = [(P["flips"]["full"]["nearest"], "#3fd0ff", "full flip", 0), (P["flips"]["ex0"]["nearest"], "#ffa64d", "ex-0DTE flip", 1)]
    if sg.get("ok"):
        flips.append((sg["flips"]["nearest"], "#3ddc84", "signed flip", 2))
    for x, col, lab, row in flips:
        if x is not None and xs[0] <= x <= xs[-1]:
            out.append(f'<circle cx="{X(x):.1f}" cy="{Y(0):.1f}" r="6" fill="{col}" stroke="#000" stroke-width="2"/>'
                       f'<text x="{X(x) + 9:.1f}" y="{Y(0) + (-12 if row == 0 else 22 if row == 1 else 42):.1f}" fill="{col}" font-size="15" font-weight="700">{lab} {x:,.0f}</text>')
    out.append(f'<text x="{L - 8}" y="{T + 13}" fill="#e8edf2" font-size="14" text-anchor="end">+{hi:.1f}</text>'
               f'<text x="{L - 8}" y="{h - B - 2}" fill="#e8edf2" font-size="14" text-anchor="end">{lo:.1f}</text></svg>')
    return "".join(out)


def card_html(P: dict, orders: list[dict], lv: dict | None, lv_src: str) -> str:
    f = lambda v: f"{v:,.0f}"  # noqa: E731
    S = P["spot"]
    full, ex0 = P["at_spot"]["full"], P["at_spot"]["ex0"]
    word = lambda v: "LONG gamma" if v > 0 else "SHORT gamma"  # noqa: E731
    ordn = lambda p: "–" if p is None else f"{p}th"  # noqa: E731
    k = lambda v: f"{abs(v) / 1000:.0f}k" if abs(v) >= 10_000 else f"{abs(v) / 1000:.1f}k" if abs(v) >= 1000 else str(abs(v))  # noqa: E731
    hedge = "".join(
        f'<td><div class="m">{r["move_pct"]:+g}% → {f(r["spx"])}</div><div class="v" style="color:{"#3ddc84" if r["es"] >= 0 else "#ff5c5c"}">'
        f'{"buy" if r["es"] >= 0 else "sell"} {k(r["es"])} ES</div><div class="x">ex-0DTE {"buy" if r["es_ex0"] >= 0 else "sell"} {k(r["es_ex0"])}</div></td>'
        for r in P.get("hedge", []))
    O = (P.get("odds") or {}).get("odds") or {}
    cols = ["#ff5c5c", "#ff9a5c", "#7fb0ff", "#4fd1a5", "#3ddc84", "#2bb673"]
    odds = ("".join(f'<div class="ob" style="flex:{max(b["odds_pct"], 0.6)} 1 0;background:{cols[i % 6]}"><b>{b["odds_pct"]}%</b><span>{E(b["range"])}</span></div>'
                    for i, b in enumerate(O.get("buckets", []))) if O.get("found") else '<i>close odds not available</i>')
    ch = P.get("charm") or {}
    rf = ch.get("red_flag") or {}
    ctot = None
    if ch.get("total"):
        i = int(round((S - P["grid"]["lo"]) / P["grid"]["step"]))
        ctot = ch["total"][max(0, min(len(ch["total"]) - 1, i))]
    rows = "".join(f'<tr><td>{E(o["name"])}</td><td>{E(str(o["legs"][0][0]))}</td><td>{o["size"]:,}</td><td>${o["premium"] / 1e6:.1f}M</td>'
                   f'<td>{E(order_side(o))}</td><td>{"opening" if o.get("opening") else ("closing / mixed" if o.get("opening") is False else "–")}</td></tr>'
                   for o in orders[:6]) or '<tr><td colspan="6"><i>no SPX tape for the last session</i></td></tr>'
    lvt = ("–" if not lv else
           f'Balance <b>{f(lv["balance"]) if lv["balance"] else "–"}</b> · up test <b>{f(lv["up_test"]) if lv["up_test"] else "–"}</b> · '
           f'down test <b>{f(lv["down_test"]) if lv["down_test"] else "–"}</b> <i>(within ±{lv["band"]:.0f} pts; {E(lv_src)})</i>')
    sg = P.get("signed") or {}
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8"><title>SPX morning card {E(P['session'])}</title><style>
body{{margin:0;background:#0b0f14;color:#f2f5f8;font:17px system-ui,sans-serif}} .c{{width:1100px;padding:16px 20px}}
h1{{font-size:24px;margin:0 0 4px}} .s{{font-size:15px;color:#dfe6ee}} .big{{font-size:20px;margin:8px 0}} .F{{color:#3fd0ff}} .X{{color:#ffa64d}} .G{{color:#3ddc84}}
table.h td{{border:1px solid #556;padding:5px 9px;background:#111820}} .m{{font-size:14px}} .v{{font-size:20px;font-weight:800}} .x{{font-size:14px}}
.obar{{display:flex;gap:2px;height:52px;margin:6px 0}} .ob{{display:flex;flex-direction:column;justify-content:center;align-items:center;color:#000;border-radius:3px;overflow:hidden;min-width:0}} .ob b{{font-size:18px}} .ob span{{font-size:12px;white-space:nowrap}}
table.t{{border-collapse:collapse;width:100%;font-size:16px}} table.t td,table.t th{{border-bottom:1px solid #334;padding:4px 6px;text-align:left}}
.rf{{color:#ff8080;font-weight:700}} .ok{{color:#7be3a6}} h2{{font-size:18px;margin:12px 0 4px}}</style></head><body><div class="c">
<h1>SPX morning card · {E(P['session'])}</h1>
<div class="s">Book {E(P['book']['ts'])} ({E(P['book']['oi_note'])}) · written {E(P['written'])} · naive book unless marked signed · descriptor, not a signal</div>
<div class="big">At <b>{f(S)}</b>: <span class="F"><b>{word(full)}</b> ({ordn(P['pct']['full'])} pct)</span>, flip <b>{f(P['flips']['full']['nearest']) if P['flips']['full']['nearest'] else '–'}</b> ·
<span class="X">ex-0DTE <b>{word(ex0)}</b> ({ordn(P['pct']['ex0'])} pct), flip <b>{f(P['flips']['ex0']['nearest']) if P['flips']['ex0']['nearest'] else '–'}</b></span>
{f' · <span class="G">signed flip <b>{f(sg["flips"]["nearest"])}</b></span>' if sg.get('ok') and sg['flips']['nearest'] else ''}</div>
{svg_profile(P)}
<h2>If SPX moves (ES contracts dealers trade to stay hedged)</h2><table class="h"><tr>{hedge}</tr></table>
<h2>Close odds · front expiry {E(str(O.get('expiry', '–')))}</h2><div class="obar">{odds}</div>
<h2>Charm &amp; levels</h2><div>{'Charm at price: dealers <b>' + ('sell' if (ctot or 0) >= 0 else 'buy') + f'</b> about ${abs(ctot or 0):.2f}bn of delta a day as time passes · ' if ctot is not None else ''}
<span class="{'rf' if rf.get('flag') else 'ok'}">{'⚑ RED FLAG: mixed charm near price - trust levels less' if rf.get('flag') else '✓ no red flag'}</span></div>
<div style="margin-top:4px">{lvt}</div>
<h2>Largest trades last session (legs read together)</h2><table class="t"><tr><th>Order</th><th>Expiry</th><th>Size</th><th>Premium</th><th>Side</th><th>Open / close</th></tr>{rows}</table>
</div></body></html>"""


# ---------------------------------------------------------------- DB side
def last_orders(db, day, start: dict, fresh: dict) -> list[dict]:
    import sides_feed as sf

    rows = db.tape(day)
    if not rows:
        return []
    rows = sf.to_et(rows, sf.tape_clock(rows))
    pkg = sf.sign_packages(r for r in rows if r.get("spread_leg") or r.get("leg_group"))
    by_sym: dict[str, list[int]] = defaultdict(list)
    for i, r in enumerate(rows):
        by_sym[r["symbol"]].append(i)
    sides = [0] * len(rows)
    for idx in by_sym.values():
        idx.sort(key=lambda i: (rows[i]["ts"], rows[i].get("id") or 0))
        for i, (side, how) in zip(idx, sf.classify([rows[i] for i in idx]), strict=True):
            sides[i] = pkg.get(rows[i].get("id"), 0) if how == "leg" else (side or 0)
    orders = group_orders(rows, sides)[:12]
    for o in orders:   # opening when open interest rose by at least half the order on every leg
        chg = []
        for e, k, c, n, _ in o["legs"]:
            keys = [x for x in fresh if str(x[0]) == e and x[1] == k and x[2] == c]
            if keys:
                chg.append(fresh[keys[0]]["oi"] - (start.get(keys[0]) or {}).get("oi", 0.0) >= 0.5 * n)
        o["opening"] = all(chg) if chg else None
    return orders


def per_strike_gamma(rows: dict, weights: dict | None, ref, spot: float) -> dict[float, float]:
    from trading_intel.greeks.black_scholes import bs_gamma
    from vs_profile_feed import book_arrays

    K, sig, t, w = book_arrays(rows, ref, None, weights)
    if not len(K):
        return {}
    g = bs_gamma(spot, K, sig, t) * w
    out: dict[float, float] = defaultdict(float)
    for k, v in zip(K, g, strict=True):
        out[float(k)] += float(v)
    return dict(out)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-telegram", action="store_true")
    a = ap.parse_args()
    if not PROFILE.exists():
        print("vs_morning_card.skip no profile_SPX.json (vs_profile_feed runs first)")
        return
    P = json.loads(PROFILE.read_text(encoding="utf-8"))
    if not P.get("ok"):
        print("vs_morning_card.skip profile not ok:", P.get("reason"))
        return
    from trading_intel.timeutils import is_trading_session
    from vs_flip_score import Db, tape_methods
    from vs_profile_feed import last_trade_day

    db = Db()
    ref = datetime.fromisoformat(P["ref"])
    now = datetime.fromisoformat(P["written"])
    day = last_trade_day(ref, now, is_trading_session)
    bk_ts = datetime.fromisoformat(P["book"]["ts"])
    fresh = db.rows(bk_ts, P["book"]["kind"])
    st_ts, kind = db.book_on(day, "am"), "am"
    if st_ts is None:
        st_ts, kind = db.book_on(day, "eod"), "eod"
    start = db.rows(st_ts, kind) if st_ts else {}
    orders = last_orders(db, day, start, fresh if ref.date() > day else {})
    w, src = None, "naive book"
    try:
        t1, t2, _ = tape_methods(db, day, start, fresh)
        w = t2 or t1
        src = f"signed by {day}'s tape" if w else src
    except Exception:  # noqa: BLE001 - levels fall back to the naive book
        w = None
    band = 2 * ((P.get("charm") or {}).get("straddle") or P["spot"] * 0.006)
    lv = balance_tests(per_strike_gamma({**start, **fresh}, w, ref, P["spot"]), P["spot"], band)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stem = OUT_DIR / f"vs_card_{P['session']}_{now:%H%M}"
    stem.with_suffix(".html").write_text(card_html(P, orders, lv, src), encoding="utf-8")
    png = stem.with_suffix(".png")
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        pg = b.new_page(viewport={"width": 1140, "height": 900})
        pg.goto(stem.with_suffix(".html").as_uri())
        pg.locator(".c").screenshot(path=str(png))
        b.close()
    cap = caption(P)
    print("vs_morning_card.done", png, "|", cap.replace("\n", " | "))
    if not a.no_telegram:
        from trading_intel.clients.telegram import TelegramClient
        from trading_intel.config import get_settings
        print("telegram_sent=", TelegramClient(get_settings()).send_photo(png, caption=cap))


if __name__ == "__main__":
    main()
