"""rail_score.py - 2026-11-13, Claude for Mithil. Nightly REACTION vs TRANSITION score for the straddle rails and the
TEST short-strike marker. Called by scripts/cvx_replay.py right after p1score (its p1.json gets a "rails" key).

Why (Mithil 2026-11-13 22:59): our range is the inner SHORT STRIKE for selling credit spreads. It is not about crossing
or closing - it is whether a line acts as a reaction zone (price touches and turns back) or a transition zone (price
goes through and keeps going). 13 days 10/28-11/13 (claude/band-compare-20261113.md): straddle rails alone held about
1 first touch in 4; put-side breaks ran 0.7-1.9 straddles past the line, call-side 0.4-0.7.

Rules v1, fixed here before any result:
  lines   Doc's rails (every rail in his report), OUR rails (STR = 1x, 1σ = 1.25x) plus 1.5x and 2x of our STR, and the
          TEST marker's call / put short strike frozen at 09:45 (rule sm-v0 below).
  side    call = the line is above the 09:31 price, put = below.
  result  the p1score #17 rules: touch = within 2 pts; held = 10 pts back before a break; broke = 3 one-minute closes
          5+ pts through; first touch only; "untouched" / "gapped through" when never touched.
  past    after the touch, the furthest price went beyond the line (pts, and in straddles), and whether 16:00 closed beyond.

sm-v0 - TEST short-strike marker (same rule as the test cockpit's "Short strikes" button, build/tg_strike.js):
  today's 0DTE board, start-of-day OI, naive sign (dealers long calls, short puts). Each strike's dealer gamma if price
  goes there = (call OI - put OI) x phi(d1) / (k x sigma x sqrt(T)) x 2.5, d1 = sigma x sqrt(T) / 2, sigma = the strike's
  OTM IV, T = minutes to 16:00 / 525,600. Straddle = the 0DTE ATM straddle (nearest strike with both mids).
  Significant = at least 20% of the biggest |gamma| within 3 straddles of price.
  Call side: from the 1.5x rail out to 3x, the first significant POSITIVE strike (dealers absorb = reaction zone) plus the
  significant positive strikes right after it = the zone; short = the next listed strike beyond the zone.
  Put side: the same from the 1.75x rail (puts run further: 09:31 chain 2.1x down vs 1.85x up).
  Nothing found = that side has no short (transition all the way out).
Descriptors only (rule 4): nothing here is a trade signal.
"""
import json
import math
import re
import statistics as stx

MARK_RULE = "sm-v0"
SIG = 0.20
CALL_RAIL, PUT_RAIL, LIM = 1.5, 1.75, 3.0
MARK_T = "09:45"


