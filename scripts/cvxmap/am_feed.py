#!/usr/bin/env python
"""am_feed.py - Doc's morning plan + trading-intel AM context for the TOS cockpit.

Writes ti.json next to the dashboard every minute. The cockpit draws Doc's levels on
the flow map and grades his IF/THEN road map live against SPX.

  doc : Doc McGraw's SPX report, parsed WITHOUT an LLM (the TradingView strings, the
        rails, the road map IF/THEN lines and the cheat card are regular text).
        Source order: Gmail (your trading-intel gmail.readonly token) -> a pasted
        C:\\tos-dashboard\\doc_today.txt -> the NAS ResearchNote '__DOC__' (that copy is
        cut at 8,000 characters, so it usually lacks the road map).
  ti  : trading-intel's morning book (08:35 index chain): the fused market read,
        the all-expiry dealer-gamma profile by spot + flip, and the 0-7 DTE walls.
        Descriptive only (CLAUDE.md rule 4). Reads the DB; no vendor calls.

Run it next to the dashboard (start_dashboard.bat does this):
    python C:\\tos-dashboard\\am_feed.py          (loops; Ctrl+C to stop)
    python C:\\tos-dashboard\\am_feed.py --once   (one pass, prints a summary)
Never prints anything from .env (rule 2): errors are reported by type only.
"""
from __future__ import annotations

import os as _os
from pathlib import Path as _P
_TI_DEFAULT = r"C:\\Users\\drmit\\PycharmProjects\\trading-intel"
_DASH_DEFAULT = r"C:\\tos-dashboard"
_TI  = _os.environ.get("TI_DIR")  or _TI_DEFAULT
_DASH= _os.environ.get("DASH_DIR") or _DASH_DEFAULT
import json
import math
import os
import re
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

TI = _TI
DASH = _P(_DASH)
OUT = DASH / "ti.json"
PASTE = DASH / "doc_today.txt"
DOC_SENDER = "docmcgraw@substack.com"
LOOP_S = 60

# ===================================================================== Doc parser (pure)
_NUM = r"\d{1,2},\d{3}(?:\.\d+)?|\d{4,5}(?:\.\d+)?"
_PAIR = re.compile(rf"({_NUM})\s*(?:/|to|↔|<>|–|-)\s*({_NUM})")
_ZONE = re.compile(rf"({_NUM})(?:\s*[–-]\s*({_NUM}))?")


def _v(s: str) -> float:
    x = float(s.replace(",", ""))
    return int(x) if x == int(x) else x


def _ok(x: float, lo: float = 1000, hi: float = 30000) -> bool:
    return lo <= x <= hi


def _norm(text: str) -> str:
    t = text.replace("\r", "")
    for a, b in (("’", "'"), ("‘", "'"), ("“", '"'), ("”", '"'), ("—", " — "), ("\u00a0", " "), ("−", "-")):
        t = t.replace(a, b)
    return t


def zones(s: str) -> list[list[float]]:
    """'7,765–7,770, then 7,800' -> [[7765, 7770], [7800, 7800]] (SPX-sized numbers only)."""
    out = []
    for m in _ZONE.finditer(s):
        a = _v(m.group(1))
        b = _v(m.group(2)) if m.group(2) else a
        if _ok(a) and _ok(b) and abs(a - b) < 400:
            out.append([min(a, b), max(a, b)])
    return out


def _first(s: str):
    z = zones(s)
    return z[0][0] if z else None


def role_of(label: str) -> str:
    """Doc's vocabulary -> how the level behaves (colour on the map)."""
    l = label.lower()
    if re.search(r"pivot|control", l):
        return "piv"
    if re.search(r"neg|trigger|gate|-gex", l):
        return "trig"
    if re.search(r"pos|\+gex|catch|stall|brake|sticky|lid|ceiling|cluster|support|floor|magnet", l):
        return "pos"
    if re.search(r"str|σ|\bem\b|straddle|sigma", l):
        return "rail"
    return "chart"


def _tv_line(rest: str) -> list[dict]:
    out, lab = [], []
    for tok in [t.strip() for t in rest.split(",")]:
        if re.fullmatch(r"\d+(?:\.\d+)?", tok):
            label = " ".join(lab).strip() or "level"
            out.append({"v": _v(tok), "label": label})
            lab = []
        elif tok:
            lab.append(tok)
    return out


def _section(lines: list[str], start_pat: str, end_pats: list[str]) -> list[str]:
    s = next((i for i, l in enumerate(lines) if re.search(start_pat, l)), None)
    if s is None:
        return []
    out = []
    for l in lines[s + 1:]:
        if any(re.search(p, l) for p in end_pats):
            break
        out.append(l)
    return out


def _sentences(line: str) -> list[str]:
    return [x.strip() for x in re.split(r"(?<=[.!?])\s+(?=[A-Z])", line) if x.strip()]


