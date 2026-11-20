"""vol_monitor.py - created 2026-11-19. Risk tab "Vol monitor": the 8-gauge fear panel + plain-English read.

Mithil 2026-11-19: "Start with VIX + COR1M + COR3M + VVIX. Add VIXEQ and VXN and MOVE and term structure"
-> one panel on the Risk tab, a "how to read" guide, and combinations explained for a non-technical reader.

What it does (end of day, called by scripts/risk_tab.py, ZERO Convex calls - Cboe indices via the FMP passthrough):
  gauges()       each gauge: today, 1-year percentile, 5-day change, plain meaning for its zone
  combos()       rule set vm-v1: 9 named combinations, each a True/False series over 2010-2026
  combo_stats()  what SPX did after each combination since 2010 (days, separate episodes,
                 up after 20 sessions, median 20-session move, 3%+ drop within 5 sessions)
  plain_read()   the rule-written "today's read" (always there)
  ai_read()      optional: the laptop's free local AI rewrites the facts in 3-4 plain sentences;
                 a checker throws it away if it writes any number that is not in the facts
                 (then the rule read is shown). No model call leaves the LAN (rule 7).

Descriptor only (rule 4) - not a trade signal. Percentiles use the trailing 252 sessions only (no peeking).
"""
from __future__ import annotations

import json
import math
import re

import numpy as np
import pandas as pd

RULESET = "vm-v1"
WIN = 252
MINP = 126
SPARK = 130
LONG = 756         # ~3 years for the pair charts and the full view
LONG_SERIES = ["vix", "vvix", "cor1m", "cor3m", "vixeq", "vxn", "move", "dspx", "vix9d", "vix3m", "vix6m", "spx"]
LONG_PCT = ["vix", "vvix", "cor1m", "cor3m", "vixeq", "vxn", "move", "dspx"]

# symbol columns expected in X (risk_tab's FMP frame)
NEED = ["^SPX", "^VIX", "^VIX9D", "^VIX3M", "^VIX6M", "^VVIX", "^COR1M", "^COR3M", "^VIXEQ", "^VXN", "^MOVE",
        "^DSPX", "HYG", "IEF"]

# gauge id -> (column, short name, what it is in plain words, [low, normal, high, very high] meaning)
GAUGES = [
    ("vix", "^VIX", "VIX", "S&P 500 fear gauge - what traders pay to protect the index for 1 month",
     ["Calm. Little fear priced; protection is cheap.",
      "Normal amount of fear.",
      "Fear is high. Investors are paying up for protection.",
      "Very high fear - top 10% of the past year."]),
    ("vvix", "^VVIX", "VVIX", "How jumpy fear itself is - the price of insurance on the VIX",
     ["Fear is steady; nobody expects a VIX jump.",
      "Normal.",
      "Traders are buying VIX calls - they expect fear could jump fast.",
      "Panic-style demand for VIX calls - top 10% of the year."]),
    ("cor1m", "^COR1M", "COR1M", "Are stocks moving together? Expected over the next month (0-100)",
     ["Stocks are moving on their own news - a stock-picker market.",
      "Normal mix of together and apart.",
      "Stocks are expected to move as one block - big-picture (macro) worry.",
      "Everything moving as one - typical of a sell-off or a macro scare."]),
    ("cor3m", "^COR3M", "COR3M", "Same as COR1M but over the next 3 months - the slower, longer view",
     ["Longer term: stocks expected to go their own way.",
      "Normal.",
      "Longer term also expects stocks to move together.",
      "Longer term worry is high too - not just a short scare."]),
    ("vixeq", "^VIXEQ", "VIXEQ", "Average fear in single stocks (the big S&P names), not the index",
     ["Single stocks are calm.",
      "Normal single-stock fear.",
      "Single stocks are jumpy - company-level risk is high.",
      "Very high single-stock fear."]),
    ("vxn", "^VXN", "VXN", "Nasdaq-100 fear gauge - the tech version of VIX",
     ["Tech is calm.",
      "Normal tech fear.",
      "Tech fear is high.",
      "Very high tech fear - top 10% of the year."]),
    ("move", "^MOVE", "MOVE", "Bond market fear gauge - expected swings in Treasury yields",
     ["Bond market is calm.",
      "Normal bond-market fear.",
      "Bond market is nervous - rates could swing.",
      "Very high bond fear - rate shock risk."]),
    ("dspx", "^DSPX", "DSPX", "Dispersion - how far apart single stocks are expected to move",
     ["Stocks expected to move alike.",
      "Normal spread between stocks.",
      "Big gaps expected between winners and losers.",
      "Very large gaps expected between stocks."]),
]

