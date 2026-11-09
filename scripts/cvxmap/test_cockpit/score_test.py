"""
Nightly scorer for the TEST cockpit - scoring v1 (Mithil 2026-11-08 16:12 / 16:23; MAP master tracker §0.6).

Joins the frozen 15-minute read-outs (data/test/readouts_YYYYMMDD.jsonl, written by the test page) to the SPX minute
path in the production minute log (data/flow_YYYYMMDD.ndjson). It never changes a read-out, and it recomputes every
score from scratch on each run, so the same inputs always give the same output (deterministic, no state).

    python score_test.py                      score everything available  -> data/test/scores/
    python score_test.py --data DIR --out DIR (tests)

Rules (frozen as v1; a change makes v2 and applies forward only):
  Read-outs  first record per (date, slot) counts; later duplicates are listed, never used.
  Prices     minute prices x from the minute log, 09:30 to the session close. Close = price at the close minute, else
             the last minute before it.
  S1 width   zone widths as recorded (zero-line band |g| <= 10% of R; peak/trough flat zone |g| >= 95% of the extreme).
             Median width per config and per type. Judged together with S2.
  S2 works   each zone of each read-out (and the plain levels) separately. Approach side = where spot was at the read-out
             (below lo-2 / above hi+2; inside -> 'started inside', excluded). Touch = first minute after the read-out within
             [lo-2, hi+2], up to 60 min or the close. After the touch, up to 60 min or the close: held = price 10 pts back out
             the approach side (<= lo-10 from below / >= hi+10 from above); broke = 3 consecutive minutes 5+ pts through
             (>= hi+5 from below / <= lo-5 from above); first event wins; neither = unresolved (reported, not in the rate).
             Move back = maximum favourable excursion from the touch price over the next 15 min (MFE15); adverse (MAE15)
             also reported. Plain levels = round 25s within 1% of spot, prior session high and low, today's open.
  S3 gamma   sign of gamma at spot vs (t, t+30]: Q = realized range / (2 x im30), im30 = 0DTE straddle x sqrt(30 / minutes
             to close). Q = 1 is what the straddle implies for a random walk. Long gamma called right when Q < 1, short
             when Q > 1. Window needs t+30 <= close and >= 25 minute prices, else 'incomplete'. Lag-1 autocorrelation of
             1-min returns reported alongside (long gamma should be negative = choppy).
  S4 close   prediction = recorded close.pred; error = |pred - close|; null = miss. First = first slot within 5 pts.
             Lock = earliest slot after which EVERY present slot is within 5 pts (a miss breaks it). A day with any slot
             absent is 'incomplete' and kept out of the headline medians. Baseline = spot at each read-out, same rules.
  S5 1DTE/Fri same as S4 for the 1DTE and Friday forecasts, grouped by target date; errors also / that horizon's straddle.
             Pending until the target's minute log exists.
"""
import argparse, datetime, glob, json, math, os, statistics as stats, sys

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DEF = os.path.join(os.path.dirname(HERE), "data")
RULES = "score-v1"
TOUCH, HELD, BROKE, BROKE_N, WIN, MFE_WIN, ZONE_PCT = 2.0, 10.0, 5.0, 3, 60, 15, 0.01
LOCK_PTS = 5.0
OPEN = 9 * 60 + 30


