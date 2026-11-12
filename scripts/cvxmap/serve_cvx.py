"""Serve the Convex cockpit + its data over the LAN, no-cache.

Same idea as C:\\tos-dashboard\\serve.py but rooted at this folder's data/ so the
cockpit's relative fetches (live.json, ti.json, flow_*.ndjson) resolve. Runs on
the NAS so the cockpit is reachable from any browser on the LAN, phone included:

    http://192.168.1.211:8899/cvx_cockpit.html
"""
import http.server, os, socketserver, sys

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8899
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
os.makedirs(ROOT, exist_ok=True)
# the page itself lives one level up (it is version-controlled; data/ is not)
import shutil
# VIX is a TAB on the cockpit now, not a page of its own.
for _pg in ("cvx_cockpit.html",):
    _src = os.path.join(os.path.dirname(ROOT), _pg)
    if os.path.exists(_src):
        try:
            shutil.copyfile(_src, os.path.join(ROOT, _pg))
        except Exception:
            pass
# 2026-11-12: the Help tab's screenshots live in scripts/cvxmap/help/ (version-controlled, like the page);
# copy them to data/help/ so the page's relative help/*.webp links resolve. Missing folder = no-op.
_HSRC = os.path.join(os.path.dirname(ROOT), "help")
if os.path.isdir(_HSRC):
    try:
        os.makedirs(os.path.join(ROOT, "help"), exist_ok=True)
        for _f in os.listdir(_HSRC):
            if _f.lower().endswith((".webp", ".png", ".jpg")):
                shutil.copyfile(os.path.join(_HSRC, _f), os.path.join(ROOT, "help", _f))
    except Exception:
        pass
os.chdir(ROOT)


class NoCache(http.server.SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Cache-Control", "no-store, max-age=0")
        super().end_headers()

    def log_message(self, fmt, *args):
        pass


class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


print("Convex cockpit  http://0.0.0.0:%d/cvx_cockpit.html  (VIX = tab, key X)" % PORT, flush=True)
print("serving %s" % ROOT, flush=True)
Server(("", PORT), NoCache).serve_forever()