GUIDE = {
    "intro": ("These gauges are prices of options - what investors pay today for protection. They show how scared the "
              "market is and WHERE the fear sits (the whole index, single stocks, tech, or bonds). They do not tell you "
              "the direction by themselves. The colour of each gauge is where today sits against its own past year: "
              "green = low, white = normal, orange = high (top 30%), red = very high (top 10%)."),
    "gauges": [
        ["VIX", "The main fear gauge for the S&P 500. 12-15 = calm, around 20 = normal worry, 25-30 = stressed, 40+ = panic."],
        ["VVIX", "The fear of fear. When VVIX rises while VIX is still low, big players are quietly buying crash insurance."],
        ["COR1M / COR3M", "Implied correlation, 0-100. Low = stocks move on their own stories. High = everything moves together, "
                          "which happens when one big worry (rates, recession, war) drives the whole market."],
        ["COR1M minus COR3M", "Positive = the worry is NEAR-TERM (a short scare). Negative = normal; longer-term is priced higher."],
        ["VIXEQ", "Average fear across single stocks. It is almost always above VIX, because single stocks swing more than "
                  "the index. A shrinking gap (VIXEQ minus VIX) means the index fear is catching up - investors are hedging "
                  "the whole market, not individual stocks."],
        ["VXN", "Tech fear. VXN running far above VIX means tech is where the worry is."],
        ["MOVE", "Bond fear. If stocks are scared but MOVE is calm, the stress is in stocks only. If both are high, it is broad."],
        ["DSPX", "Dispersion. High = winners and losers far apart (a picker's market). Falling DSPX in a sell-off = "
                 "stocks being sold together."],
        ["Term structure", "VIX for 9 days, 1 month, 3 months, 6 months. Normal = upward slope (later costs more). "
                           "Upside-down (9-day above 1-month above 3-month) = the market fears the next few days - "
                           "panic. When it flips back to upward, the panic is usually ending."],
        ["Credit (HYG vs IEF)", "Junk bonds against safe Treasuries over 20 days. If junk is falling behind, "
                                "real money is worried about companies' finances - stress is spreading beyond options."],
    ],
    "steps": [
        "1. How scared? Look at VIX and its colour.",
        "2. Where is the fear? Index only (COR1M high, VIXEQ gap shrinking), single stocks (VIXEQ high, COR1M low), "
        "tech (VXN), or bonds (MOVE).",
        "3. Does anything else agree? Bonds (MOVE) and credit confirming = broad stress. Not confirming = stock-only scare, "
        "which has faded more often.",
        "4. How urgent? Term structure upside-down and COR1M above COR3M = near-term panic.",
        "5. Is it ending? Watch for the curve to turn upward again, COR1M to roll over, and VVIX to drop.",
    ],
    "caveat": ("The history numbers show what the S&P 500 did after past days like this, 2010-2026 (VIXEQ/DSPX from "
               "mid-2014). They describe; they do not promise. Overlapping days from one episode are counted once in "
               "'episodes'. Not a trade signal."),
}

