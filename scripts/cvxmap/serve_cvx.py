"""Serve the Convex cockpit + its data over the LAN.

Same idea as C:\\tos-dashboard\\serve.py but rooted at this folder's data/ so the
cockpit's relative fetches (live.json, ti.json, flow_*.ndjson) resolve. Runs on
the NAS so the cockpit is reachable from any browser on the LAN, phone included:

    http://192.168.1.211:8899/cvx_cockpit.html

2026-11-14 (O3, code audit A2 + A5): every answer carries an ETag (file mtime in ns + size) and
"Cache-Control: no-cache", so the page's polls ask "changed?" and get a 304 with no body when the file
is the same (live.json changes ~once a minute but was pulled every 3 s). The server speaks HTTP/1.1:
Chrome only sends If-None-Match on an HTTP/1.1 answer (on 1.0 it re-downloads every time), and it
also keeps the connection open between polls. Idle connections are dropped after 60 s. Text files (.html .json
.ndjson .js .css .txt .csv .svg, >= 1 KB) go out gzipped when the browser accepts it (page 911 KB ->
~294 KB, live.json 607 -> ~186 KB). The page still never sees stale data: no-cache = always revalidate.
"""
import gzip, http.server, os, shutil, socketserver, sys, threading

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8899
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
os.makedirs(ROOT, exist_ok=True)
# the page itself lives one level up (it is version-controlled; data/ is not)
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

GZ_EXT = (".html", ".json", ".ndjson", ".js", ".css", ".txt", ".csv", ".svg")
GZ_MIN, GZ_MAX, GZ_KEEP = 1024, 64 << 20, 24
_gz_cache, _gz_lock = {}, threading.Lock()   # path -> (etag, gzipped bytes): one compression per file version


def _gzipped(path, etag, data):
    with _gz_lock:
        hit = _gz_cache.get(path)
        if hit and hit[0] == etag:
            return hit[1]
    gz = gzip.compress(data, 5)
    with _gz_lock:
        if len(_gz_cache) >= GZ_KEEP and path not in _gz_cache:
            _gz_cache.pop(next(iter(_gz_cache)))
        _gz_cache[path] = (etag, gz)
    return gz


class Handler(http.server.SimpleHTTPRequestHandler):
    protocol_version = "HTTP/1.1"   # 1.0 answers are never revalidated by Chrome (measured 2026-11-14)
    timeout = 60                    # drop idle keep-alive connections

    def end_headers(self):
        self.send_header("Cache-Control", "no-cache")
        super().end_headers()

    def log_message(self, fmt, *args):
        pass

    def do_GET(self):
        self._serve(True)

    def do_HEAD(self):
        self._serve(False)

    def _serve(self, body):
        path = self.translate_path(self.path)
        if not os.path.isfile(path):                      # folders, missing files: the stock handler (listing / 404)
            return super().do_GET() if body else super().do_HEAD()
        try:
            fh = open(path, "rb")
        except OSError:
            self.send_error(404, "File not found")
            return
        with fh:
            fs = os.fstat(fh.fileno())                    # the version actually opened (writers replace files atomically)
            tag = '"%x-%x"' % (fs.st_mtime_ns, fs.st_size)
            gz_ok = (path.lower().endswith(GZ_EXT) and GZ_MIN <= fs.st_size <= GZ_MAX
                     and "gzip" in self.headers.get("Accept-Encoding", ""))
            etag = tag[:-1] + '-gz"' if gz_ok else tag
            inm = self.headers.get("If-None-Match", "")
            if inm and etag in [t.strip() for t in inm.split(",")]:
                self.send_response(304)
                self.send_header("ETag", etag)
                if gz_ok:
                    self.send_header("Vary", "Accept-Encoding")
                self.end_headers()
                return
            data = fh.read()
        if gz_ok:
            data = _gzipped(path, etag, data)
        self.send_response(200)
        self.send_header("Content-Type", self.guess_type(path))
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Last-Modified", self.date_time_string(fs.st_mtime))
        self.send_header("ETag", etag)
        if gz_ok:
            self.send_header("Content-Encoding", "gzip")
            self.send_header("Vary", "Accept-Encoding")
        self.end_headers()
        if body:
            try:
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError):
                pass


class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def handle_error(self, request, client_address):
        # a browser closing a kept-open connection is normal, not an error worth a traceback
        if isinstance(sys.exc_info()[1], (ConnectionResetError, BrokenPipeError, TimeoutError)):
            return
        super().handle_error(request, client_address)


if __name__ == "__main__":
    print("Convex cockpit  http://0.0.0.0:%d/cvx_cockpit.html  (VIX = tab, key X)" % PORT, flush=True)
    print("serving %s" % ROOT, flush=True)
    Server(("", PORT), Handler).serve_forever()
