"""serve_cvx.py (2026-11-14, O3 / code audit A2 + A5): ETag + 304 "changed?" answers and gzip for text files.
Runs a copy of the server on a free port over a temp data folder."""
import gzip
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "scripts" / "cvxmap" / "serve_cvx.py"


def _port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


def _get(url, headers=None, method="GET"):
    req = urllib.request.Request(url, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), b""


def test_etag_304_and_gzip(tmp_path):
    srv = tmp_path / "cvxmap"; (srv / "data").mkdir(parents=True)
    shutil.copy(SRC, srv / "serve_cvx.py")
    body = ('{"ts":"x","rows":[%s]}' % ",".join(str(i) for i in range(3000))).encode()
    (srv / "data" / "live.json").write_bytes(body)
    (srv / "data" / "small.json").write_bytes(b'{"a":1}')
    port = _port()
    p = subprocess.Popen([sys.executable, str(srv / "serve_cvx.py"), str(port)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        base = "http://127.0.0.1:%d/" % port
        for _ in range(50):
            try:
                _get(base + "small.json"); break
            except Exception:
                time.sleep(0.1)
        st, h, b = _get(base + "live.json")                                   # plain
        assert st == 200 and b == body and h["Cache-Control"] == "no-cache" and h.get("ETag") and "Content-Encoding" not in h
        st, h2, b2 = _get(base + "live.json", {"Accept-Encoding": "gzip"})     # gzipped
        assert st == 200 and h2["Content-Encoding"] == "gzip" and gzip.decompress(b2) == body and len(b2) < len(body)
        assert h2["ETag"] != h["ETag"] and "Accept-Encoding" in h2["Vary"]
        st, h3, b3 = _get(base + "live.json?123", {"Accept-Encoding": "gzip", "If-None-Match": h2["ETag"]})
        assert st == 304 and b3 == b""                                        # unchanged -> no body
        st, _, _ = _get(base + "live.json", {"If-None-Match": h["ETag"]})
        assert st == 304
        time.sleep(0.01)
        (srv / "data" / "live.json").write_bytes(body + b" ")                 # the writer replaced the file
        st, h4, b4 = _get(base + "live.json", {"If-None-Match": h["ETag"]})
        assert st == 200 and b4 == body + b" " and h4["ETag"] != h["ETag"]
        st, h5, b5 = _get(base + "small.json", {"Accept-Encoding": "gzip"})  # under 1 KB: not worth gzip
        assert st == 200 and b5 == b'{"a":1}' and "Content-Encoding" not in h5
        st, h6, b6 = _get(base + "live.json", method="HEAD")
        assert st == 200 and b6 == b"" and h6.get("ETag")
        st, _, _ = _get(base + "missing.json")
        assert st == 404
        assert (srv / "data").exists() and os.path.exists(srv / "data" / "live.json")
        # HTTP/1.1 keep-alive: Chrome only revalidates (If-None-Match) 1.1 answers; several asks, one connection
        import http.client
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        c.request("GET", "/live.json"); r = c.getresponse(); r.read()
        assert r.version == 11 and r.status == 200
        et = r.getheader("ETag")
        c.request("GET", "/live.json", headers={"If-None-Match": et}); r = c.getresponse(); r.read()
        assert r.status == 304
        c.request("GET", "/missing.json"); r = c.getresponse(); r.read()
        assert r.status == 404
        c.close()
    finally:
        p.terminate(); p.wait(5)
