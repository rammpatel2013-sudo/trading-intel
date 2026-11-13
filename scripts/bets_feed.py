"""bets_feed.py - created 2026-11-12 for Mithil. Feeds the cockpit's Bets tab: today's biggest
bullish and bearish option bets on single stocks, mega caps next to small + micro caps.

[Mithil 11/12 18:40] "any way to better visualize bullish vs bearish bet" + "see in cockpit around
3:30: mega vs small or micro cap. Show only top 5"; 19:04 "Yes" + the optionsmatrixpro note on
complex quotes (a package's net price sign shows cash flow, not direction).

WHAT IT DOES
  Every 5 minutes (09:35-16:05 ET, Mon-Fri) it reads today's equity tape from tas_prints (the
  market-wide prints >= $25k that tas_capture already banks - no vendor call for the tape) and
  writes scripts/cvxmap/data/bets_live.json (the cockpit polls it) and bets_YYYYMMDD.json (one
  snapshot per run, for a later replay).

HOW A PRINT COUNTS (each leg on its own, then summed per name)
  calls bought + puts sold = BULLISH premium; puts bought + calls sold = BEARISH premium.
  A multi-leg package is judged by its legs, never by the sign of its net price or by one
  leg's tag (a credit put spread is a bullish bet; a negative package price can still be a buy).
  Left out, and counted separately (by reason in totals.fin_by):
    financing   - deep in the money: boxes, conversions, dividend and stock-loan trades, not bets.
                  tagged: is_financing (tas_capture: |delta| >= 0.85, or >= 0.70 and a year+ out);
                  delta: |delta| >= 0.85; itm: in the money with time value under 10% of the price
                  (catches prints with no greeks); iv: IV >= 150% on an in-the-money print (a broken
                  IV fit). A high-IV OTM option (small caps, wings) stays a bet. [11/12 night fix:
                  the first real run counted a DELL Jan28 550 put, ~all intrinsic, as a +$22M bet
                  and dropped every IV >= 150% print, ITM or not.]
    unsigned    - side mid / unknown (the tape cannot say who paid)
    not a stock - index and ETF roots, and names with no market cap
  The "biggest bet" of a name = its largest same-size package (or single print) by |net premium|.
  Times are New York time: tas_prints.ts is naive and on the NAS it is UTC (the 11/12 run showed
  "as of 20:59"), so the clock is detected the way sides_feed.tape_clock does and converted.

MARKET CAPS
  FMP through the existing clients (rule 1): company-screener once a day (stocks only, so ETFs
  drop out), else batch-quote in chunks of 100 for the day's names plus an ETF check (profile)
  for the names about to be shown. Cached in reports/bets/mktcap_cache.json; a name with no cap
  is retried the next day.
  Buckets: mega >= $200B, large $10-200B, mid $2-10B, small $300M-2B, micro < $300M.

RUN (NAS, DSM task "TI bets feed", Mon-Fri 09:31, user root):
    bash /var/services/homes/drmithil/trading-intel/scripts/nas/run_script.sh bets_feed
  One pass now (e.g. after the close for today):   ... run_script.sh bets_feed --once
  A stored day:                                     ... run_script.sh bets_feed --date 2026-11-12

Descriptive only (rule 4): which way the money leaned, not a signal. No AI (rule 7).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import defaultdict
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "cvxmap", "data")
CACHE = os.path.join(os.path.dirname(HERE), "reports", "bets", "mktcap_cache.json")
EVERY_S = 300
START_HM, END_HM = (9, 35), (16, 5)
FIN_DELTA = 0.85
BAD_IV = 1.5
FIN_TV = 0.10    # in the money with time value under 10% of the price = deep ITM
TOP = 5
BUCKETS = (("mega", 200e9), ("large", 10e9), ("mid", 2e9), ("small", 300e6), ("micro", 0.0))
X = "\u00d7"     # the multiplication sign in "x35K"
NON_STOCK = frozenset("""
SPX SPXW XSP NDX NDXP XND RUT RUTW MRUT VIX VIXW DJX OEX XEO SPY QQQ IWM DIA MDY RSP SMH SOXX
XLF XLE XLK XLV XLY XLP XLI XLU XLB XLRE XLC GLD SLV IAU TLT IEF SHY HYG LQD JNK EEM EFA FXI KWEB
MCHI EWZ EWJ EWW ARKK SOXL SOXS TQQQ SQQQ SPXL SPXS SPXU UPRO TNA TZA LABU LABD UVXY VXX VIXY
SVXY UVIX SVIX USO UNG GDX GDXJ XBI IBB KRE KBE XOP OIH XHB ITB XRT JETS BITO IBIT FBTC ETHA ETHE
GBTC MSTU MSTX NVDL NVDU TSLL TSLQ CONL BITX YINN YANG FAS FAZ ERX TECL TMF TBT BOIL KOLD UCO
SCO SDS SSO QLD PSQ SH DOG SDOW UDOW TLTW VOO VTI IVV
""".split())


def leg_dir(side: str | None, cp: str | None) -> int:
    """+1 bullish, -1 bearish, 0 unsigned. Calls bought / puts sold = bullish."""
    if side not in ("buy", "sell") or cp not in ("C", "P"):
        return 0
    return 1 if (cp == "C") == (side == "buy") else -1


def intrinsic(r: dict) -> float | None:
    s, k, cp = r.get("spot"), r.get("strike"), r.get("cp")
    if s is None or k is None or cp not in ("C", "P"):
        return None
    return max(0.0, (s - k) if cp == "C" else (k - s))


def fin_reason(r: dict) -> str | None:
    """Why a print is financing (deep in the money, not a bet): tagged / delta / itm / iv.
    None = a bet."""
    if r.get("is_financing"):
        return "tagged"
    d = r.get("delta")
    if d is not None and abs(d) >= FIN_DELTA:
        return "delta"
    itm, px = intrinsic(r), r.get("price")
    if itm and px and px > 0 and (px - itm) / px < FIN_TV:
        return "itm"
    iv = r.get("iv")
    if iv is not None and iv >= BAD_IV and itm:
        return "iv"
    return None


def tape_clock(rows: list[dict], now: datetime | None = None) -> str:
    """'et' or 'utc' for the naive ts values (the rule of scripts/cvxmap/sides_feed.tape_clock):
    a print after 16:30 -> UTC; one before 13:00 -> ET; else the clock whose last print is nearer
    to now."""
    naive = [r["ts"] for r in rows if r.get("ts") is not None and r["ts"].tzinfo is None]
    if not naive:
        return "et"
    mins = [t.hour * 60 + t.minute for t in naive]
    if max(mins) > 16 * 60 + 30:
        return "utc"
    if min(mins) < 13 * 60 or now is None:
        return "et"
    last = max(naive)
    n_utc = now.replace(tzinfo=ET).astimezone(UTC).replace(tzinfo=None)
    return "utc" if abs(last - n_utc) < abs(last - now) else "et"


def to_et(rows: list[dict], now: datetime | None = None) -> list[dict]:
    """ts -> naive New York time (aware values converted, naive UTC shifted)."""
    clock = tape_clock(rows, now)
    for r in rows:
        t = r.get("ts")
        if t is None:
            continue
        if t.tzinfo is not None:
            r["ts"] = t.astimezone(ET).replace(tzinfo=None)
        elif clock == "utc":
            r["ts"] = t.replace(tzinfo=UTC).astimezone(ET).replace(tzinfo=None)
    return rows


def bucket_of(cap: float | None) -> str | None:
    if not cap or cap <= 0:
        return None
    for name, lo in BUCKETS:
        if cap >= lo:
            return name
    return "micro"


def _exp(e: object) -> str:
    """19Mar27 - day, month, year (the thinkorswim way; 'Mar27' alone reads like a day)."""
    try:
        d = e if isinstance(e, date) else date.fromisoformat(str(e)[:10])
        return d.strftime("%d%b%y")
    except Exception:
        return str(e or "")


def _k(x: object) -> str:
    try:
        f = float(x)
        return str(int(f)) if f == int(f) else f"{f:g}"
    except Exception:
        return str(x)


def _qty(n: int) -> str:
    return f"{n / 1e3:.1f}K".replace(".0K", "K") if n >= 1000 else str(n)


def describe(legs: list[dict]) -> str:
    """'19Mar27 170C buy . 185C sell x35K' - legs by expiry then strike; the expiry once when all
    legs share it (each leg's own on a calendar); the size once when all legs are the same size."""
    legs = sorted(legs, key=lambda r: (str(r.get("expiry")), r.get("strike") or 0))
    one = len({str(r.get("expiry")) for r in legs}) == 1
    parts = [("" if one and i else _exp(r.get("expiry")) + " ")
             + f"{_k(r.get('strike'))}{r.get('cp')} {r.get('side')}" for i, r in enumerate(legs)]
    sizes = {int(r.get("size") or 0) for r in legs}
    if len(sizes) == 1:
        tail = " " + X + _qty(sizes.pop())
    else:
        tail = " " + X + "/".join(_qty(int(r.get("size") or 0)) for r in legs)
    return " · ".join(parts) + tail


def _net(legs: list[dict]) -> float:
    return sum(leg_dir(r.get("side"), r.get("cp")) * float(r.get("notional") or 0) for r in legs)


def _hhmm(ts: object) -> str:
    if hasattr(ts, "strftime"):
        return ts.strftime("%H:%M")
    return str(ts)[11:16] if ts else ""


def classify(rows: list[dict]) -> tuple[dict, dict]:
    """Per-name bullish / bearish premium + the biggest bet; plus the left-out totals."""
    names: dict[str, dict] = defaultdict(lambda: {"bull": 0.0, "bear": 0.0, "n": 0, "groups": {}})
    out = {"fin_n": 0, "fin_prem": 0.0, "uns_n": 0, "uns_prem": 0.0, "nonstock_n": 0,
           "fin_by": {"tagged": 0, "delta": 0, "itm": 0, "iv": 0}}
    for i, r in enumerate(rows):
        root = (r.get("root") or "").upper()
        prem = float(r.get("notional") or 0.0)
        if not root or root in NON_STOCK:
            out["nonstock_n"] += 1
            continue
        why = fin_reason(r)
        if why:
            out["fin_n"] += 1
            out["fin_prem"] += prem
            out["fin_by"][why] += 1
            continue
        d = leg_dir(r.get("side"), r.get("cp"))
        if d == 0:
            out["uns_n"] += 1
            out["uns_prem"] += prem
            continue
        x = names[root]
        x["n"] += 1
        x["bull" if d > 0 else "bear"] += prem
        x["groups"].setdefault(r.get("leg_group") or f"#{i}", []).append(r)
    res = {}
    for root, x in names.items():
        best, best_net = None, 0.0
        for legs in x["groups"].values():
            if len(legs) > 1 and len({int(r.get("size") or 0) for r in legs}) > 1:
                # a same-poll cluster of different sizes: not one package
                cands = [[r] for r in legs]
            else:
                cands = [legs]
            for c in cands:
                net = _net(c)
                if best is None or abs(net) > abs(best_net):
                    best, best_net = c, net
        ts = max((r.get("ts") for r in best or [] if r.get("ts")), default=None)
        res[root] = {"bull": x["bull"], "bear": x["bear"], "net": x["bull"] - x["bear"],
                     "n": x["n"], "big": {"txt": describe(best) if best else "", "net": best_net,
                                          "t": _hhmm(ts)}}
    return res, out


def build(rows: list[dict], caps: dict, day: str, now: datetime) -> dict:
    """The bets_live.json payload."""
    per, out = classify(rows)
    asof = max((r["ts"] for r in rows if r.get("ts")), default=None)
    bk: dict[str, dict] = {b: {"bull": 0.0, "bear": 0.0, "names": 0, "list": []}
                           for b, _ in BUCKETS}
    nocap = 0
    for root, x in per.items():
        b = bucket_of(caps.get(root))
        if b is None:
            nocap += 1
            continue
        one = bk[b]
        one["bull"] += x["bull"]
        one["bear"] += x["bear"]
        one["names"] += 1
        one["list"].append({"s": root, "cap": caps.get(root), "bull": round(x["bull"]),
                            "bear": round(x["bear"]), "net": round(x["net"]), "n": x["n"],
                            "big": {**x["big"], "net": round(x["big"]["net"])}})
    for one in bk.values():
        one["list"].sort(key=lambda r: abs(r["net"]), reverse=True)
        one["top"] = one.pop("list")[:TOP]
        one["bull"], one["bear"] = round(one["bull"]), round(one["bear"])
    small = sorted(bk["small"]["top"] + bk["micro"]["top"], key=lambda r: abs(r["net"]),
                   reverse=True)[:TOP]
    totals = {"names": len(per), "bull": round(sum(x["bull"] for x in per.values())),
              "bear": round(sum(x["bear"] for x in per.values())), "nocap": nocap,
              "fin_n": out["fin_n"], "fin_prem": round(out["fin_prem"]), "fin_by": out["fin_by"],
              "uns_n": out["uns_n"], "uns_prem": round(out["uns_prem"])}
    return {"v": 1, "day": day, "asof": _hhmm(asof), "gen": now.isoformat(timespec="seconds"),
            "src": "tas_prints - market-wide option prints >= $25k (equity roots only)",
            "rule": "calls bought + puts sold = bullish; puts bought + calls sold = bearish; "
                    "packages by their legs",
            "totals": totals, "mega": bk["mega"],
            "smallmicro": {"bull": bk["small"]["bull"] + bk["micro"]["bull"],
                           "bear": bk["small"]["bear"] + bk["micro"]["bear"],
                           "names": bk["small"]["names"] + bk["micro"]["names"], "top": small},
            "large": bk["large"], "mid": bk["mid"]}


# ---------------------------------------------------------------- market caps
def _load_cache() -> dict:
    try:
        with open(CACHE, encoding="utf-8") as fh:
            c = json.load(fh)
        return c if isinstance(c, dict) else {}
    except Exception:
        return {}


def _save_cache(c: dict) -> None:
    os.makedirs(os.path.dirname(CACHE), exist_ok=True)
    tmp = CACHE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(c, fh, separators=(",", ":"))
    os.replace(tmp, CACHE)


def _screener(client: object) -> dict:
    """{symbol: cap} for listed stocks (ETFs / funds excluded) from FMP's company-screener."""
    try:
        data = client.fmp("company-screener", {"isEtf": "false", "isFund": "false",
                                                "isActivelyTrading": "true", "limit": 10000})
    except Exception:
        return {}
    got = {}
    for r in data if isinstance(data, list) else []:
        s = str(r.get("symbol") or "").upper() if isinstance(r, dict) else ""
        v = r.get("marketCap") if isinstance(r, dict) else None
        if s and isinstance(v, (int, float)) and v > 0:
            got[s] = float(v)
    return got


def fill_caps(client: object, roots: list[str], cache: dict, today: str) -> dict:
    """Market cap per root: the day's screener (stocks only) first, then batch-quote for the rest.
    Returns {root: cap}. ``client`` has .fmp(endpoint, params): CVForge passthrough or FmpClient."""
    caps = cache.setdefault("caps", {})
    miss = cache.setdefault("miss", {})
    if cache.get("screener_day") != today:
        cache["screener_day"] = today
        got = _screener(client)
        cache["screener_ok"] = len(got) >= 1000
        if cache["screener_ok"]:
            caps.update(got)
            cache["stocks"] = sorted(got)          # the screener lists stocks only: ETFs are absent
    need = [r for r in roots if r not in caps and r not in NON_STOCK and miss.get(r) != today]
    if cache.get("screener_ok"):
        stocks = set(cache.get("stocks") or [])
        need = [r for r in need if r in stocks]
    for i in range(0, len(need), 100):
        part = need[i:i + 100]
        try:
            data = client.fmp("batch-quote", {"symbols": ",".join(part)})
        except Exception:
            data = None
        for q in data if isinstance(data, list) else []:
            s = str(q.get("symbol") or "").upper() if isinstance(q, dict) else ""
            v = q.get("marketCap") if isinstance(q, dict) else None
            if s and isinstance(v, (int, float)) and v > 0:
                caps[s] = float(v)
        for s in part:
            if s not in caps:
                miss[s] = today
    if cache.get("screener_ok"):
        stocks = set(cache.get("stocks") or [])
        return {r: caps[r] for r in roots if r in caps and r in stocks}
    return {r: caps[r] for r in roots if r in caps}


def etf_flag(client: object, sym: str) -> bool:
    """True if FMP's profile says ETF or fund (only for names about to be shown)."""
    try:
        data = client.fmp("profile", {"symbol": sym})
    except Exception:
        return False
    p = data[0] if isinstance(data, list) and data and isinstance(data[0], dict) else {}
    return bool(p.get("isEtf") or p.get("isFund"))


# ---------------------------------------------------------------- tape + files
COLS = ("ts", "root", "expiry", "strike", "cp", "side", "price", "size", "notional", "spot",
        "delta", "iv", "is_financing", "leg_group")


def load_rows(session: object, day: date) -> list[dict]:
    from sqlalchemy import select

    from trading_intel.memory.models import TasPrint

    q = select(*[getattr(TasPrint, c) for c in COLS]).where(
        TasPrint.trade_date == day, TasPrint.source == "convex")
    return [dict(zip(COLS, r, strict=False)) for r in session.execute(q).all()]


def _write(path: str, obj: object) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, separators=(",", ":"), default=str)
    os.replace(tmp, path)