def parse_scenarios(rm: list[str], pivot) -> tuple[list[dict], list[str], dict]:
    """Road-map lines -> scenarios. Kinds: cross (a 5-min close through the level),
    reject (a touch then a close back), fail/reclaim (only after an earlier
    conversion at that level), lean (which side of the pivot sets the bias)."""
    sc, ctx, hdr = [], [], {}
    sect = None
    last_lean_L = None
    for raw in rm:
        line = re.sub(r"^[\u2022*\-]\s*", "", raw.strip())   # 2026-10-28 format: bulleted IF lines
        if not line:
            continue
        m = re.match(rf"^(?:THE\s+)?PIVOT\s*:\s*({_NUM})", line, re.I)
        if m:
            hdr["pivot"] = _v(m.group(1))
            continue
        m = re.match(r"^CHOP ZONE\b[^:]*:\s*(.+)$", line, re.I)   # also "CHOP ZONE (THE BOX): ..."
        if m:
            p = _PAIR.search(m.group(1))
            if p:
                hdr["chop"] = sorted([_v(p.group(1)), _v(p.group(2))])
            continue
        if re.match(rf"^(ABOVE|BELOW)\b(?:\s+THE\s+[A-Z ]+|\s+(?:{_NUM}))?(?:\s*\(.*\))?\s*$", line):
            sect = "up" if line.upper().startswith("ABOVE") else "down"
            continue
        if re.match(r"^(SCALPER|LEVELS:|UP:|DOWN:|TOUCH IS)", line, re.I):
            break
        for s in _sentences(line):
            item = None
            m = re.match(r"^IF\s+(?P<c>.+?),?\s+THEN\s+(?P<t>.+)$", s, re.I)
            if m:
                c, t = m.group("c"), m.group("t")
                L = _first(c)
                if L is None:
                    ctx.append(s)
                    continue
                cl = c.lower()
                rule = "5m" if re.search(r"5[- ]?min", cl) else None
                piv = hdr.get("pivot", pivot)
                side = sect or ("up" if piv is not None and L >= piv else "down")
                if re.search(r"push (?:into|to|through)|tag of|test of|reject", cl):
                    kind, d = "reject", ("down" if side == "up" else "up")
                elif re.search(r"\bholds?\b", cl) and side == "down":
                    kind, d = "reject", "up"
                elif re.search(r"\bfails?\b", cl):
                    kind, d = ("reject", "down") if side == "up" else ("cross", "down")
                elif re.search(r"is lost|\blos(?:t|es|ing)\b|breaks? down|gives way|cracks", cl):
                    kind, d = "cross", "down"
                elif re.search(r"convert|clear|break|reclaim|taken out|accept", cl):
                    kind = "cross"
                    d = "down" if re.search(r"lower|down|below", cl) else ("up" if re.search(r"higher|above", cl) else side)
                else:
                    kind, d = "cross", side
                tz = zones(t) or ([[piv, piv]] if re.search(r"pivot", t, re.I) and piv else [])
                item = {"kind": kind, "dir": d, "level": L, "targets": tz, "rule": rule, "cond": c, "then": t}
            if item is None:
                m = re.match(r"^A\s+(failure|reclaim)\b(?P<c>.+?)\bsends\s+(?:price|it)\s+(?:back\s+)?(?:to|toward|towards|into)\s+(?P<t>.+)$", s, re.I)
                if m:
                    L = _first(m.group("c"))
                    if L is not None:
                        t = m.group("t")
                        piv = hdr.get("pivot", pivot)
                        tz = zones(t) or ([[piv, piv]] if re.search(r"pivot", t, re.I) and piv else [])
                        kind = m.group(1).lower()
                        item = {"kind": "fail" if kind == "failure" else "reclaim", "dir": "down" if kind == "failure" else "up",
                                "level": L, "targets": tz, "rule": None, "cond": m.group(0).split(" sends ")[0], "then": t}
            if item is None:
                m = re.match(r"^(?P<p>.+?)\s+leans?\s+toward\s+(?P<t>.+)$", s, re.I)
                if m:
                    p = m.group("p").lower()
                    L = _first(m.group("p"))
                    if L is None and re.search(r"\bit\b", p):
                        L = last_lean_L
                    if L is not None:
                        d = "down" if re.search(r"under|below|los|lose|lost|fail|staying under", p) else "up"
                        last_lean_L = L
                        item = {"kind": "lean", "dir": d, "level": L, "targets": zones(m.group("t")), "rule": None,
                                "cond": m.group("p"), "then": m.group("t")}
            if item is not None:
                item["text"] = s
                item["sect"] = sect
                item["id"] = f"s{len(sc) + 1}"
                sc.append(item)
            elif len(ctx) < 4 and len(s) > 25:
                ctx.append(s)
    return sc, ctx, hdr