def hm(m):
    return "%d:%02d" % (m // 60, m % 60)


def tmin(s):
    h, m = str(s)[:5].split(":")
    return int(h) * 60 + int(m)


def load_prices(data, day):
    """{minute: price} for SPX from flow_YYYYMMDD.ndjson (last record per minute wins)."""
    p = os.path.join(data, "flow_%s.ndjson" % day.replace("-", ""))
    if not os.path.exists(p):
        return None
    out = {}
    with open(p, encoding="utf-8") as fh:
        for line in fh:
            try:
                j = json.loads(line)
            except Exception:
                continue
            if j.get("s") != "SPX" or j.get("x") is None or not j.get("t"):
                continue
            out[tmin(j["t"])] = float(j["x"])
    return out or None


def session_close_of(exp_t):
    try:
        return tmin(str(exp_t)[11:16])
    except Exception:
        return 16 * 60


def close_price(px, close_min):
    ks = [m for m in px if m <= close_min]
    return px[max(ks)] if ks else None


def path(px, a, b):
    """[(minute, price)] for a < minute <= b, sorted."""
    return [(m, px[m]) for m in sorted(px) if a < m <= b]


def score_zone(px, t, spot, lo, hi, close):
    """S2 for one zone read at minute t. Returns dict with 'status' in started_inside/untouched/held/broke/unresolved."""
    if lo - TOUCH <= spot <= hi + TOUCH:
        return {"status": "started_inside"}
    side = "below" if spot < lo else "above"
    touch = None
    for m, x in path(px, t, min(t + WIN, close)):
        if lo - TOUCH <= x <= hi + TOUCH:
            touch = (m, x)
            break
    if not touch:
        return {"status": "untouched", "side": side}
    m0, p0 = touch
    run, status, at = 0, "unresolved", None
    for m, x in path(px, m0, min(m0 + WIN, close)):
        if side == "below":
            if x <= lo - HELD:
                status, at = "held", m
                break
            run = run + 1 if x >= hi + BROKE else 0
        else:
            if x >= hi + HELD:
                status, at = "held", m
                break
            run = run + 1 if x <= lo - BROKE else 0
        if run >= BROKE_N:
            status, at = "broke", m
            break
    w = path(px, m0, min(m0 + MFE_WIN, close))
    sgn = -1 if side == "below" else 1          # favourable = back out the approach side
    fav = [sgn * (x - p0) for _m, x in w]
    return {"status": status, "side": side, "touch_min": m0, "touch_px": p0, "event_min": at,
            "mfe15": max(fav) if fav else None, "mae15": -min(fav) if fav else None, "short_window": len(w) < MFE_WIN - 1}


def plain_levels(spot, open_px, prior):
    lv = []
    k = math.ceil(spot * (1 - ZONE_PCT) / 25.0) * 25
    while k <= spot * (1 + ZONE_PCT):
        lv.append(("round25", float(k)))
        k += 25
    if open_px is not None:
        lv.append(("open", open_px))
    if prior:
        lv += [("prior_high", prior[0]), ("prior_low", prior[1])]
    return lv


def prior_session(data, day):
    files = sorted(glob.glob(os.path.join(data, "flow_*.ndjson")))
    days = [os.path.basename(f)[5:13] for f in files]
    prev = [d for d in days if d < day.replace("-", "")]
    for d in reversed(prev):
        px = load_prices(data, "%s-%s-%s" % (d[:4], d[4:6], d[6:]))
        if px:
            v = [x for m, x in px.items() if OPEN <= m <= 16 * 60]
            if v:
                return (max(v), min(v)), d
    return None, None


def s3_window(px, t, close, im30):
    if im30 is None or not im30 > 0 or t + 30 > close:
        return None
    w = [(m, x) for m, x in sorted(px.items()) if t <= m <= t + 30]
    if len(w) < 25:
        return None
    xs = [x for _m, x in w]
    q = (max(xs) - min(xs)) / (2.0 * im30)
    r = [b - a for a, b in zip(xs, xs[1:])]
    ac = None
    if len(r) > 3:
        a, b = r[:-1], r[1:]
        ma, mb = sum(a) / len(a), sum(b) / len(b)
        va = sum((x - ma) ** 2 for x in a)
        vb = sum((x - mb) ** 2 for x in b)
        if va > 0 and vb > 0:
            ac = sum((x - ma) * (y - mb) for x, y in zip(a, b)) / math.sqrt(va * vb)
    return {"q": q, "ac1": ac}


def lock_stats(seq):
    """seq = [(slot_label, error_or_None)] in time order. First within LOCK_PTS, and lock = earliest slot after which all are."""
    first = next((s for s, e in seq if e is not None and e <= LOCK_PTS), None)
    lock = None
    for i in range(len(seq) - 1, -1, -1):
        e = seq[i][1]
        if e is None or e > LOCK_PTS:
            break
        lock = seq[i][0]
    return first, lock


def med(v):
    v = [x for x in v if x is not None]
    return stats.median(v) if v else None


def mean(v):
    v = [x for x in v if x is not None]
    return sum(v) / len(v) if v else None


def expected_slots(close):
    return [m for m in range(OPEN + 15, min(close, 16 * 60) - 15 + 1, 15)]


def load_readouts(outdir):
    recs, dups = {}, []
    for f in sorted(glob.glob(os.path.join(outdir, "readouts_*.jsonl"))):
        with open(f, encoding="utf-8") as fh:
            for line in fh:
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                if r.get("v") != RULES or not r.get("date") or not r.get("slot"):
                    continue
                key = (r["date"], r["slot"])
                if key in recs:
                    dups.append({"date": r["date"], "slot": r["slot"], "server_ts": r.get("server_ts")})
                    continue
                recs[key] = r
    return recs, dups


def score_all(data, outdir):
    recs, dups = load_readouts(outdir)
    by_day = {}
    for (d, s), r in recs.items():
        by_day.setdefault(d, []).append(r)
    px_cache = {}

    def px_of(day):
        if day not in px_cache:
            px_cache[day] = load_prices(data, day)
        return px_cache[day]

    days_out, board = {}, {}
    f5 = {}   # (horizon, cfg, target) -> list of (date, t, slot, pred, spot, straddle)
    for day in sorted(by_day):
        rs = sorted(by_day[day], key=lambda r: r["t"])
        px = px_of(day)
        close_min = rs[0].get("session_close") or 16 * 60
        exp = [hm(m) for m in expected_slots(close_min)]
        present = [r["slot"] for r in rs]
        complete = all(s in present for s in exp)
        day_rec = {"date": day, "slots_expected": len(exp), "slots_present": len(present),
                   "slots_absent": [s for s in exp if s not in present], "complete": complete, "configs": {}}
        cfgs = sorted({k for r in rs for k in (r.get("cfgs") or {})})
        # S5 collection (needs no prices today)
        for r in rs:
            for cfg in cfgs:
                c = (r.get("cfgs") or {}).get(cfg) or {}
                for h in ("d1", "fri"):
                    f = c.get(h)
                    if not f or not f.get("e"):
                        continue
                    dte = (datetime.date.fromisoformat(f["e"]) - datetime.date.fromisoformat(day)).days
                    st_h = (r.get("term_straddle") or {}).get(str(dte))
                    f5.setdefault((h, cfg, f["e"], f.get("exp_t")), []).append(
                        (day, r["t"], r["slot"], f.get("pred"), r.get("spot"), st_h))
        if not px:
            day_rec["status"] = "no minute log yet"
            days_out[day] = day_rec
            continue
        close = close_price(px, close_min)
        day_rec["close"] = close
        open_px = next((px[m] for m in sorted(px) if m >= OPEN), None)
        prior, prior_day = prior_session(data, day)
        day_rec["prior_session"] = prior_day
        # plain levels (baseline for S2) and spot baseline for S4
        plain = {"held": 0, "broke": 0, "unresolved": 0, "untouched": 0, "inside": 0, "mfe15": [], "mae15": []}
        spot_seq = []
        for r in rs:
            for _kind, L in plain_levels(r["spot"], open_px, prior):
                z = score_zone(px, r["t"], r["spot"], L, L, close_min)
                k = {"started_inside": "inside"}.get(z["status"], z["status"])
                plain[k] += 1
                if z.get("mfe15") is not None:
                    plain["mfe15"].append(z["mfe15"])
                    plain["mae15"].append(z["mae15"])
            spot_seq.append((r["slot"], abs(r["spot"] - close) if close is not None else None))
        sf, sl = lock_stats(spot_seq)
        day_rec["plain_levels"] = {**{k: v for k, v in plain.items() if not isinstance(v, list)},
                                   "hold_rate": (plain["held"] / (plain["held"] + plain["broke"])) if plain["held"] + plain["broke"] else None,
                                   "mfe15_mean": mean(plain["mfe15"]), "mae15_mean": mean(plain["mae15"])}
        day_rec["spot_baseline"] = {"first": sf, "lock": sl}
        for cfg in cfgs:
            widths = {"zero": [], "peak": [], "trough": []}
            s2 = {"held": 0, "broke": 0, "unresolved": 0, "untouched": 0, "inside": 0, "mfe15": [], "mae15": []}
            s3 = {"long": [], "short": [], "ac_long": [], "ac_short": [], "hit": 0, "n": 0, "incomplete": 0, "missing": 0}
            seq, at = [], {}
            for r in rs:
                c = (r.get("cfgs") or {}).get(cfg)
                if not c or c.get("err"):
                    seq.append((r["slot"], None))
                    continue
                zones = [("zero", z["lo"], z["hi"]) for z in c.get("zeros") or []] + \
                        [("peak", z["lo"], z["hi"]) for z in c.get("peaks") or []] + \
                        [("trough", z["lo"], z["hi"]) for z in c.get("troughs") or []]
                for kind, lo, hi in zones:
                    widths[kind].append(hi - lo)
                    z = score_zone(px, r["t"], r["spot"], lo, hi, close_min)
                    k = {"started_inside": "inside"}.get(z["status"], z["status"])
                    s2[k] += 1
                    if z.get("mfe15") is not None:
                        s2["mfe15"].append(z["mfe15"])
                        s2["mae15"].append(z["mae15"])
                g = c.get("g_spot")
                w = s3_window(px, r["t"], close_min, r.get("im30"))
                if g is None or g == 0:
                    s3["missing"] += 1
                elif w is None:
                    s3["incomplete"] += 1
                else:
                    s3["n"] += 1
                    if g > 0:
                        s3["long"].append(w["q"]); s3["ac_long"].append(w["ac1"]); s3["hit"] += w["q"] < 1
                    else:
                        s3["short"].append(w["q"]); s3["ac_short"].append(w["ac1"]); s3["hit"] += w["q"] > 1
                pred = (c.get("close") or {}).get("pred")
                err = abs(pred - close) if (pred is not None and close is not None) else None
                seq.append((r["slot"], err))
                if r["slot"] in ("9:45", "10:30", "12:00", "14:00", "15:45"):   # report-only checkpoints
                    at[r["slot"]] = err
            first, lock = lock_stats(seq)
            allw = widths["zero"] + widths["peak"] + widths["trough"]
            day_rec["configs"][cfg] = {
                "S1": {"n": len(allw), "median_width": med(allw), **{"median_" + k: med(v) for k, v in widths.items()}},
                "S2": {**{k: v for k, v in s2.items() if not isinstance(v, list)},
                       "hold_rate": (s2["held"] / (s2["held"] + s2["broke"])) if s2["held"] + s2["broke"] else None,
                       "mfe15_mean": mean(s2["mfe15"]), "mae15_mean": mean(s2["mae15"])},
                "S3": {"n": s3["n"], "hit": s3["hit"], "incomplete": s3["incomplete"], "missing": s3["missing"],
                       "median_q_long": med(s3["long"]), "median_q_short": med(s3["short"]),
                       "median_ac1_long": med(s3["ac_long"]), "median_ac1_short": med(s3["ac_short"]),
                       "_q_long": s3["long"], "_q_short": s3["short"]},
                "S4": {"first": first, "lock": lock, "err_at": at, "slots": len(seq),
                       "misses": sum(1 for _s, e in seq if e is None), "vs_spot": _vs(lock, sl)}}
        day_rec["status"] = "scored"
        days_out[day] = day_rec
    # S5: group by target
    s5 = {}
    for (h, cfg, target, exp_t), rows in sorted(f5.items()):
        rows.sort(key=lambda x: (x[0], x[1]))
        tpx = px_of(target)
        key = "%s|%s|%s" % (h, cfg, target)
        if not tpx:
            s5[key] = {"horizon": h, "config": cfg, "target": target, "status": "pending", "n": len(rows)}
            continue
        cl = close_price(tpx, session_close_of(exp_t))
        seq = [("%s %s" % (d, s), abs(p - cl) if p is not None else None) for d, _t, s, p, _sp, _st in rows]
        spot_seq = [("%s %s" % (d, s), abs(sp - cl) if sp is not None else None) for d, _t, s, _p, sp, _st in rows]
        first, lock = lock_stats(seq)
        sf, sl = lock_stats(spot_seq)
        rel = [abs(p - cl) / st_ for _d, _t, _s, p, _sp, st_ in rows if p is not None and st_]
        s5[key] = {"horizon": h, "config": cfg, "target": target, "status": "scored", "close": cl, "n": len(rows),
                   "misses": sum(1 for _s, e in seq if e is None), "first": first, "lock": lock,
                   "mean_abs_err": mean([e for _s, e in seq]), "mean_err_over_straddle": mean(rel),
                   "spot_first": sf, "spot_lock": sl, "vs_spot": _vs_lbl(lock, sl, seq)}
    # scoreboard over complete, scored days
    for day, dr in days_out.items():
        if dr.get("status") != "scored":
            continue
        for cfg, c in dr["configs"].items():
            b = board.setdefault(cfg, {"days": 0, "complete_days": 0, "S1_widths": [], "S2": {"held": 0, "broke": 0, "unresolved": 0, "touches": 0},
                                       "S2_mfe": [], "S3_hit": 0, "S3_n": 0, "S3_ql": [], "S3_qs": [], "S4_locks": [], "S4_locked": 0, "S4_vs": {"win": 0, "tie": 0, "loss": 0}})
            b["days"] += 1
            if c["S1"]["median_width"] is not None:
                b["S1_widths"].append(c["S1"]["median_width"])
            for k in ("held", "broke", "unresolved"):
                b["S2"][k] += c["S2"][k]
            b["S2"]["touches"] += c["S2"]["held"] + c["S2"]["broke"] + c["S2"]["unresolved"]
            if c["S2"]["mfe15_mean"] is not None:
                b["S2_mfe"].append(c["S2"]["mfe15_mean"])
            b["S3_hit"] += c["S3"]["hit"]; b["S3_n"] += c["S3"]["n"]
            b["S3_ql"] += c["S3"].pop("_q_long"); b["S3_qs"] += c["S3"].pop("_q_short")
            if dr["complete"]:
                b["complete_days"] += 1
                b["S4_locks"].append(tmin(c["S4"]["lock"]) if c["S4"]["lock"] else 16 * 60)
                b["S4_locked"] += c["S4"]["lock"] is not None
                b["S4_vs"][c["S4"]["vs_spot"]] += 1
    out = {"rules": RULES, "generated": datetime.datetime.now().isoformat(timespec="seconds"), "configs": {}}
    for cfg, b in sorted(board.items()):
        hr = b["S2"]["held"] / (b["S2"]["held"] + b["S2"]["broke"]) if b["S2"]["held"] + b["S2"]["broke"] else None
        out["configs"][cfg] = {
            "days": b["days"], "complete_days": b["complete_days"],
            "S1_median_width": med(b["S1_widths"]),
            "S2_hold_rate": hr, "S2_touches": b["S2"]["touches"], "S2_unresolved": b["S2"]["unresolved"], "S2_mfe15_mean": mean(b["S2_mfe"]),
            "S3_hit_rate": b["S3_hit"] / b["S3_n"] if b["S3_n"] else None, "S3_n": b["S3_n"],
            "S3_median_q_long": med(b["S3_ql"]), "S3_median_q_short": med(b["S3_qs"]),
            "S4_median_lock": hm(int(med(b["S4_locks"]))) if b["S4_locks"] else None,
            "S4_share_locked_by_1545": b["S4_locked"] / b["complete_days"] if b["complete_days"] else None,
            "S4_vs_spot": b["S4_vs"],
            "S5": {h: _s5_board(s5, cfg, h) for h in ("d1", "fri")}}
    for d in days_out.values():
        for c in d.get("configs", {}).values():
            c["S3"].pop("_q_long", None); c["S3"].pop("_q_short", None)
    out["plain_levels_hold_rate"] = _plain_board(days_out)
    out["duplicates_ignored"] = dups
    return days_out, s5, out


def _vs(lock, spot_lock):
    a = tmin(lock) if lock else 10 ** 6
    b = tmin(spot_lock) if spot_lock else 10 ** 6
    return "win" if a < b else ("tie" if a == b else "loss")


def _vs_lbl(lock, spot_lock, seq):
    order = {s: i for i, (s, _e) in enumerate(seq)}
    a = order.get(lock, 10 ** 6)
    b = order.get(spot_lock, 10 ** 6)
    return "win" if a < b else ("tie" if a == b else "loss")


def _s5_board(s5, cfg, h):
    rows = [v for v in s5.values() if v["config"] == cfg and v["horizon"] == h]
    sc = [v for v in rows if v["status"] == "scored"]
    return {"scored": len(sc), "pending": len(rows) - len(sc), "locked": sum(1 for v in sc if v["lock"]),
            "mean_abs_err": mean([v["mean_abs_err"] for v in sc]), "mean_err_over_straddle": mean([v["mean_err_over_straddle"] for v in sc]),
            "vs_spot": {k: sum(1 for v in sc if v["vs_spot"] == k) for k in ("win", "tie", "loss")}}


def _plain_board(days_out):
    h = b = 0
    for d in days_out.values():
        p = d.get("plain_levels")
        if p:
            h += p["held"]; b += p["broke"]
    return h / (h + b) if h + b else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=DATA_DEF)
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    outdir = a.out or os.path.join(a.data, "test")
    days, s5, board = score_all(a.data, outdir)
    sdir = os.path.join(outdir, "scores")
    os.makedirs(sdir, exist_ok=True)
    for day, rec in days.items():
        with open(os.path.join(sdir, "scores_%s.json" % day.replace("-", "")), "w") as fh:
            json.dump(rec, fh, indent=1)
    with open(os.path.join(sdir, "s5_forecasts.json"), "w") as fh:
        json.dump(s5, fh, indent=1)
    with open(os.path.join(sdir, "scoreboard.json"), "w") as fh:
        json.dump(board, fh, indent=1)
    print("scored %d day(s); %d S5 groups (%d pending) -> %s" % (
        len(days), len(s5), sum(1 for v in s5.values() if v["status"] == "pending"), sdir))
    return 0


if __name__ == "__main__":
    sys.exit(main())