# combination id -> (title, plain meaning, what to watch next, colour)
COMBO_TEXT = {
    "broad": ("Broad stress - stocks, bonds and credit agree",
              "Fear is high in stocks AND the bond market is nervous AND junk bonds are falling behind Treasuries. "
              "This is the kind of stress that can last, because it is not just option traders - real money is worried.",
              "Credit and MOVE calming down first; the term structure turning upward again.", "#FF5C5C"),
    "panic_curve": ("Panic in the front of the curve",
                    "Short-term protection costs more than longer-term (9-day VIX above 1-month above 3-month). "
                    "The market fears the next few days most. This often appears near short-term lows, but it can stay "
                    "upside-down for days in a real sell-off.",
                    "The curve flipping back to upward (9-day VIX below VIX) - that is usually the first sign the panic is ending.",
                    "#FF5C5C"),
    "equity_hedge": ("Stock-only hedging spike",
                     "Index fear is high and stocks are expected to move as one block, but bond fear is not rising "
                     "and credit is not breaking. Investors are buying protection on the whole market for one "
                     "big worry. Historically these spikes faded more often than they turned into crashes.",
                     "COR1M rolling over below COR3M, VVIX falling, and the curve turning upward. If MOVE or credit join in, "
                     "it becomes broad stress.", "#FF9F43"),
    "tech": ("Tech is the fear source",
             "Nasdaq fear (VXN) is running unusually far above S&P fear. The worry is concentrated in tech and growth "
             "names - the rest of the market is calmer.",
             "Whether the gap closes (tech calming) or VIX rises to meet it (fear spreading).", "#FF9F43"),
    "rates": ("Bond market nervous, stocks not (yet)",
              "Bond fear (MOVE) is high while stock fear is ordinary. Rate swings can spill into stocks later - "
              "or settle down without stocks noticing.",
              "VIX starting to rise with MOVE (spreading) or MOVE dropping back (settled).", "#FF9F43"),
    "hidden": ("Fear building under a calm surface",
               "The VIX looks ordinary, but the fear of fear (VVIX) is high or stocks are quickly starting to move "
               "together. Bigger players are buying insurance before it shows up in the headline number.",
               "VIX following VVIX up (the warning was right) or VVIX easing back (false alarm).", "#F4D03F"),
    "fading": ("Fear fading - a spike is releasing",
               "The VIX was very high in the last two weeks, but the curve is back to normal (upward), and stocks are "
               "starting to move apart again. Protection bought in the scare is being sold.",
               "A new spike in VIX9D or COR1M would cancel it.", "#3FD0B0"),
    "picker": ("Stock-picker market",
               "Stocks are moving on their own stories and single stocks are much jumpier than the index. The index "
               "looks calm because winners and losers cancel out. Good for stock selection, not a market-wide worry.",
               "COR1M rising fast - that is when 'everything moves together' comes back.", "#7FB8FF"),
    "calm": ("Calm and quiet",
             "Low fear, stocks going their own way, normal upward curve. Protection is cheap. Quiet markets can stay "
             "quiet for a long time; they are also when insurance costs least.",
             "VVIX or COR1M lifting first - the early warnings that calm is ending.", "#3FD0B0"),
}
PRIORITY = ["broad", "panic_curve", "equity_hedge", "tech", "rates", "hidden", "fading", "picker", "calm"]


# ------------------------------------------------------------------ helpers
def roll_pct(s: pd.Series, win: int = WIN, minp: int = MINP) -> pd.Series:
    """Share of the trailing window (today included) at or below today - no look-ahead."""
    return s.rolling(win, min_periods=minp).apply(lambda w: float((w <= w[-1]).mean()), raw=True)


def zone(p) -> int:
    if p is None or not np.isfinite(p):
        return 1
    return 0 if p < 0.2 else 1 if p < 0.7 else 2 if p < 0.9 else 3


def fnum(x, k=2):
    if x is None:
        return None
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    return round(x, k) if math.isfinite(x) else None