def parse_doc(text: str, subject: str = "") -> dict:
    t = _norm(text)
    lines = [l.strip() for l in t.split("\n")]
    out: dict = {"title": None, "levels": {}, "scalper": {}, "weekly": {}, "premium": {}, "rails": [],
                 "pivot": None, "chop": None, "working": None, "em": None, "scenarios": [], "context": [],
                 "up_path": [], "down_path": [], "one_thing": None, "conv_rule": "5m", "warnings": []}
    title = subject or next((l for l in lines if re.search(r"MARKET REPORT|DAILY PLAN", l, re.I) and re.search(r"\d{1,2}/\d{1,2}", l)), "") \
        or next((l for l in lines if re.search(r"MARKET REPORT|DAILY PLAN", l, re.I)), "")
    out["title"] = re.sub(r"^[^\w$]+", "", title).strip() or None
    md = re.search(r"\b(\d{1,2})/(\d{1,2})\b", out["title"] or "")
    out["md"] = [int(md.group(1)), int(md.group(2))] if md else None

    # --- TradingView strings: the cleanest level list Doc publishes
    tv = _section(lines, r"(?i)TRADINGVIEW STRINGS", [r"^QUANTUITION", r"^Unsubscribe", r"CHEAT CARD", r"^— Doc"])
    grp = "MAIN"
    for l in tv:
        if re.fullmatch(r"[0-9A-Z ]{3,20}", l):
            grp = "MAIN" if "MAIN" in l else l.strip()   # 2026-10-28 format: "0DTE MAIN"
            continue
        m = re.match(r"^\$([A-Z0-9!]+)\s*:\s*(.+)$", l)
        if not m:
            continue
        sym = {"ES1!": "ES", "SPX": "SPX", "SPY": "SPY", "QQQ": "QQQ", "NDX": "NDX"}.get(m.group(1), m.group(1))
        items = _tv_line(m.group(2))
        if grp == "MAIN":
            for it in items:
                it["role"] = role_of(it["label"])
            out["levels"][sym] = items
        elif grp == "SCALPER":
            for it in items:
                w = it["label"].split()
                it["action"] = w[-1].lower() if len(w) > 1 else ""
                it["label"] = " ".join(w[:-1]) if len(w) > 1 else it["label"]
            out["scalper"][sym] = items
        elif grp == "WEEKLY":
            out["weekly"][sym] = items
        elif grp == "PREMIUM":
            out["premium"][sym] = items

    # --- structure board as a fallback level list (the NAS copy is cut before the TV strings)
    if not out["levels"].get("SPX"):
        sb = _section(lines, r"STRUCTURE BOARD", [r"ROAD MAP", r"CHEAT CARD", r"PREMIUM SELLERS"])
        lv = []
        for l in sb:
            m = re.match(rf"^(?:•\s*)?({_NUM})(?:\s*[–-]\s*({_NUM}))?\s*:\s*(.+)$", l)
            if not m:
                continue
            a = _v(m.group(1))
            b = _v(m.group(2)) if m.group(2) else None
            desc = m.group(3)
            if re.match(r"light|small|nothing", desc, re.I):
                continue
            lab = re.split(r"[.;]", desc)[0][:40]
            tail = re.findall(r"\bThe ([a-z0-9 -]{3,24})\.", desc)
            if tail:
                lab = tail[-1]
            lv.append({"v": a if b is None else round((a + b) / 2), "v2": b, "lo": a, "label": lab, "role": role_of(desc)})
        if lv:
            out["levels"]["SPX"] = lv
            out["warnings"].append("levels from the structure board (no TradingView strings in this copy)")

    # --- rails (expected-move bands)
    seen = set()
    for l in lines:
        m = re.match(r"^(?:[•\-\*📏📐]\s*)*(?P<n>STR 1\.5|STR|1σ|½σ(?: study)?|OVN EM|Weekly EM|Weekly straddle|Straddle|1\.5x|0DTE EM)\b(?P<q>\s*\([^)]*\))?\s*:\s*(?P<r>.+)$", l)
        if not m:
            continue
        p = _PAIR.search(m.group("r"))
        if not p:
            continue
        n = m.group("n").replace(" study", "")
        if n == "0DTE EM":
            n = "STR"
        q = (m.group("q") or "").strip()
        q = "(6 AM)" if "6 AM" in q else "(close)" if re.search(r"close", q, re.I) else ""
        key = n + q
        if key in seen or n in seen and not q:
            continue
        seen.add(key)
        seen.add(n)
        lo, hi = sorted([_v(p.group(1)), _v(p.group(2))])
        if _ok(lo) and _ok(hi):
            out["rails"].append({"name": n + (" " + q if q else ""), "lo": lo, "hi": hi})

    # --- expected range / pivot / working area / paths / the one thing
    for l in lines:
        if out["em"] is None and re.search(r"Expected range (?:today|from)", l, re.I):
            p = _PAIR.search(l)
            if p:
                out["em"] = sorted([_v(p.group(1)), _v(p.group(2))])
        m = re.search(r"WORKING AREA\s*:\s*(.+)$", l)
        if m:
            p = _PAIR.search(m.group(1))
            if p:
                out["working"] = sorted([_v(p.group(1)), _v(p.group(2))])
            pv = re.search(rf"pivot\s+({_NUM})", m.group(1), re.I)
            if pv and out["pivot"] is None:
                out["pivot"] = _v(pv.group(1))
        m = re.search(r"(?:UP PATH|^\W*UP)\s*:\s*(.+)$", l)
        if m:
            out["up_path"] = zones(m.group(1))
        m = re.search(r"(?:DOWN PATH|^\W*DOWN)\s*:\s*(.+)$", l)
        if m:
            out["down_path"] = zones(m.group(1))
        m = re.search(r"THE ONE THING\s*:\s*(.+)$", l)
        if m:
            out["one_thing"] = m.group(1).strip()

    # --- road map
    rm = _section(lines, r"ROAD MAP|^\W*THE TRADE\s*$", [r"SCALPER MAP", r"PREMIUM SELLERS", r"BOTTOM LINE", r"CHEAT CARD", r"TRADINGVIEW", r"WHY I'M NOT SELLING"])
    sc, ctx, hdr = parse_scenarios(rm, out["pivot"])
    if hdr.get("pivot") is not None:
        out["pivot"] = hdr["pivot"]
    if out["pivot"] is None:
        for l in lines:
            m = re.match(r"^THE PIVOT\s*:\s*(.+)$", l)
            if m and zones(m.group(1)):
                out["pivot"] = zones(m.group(1))[-1][1]
                break
    if out["pivot"] is None:
        piv = [x["v"] for x in out["levels"].get("SPX", []) if x.get("role") == "piv"]
        out["pivot"] = piv[0] if piv else None
    out["chop"] = hdr.get("chop")
    # 'the pivot' targets parsed before the header was known
    for s in sc:
        if not s["targets"] and re.search(r"pivot", s["then"], re.I) and out["pivot"]:
            s["targets"] = [[out["pivot"], out["pivot"]]]
    out["scenarios"], out["context"] = sc, ctx
    if not rm:
        out["warnings"].append("no road map in this copy")
    elif not sc:
        out["warnings"].append("road map found but no IF/THEN lines parsed")
    if re.search(r"5[- ]minute close", t, re.I) or not sc:
        out["conv_rule"] = "5m"
    return out


