"""Incremental reader for the collector's raw_YYYYMMDD.ndjson.gz (2026-11-14, O2 / code audit B1).

stdlib only (the cockpit scripts stay importable on their own). The collector appends ONE gzip
member per record (gzip.open(path, "at") + one json line + "\\n"), so a reader can remember the byte
offset of the last complete member and decompress only what came after it.

    lines, off, line0 = read_lines(path, off, line0)

returns the complete lines after byte ``off`` as (line_no, text) pairs - line numbers continue from
``line0``, exactly as ``enumerate(gzip.open(path, "rt"))`` would number them - plus the new offset
and line number to pass next time. Only whole members whose text ends in "\\n" move the offset; a
half-written member (or a line the collector has not finished) is left for the next call, where
``gzip.open`` would raise EOFError. From offset 0 the lines are the same lines gzip gives
(a CRLF line end reads as LF, as in gzip "rt" - the NAS collector writes LF; a file written by
Windows text mode has CRLF, fixed 2026-11-14 after the o2 ship tests failed on the laptop).
Same idea as extraw.read_new (Straddle / Constellation feeds) and wide_feed.RawReader (Orbs).
"""
import os
import zlib

CHUNK = 1 << 20


def read_lines(path, offset=0, line0=0):
    if not os.path.exists(path):
        return [], offset, line0
    out = []
    good_off, good_line = offset, line0
    with open(path, "rb") as fh:
        fh.seek(offset)
        dz = zlib.decompressobj(31)
        buf = bytearray()   # text decompressed since the last clean boundary
        pend = 0            # compressed bytes read since the last clean boundary
        while True:
            data = fh.read(CHUNK)
            if not data:
                break
            while data:
                try:
                    buf += dz.decompress(data)
                except zlib.error:
                    return out, good_off, good_line    # corrupt tail: keep what was good
                if not dz.eof:
                    pend += len(data)
                    break
                rest = dz.unused_data
                pend += len(data) - len(rest)
                if not buf or buf.endswith(b"\n"):     # member ends on a line end: a clean boundary
                    for ln in bytes(buf).split(b"\n")[:-1]:
                        if ln.endswith(b"\r"):              # CRLF line end -> same text gzip "rt" gives
                            ln = ln[:-1]
                        out.append((good_line, ln.decode("utf-8", "replace")))
                        good_line += 1
                    good_off += pend
                    pend = 0
                    buf = bytearray()
                dz = zlib.decompressobj(31)
                data = rest
    return out, good_off, good_line