def hh(m):
    return "%02d:%02d" % (m // 60, m % 60)


def mm(t):
    h, m = t.split(":")
    return int(h) * 60 + int(m)


# ------------------------------------------------------------------ #17 rules (identical to p1score.score_level)
def score_level(path, L):
    """path [(minute, price)] -> {touch, result: held/broke/touched/untouched/gapped through, t}."""
    if not path:
        return {"result": "no data"}
    side0 = 1 if path[0][1] > L else -1
    touch = None
    for i, (m, x) in enumerate(path):
        if abs(x - L) <= 2:
            touch = i
            break
        side0 = 1 if x > L else -1
    if touch is None:
        crossed = any((x - L) * side0 < 0 for _, x in path)
        return {"result": "untouched" if not crossed else "gapped through", "dist": round(min(abs(x - L) for _, x in path), 1)}
    run = 0
    for m, x in path[touch:]:
        if (x - L) * side0 >= 10:
            return {"touch": hh(path[touch][0]), "result": "held", "t": hh(m), "_i": touch}
        run = run + 1 if (L - x) * side0 >= 5 else 0
        if run >= 3:
            return {"touch": hh(path[touch][0]), "result": "broke", "t": hh(m), "_i": touch}
    return {"touch": hh(path[touch][0]), "result": "touched", "_i": touch}


# ------------------------------------------------------------------ sm-v0 marker
def otm_iv(k, S, ci, pi):
    a, b = (ci, pi) if k >= S else (pi, ci)
    return a if a and a > 0 else (b if b and b > 0 else 0.0)


def strike_gamma(k, coi, poi, sg, ty):
    """dealer gamma at strike k if price goes there (naive sign), dealer delta per 2.5 pts."""
    if not (sg > 0) or not (ty > 0):
        return 0.0
    sq = math.sqrt(ty)
    d1 = 0.5 * sg * sq
    return ((coi or 0) - (poi or 0)) * math.exp(-0.5 * d1 * d1) / math.sqrt(2 * math.pi) / (k * sg * sq) * 2.5


def marks(rows, S, em, t_min):
    """rows [(k, cOI, pOI, cIV, pIV)], spot S, straddle em, clock minute t_min -> sm-v0 dict (None if no data)."""
    if not rows or not (S > 0) or not (em and em > 0):
        return None
    ty = max(960 - t_min, 1) / 525600.0
    R = sorted((float(k), c or 0, p or 0, strike_gamma(float(k), c, p, otm_iv(float(k), S, ci, pi), ty))
               for k, c, p, ci, pi in rows)
    win = [g for k, c, p, g in R if abs(k - S) <= LIM * em]
    M = max((abs(g) for g in win), default=0.0)
    out = {"rule": MARK_RULE, "spot": round(S, 2), "straddle": round(em, 2), "t": hh(t_min), "call": None, "put": None}
    if not M:
        return out
    for side, mult, sgn in (("call", CALL_RAIL, 1), ("put", PUT_RAIL, -1)):
        rail, lim = S + sgn * mult * em, S + sgn * LIM * em
        seq = [r for r in (R if sgn > 0 else R[::-1]) if (r[0] - rail) * sgn >= 0 and (r[0] - lim) * sgn <= 0]
        i0 = next((i for i, r in enumerate(seq) if r[3] >= SIG * M), None)
        if i0 is None:
            out[side] = {"rail": round(rail, 2), "mult": mult, "short": None,
                         "why": "no absorbing strike between the %gx rail and 3x - transition all the way" % mult}
            continue
        j = i0
        while j + 1 < len(seq) and seq[j + 1][3] >= SIG * M:
            j += 1
        beyond = [r[0] for r in (R if sgn > 0 else R[::-1]) if (r[0] - seq[j][0]) * sgn > 0]
        short = beyond[0] if beyond else seq[j][0] + sgn * 5
        out[side] = {"rail": round(rail, 2), "mult": mult, "zone": sorted([seq[i0][0], seq[j][0]]),
                     "short": short, "x": round((short - S) * sgn / em, 2),
                     "zone_gamma_pct": round(100 * max(r[3] for r in seq[i0:j + 1]) / M)}
    return out


def marks_from_flow(row, oi_map, t_min):
    """row = one SPX flow-log minute (k, ci, pi, cm, pm, x); oi_map {"7600.0": [cOI, pOI]} = the day's 0DTE OI."""
    if not row or not row.get("k"):
        return None
    S = float(row.get("x") or 0)
    ks, cm, pm, ci, pi = row["k"], row.get("cm") or [], row.get("pm") or [], row.get("ci") or [], row.get("pi") or []
    em, best = None, None
    for q, k in enumerate(ks):
        c = cm[q] if q < len(cm) else None
        p = pm[q] if q < len(pm) else None
        if c and p and c > 0 and p > 0 and (best is None or abs(k - S) < best):
            best, em = abs(k - S), c + p
    rows = []
    for q, k in enumerate(ks):
        o = oi_map.get("%.1f" % float(k)) or [0, 0]
        rows.append((k, o[0] or 0, o[1] or 0, ci[q] if q < len(ci) else 0, pi[q] if q < len(pi) else 0))
    return marks(rows, S, em, t_min)


# ------------------------------------------------------------------ lines
def mult_of(name):
    n = str(name)
    if re.search(r"week", n, re.I):
        return "weekly"
    if re.search(r"ovn|overnight", n, re.I):
        return "overnight"
    if "½" in n or "0.5" in n:
        return "0.5σ"
    if "1.5" in n:
        return "1.5x"
    if re.search(r"(^|\s)2\s*[x×]|STR\s*2|\b2x\b", n, re.I):
        return "2x"
    if "1σ" in n or "1.25" in n:
        return "1.25x"
    if re.search(r"STR|straddle|expected|0DTE EM|\bEM\b", n, re.I):
        return "1x"
    return n


def plan_lines(doc, own, mark):
    out = []
    for r in (doc or {}).get("rails") or []:
        for k, side in (("lo", "put"), ("hi", "call")):
            if r.get(k):
                out.append(("Doc", mult_of(r.get("name", "")), str(r.get("name", "")), float(r[k])))
    str_ = None
    for r in (own or {}).get("rails") or []:
        nm = str(r.get("name", ""))
        if nm == "STR" and r.get("lo") and r.get("hi"):
            str_ = (float(r["lo"]), float(r["hi"]))
        for k in ("lo", "hi"):
            if r.get(k):
                out.append(("Ours", mult_of(nm), nm, float(r[k])))
    if str_:
        c, h = (str_[0] + str_[1]) / 2, (str_[1] - str_[0]) / 2
        for m in (1.5, 2.0):
            out += [("Ours", "%gx" % m, "STR %g×" % m, c - m * h), ("Ours", "%gx" % m, "STR %g×" % m, c + m * h)]
    for side in ("call", "put"):
        s = (mark or {}).get(side) or {}
        if s.get("short"):
            out.append(("Marker", "short", "sm-v0 %s short" % side, float(s["short"])))
    seen, uniq = set(), []          # Doc's "STR" and "Straddle" can be the same line: count it once
    for src, mult, name, L in out:
        key = (src, mult, round(L, 1))
        if key not in seen:
            seen.add(key)
            uniq.append((src, mult, name, L))
    return uniq, str_


def score_day(path, spx_flow, oi_map, doc, own):
    """path [(minute, price)] 09:31-16:00; spx_flow {HH:MM: flow row}; oi_map 0DTE OI; doc / own = morning plans."""
    if not path:
        return {"rule": "rails-v1", "lines": [], "mark": None, "err": "no path"}
    mark = None
    try:
        row = (spx_flow or {}).get(MARK_T)
        mark = marks_from_flow(row, oi_map or {}, mm(MARK_T)) if row else None
    except Exception as exc:
        mark = {"err": str(exc)[:120]}
    lines, str_ = plan_lines(doc, own, mark if isinstance(mark, dict) and "err" not in mark else None)
    S0, close = path[0][1], path[-1][1]
    h = (str_[1] - str_[0]) / 2 if str_ else ((mark or {}).get("straddle") if isinstance(mark, dict) else None)
    out = []
    for src, mult, name, L in lines:
        side = "call" if L > S0 else "put"
        sgn = 1 if side == "call" else -1
        r = score_level(path, L)
        i = r.pop("_i", None)
        past = None
        if i is not None:
            past = max(0.0, max((x - L) * sgn for _, x in path[i:]))
        if src == "Marker":
            side = name.split()[1]
        out.append({"src": src, "mult": mult, "name": name, "side": side, "level": round(L, 2), "result": r.get("result"),
                    "touch": r.get("touch"), "t": r.get("t"), "past": None if past is None else round(past, 1),
                    "past_x": None if past is None or not h else round(past / h, 2),
                    "close_beyond": (close - L) * sgn > 0})
    return {"rule": "rails-v1", "spot0": round(S0, 2), "close": round(close, 2), "straddle": round(h, 2) if h else None,
            "lines": out, "mark": mark}


# ------------------------------------------------------------------ running + page section
ORDER = {"1x": 0, "1.25x": 1, "1.5x": 2, "2x": 3, "short": 4, "0.5σ": 5, "overnight": 6, "weekly": 7}


def running(day_dirs):
    """day_dirs [(day_iso, p1 dict)] -> pooled per source x rail x side."""
    A = {}
    for _, p in day_dirs:
        for r in ((p.get("rails") or {}).get("lines") or []):
            k = (r["src"], r["mult"], r["side"])
            a = A.setdefault(k, {"n": 0, "held": 0, "broke": 0, "touched": 0, "untouched": 0, "gapped": 0, "past": [], "close_beyond": 0})
            a["n"] += 1
            res = r.get("result")
            if res == "held":
                a["held"] += 1
            elif res == "broke":
                a["broke"] += 1
            elif res == "touched":
                a["touched"] += 1
            elif res == "gapped through":
                a["gapped"] += 1
            else:
                a["untouched"] += 1
            if r.get("past_x") is not None:
                a["past"].append(r["past_x"])
            a["close_beyond"] += 1 if r.get("close_beyond") else 0
    out = []
    for (src, mult, side), a in sorted(A.items(), key=lambda kv: (kv[0][0], ORDER.get(kv[0][1], 9), kv[0][2])):
        t = a["held"] + a["broke"] + a["touched"]
        out.append({"src": src, "mult": mult, "side": side, "n": a["n"], "touches": t, "held": a["held"], "broke": a["broke"],
                    "held_pct": round(100 * a["held"] / t) if t else None, "untouched": a["untouched"], "gapped": a["gapped"],
                    "past_med_x": round(stx.median(a["past"]), 2) if a["past"] else None, "close_beyond": a["close_beyond"]})
    return {"days": len(day_dirs), "rows": out}


def _f(x, d=0):
    return "–" if x is None else ("{:,.%df}" % d).format(x)


def html(day, R):
    """day = tonight's p1["rails"]; R = running(...)."""
    if not day:
        return ""
    h = ["<h2>Rails: reaction or transition</h2><div class='sub'>Credit-spread view. Each line from the morning plans: did price "
         "touch it and turn back (<b>held</b> = 10 pts back first) or go through (<b>broke</b> = 3 one-minute closes 5+ pts past)? "
         "<b>Past</b> = how far price went beyond the line after touching it, in straddles. A short strike behind a line that held, "
         "or was never reached, kept its credit. Rules rails-v1 (fixed before results); marker = TEST rule sm-v0 at 09:45.</div>"]
    mk = day.get("mark") or {}
    if mk and "err" not in mk:
        def side_txt(s, lab):
            v = mk.get(s) or {}
            if not v:
                return "%s: no data" % lab
            if not v.get("short"):
                return "%s: <b>no short</b> (%s)" % (lab, v.get("why", "no absorbing strike"))
            return "%s: zone %s–%s, short <b>%s</b> (%.2fx straddle)" % (lab, _f(v["zone"][0]), _f(v["zone"][1]), _f(v["short"]), v["x"])
        h.append("<div class='sub'>Marker at %s (spot %s, straddle %s): %s · %s</div>" % (
            mk.get("t"), _f(mk.get("spot"), 2), _f(mk.get("straddle"), 2), side_txt("call", "Calls"), side_txt("put", "Puts")))
    rows = sorted(day.get("lines") or [], key=lambda r: (r["src"], ORDER.get(r["mult"], 9), r["side"]))
    h.append("<div class='wrap'><table><tr><th>source</th><th>rail</th><th>side</th><th>level</th><th>result</th><th>touch</th>"
             "<th>past (pts)</th><th>past (straddles)</th><th>closed beyond</th></tr>")
    for r in rows:
        h.append("<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>" % (
            r["src"], r["mult"], r["side"], _f(r["level"], 1), r["result"], r.get("touch") or "–", _f(r.get("past"), 1),
            _f(r.get("past_x"), 2), "yes" if r.get("close_beyond") else "no"))
    h.append("</table></div>")
    if R and R.get("rows"):
        h.append("<details open><summary>Running, %d nights - held %% = held ÷ touched (the reaction rate)</summary>"
                 "<div class='wrap'><table><tr><th>source</th><th>rail</th><th>side</th><th>days</th><th>touched</th><th>held</th>"
                 "<th>broke</th><th>held %%</th><th>never reached</th><th>past median (straddles)</th><th>closed beyond</th></tr>" % R["days"])
        for r in R["rows"]:
            h.append("<tr><td>%s</td><td>%s</td><td>%s</td><td>%d</td><td>%d</td><td>%d</td><td>%d</td><td>%s</td><td>%d</td><td>%s</td><td>%d</td></tr>" % (
                r["src"], r["mult"], r["side"], r["n"], r["touches"], r["held"], r["broke"], _f(r["held_pct"]), r["untouched"],
                _f(r["past_med_x"], 2), r["close_beyond"]))
        h.append("</table></div><div class='sub'>Trust a rail as a reaction zone only after 20+ nights and 20+ touches per row.</div></details>")
    return "".join(h)


if __name__ == "__main__":   # quick look: python rail_score.py reports/replay/<ymd>/p1.json
    import sys
    p = json.load(open(sys.argv[1], encoding="utf-8"))
    print(json.dumps(p.get("rails"), indent=1)[:4000])