# ===================================================================== sources (need trading-intel)
def _et_now() -> datetime:
    """Eastern time, naive. trading-intel's helper first (Windows has no tz database
    unless the tzdata package is installed), then zoneinfo, then the laptop clock."""
    try:
        from trading_intel.timeutils import eastern_now
        n = eastern_now()
        return n.replace(tzinfo=None) if getattr(n, "tzinfo", None) else n
    except Exception:
        pass
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("America/New_York")).replace(tzinfo=None)
    except Exception:
        return datetime.now()


def _to_et(epoch_s: float) -> datetime:
    try:
        from zoneinfo import ZoneInfo
        return datetime.fromtimestamp(epoch_s, ZoneInfo("America/New_York")).replace(tzinfo=None)
    except Exception:
        return datetime.fromtimestamp(epoch_s)      # the laptop runs on Eastern time


def _doc_date(parsed: dict, received: datetime | None) -> str | None:
    base = received or _et_now()
    if parsed.get("md"):
        mth, day = parsed["md"]
        try:
            d = date(base.year, mth, day)
            if d > base.date() + timedelta(days=7):
                d = date(base.year - 1, mth, day)
            return d.isoformat()
        except ValueError:
            pass
    return received.date().isoformat() if received else None


def doc_from_gmail(settings) -> tuple[dict | None, str | None]:
    try:
        from trading_intel.letters.gmail_source import _header, _service, _walk_body_and_attachments
    except Exception as exc:
        return None, f"gmail import: {type(exc).__name__}"
    try:
        svc = _service(settings)
        if svc is None:
            return None, "gmail: token or google libraries not available"
        res = svc.users().messages().list(userId="me", q=f"from:{DOC_SENDER} newer_than:3d",
                                          maxResults=5, includeSpamTrash=True).execute()
        best = None
        for ref in res.get("messages", [])[:5]:          # newest first: stop at the first daily report
            msg = svc.users().messages().get(userId="me", id=ref["id"], format="full").execute()
            payload = msg.get("payload", {})
            subj = _header(payload, "Subject")
            if not re.search(r"MARKET REPORT|DAILY PLAN", subj, re.I):
                continue
            body, _ = _walk_body_and_attachments(payload)
            best = (int(msg.get("internalDate") or 0), subj, body)
            break
        if not best:
            return None, "gmail: no Doc report in the last 3 days"
        ms, subj, body = best
        rec = _to_et(ms / 1000)
        return {"text": body, "subject": subj, "received": rec, "source": "gmail"}, None
    except Exception as exc:
        return None, f"gmail: {type(exc).__name__}"


def doc_from_paste() -> tuple[dict | None, str | None]:
    try:
        if not PASTE.is_file():
            return None, None
        mt = datetime.fromtimestamp(PASTE.stat().st_mtime)
        return {"text": PASTE.read_text(encoding="utf-8", errors="ignore"), "subject": "", "received": mt, "source": "doc_today.txt"}, None
    except Exception as exc:
        return None, f"paste: {type(exc).__name__}"


def doc_from_db(factory) -> tuple[dict | None, str | None]:
    try:
        from sqlalchemy import select
        from trading_intel.memory.models import ResearchNote
        with factory() as s:
            row = s.execute(select(ResearchNote.as_of, ResearchNote.note_md)
                            .where(ResearchNote.symbol == "__DOC__")
                            .order_by(ResearchNote.as_of.desc()).limit(1)).first()
        if not row:
            return None, "db: no __DOC__ note"
        as_of, body = row
        rec = datetime.combine(as_of, datetime.min.time()) if isinstance(as_of, date) else None
        return {"text": body or "", "subject": "", "received": rec, "source": "nas note (8k cut)"}, None
    except Exception as exc:
        return None, f"db doc: {type(exc).__name__}"


def best_doc(settings, factory, use_gmail=True) -> tuple[dict | None, list[str]]:
    """Gmail first; the pasted file and the NAS note only when Gmail has no usable copy for today."""
    errs, cands = [], []
    today = _et_now().date().isoformat()

    def take(got):
        p = parse_doc(got["text"], got.get("subject") or "")
        p["date"] = _doc_date(p, got.get("received"))
        p["source"] = got["source"]
        p["received"] = got["received"].isoformat(timespec="minutes") if got.get("received") else None
        cands.append(p)
        return p

    sources = ([lambda: doc_from_gmail(settings)] if use_gmail else []) + [doc_from_paste, lambda: doc_from_db(factory)]
    for fn in sources:
        got, err = fn()
        if err:
            errs.append(err)
        if got and got.get("text"):
            p = take(got)
            if p.get("date") == today and p["scenarios"]:
                break
    if not cands:
        return None, errs
    # today's copy first, then the one with the most road map, then the newest
    cands.sort(key=lambda p: (p.get("date") == today, len(p["scenarios"]) > 0, len(p["levels"].get("SPX", [])) > 0, p.get("date") or ""), reverse=True)
    return cands[0], errs


