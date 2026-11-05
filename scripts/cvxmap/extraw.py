"""Shared helpers for the Straddle (S1) and Flow Constellation (S2) cockpit feeds.

Added 2026-11-04. No Convex calls here: these helpers only READ files the collector
already writes. Nothing in this module is used by the Map.

- read_new(path, offset): incremental reader for an append-only .ndjson.gz file that
  the collector writes one gzip member per record (gzip.open(..., "at")). Returns the
  records after `offset` and the new offset. A half-written last member is left for
  the next call instead of raising.
- expiry helpers: third Friday, this week's last listed expiry, next monthly.
"""
import datetime
import json
import os
import zlib


def read_new(path, offset=0):
    if not os.path.exists(path):
        return [], offset
    with open(path, "rb") as fh:
        fh.seek(offset)
        data = fh.read()
    recs, used = [], 0
    while data:
        d = zlib.decompressobj(wbits=31)
        try:
            out = d.decompress(data)
        except zlib.error:
            break
        if not d.eof:                      # partial member at EOF: try again next time
            break
        consumed = len(data) - len(d.unused_data)
        for line in out.decode("utf-8", "replace").splitlines():
            line = line.strip()
            if line:
                try:
                    recs.append(json.loads(line))
                except Exception:
                    pass
        used += consumed
        data = d.unused_data
    return recs, offset + used


def third_friday(y, m):
    d = datetime.date(y, m, 15)
    return d + datetime.timedelta(days=(4 - d.weekday()) % 7)


def next_monthly(today):
    tf = third_friday(today.year, today.month)
    if tf >= today:
        return tf
    y, m = (today.year + 1, 1) if today.month == 12 else (today.year, today.month + 1)
    return third_friday(y, m)


def prev_monthly(today):
    """The monthly expiry before the current cycle's (the one that started this cycle)."""
    nm = next_monthly(today)
    y, m = (nm.year - 1, 12) if nm.month == 1 else (nm.year, nm.month - 1)
    return third_friday(y, m)


def week_bounds(today):
    mon = today - datetime.timedelta(days=today.weekday())
    return mon, mon + datetime.timedelta(days=4)


def is_monthly(sym_code, exp_iso):
    """sym_code like '.SPXW261120C7300' / '.VIX261118C20' / '.SPY261120C700'.
    VIX: the non-'W' root is the monthly. Others: the 3rd-Friday date is the monthly."""
    root = ""
    s = (sym_code or "").lstrip(".")
    for ch in s:
        if ch.isdigit():
            break
        root += ch
    try:
        d = datetime.date.fromisoformat(exp_iso)
    except Exception:
        return False
    if root.startswith("VIX"):
        return root == "VIX"
    return d == third_friday(d.year, d.month)


def atomic_write(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, separators=(",", ":"))
    os.replace(tmp, path)
