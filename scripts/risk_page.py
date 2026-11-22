#!/usr/bin/env python3
"""risk_page.py - created 2026-11-22. The merged Risk page -> scripts/cvxmap/data/risk.html (shown in the cockpit Risk tab).

One page instead of three (Mithil 2026-11-22: Risk tab + Fuse tab + zone cards merged, "for not technical people"):
  top     the verdict: chance of a 3%+ S&P 500 drop within a week (risk_tab.py score, tested 2010-2026), the
          Correlation Fuse phase (fuse_tab.py) and the two warning flags
  middle  one card per gauge in plain words, in fixed groups: where it sits in its own year, its trend, what it
          means, and what usually came next (same event as the score: a 3%+ drop within 5 sessions)
  bottom  combinations of gauges that are on now, links to the full Fuse detail and the zone (z-score) view, method
Reads only the evening files already written next to it: risktab.json (risk_tab.py), fusetab.json + zonecards.json
(fuse_tab.py / zone_cards.py), stagetab.json (stage_breadth job), convexity.json (convexity job). Static HTML, every number and chart written in at build time; a few lines of script
only make a card big on click. Rebuilt at the end of BOTH risk_tab.py and fuse_tab.py, so it never waits for the
other. Descriptor only (CLAUDE.md rule 4). No LLM, no Convex, no network.

Run by hand (NAS):  bash /var/services/homes/drmithil/trading-intel/scripts/nas/run_script.sh risk_page
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent / "cvxmap" / "data"

# Groups in reading order: id, plain question, one-line explanation
GROUPS = [
    ("fear", "How scared is the market?", "What traders pay to protect the S&P 500, and whether that fear is for this week or later."),
    ("tech", "Is tech more scared than the rest?", "The same fear gauge for the Nasdaq-100 (big tech)."),
    ("together", "Are stocks moving as one?", "When stocks move together, trouble in a few spreads to the whole index. When they move apart, the index looks calm even if single stocks are wild."),
    ("dealers", "What are dealers and big buyers doing?", "Option dealers' hedging can calm moves or make them bigger. Big buyers show up in dark pools."),
    ("breadth", "How many stocks are joining in?", "A healthy market has most stocks rising. When only a few hold up, the index is fragile."),
    ("credit", "Are bonds and credit worried?", "Lenders and the bond market often sense trouble before stocks do."),
    ("appetite", "Are investors taking risk or hiding?", "Risky stocks vs safe ones, and where the S&P 500 sits against its own trend."),
]

# Cards in order. src "r:<id>" = Risk tab tile (tested 2010-2026), "z:<id>" = zone card (Fuse panel, from 2014).
# stress: +1 high = stress, -1 low = stress, 0 both ends unusual (zone cards only; Risk tiles are already oriented).
# group, src, plain title, short name, what it means (plain), stress, decimals
CARDS = [
    ("fear", "r:vix", "Fear gauge", "VIX", "What traders pay to protect the S&P 500 for the next month. Higher = more fear.", 1, 1),
    ("fear", "r:vix_vix3m", "Fear now vs the next 3 months", "VIX ÷ VIX3M", "Above 1 means people fear the next month more than the next three: the stress is happening now. Volatility funds lose money when this flips above 1, and their selling can add to the move.", 1, 2),
    ("fear", "r:vix9d_vix", "Fear this week vs this month", "VIX9D ÷ VIX", "Above 1 means the coming week is priced as the danger zone.", 1, 2),
    ("fear", "r:vix1d_vix", "Fear for tomorrow vs this month", "VIX1D ÷ VIX", "A jump means traders expect a big move within one day (an event or a shock).", 1, 2),
    ("fear", "z:VOLI", "Fear priced on normal-size moves", "Nations VOLI", "The same idea as VIX but only from options near today's price. Rises when traders expect ordinary days to get bigger.", 1, 1),
    ("fear", "r:vix_overshoot", "Fear jumping more than the drop explains", "VIX overshoot", "Positive = fear rose more than the S&P 500's move would normally cause. Traders are paying up.", 1, 1),
    ("fear", "r:vrp_low", "Fear vs what really happened", "VIX − realized", "How much more traders priced than the market actually moved. Near zero or below = real moves have caught up with the fear.", 1, 1),
    ("fear", "r:rv_ratio", "Are daily swings getting bigger?", "1-month ÷ 3-month moves", "Above 1 = the last month swung more than the last three months. Turbulence is rising.", 1, 2),
    ("fear", "r:vix_floor_up", "Is fear's floor rising?", "VIX low vs 3 months ago", "When even the calm days have a higher VIX than before, fear is not fully leaving.", 1, 1),
    ("fear", "r:vvix", "Fear of a fear spike", "VVIX", "What traders pay for a sudden VIX jump. High = they want crash insurance on fear itself.", 1, 0),
    ("fear", "z:VV_RATIO", "Crash insurance vs today's fear", "VVIX ÷ VIX", "High while VIX is low = traders buying protection quietly before anything happens.", 1, 2),
    ("fear", "x:tdex", "Tail insurance: fear level or extra demand?", "Nations TailDex vs VIX, since 2013",
     "TailDex is what far-out S&P puts cost. Divided by VIX it shows whether people pay extra for crash protection, "
     "and where the S&P 500 sits says whether it was bought before the drop or in the flush.", 0, 0),
    ("tech", "z:VXN", "Tech fear gauge", "VXN", "What traders pay to protect the Nasdaq-100 for the next month.", 1, 1),
    ("tech", "z:VXN_VIX", "Tech fear vs S&P fear", "VXN ÷ VIX", "High = tech is priced riskier than the wider market, more than usual.", 1, 2),
    ("together", "x:width", "Is this fear broad or narrow?", "implied correlation vs VIX, since 2006",
     "Each grey dot is one day since 2006: how scared the index was (VIX, across) and how much stocks were priced to "
     "move together (up). Black dots = past first days with VIX 30+. Red = now. The blue box is normal for today's VIX.",
     0, 0),
    ("together", "x:where", "Where the stress is, and where it is not", "sectors and industry groups",
     "When the fear is narrow, this shows which parts of the market are taking the hit and which are holding up.",
     0, 0),
    ("together", "r:cor1m", "Do stocks move together? (next month)", "COR1M", "High = options price stocks moving as one, so stress in a few spreads to all.", 1, 1),
    ("together", "r:cor1m_chg20", "Is that rising fast?", "COR1M 20-day change", "A sharp rise means stocks are suddenly being priced to move together.", 1, 1),
    ("together", "z:COR3M", "Do stocks move together? (3 months)", "COR3M", "The same, three months out. When the 1-month is above this, the stress is now.", 1, 1),
    ("together", "z:EQ_RATIO", "Single-stock fear vs index fear", "VIXEQ ÷ VIX", "Very high = single stocks are wild but cancel out, so the index looks calm (fragile). Very low = the stress has reached the index.", 0, 2),
    ("together", "z:CSKEW", "Crash-together vs rally-together", "COR90D ÷ COR10D", "High = options price stocks falling together far more than rising together. Puts are bid, upside is not.", 1, 2),
    ("together", "z:DSPX", "How far apart stocks may move", "Cboe DSPX", "High = single stocks are expected to scatter far from each other.", 1, 1),
    ("dealers", "r:sqgex_low", "Dealer cushion", "SqueezeMetrics gamma", "When dealers have a big cushion they buy dips and sell rallies, so moves stay small. Low or negative = no cushion, moves run further.", 1, 1),
    ("dealers", "z:DIX", "Big buyers in dark pools", "SqueezeMetrics DIX", "Share of dark-pool trading that is buying. High = big investors quietly buying. Low = they are not.", -1, 1),
    ("dealers", "r:our_spx_gamma", "Our S&P 500 dealer gamma", "our map", "Our own measure of the dealer cushion, and whether the S&P 500 is above or below the line where it flips.", 0, 0),
    ("dealers", "r:our_mag7_neg", "Big tech names without a cushion", "Mag7 negative gamma", "How many of the 7 biggest tech stocks have dealers on the wrong side (moves can run).", 0, 0),
    ("dealers", "r:kurt_bear", "4-part warning checklist", "Kurt Altrichter", "Dealer cushion low, big funds selling, swings growing, under 40% of stocks in an uptrend. More parts on = more risk.", 0, 0),
    ("breadth", "x:mix", "Where stocks are in their cycle", "stage mix, S&P 1500",
     "Every stock is put in one of four stages each night: 1 basing (flat), 2 rising, 3 topping, 4 falling. "
     "Healthy markets have lots of green (Stage 2). Lots of red (Stage 4) = most stocks are falling.", 0, 0),
    ("breadth", "s:STAGE2", "Stocks in a rising stage", "% in Stage 2",
     "Share of stocks in Stage 2: price above rising short, medium and 50-day averages. Low = few stocks are "
     "actually going up.", -1, 1),
    ("breadth", "s:STAGE4", "Stocks in a falling stage", "% in Stage 4",
     "Share of stocks in Stage 4: price under falling averages. High = the selling is wide.", 1, 1),
    ("breadth", "s:NET5", "Stocks turning up minus turning down", "entered Stage 2 − Stage 4, 5 days",
     "Over the last week, how many stocks moved into a rising stage minus how many moved into a falling stage "
     "(as % of all). Negative = more breaking down than breaking out.", -1, 1),
    ("breadth", "s:WOE", "Stocks above their 50, 150 and 200-day averages", "weight of evidence",
     "The three shares averaged into one line (the \"weight of evidence\" used by stage analysts). Under 40 = most "
     "stocks are below their trends.", -1, 1),
    ("breadth", "s:EXT", "Stocks stretched far above trend", "Stage 2C / 2D",
     "Share of stocks so far above their 50-day average that they usually need a rest. High = overheated.", 1, 1),
    ("breadth", "x:moves", "Today's breakouts and breakdowns", "new Stage 2 / Stage 4",
     "Biggest stocks that broke out into a rising stage today, and those that broke down into a falling stage.", 0, 0),
    ("breadth", "x:wmix", "The long-term picture (weekly)", "30-week stage mix",
     "The same four stages on weekly charts with a 30-week average, the classic slow version. It changes much less "
     "often than the daily picture, so a shift here matters more.", 0, 0),
    ("breadth", "s:WS2", "Stocks in a long-term uptrend", "% in weekly Stage 2",
     "Share of stocks above a rising 30-week average. Low = few stocks are in real, lasting uptrends.", -1, 1),
    ("breadth", "s:WS4", "Stocks in a long-term downtrend", "% in weekly Stage 4",
     "Share of stocks below a falling 30-week average. High = lasting damage, not just a dip.", 1, 1),
    ("breadth", "x:wmoves", "New long-term uptrends and downtrends", "weekly Stage 2 / Stage 4 this week",
     "Stocks that started a long-term uptrend this week while beating the S&P 500, and stocks that started a "
     "long-term downtrend.", 0, 0),
    ("breadth", "r:pct200_low", "Stocks in a long uptrend", "% above 200-day", "Share of S&P 500 stocks above their 200-day average. Under 40% = most stocks are in downtrends.", 1, 0),
    ("breadth", "r:pct50_low", "Stocks in a medium uptrend", "% above 50-day", "Share above the 50-day average.", 1, 0),
    ("breadth", "r:pct20_low", "Stocks in a short uptrend", "% above 20-day", "Share above the 20-day average. Falls first when selling spreads.", 1, 0),
    ("breadth", "r:sb_dn5", "Stocks hit hard this week", "down 4%+ days, 5 days", "How many S&P 500 stocks fell 4% or more in a day over the last week. Big counts = selling is broad.", 1, 0),
    ("breadth", "r:nhnl_low", "New highs minus new lows", "10-day average", "Negative = more stocks breaking down to new lows than up to new highs.", 1, 0),
    ("breadth", "r:ad_lag20", "Rising vs falling stocks, last month", "advance-decline", "Falling = most stocks are slipping even if the index holds.", 1, 2),
    ("breadth", "r:sectors_below_50", "Sectors in a downtrend", "of 9 below 50-day", "How many of the 9 big sectors are below their 50-day average.", 1, 0),
    ("breadth", "r:mcosc_low", "Breadth momentum", "McClellan oscillator", "Below zero = the share of rising stocks is fading.", 1, 0),
    ("breadth", "r:mcsum_low", "Breadth over months", "McClellan summation", "Below its own yearly average = breadth weaker than usual.", 1, 0),
    ("breadth", "r:mc_div", "Index high, stocks not following", "McClellan divergence", "Only read when the S&P 500 is near its high: is breadth falling while price holds up?", 1, 0),
    ("credit", "r:nfci", "Financial stress (Chicago Fed)", "NFCI", "Weekly index of how easy it is to borrow and take risk. Rising = money getting tighter. One of the three readings in the main score.", 1, 2),
    ("credit", "r:lqd_ief_lag20", "Good-company bonds vs Treasuries", "LQD ÷ IEF, 20 days", "Falling behind = lenders want more pay to lend even to solid companies.", 1, 2),
    ("credit", "r:hyg_ief_lag20", "Junk bonds vs Treasuries", "HYG ÷ IEF, 20 days", "Falling behind = lenders pulling back from risky borrowers.", 1, 2),
    ("credit", "r:hy_oas", "Extra pay on junk bonds", "high-yield spread", "How much more junk bonds pay than Treasuries. Kurt's warning line is 5%.", 1, 2),
    ("credit", "r:ccc_aaa", "Weakest vs strongest companies' bonds", "CCC − AAA", "A widening gap = trouble starting at the weakest borrowers.", 1, 2),
    ("credit", "r:ccc_aaa_chg20", "Is that gap widening fast?", "20-day change", "A fast widening often comes before stock trouble.", 1, 2),
    ("credit", "z:MOVE", "Bond-market fear gauge", "MOVE", "The VIX of Treasury bonds. High = the rates market is unstable.", 1, 0),
    ("credit", "r:move_chg20", "Is bond fear rising fast?", "MOVE 20-day change", "A jump in bond fear often spills into stocks.", 1, 0),
    ("credit", "z:SKEW", "Price of crash insurance", "Cboe SKEW", "How expensive far-out crash protection is. High = someone is paying up for a disaster hedge.", 1, 0),
    ("credit", "r:gld_lead20", "Gold beating stocks", "GLD ÷ S&P 500, 20 days", "Gold doing better than stocks = money hiding.", 1, 2),
    ("appetite", "x:rot", "Which sectors are leading", "sector rotation vs SPY",
     "Each dot is a sector fund compared with the S&P 500. Right = stronger than the market, top = getting "
     "stronger. Sectors usually turn clockwise: Improving, Leading, Weakening, Lagging. The tail is the last 5 weeks.",
     0, 0),
    ("appetite", "x:secstage", "Sectors by stage", "share of each sector's stocks in each stage",
     "Green = share of the sector's stocks in a rising stage, red = falling. The % is the green share; the arrow "
     "shows how many points it moved in the last month.", 0, 0),
    ("appetite", "s:INDPOS", "Industry groups with more rising than falling stocks", "% of groups",
     "Of all industry groups with 4+ stocks (banks, chips, restaurants...), how many have more stocks rising than "
     "falling. Low = weakness is spread across most of the market, not just a few corners.", -1, 1),
    ("appetite", "x:industry", "Industry groups: strongest and weakest", "share of each group rising / falling",
     "Where money is flowing inside the sectors. Green = share of the group's stocks rising, red = falling.", 0, 0),
    ("appetite", "x:convex", "Where options look mispriced", "convexity lists, S&P 1500",
     "Stocks whose options look cheap or rich against their own trend: cheap calls on rising stocks, cheap puts on "
     "falling ones, rich premium on strong ones, and falling stocks where options underprice the moves. Full lists, "
     "the cross-asset ETF vol board and the track record are on the Convexity page.", 0, 0),
    ("appetite", "r:hb_lb_lag20", "Risky stocks vs calm stocks", "SPHB ÷ SPLV, 20 days", "Risky stocks falling behind = investors pulling back from risk.", 1, 2),
    ("appetite", "r:xly_xlp_lag20", "Wants vs needs", "XLY ÷ XLP, 20 days", "Discretionary (wants) falling behind staples (needs) = caution.", 1, 2),
    ("appetite", "r:spx_below_ma50", "S&P 500 vs its 50-day trend", "% from 50-day", "How far the index is from its 50-day average.", 1, 2),
    ("appetite", "r:bbl_close", "Distance to the bull/bear line", "Norseman line", "The line is 90% of the record close. Close to it or below = a deep pullback is under way.", 1, 1),
]

PHASE = {
    "QUIET": ("Quiet", "Nothing is loading: stocks are not moving as one and fear is calm.", "#3F7A4E"),
    "LOADED": ("Loaded", "Calm on the surface, risk building underneath: stocks are wild but cancel out.", "#A06A10"),
    "TRANSMITTING": ("Move happening", "Stocks are moving together, so trouble in a few is reaching the whole index.", "#A8473A"),
    "RELEASING": ("Easing", "The stress is fading: stocks are starting to move apart again.", "#2B6C9B"),
}


def load(data_dir: Path) -> tuple[dict, dict, dict, dict]:
    def rd(name):
        try:
            return json.loads((data_dir / name).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
    return rd("risktab.json"), rd("fusetab.json"), rd("zonecards.json"), rd("stagetab.json")


def _f(v):
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def _q(xs: list, f: float):
    w = sorted(v for v in xs if v is not None)
    return w[min(len(w) - 1, int(f * (len(w) - 1) + 0.5))] if w else None


def from_tile(t: dict, dates: list, base5: float) -> dict:
    """Risk tab tile -> card fields. Tile values are in display units; pct is already 'stress' oriented."""
    vals = [_f(v) for v in (t.get("long") or [])]
    yr = vals[-252:]
    lift = _f(t.get("lift5"))
    v = _f(t.get("value"))
    rank = _f(t.get("pct"))
    thr = _f(t.get("thr"))
    hi_is_bad = None
    if thr is not None and yr:
        hi_is_bad = thr >= (_q(yr, 0.5) or thr)
    c = {"value": v, "unit": t.get("unit") or "", "rank": rank, "vals": vals, "dates": dates[-len(vals):] if vals else [],
         "q25": _q(yr, 0.25), "q75": _q(yr, 0.75), "line": thr, "hi_bad": hi_is_bad,
         "v2wk": vals[-11] if len(vals) >= 11 else None, "role": t.get("role"),
         "rate": None if lift is None else lift * base5, "base": base5, "verdict": t.get("verdict") or ""}
    if t.get("id") == "kurt_bear":
        c.update({"parts": t.get("parts") or [], "rates": t.get("rates") or {}, "line": 3, "hi_bad": True,
                  "rank": None})
    if t.get("id") == "our_spx_gamma":
        c.update({"flip": t.get("flip"), "spot": t.get("spot"), "flip_dist": t.get("flip_dist"), "hi_bad": False,
                  "rank": None, "vals": [_f(x) for x in (t.get("spark") or [])],
                  "dates": t.get("spark_dates") or []})
    if t.get("id") == "our_mag7_neg":
        c.update({"of": t.get("of", 7), "hi_bad": True, "rank": None})
    return c


def from_zone(z: dict, stress: int) -> dict:
    y = z.get("year") or {}
    rk = y.get("rank")
    if stress == 0:
        side = "top" if (rk or 0) >= 0.5 else "bottom"
        srank = None if rk is None else max(rk, 1 - rk)
    else:
        side = "top" if stress > 0 else "bottom"
        srank = None if rk is None else (rk if stress > 0 else 1 - rk)
    rec = (y.get("rec") or {}).get(side) or {}
    line = y.get("q90") if side == "top" else y.get("q10")
    vals = y.get("vals") or []
    return {"value": z.get("value"), "unit": "", "rank": srank, "raw": rk, "two": stress == 0, "vals": vals,
            "dates": (z.get("dates3y") or [])[-len(vals):] if vals else [], "q25": y.get("q25"), "q75": y.get("q75"),
            "line": line, "hi_bad": side == "top", "v2wk": y.get("v2wk"), "role": "zone", "rate": rec.get("rate"),
            "base": y.get("base5"), "n_on": rec.get("n"), "fast": abs(z.get("z") or 0) >= 2, "z": z.get("z")}


NEEDS = {"where": "sectors", "wmix": "weekly", "wmoves": "weekly", "industry": "industries", "rot": "rotation", "secstage": "sectors"}


def build(risk: dict, fuse: dict, zones: dict, stage: dict | None = None, convex: dict | None = None) -> dict:
    base5 = 0.09
    tiles = {t["id"]: t for t in risk.get("tiles") or []}
    zc = {c["id"]: c for c in zones.get("cards") or []}
    stage = stage or {}
    sc = {c["id"]: c for c in stage.get("cards") or []}
    tdates = risk.get("tile_dates") or []
    cards = []
    for grp, src, title, short, means, stress, dec in CARDS:
        kind, key = src.split(":")
        if kind == "r" and key in tiles:
            c = from_tile(tiles[key], tdates, base5)
        elif kind == "z" and key in zc:
            c = from_zone(zc[key], stress)
        elif kind == "s" and key in sc:
            c = from_zone(sc[key], stress)
            c["role"] = "stage"
        elif kind == "x" and key == "tdex":
            if not (fuse.get("tdex") or {}).get("tdex"):
                continue
            c = {"value": None, "rank": None, "role": "special", "tdex": fuse["tdex"], "stage": stage}
        elif kind == "x" and key == "width":
            if not (fuse.get("width") or {}).get("vix"):
                continue
            c = {"value": None, "rank": None, "role": "special", "width": fuse["width"], "stage": stage}
        elif kind == "x" and key == "convex":
            if not (convex or {}).get("picks"):
                continue
            c = {"value": None, "rank": None, "role": "special", "convex": convex, "stage": stage}
        elif kind == "x" and stage.get("asof") and stage.get(NEEDS.get(key, "asof")):
            c = {"value": None, "rank": None, "role": "special", "stage": stage}
        else:
            continue
        if c.get("value") is None and key not in ("kurt_bear",) and kind != "x":
            continue
        c.update({"id": key, "group": grp, "title": title, "short": short, "means": means, "dec": dec, "src": kind})
        cards.append(c)
    return {"asof_stage": stage.get("asof"), "asof_risk": risk.get("asof"), "asof_fuse": fuse.get("asof") or zones.get("asof"), "score": risk.get("score"),
            "flags": risk.get("flags"), "hist": risk.get("hist") or [], "fuse": fuse if fuse.get("phase") else None,
            "combos": ((risk.get("volmon") or {}).get("combos") or []), "groups": GROUPS, "cards": cards,
            "method": risk.get("method") or {}, "built": datetime.now().isoformat(timespec="seconds")}


def write(data_dir: Path = DATA_DIR) -> dict:
    """Build risk.html in data_dir from the evening files there. Atomic writes."""
    from risk_page_html import page  # same folder (scripts/)

    risk, fuse, zones, stage = load(data_dir)
    try:
        convex = json.loads((data_dir / "convexity.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        convex = {}
    o = build(risk, fuse, zones, stage, convex)
    tmp = data_dir / "risk.html.tmp"
    tmp.write_text(page(o), encoding="utf-8", newline="\n")
    tmp.replace(data_dir / "risk.html")
    return o


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dir", type=Path, default=DATA_DIR)
    a = ap.parse_args()
    o = write(a.dir)
    print(f"risk.html: risk asof {o['asof_risk']} fuse asof {o['asof_fuse']} cards {len(o['cards'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
