"""
Fill every hole in the Convex flow series for one symbol.

Reconstructs the WHOLE session from CVForge bars, then merges so that
LIVE lines always win and backfilled lines only fill minutes that are
missing. That fills the collector outage and the writer gap automatically,
with no hand-set cutoff.

    python backfill_merge.py SPX
"""
import json, os, sys, glob, gzip
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cvx_live as L
import cvx_backfill as B

def main():
    sym = (sys.argv[1] if len(sys.argv) > 1 else "SPX").upper()
    day = L.now_et().strftime("%Y%m%d")
    fp = os.path.join("data", "flow_%s.ndjson" % day)
    now = L.now_et().strftime("%H:%M")

    sys.argv = [sys.argv[0], "--sym", sym]
    got, strikes, spot, today, _s, step = B.main()
    rows = B.reconstruct(got, strikes, today, sym, step, now)
    print("%s reconstructed %d minutes (09:30 -> %s)" % (sym, len(rows), now))

    live, back_other = {}, []
    if os.path.exists(fp):
        for line in open(fp, encoding="utf-8"):
            try:
                j = json.loads(line)
            except Exception:
                continue
            if j.get("s") != sym:
                back_other.append(j)          # other symbols, untouched
            elif j.get("_bf"):
                continue                      # drop this symbol's old backfill
            else:
                live[j["t"]] = j              # live lines win

    added = [r for r in rows if r["t"] not in live]
    out = back_other + list(live.values()) + added
    out.sort(key=lambda j: (j.get("t", ""), j.get("s", "")))
    tmp = fp + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        for j in out:
            fh.write(json.dumps(j, separators=(",", ":")) + "\n")
    os.replace(tmp, fp)

    mine = sorted(j["t"] for j in out if j.get("s") == sym)
    print("  %s: live=%d  backfilled=%d  total=%d  (%s -> %s)"
          % (sym, len(live), len(added), len(mine), mine[0], mine[-1]))
    # report any remaining holes in the RTH minute grid
    want = ["%02d:%02d" % (h, m) for h in range(9, 17) for m in range(60)
            if "09:30" <= "%02d:%02d" % (h, m) <= min(now, "16:15")]
    holes = [t for t in want if t not in set(mine)]
    print("  remaining holes: %d%s" % (len(holes), (" e.g. " + ", ".join(holes[:8])) if holes else ""))

    # seed the session open from the first reconstructed minute
    sp = os.path.join("data", "live_state_%s.json" % day)
    if rows and os.path.exists(sp):
        st = json.load(open(sp, encoding="utf-8"))
        st.setdefault("open", {})[sym] = rows[0]["x"]
        json.dump(st, open(sp, "w", encoding="utf-8"))
        print("  seeded %s open = %.2f (from %s)" % (sym, rows[0]["x"], rows[0]["t"]))

if __name__ == "__main__":
    main()