def append_day(payload: dict) -> None:
    """bets_YYYYMMDD.json: one compact snapshot per run (the top lists), for a later replay."""
    path = os.path.join(DATA, "bets_" + payload["day"].replace("-", "") + ".json")
    try:
        with open(path, encoding="utf-8") as fh:
            day = json.load(fh)
    except Exception:
        day = {"v": 1, "day": payload["day"], "snaps": []}
    snap = {"t": payload["asof"], "totals": payload["totals"],
            "mega": payload["mega"]["top"], "smallmicro": payload["smallmicro"]["top"]}
    # time order: a run for the same or an earlier as-of replaces the snapshots from there on
    day["snaps"] = [x for x in day["snaps"] if str(x.get("t") or "") < snap["t"]] + [snap]
    _write(path, day)


def _shown(payload: dict) -> list[str]:
    return [r["s"] for r in payload["mega"]["top"] + payload["smallmicro"]["top"]]


def run_once(session_factory: object, client: object, day: date) -> dict:
    with session_factory() as s:          # type: ignore[operator]
        rows = to_et(load_rows(s, day), datetime.now(ET).replace(tzinfo=None))
    roots = sorted({(r.get("root") or "").upper() for r in rows} - {""})
    cache = _load_cache()
    caps = fill_caps(client, roots, cache, day.isoformat())
    etf = cache.setdefault("etf", {})
    caps = {k: v for k, v in caps.items() if not etf.get(k)}
    payload = build(rows, caps, day.isoformat(), datetime.now(ET))
    if not cache.get("screener_ok"):        # batch-quote caps cover ETFs too: check the names shown
        for _ in range(3):
            todo = [s for s in _shown(payload) if s not in etf]
            if not todo:
                break
            for s in todo[:12]:
                etf[s] = etf_flag(client, s)
            caps = {k: v for k, v in caps.items() if not etf.get(k)}
            payload = build(rows, caps, day.isoformat(), datetime.now(ET))
    _save_cache(cache)
    os.makedirs(DATA, exist_ok=True)
    _write(os.path.join(DATA, "bets_live.json"), payload)
    append_day(payload)
    t = payload["totals"]
    print(f"{datetime.now(ET):%H:%M:%S} bets_feed {payload['day']} asof {payload['asof']}: "
          f"{t['names']} names, bull ${t['bull'] / 1e6:.0f}M bear ${t['bear'] / 1e6:.0f}M, "
          f"financing {t['fin_n']} {t['fin_by']}, unsigned {t['uns_n']}, no cap {t['nocap']}, "
          f"caps from {'screener' if cache.get('screener_ok') else 'batch-quote'}", flush=True)
    return payload


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="one pass now, then exit")
    ap.add_argument("--date", help="YYYY-MM-DD (one pass for that stored day)")
    a = ap.parse_args()
    from trading_intel.config import get_settings
    from trading_intel.memory.db import make_session_factory

    settings = get_settings()
    sf = make_session_factory(settings)
    try:
        from trading_intel.clients.cvforge import CVForgeClient
        client = CVForgeClient(settings)
    except Exception:
        from trading_intel.clients.fmp import FmpClient
        client = FmpClient(settings)
    if a.once or a.date:
        run_once(sf, client, date.fromisoformat(a.date) if a.date else datetime.now(ET).date())
        return 0
    while True:                                        # daemon: 09:35-16:05 ET, every 5 min
        now = datetime.now(ET)
        hm = (now.hour, now.minute)
        if now.weekday() >= 5 or hm > END_HM:
            if hm > END_HM and now.weekday() < 5:
                run_once(sf, client, now.date())       # last pass with the close
            print("bets_feed: window closed - exiting", flush=True)
            return 0
        if hm >= START_HM:
            try:
                run_once(sf, client, now.date())
            except Exception as exc:
                print(f"bets_feed ERROR: {str(exc)[:200]}", flush=True)
        time.sleep(EVERY_S - (time.time() % EVERY_S) + 5)


if __name__ == "__main__":
    sys.exit(main())
