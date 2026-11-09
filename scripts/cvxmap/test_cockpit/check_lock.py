"""Production lock check (2026-11-08). Exit 0 if every locked production file still matches LOCK.json, 1 otherwise.
Hashes are taken with CRLF -> LF, so a Windows checkout and the NAS tarball give the same answer.
  python scripts/cvxmap/test_cockpit/check_lock.py            check
  python scripts/cvxmap/test_cockpit/check_lock.py --write    (re)write LOCK.json from the current files - only on Mithil's OK"""
import hashlib, json, os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
LOCK = os.path.join(HERE, "LOCK.json")
FILES = ["scripts/cvxmap/cvx_cockpit.html", "scripts/cvxmap/cvx_live.py", "scripts/cvxmap/am_feed.py",
         "scripts/cvxmap/market_cal.py", "scripts/cvxmap/serve_cvx.py", "scripts/cvxmap/cvx_collect.py",
         "scripts/nas/run_cvxmap.sh"]


def nhash(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read().replace(b"\r\n", b"\n")).hexdigest()


def main():
    if "--write" in sys.argv:
        obj = {"locked": "2026-11-08", "commit": "824a06c", "note": "production MAP files; sha256 after CRLF->LF",
               "files": {f: nhash(os.path.join(REPO, f)) for f in FILES}}
        json.dump(obj, open(LOCK, "w"), indent=1)
        print("wrote", LOCK)
        return 0
    lock = json.load(open(LOCK))["files"]
    bad = []
    for f, h in lock.items():
        p = os.path.join(REPO, f)
        got = nhash(p) if os.path.exists(p) else "MISSING"
        if got != h:
            bad.append(f)
        print(("OK   " if got == h else "DIFF ") + f + "  " + got[:16])
    print("PRODUCTION LOCK OK" if not bad else "PRODUCTION LOCK BROKEN: " + ", ".join(bad))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