def frame(X: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """X (closes) -> F (levels + derived) and P (1-year percentiles)."""
    X = X.copy()
    for c in NEED:
        if c not in X:
            X[c] = np.nan
    F = pd.DataFrame(index=X.index)
    for gid, col, *_ in GAUGES:
        F[gid] = X[col]
    F["spx"] = X["^SPX"]
    F["vix9d"], F["vix3m"], F["vix6m"] = X["^VIX9D"], X["^VIX3M"], X["^VIX6M"]
    F["cor_slope"] = X["^COR1M"] - X["^COR3M"]
    F["eq_gap"] = X["^VIXEQ"] - X["^VIX"]
    F["tech_gap"] = X["^VXN"] - X["^VIX"]
    F["credit"] = -(np.log(X["HYG"] / X["IEF"]) - np.log(X["HYG"] / X["IEF"]).shift(20))  # + = junk lagging
    F["cor1m_chg10"] = X["^COR1M"] - X["^COR1M"].shift(10)
    F["eq_gap_chg5"] = F["eq_gap"] - F["eq_gap"].shift(5)
    F["move_chg5"] = X["^MOVE"] - X["^MOVE"].shift(5)
    P = pd.DataFrame(index=F.index)
    for c in ["vix", "vvix", "cor1m", "cor3m", "vixeq", "vxn", "move", "dspx", "eq_gap", "tech_gap", "credit",
              "cor1m_chg10", "cor_slope"]:
        P[c] = roll_pct(F[c])
    return F, P


def combos(F: pd.DataFrame, P: pd.DataFrame) -> pd.DataFrame:
    """Rule set vm-v1. Each column = True on days the combination is on (NaN inputs -> False)."""
    g = lambda c: P[c].fillna(-1)          # noqa: E731  unknown percentile never passes a 'high' test
    lo = lambda c: P[c].fillna(2)          # noqa: E731  ...nor a 'low' test
    inv = (F["vix9d"] > F["vix"]) & (F["vix"] > F["vix3m"])
    upward = (F["vix9d"] < F["vix"]) & (F["vix"] < F["vix3m"])
    # bonds confirm = MOVE high AND still rising this week (a high but falling MOVE is the old scare leaving)
    bonds = (g("move") > 0.8) & (F["move_chg5"].fillna(-1) > 0)
    C = pd.DataFrame(index=F.index)
    C["broad"] = (g("vix") > 0.85) & bonds & (g("credit") > 0.8)
    C["panic_curve"] = inv
    C["equity_hedge"] = (g("vix") > 0.8) & (g("cor1m") > 0.85) & ~bonds & (lo("credit") < 0.8)
    C["tech"] = (g("tech_gap") > 0.85) & (g("vxn") > 0.7)
    C["rates"] = (g("move") > 0.8) & (lo("vix") < 0.6)
    C["hidden"] = (lo("vix") < 0.5) & ((g("vvix") > 0.8) | (g("cor1m_chg10") > 0.85))
    recent_hi = g("vix").rolling(10, min_periods=1).max() > 0.85
    C["fading"] = recent_hi & upward & (F["cor1m"] < F["cor1m"].rolling(5).max() - 2) & (g("vix") < 0.9)
    C["picker"] = (lo("cor1m") < 0.2) & (g("eq_gap") > 0.7)
    C["calm"] = (lo("vix") < 0.3) & (lo("cor1m") < 0.3) & ((F["vix"] / F["vix3m"]) < 0.9)
    return C.fillna(False).astype(bool)


def forward(spx: pd.Series) -> pd.DataFrame:
    a = spx.to_numpy(dtype=float)
    n = len(a)
    r20 = np.full(n, np.nan)
    w5 = np.full(n, np.nan)
    for i in range(n):
        if i + 20 < n:
            r20[i] = a[i + 20] / a[i] - 1
        if i + 5 < n:
            w5[i] = a[i + 1:i + 6].min() / a[i] - 1
    return pd.DataFrame({"r20": r20, "w5": w5}, index=spx.index)


def episodes(mask: pd.Series, gap: int = 5) -> int:
    idx = np.flatnonzero(mask.to_numpy())
    if not len(idx):
        return 0
    return int(1 + (np.diff(idx) > gap).sum())


def _stats(m: pd.Series, fw: pd.DataFrame) -> dict:
    ok = m & fw["r20"].notna() & fw["w5"].notna()
    n = int(ok.sum())
    if n == 0:
        return {"days": int(m.sum()), "episodes": episodes(m), "n_fwd": 0}
    r, w = fw.loc[ok, "r20"], fw.loc[ok, "w5"]
    return {"days": int(m.sum()), "episodes": episodes(m), "n_fwd": n,
            "up20": fnum((r > 0).mean(), 3), "med20": fnum(100 * r.median(), 2),
            "p10_20": fnum(100 * r.quantile(0.1), 2), "drop5": fnum((w <= -0.03).mean(), 3)}


def combo_stats(F: pd.DataFrame, C: pd.DataFrame) -> dict:
    fw = forward(F["spx"])
    valid = F["spx"].notna() & F["vix"].notna()
    out = {"_all": _stats(valid, fw)}
    for c in C.columns:
        out[c] = _stats(C[c] & valid, fw)
    return out


def term_shape(v9, v, v3, v6) -> tuple[str, str]:
    if None in (v9, v, v3) or not all(np.isfinite([v9, v, v3])):
        return "unknown", "No term data."
    if v9 > v > v3:
        return "inverted", "Upside-down: the next 9 days cost the most. Near-term panic."
    if v > v3:
        return "front_high", "1-month above 3-month: stress, but not full panic."
    if v9 > v:
        return "kink", "Mostly normal, but the next 9 days are bid - a near-term event or worry."
    if v6 is not None and np.isfinite(v6) and (v6 - v) > 3:
        return "steep", "Steep upward slope: calm now, longer-term fear priced higher. Normal."
    return "normal", "Normal upward slope: later protection costs more. No near-term panic."


# ------------------------------------------------------------------ the panel
def gauges(F: pd.DataFrame, P: pd.DataFrame) -> list[dict]:
    last = F.index[-1]
    out = []
    for gid, col, name, what, zones in GAUGES:
        s = F[gid]
        v = s.iloc[-1]
        p = P[gid].iloc[-1] if gid in P else np.nan
        z = zone(p)
        lv = s.dropna()
        c5 = s.iloc[-1] - s.iloc[-6] if len(s) > 6 else np.nan
        out.append({"id": gid, "name": name, "what": what, "value": fnum(v, 2), "pct": fnum(p, 3), "zone": z,
                    "chg5": fnum(c5, 2), "meaning": zones[z], "spark": [fnum(x, 2) for x in s.iloc[-SPARK:].tolist()],
                    "since": lv.index[0].date().isoformat() if len(lv) else None,
                    "stale": bool(len(lv) and lv.index[-1] != last)})
    return out


def derived(F: pd.DataFrame, P: pd.DataFrame) -> dict:
    L = F.iloc[-1]
    v9, v, v3, v6 = (fnum(L[k], 2) for k in ("vix9d", "vix", "vix3m", "vix6m"))
    shape, shape_txt = term_shape(v9, v, v3, v6)
    sl = fnum(L["cor_slope"], 2)
    eg, eg5 = fnum(L["eq_gap"], 2), fnum(L["eq_gap_chg5"], 2)
    tg = fnum(L["tech_gap"], 2)
    cr, crp = fnum(100 * L["credit"], 2), fnum(P["credit"].iloc[-1], 3)
    return {
        "term": {"vix9d": v9, "vix": v, "vix3m": v3, "vix6m": v6, "shape": shape, "text": shape_txt},
        "cor_slope": {"value": sl, "pct": fnum(P["cor_slope"].iloc[-1], 3),
                      "text": None if sl is None else (
                          f"COR1M is {abs(sl):.1f} above COR3M: the together-worry is near-term." if sl > 0.5 else
                          f"COR1M is {abs(sl):.1f} below COR3M: normal - no near-term correlation scare." if sl < -0.5 else
                          "COR1M and COR3M about equal.")},
        "eq_gap": {"value": eg, "chg5": eg5, "pct": fnum(P["eq_gap"].iloc[-1], 3),
                   "text": None if eg is None else (
                       f"Single-stock fear is {eg:.1f} points above index fear and the gap shrank {abs(eg5):.1f} in 5 days: "
                       "the index is being hedged as a block." if eg5 is not None and eg5 < -1 else
                       f"Single-stock fear is {eg:.1f} points above index fear and the gap widened {eg5:.1f} in 5 days: "
                       "the worry is company-level." if eg5 is not None and eg5 > 1 else
                       f"Single-stock fear is {eg:.1f} points above index fear - about steady this week.")},
        "tech_gap": {"value": tg, "pct": fnum(P["tech_gap"].iloc[-1], 3),
                     "text": None if tg is None else f"Tech fear (VXN) is {tg:.1f} points above S&P fear (VIX)."},
        "credit": {"value": cr, "pct": crp,
                   "text": None if cr is None else (
                       "Junk bonds are falling behind Treasuries - credit is worried." if (crp or 0) > 0.8 else
                       "Junk bonds are keeping up with Treasuries - credit is not worried." if (crp or 1) < 0.6 else
                       "Credit is a little soft, not alarming.")},
    }


def plain_read(G: list[dict], D: dict, on: list[str], stats: dict | None = None) -> dict:
    g = {x["id"]: x for x in G}
    lvl = ["low", "normal", "high", "very high"]

    def say(gid):
        x = g.get(gid)
        return "unknown" if not x or x["value"] is None else lvl[x["zone"]]

    head = COMBO_TEXT[on[0]][0] if on else "Normal - no special combination"
    s = []
    vx = g.get("vix", {})
    if vx.get("value") is not None:
        ch = vx.get("chg5")
        s.append(f"Fear is {say('vix')}: VIX {vx['value']:.2f}"
                 + (f", {'up' if ch >= 0 else 'down'} {abs(ch):.2f} in 5 days." if ch is not None else "."))
    c1 = g.get("cor1m", {})
    if c1.get("value") is not None:
        s.append("Stocks are expected to move together (macro worry)." if c1["zone"] >= 2 else
                 "Stocks are moving on their own stories." if c1["zone"] == 0 else
                 "Stocks moving together a normal amount.")
    mv = g.get("move", {})
    cr = D["credit"].get("pct")
    if mv.get("value") is not None:
        conf = (mv["zone"] >= 2 and (mv.get("chg5") or 0) > 0) or (cr is not None and cr > 0.8)
        if vx.get("zone", 1) >= 2:
            s.append("Bonds/credit are confirming - the stress is broad." if conf else
                     "Bonds and credit are NOT confirming - the stress is in stocks only.")
        elif mv["zone"] >= 2:
            s.append("The bond market is nervous even though stocks are not.")
    s.append(D["term"]["text"])
    if on and stats and stats.get(on[0], {}).get("n_fwd") and stats.get("_all", {}).get("n_fwd"):
        a, b = stats[on[0]], stats["_all"]
        s.append(f"Past days like this since 2010 ({a['episodes']} separate episodes): the S&P 500 was higher 20 sessions "
                 f"later {round(100 * a['up20'])}% of the time (all days: {round(100 * b['up20'])}%), and fell 3%+ within "
                 f"5 sessions {round(100 * a['drop5'])}% of the time (all days: {round(100 * b['drop5'])}%).")
    watch = COMBO_TEXT[on[0]][2] if on else "VVIX and COR1M - they usually move first when calm is about to end."
    return {"headline": head, "sentences": s, "watch": watch}


# ------------------------------------------------------------------ optional local AI read
AI_PROMPT = """You explain the stock market's fear gauges to someone with NO finance training.
Use ONLY the facts below. Keep the meaning of "key_conclusions_keep_these" exactly (do not reverse or soften them)
and end with what to watch. Do not add any number that is not in the facts. No jargon: if you must name a gauge,
say what it means in the same sentence. No advice, no predictions, no hype. 4 or 5 short sentences.

Facts (JSON):
{facts}

Return JSON exactly like: {{"read": "your 4-5 sentences"}}"""

_NUM = re.compile(r"-?\d+(?:\.\d+)?")


def _nums(obj) -> set[str]:
    out = set()
    for m in _NUM.findall(json.dumps(obj)):
        x = abs(float(m))
        out |= {f"{x:g}", f"{x:.0f}", f"{x:.1f}", f"{x:.2f}", f"{round(x)}"}
    return out


def check_ai(text: str, facts: dict) -> bool:
    """True if every number in the AI text appears in the facts (any rounding of them)."""
    allowed = _nums(facts) | {str(i) for i in range(0, 11)}
    for m in _NUM.findall(text or ""):
        x = abs(float(m))
        if not ({f"{x:g}", f"{x:.0f}", f"{x:.1f}", f"{x:.2f}"} & allowed):
            return False
    return True


def ai_facts(G: list[dict], D: dict, on: list[str], stats: dict, read: dict | None = None) -> dict:
    lvl = ["low", "normal", "high", "very high"]
    f = {"key_conclusions_keep_these": (read or {}).get("sentences", []), "what_to_watch": (read or {}).get("watch"),
         "gauges": {x["name"]: {"value": x["value"], "level_vs_past_year": lvl[x["zone"]], "meaning": x["meaning"]}
                    for x in G if x["value"] is not None},
         "curve": D["term"]["text"], "credit": D["credit"]["text"], "single_vs_index": D["eq_gap"]["text"],
         "combinations_on": [COMBO_TEXT[c][0] + ": " + COMBO_TEXT[c][1] for c in on]}
    if on and stats.get(on[0], {}).get("n_fwd"):
        st = stats[on[0]]
        f["history_of_top_combination"] = {"past_days": st["days"], "separate_episodes": st["episodes"],
                                           "share_of_times_SPX_higher_20_sessions_later_pct": round(100 * st["up20"]),
                                           "median_SPX_move_20_sessions_pct": st["med20"]}
    return f


def ai_read(facts: dict, llm=None, model: str | None = None) -> dict:
    """Ask the local AI; returns {"text", "ok", "error"}. Never raises."""
    if llm is None:
        return {"text": None, "ok": False, "error": "no local AI"}
    try:
        raw = llm.complete_json(AI_PROMPT.format(facts=json.dumps(facts, indent=1)), model=model, max_tokens=400)
        obj = json.loads(raw) if isinstance(raw, str) else raw
        txt = str((obj or {}).get("read") or "").strip()
        if not txt:
            return {"text": None, "ok": False, "error": "empty"}
        if len(txt) > 900:
            txt = txt[:900].rsplit(".", 1)[0] + "."
        if not check_ai(txt, facts):
            return {"text": None, "ok": False, "error": "dropped: a number not in the facts"}
        return {"text": txt, "ok": True, "error": None}
    except Exception as e:  # noqa: BLE001
        return {"text": None, "ok": False, "error": f"{type(e).__name__}: {str(e)[:120]}"}


def build_panel(X: pd.DataFrame, llm=None, model: str | None = None) -> dict:
    F, P = frame(X)
    F = F[F["vix"].notna() & F["spx"].notna()]
    P = P.reindex(F.index)
    C = combos(F, P)
    stats = combo_stats(F, C)
    G = gauges(F, P)
    D = derived(F, P)
    on = [c for c in PRIORITY if bool(C[c].iloc[-1])]
    hist = {}
    for c in C.columns:  # last 130 sessions on/off strip per combination
        hist[c] = [int(x) for x in C[c].iloc[-SPARK:].tolist()]
    read = plain_read(G, D, on, stats)
    facts = ai_facts(G, D, on, stats, read)
    ai = ai_read(facts, llm, model) if llm is not None else {"text": None, "ok": False, "error": "not run"}
    combos_out = []
    for c in PRIORITY:
        t = COMBO_TEXT[c]
        combos_out.append({"id": c, "title": t[0], "meaning": t[1], "watch": t[2], "color": t[3],
                           "on": bool(C[c].iloc[-1]), "stats": stats[c], "strip": hist[c],
                           "last_on": (C.index[C[c]][-1].date().isoformat() if C[c].any() else None)})
    tail = F.index[-LONG:]
    k = lambda c: 0 if c == "spx" else 2  # noqa: E731
    long = {"dates": [d.date().isoformat() for d in tail],
            "s": {c: [fnum(x, k(c)) for x in F.loc[tail, c].tolist()] for c in LONG_SERIES},
            "p": {c: [None if not np.isfinite(x) else int(round(100 * x)) for x in P.loc[tail, c].tolist()] for c in LONG_PCT},
            "c": {c: [int(x) for x in C.loc[tail, c].tolist()] for c in C.columns}}
    return {"ruleset": RULESET, "asof": F.index[-1].date().isoformat(), "long": long,
            "dates": [d.date().isoformat() for d in F.index[-SPARK:]],
            "gauges": G, "derived": D, "on": on, "combos": combos_out, "base": stats["_all"],
            "read": read, "ai": ai, "guide": GUIDE}