def _clean(o):
    if isinstance(o, float):
        return o if math.isfinite(o) else None
    if isinstance(o, dict):
        return {str(k): _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    if isinstance(o, (date, datetime)):
        return o.isoformat()
    return o


def live_spx() -> float | None:
    """SPX from the workbook's live.json when it is fresh, so the gamma profile centres on
    where price is now (the vendor's map is live the same way: AM OI, live spot/time)."""
    try:
        j = json.loads((DASH / "live.json").read_text(encoding="utf-8"))
        ts = datetime.fromisoformat(str(j.get("ts"))[:19])
        if abs((_et_now() - ts).total_seconds()) > 600:
            return None
        v = float((j.get("SPX") or {}).get("spot") or 0)
        return v if v > 0 else None
    except Exception:
        return None


def ti_context(factory) -> tuple[dict, list[str]]:
    out, errs = {}, []
    spot_ref = live_spx()

    def run(name, fn):
        try:
            with factory() as s:
                out[name] = fn(s)
        except Exception as exc:
            errs.append(f"{name}: {type(exc).__name__}")

    def mr(s):
        from trading_intel.api.market_read import build_market_read
        r = build_market_read(s, symbol="SPX") or {}
        keep = {k: r.get(k) for k in ("regime", "mechanics", "weather", "path", "triggers", "confluence", "narrative", "as_of")}
        if isinstance(keep.get("regime"), dict):
            keep["regime"] = {k: keep["regime"].get(k) for k in ("state", "bull_bear_line", "breadth_health", "clock_session", "clock_open")}
        return keep

    def prof(s):
        from trading_intel.mcp.profile_tool import get_profile
        p = get_profile(s, "SPX", span=0.03, n_points=61, greeks=("gamma",), spot_ref=spot_ref) or {}
        g = ((p.get("profiles") or {}).get("gamma") or {})
        be = g.get("by_expiry") or {}
        near = {k: be[k] for k in sorted(be)[:2]}
        return {"as_of": p.get("as_of"), "spot": p.get("spot"), "grid": (p.get("profiles") or {}).get("spot_ref"),
                "all": g.get("all"), "near": near, "flip": g.get("flip"), "found": p.get("found"),
                "spot_live": spot_ref is not None}

    def walls(s):
        from trading_intel.mcp.extra_tools import get_walls
        w = get_walls(s, "SPX", dte_max=7) or {}
        return {k: w.get(k) for k in ("as_of", "spot", "call_wall", "put_wall", "call_top_strikes", "put_top_strikes", "found")}

    def walls0(s):   # today's expiry only (needs the 2026-10-21 index_walls_am fix on the NAS)
        from trading_intel.mcp.extra_tools import get_walls
        w = get_walls(s, "SPX", dte_max=0) or {}
        return {k: w.get(k) for k in ("as_of", "call_wall", "put_wall", "call_top_strikes", "put_top_strikes", "found")}

    def straddle(s):
        from trading_intel.mcp.extra_tools import get_straddle
        r = get_straddle(s, "SPX", dte_max=3) or {}
        return {k: r.get(k) for k in ("as_of", "straddle", "atm_strike", "dte", "lower", "upper", "atm_iv", "found")}

    def leans(s):    # overnight OI change x IV vs ATM -> who opened what (SPX/SPY/QQQ 8:35 books)
        return {sym: oi_lean(s, sym) for sym in ("SPX", "SPY", "QQQ")}

    run("market_read", mr)
    run("profile", prof)
    run("walls", walls)
    run("walls_0dte", walls0)
    run("straddle", straddle)
    run("oi_lean", leans)
    return out, errs


# ===================================================================== overnight OI change -> lean
_DEAD_IV = 0.10   # vol points: smaller IV moves vs ATM are "unclear"


def lean_from_rows(cur: list[dict], prev: dict, spot: float | None) -> dict:
    """Overnight positioning from two 8:35 books (pure; rows are plain dicts).

    cur rows: {expiry, strike, cp, dte, oi_change, oi, iv, delta}; prev: {(expiry, strike, cp): iv}.
    OI change says opened vs closed; that strike's IV change MINUS its expiry's ATM IV
    change says who pushed (IV up = buyers, down = sellers). Customer delta added =
    |dOI| x delta x sign(dIV vs ATM) x 100. Dealers hold the other side. Descriptive.
    """
    atm_c, atm_p = {}, {}
    for r in cur:   # ATM per expiry = call with delta nearest 0.5, today and in the prior book
        if r["cp"] != "C" or r.get("delta") is None or not r.get("iv"):
            continue
        d = abs(r["delta"] - 0.5)
        if r["expiry"] not in atm_c or d < atm_c[r["expiry"]][0]:
            atm_c[r["expiry"]] = (d, r["iv"], r["strike"])
    for e, (_, ivc, k) in atm_c.items():
        ivp = prev.get((e, k, "C"))
        if ivp:
            atm_p[e] = ivp
    agg = {"calls_bought": 0, "calls_sold": 0, "puts_bought": 0, "puts_sold": 0, "unclear": 0,
           "cust_delta": 0.0, "gross_delta": 0.0}
    rows = []
    for r in cur:
        doi = r.get("oi_change")
        if not doi:
            continue
        ivp = prev.get((r["expiry"], r["strike"], r["cp"]))
        e = r["expiry"]
        rel = None
        if r.get("iv") and ivp and e in atm_c and e in atm_p:
            rel = ((r["iv"] - ivp) - (atm_c[e][1] - atm_p[e])) * 100.0
        if rel is None or abs(rel) < _DEAD_IV:
            agg["unclear"] += abs(doi)
            label = "opened" if doi > 0 else "closed"
        else:
            pushed = "bought" if rel > 0 else "sold"
            label = ("opened, " if doi > 0 else "closed, ") + pushed
            key = ("calls_" if r["cp"] == "C" else "puts_") + pushed
            agg[key] += abs(doi)
            if r.get("delta") is not None:
                cd = abs(doi) * r["delta"] * (1 if rel > 0 else -1) * 100.0
                agg["cust_delta"] += cd
                agg["gross_delta"] += abs(cd)
        rows.append({"expiry": e, "strike": r["strike"], "cp": r["cp"], "dte": r.get("dte"), "oi_change": doi,
                     "oi": r.get("oi"), "d_iv_rel": None if rel is None else round(rel, 2), "label": label})
    rows.sort(key=lambda x: abs(x["oi_change"]), reverse=True)
    g = agg["gross_delta"]
    share = agg["cust_delta"] / g if g else 0.0
    if not g:
        lean = "no read"
    elif share >= 0.15:
        lean = "bullish lean"
    elif share <= -0.15:
        lean = "bearish lean"
    else:
        lean = "mixed / hedged"
    agg["share"] = round(share, 3)
    agg["cust_delta_usd"] = agg["cust_delta"] * spot if spot else None
    agg["lean"] = lean
    return {"agg": agg, "top": rows[:10]}


def oi_lean(session, sym: str) -> dict | None:
    """Lean for one index root from its two newest 8:35 books: today's 0DTE and 0-7 DTE."""
    from sqlalchemy import select
    from trading_intel.memory.models import GreeksSnapshot, OiChainEod
    ts = session.execute(select(OiChainEod.ts).where(OiChainEod.symbol == sym).distinct()
                         .order_by(OiChainEod.ts.desc()).limit(2)).scalars().all()
    if len(ts) < 2:
        return None
    tc, tp = ts[0], ts[1]
    cols = (OiChainEod.expiry, OiChainEod.strike, OiChainEod.cp, OiChainEod.dte, OiChainEod.oi,
            OiChainEod.oi_change, OiChainEod.iv, OiChainEod.delta)
    cur = [dict(zip(("expiry", "strike", "cp", "dte", "oi", "oi_change", "iv", "delta"), r))
           for r in session.execute(select(*cols).where(OiChainEod.symbol == sym, OiChainEod.ts == tc,
                                                        OiChainEod.dte >= 0, OiChainEod.dte <= 7)).all()]
    for r in cur:
        r["cp"] = "C" if str(r["cp"]).upper().startswith("C") else "P"
    prev = {(e, k, "C" if str(cp).upper().startswith("C") else "P"): iv
            for e, k, cp, iv in session.execute(select(OiChainEod.expiry, OiChainEod.strike, OiChainEod.cp, OiChainEod.iv)
                                                .where(OiChainEod.symbol == sym, OiChainEod.ts == tp,
                                                       OiChainEod.dte <= 8)).all()}
    spot = session.execute(select(GreeksSnapshot.spot).where(GreeksSnapshot.symbol == sym, GreeksSnapshot.spot.isnot(None))
                           .order_by(GreeksSnapshot.ts.desc()).limit(1)).scalar_one_or_none()
    spot = float(spot) if spot else None
    d0 = [r for r in cur if r["dte"] == 0]
    return {"as_of": tc, "prev_as_of": tp, "spot": spot,
            "d0": lean_from_rows(d0, prev, spot) if d0 else None,
            "w": lean_from_rows(cur, prev, spot)}


# ===================================================================== flow v2: index tape (NAS tas_prints, DB read only) + 8:35 OI verdict
_ROOT_GROUP = {"SPX": "SPX", "SPXW": "SPX", "SPY": "SPY", "QQQ": "QQQ"}
_TAPE_MIN_CARD = 1_000_000.0      # yesterday's prints worth an OI verdict


def _t_et(ts) -> str:
    try:
        if getattr(ts, "tzinfo", None) is not None:
            from zoneinfo import ZoneInfo
            ts = ts.astimezone(ZoneInfo("America/New_York"))
        return ts.strftime("%H:%M")
    except Exception:
        return ""


def _print_rows(session, day) -> list[dict]:
    from sqlalchemy import select
    from trading_intel.memory.models import TasPrint
    q = (select(TasPrint.ts, TasPrint.root, TasPrint.expiry, TasPrint.strike, TasPrint.cp, TasPrint.side,
                TasPrint.price, TasPrint.size, TasPrint.notional, TasPrint.delta, TasPrint.is_sweep,
                TasPrint.is_block, TasPrint.is_financing, TasPrint.leg_group)
         .where(TasPrint.source == "convex_index", TasPrint.trade_date == day,
                TasPrint.root.in_(list(_ROOT_GROUP)))
         .order_by(TasPrint.notional.desc()).limit(2000))
    out = []
    for r in session.execute(q).all():
        ts, root, exp, k, cp, side, px, sz, notional, delta, sw, bl, fin, leg = r
        out.append({"g": _ROOT_GROUP.get(root, root), "root": root, "expiry": exp.isoformat() if exp else None,
                    "strike": k, "cp": (cp or "").upper()[:1], "side": (side or "unknown").lower(),
                    "price": px, "size": sz, "notional": notional, "delta": delta, "t": _t_et(ts),
                    "sweep": bool(sw), "block": bool(bl), "financing": bool(fin), "leg": leg})
    return out


def tape_context(session, day) -> dict:
    """Today's stored index prints grouped by root, plus bullish/bearish/unsigned notional per expiry.

    bull = call bought + put sold, bear = call sold + put bought (Convex's aggressor side), mid/unknown = unsigned.
    Financing legs (deep-ITM / box) are excluded from the expiry mood. Zero Convex calls: this is the NAS table
    tas_capture already fills every minute. Descriptive only.
    """
    rows = _print_rows(session, day)
    out = {}
    for r in rows:
        g = out.setdefault(r["g"], {"prints": [], "by_expiry": {}, "n": 0})
        g["n"] += 1
        if len(g["prints"]) < 300:
            g["prints"].append({k: r[k] for k in ("expiry", "strike", "cp", "side", "size", "notional", "t", "sweep", "block", "financing", "delta")})
        if r["financing"] or not r["expiry"]:
            continue
        e = g["by_expiry"].setdefault(r["expiry"], {"bull": 0.0, "bear": 0.0, "mid": 0.0, "n": 0})
        e["n"] += 1
        v = float(r["notional"] or 0)
        if r["side"] == "buy":
            e["bull" if r["cp"] == "C" else "bear"] += v
        elif r["side"] == "sell":
            e["bear" if r["cp"] == "C" else "bull"] += v
        else:
            e["mid"] += v
    return out


def near_book(session, sym, dte_max=7) -> dict | None:
    """The newest 8:35 book for one root, dte 1..dte_max: strikes with OI / IV / delta per side.

    Feeds the cockpit's vanna and charm maps (vanna lives in the weeklies and the monthly, not in the 0DTE
    chain — vega ∝ √τ). Sign convention downstream: calls +, puts − (naive OI). ~300-600 rows per root.
    """
    from sqlalchemy import select
    from trading_intel.memory.models import OiChainEod
    ts = session.execute(select(OiChainEod.ts).where(OiChainEod.symbol == sym).distinct()
                         .order_by(OiChainEod.ts.desc()).limit(1)).scalar_one_or_none()
    if ts is None:
        return None
    rows = session.execute(select(OiChainEod.expiry, OiChainEod.dte, OiChainEod.strike, OiChainEod.cp, OiChainEod.oi,
                                  OiChainEod.iv, OiChainEod.delta)
                           .where(OiChainEod.symbol == sym, OiChainEod.ts == ts, OiChainEod.dte >= 1,
                                  OiChainEod.dte <= dte_max, OiChainEod.oi > 0)).all()
    by = {}
    for e, dte, k, cp, oi, iv, d in rows:
        key = (e.isoformat(), float(k))
        r = by.setdefault(key, {"e": e.isoformat(), "dte": int(dte), "k": float(k), "cOI": 0, "pOI": 0, "cIV": None, "pIV": None})
        if str(cp).upper().startswith("C"):
            r["cOI"] = int(oi or 0); r["cIV"] = float(iv) if iv else None
        else:
            r["pOI"] = int(oi or 0); r["pIV"] = float(iv) if iv else None
    return {"as_of": ts, "rows": sorted(by.values(), key=lambda r: (r["e"], r["k"]))}


def _book_pair(session, sym):
    """The two newest 8:35 books for one root: (cur rows dte 0-7, prev iv map, as_of pair)."""
    from sqlalchemy import select
    from trading_intel.memory.models import OiChainEod
    ts = session.execute(select(OiChainEod.ts).where(OiChainEod.symbol == sym).distinct()
                         .order_by(OiChainEod.ts.desc()).limit(2)).scalars().all()
    if len(ts) < 2:
        return None, None, None
    tc, tp = ts[0], ts[1]
    cols = (OiChainEod.expiry, OiChainEod.strike, OiChainEod.cp, OiChainEod.dte, OiChainEod.oi,
            OiChainEod.oi_change, OiChainEod.iv, OiChainEod.delta)
    cur = [dict(zip(("expiry", "strike", "cp", "dte", "oi", "oi_change", "iv", "delta"), r))
           for r in session.execute(select(*cols).where(OiChainEod.symbol == sym, OiChainEod.ts == tc,
                                                        OiChainEod.dte >= 0, OiChainEod.dte <= 45)).all()]
    for r in cur:
        r["cp"] = "C" if str(r["cp"]).upper().startswith("C") else "P"
    prev = {(e, k, "C" if str(cp).upper().startswith("C") else "P"): iv
            for e, k, cp, iv in session.execute(select(OiChainEod.expiry, OiChainEod.strike, OiChainEod.cp, OiChainEod.iv)
                                                .where(OiChainEod.symbol == sym, OiChainEod.ts == tp,
                                                       OiChainEod.dte <= 46)).all()}
    return cur, prev, (tc, tp)


def oi_verdicts(session, sym, yday) -> dict | None:
    """Stamp yesterday's big index prints (>= $1M) with what the overnight OI change says they did.

    opened = OI added >= half the print, closed/rolled = OI added <= a fifth (or fell), mixed between.
    bought/sold from the strike's IV change minus its expiry's ATM IV change (same rule as oi_lean).
    """
    cur, prev, asof = _book_pair(session, sym)
    if cur is None:
        return None
    atm_c, atm_p = {}, {}
    for r in cur:
        if r["cp"] != "C" or r.get("delta") is None or not r.get("iv"):
            continue
        d = abs(r["delta"] - 0.5)
        if r["expiry"] not in atm_c or d < atm_c[r["expiry"]][0]:
            atm_c[r["expiry"]] = (d, r["iv"], r["strike"])
    for e, (_, ivc, k) in atm_c.items():
        ivp = prev.get((e, k, "C"))
        if ivp:
            atm_p[e] = ivp
    by = {(r["expiry"].isoformat() if r["expiry"] else None, float(r["strike"]), r["cp"]): r for r in cur}
    roots = [k for k, v in _ROOT_GROUP.items() if v == sym]
    prints = [p for p in _print_rows(session, yday) if p["root"] in roots and (p["notional"] or 0) >= _TAPE_MIN_CARD and not p["financing"]]
    cards = []
    for p in prints[:12]:
        r = by.get((p["expiry"], float(p["strike"] or 0), p["cp"]))
        if not r:
            cards.append({**p, "doi": None, "d_iv_rel": None, "verdict": "no book"})
            continue
        doi = r.get("oi_change") or 0
        sz = max(1, int(p["size"] or 0))
        e = r["expiry"]
        rel = None
        ivp = prev.get((e, r["strike"], r["cp"]))
        if r.get("iv") and ivp and e in atm_c and e in atm_p:
            rel = ((r["iv"] - ivp) - (atm_c[e][1] - atm_p[e])) * 100.0
        if doi >= 0.5 * sz:
            v = "opened"
        elif doi <= 0.2 * sz:
            v = "closed / rolled"
        else:
            v = "mixed"
        if rel is not None and abs(rel) >= _DEAD_IV and v != "closed / rolled":
            v += ", " + ("bought" if rel > 0 else "sold")
        cards.append({**{k: p[k] for k in ("expiry", "strike", "cp", "side", "size", "notional", "t", "sweep", "block")},
                      "doi": doi, "oi": r.get("oi"), "d_iv_rel": None if rel is None else round(rel, 2), "verdict": v})
    return {"as_of": asof[0], "prev_as_of": asof[1], "trade_date": yday.isoformat(), "cards": cards}


def write_json(payload: dict) -> None:
    data = json.dumps(_clean(payload), ensure_ascii=False, separators=(",", ":"))
    tmp = OUT.with_suffix(".tmp")
    tmp.write_text(data, encoding="utf-8")
    for _ in range(5):
        try:
            os.replace(tmp, OUT)
            return
        except PermissionError:      # the web server is reading it: try again in a moment
            time.sleep(0.2)
    OUT.write_text(data, encoding="utf-8")


def _quiet_logs() -> None:
    import logging
    logging.basicConfig(level=logging.ERROR)
    try:
        import structlog
        structlog.configure(wrapper_class=structlog.make_filtering_bound_logger(logging.ERROR))
    except Exception:
        pass


def main(argv: list[str]) -> int:
    once = "--once" in argv
    use_gmail = "--no-gmail" not in argv
    sys.path.insert(0, TI)
    os.chdir(TI)                      # so get_settings() finds the .env
    _quiet_logs()
    from trading_intel.config import get_settings
    from trading_intel.memory.db import make_session_factory
    settings = get_settings()
    factory = make_session_factory(settings)

    doc, doc_errs, doc_at = None, [], 0.0
    ti, ti_errs, ti_at = {}, [], 0.0
    tape, tape_errs, tape_at = {}, [], 0.0
    oiv, oiv_errs, oiv_at = {}, [], 0.0
    near = {}
    last_line = ""
    print("am_feed: Doc plan + trading-intel AM context -> " + str(OUT))
    while True:
        now = _et_now()
        today = now.date().isoformat()
        have_today = bool(doc and doc.get("date") == today and doc.get("scenarios"))
        doc_every = 1800 if have_today else 180          # poll every 3 min until today's report lands
        if time.time() - doc_at >= doc_every:
            d, doc_errs = best_doc(settings, factory, use_gmail)
            doc = d or doc
            doc_at = time.time()
        ti_every = 600 if 8 <= now.hour < 16 else 1800      # the profile re-centres on live SPX
        if time.time() - ti_at >= ti_every:
            t, ti_errs = ti_context(factory)
            if t:
                ti.update(t)
            ti_at = time.time()
        rth = (9, 25) <= (now.hour, now.minute) <= (16, 10) and now.weekday() < 5
        tape_every = 120 if rth else 1800
        if time.time() - tape_at >= tape_every:
            tape_errs = []
            try:
                with factory() as s:
                    tape = tape_context(s, now.date())
            except Exception as exc:
                tape_errs = [f"tape: {type(exc).__name__}"]
            tape_at = time.time()
        if time.time() - oiv_at >= 600:
            oiv_errs = []
            yday = now.date() - timedelta(days=1)
            while yday.weekday() >= 5:
                yday -= timedelta(days=1)
            try:
                with factory() as s:
                    oiv = {sym: oi_verdicts(s, sym, yday) for sym in ("SPX", "SPY", "QQQ")}
                    near = {sym: near_book(s, sym) for sym in ("SPX", "SPY", "QQQ")}
            except Exception as exc:
                oiv_errs = [f"oi_verdict: {type(exc).__name__}"]
            oiv_at = time.time()
        payload = {"asof": now.isoformat(timespec="seconds"), "today": today, "doc": doc, "ti": ti,
                   "tape": tape, "oi_verdict": oiv, "near_book": near, "errors": doc_errs + ti_errs + tape_errs + oiv_errs}
        try:
            write_json(payload)
        except Exception as exc:
            print(f"  write failed: {type(exc).__name__}")
        line = (f"doc {doc['date']} via {doc['source']}: {len(doc['levels'].get('SPX', []))} levels, "
                f"{len(doc['scenarios'])} IF/THEN" if doc else "doc: none yet")
        line += f" | ti: {', '.join(sorted(ti)) or 'none'}"
        line += " | tape: " + (", ".join(f"{g} {v['n']}" for g, v in sorted(tape.items())) or "none")
        if doc_errs + ti_errs:
            line += " | " + "; ".join(doc_errs + ti_errs)
        if line != last_line or once:
            print(f"{now:%H:%M} {line}")
            last_line = line
        if once:
            return 0
        time.sleep(LOOP_S)


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except KeyboardInterrupt:
        sys.exit(0)
