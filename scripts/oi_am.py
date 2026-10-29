"""Morning OI change for the watchlist: who opened what (CVForge, 08:35 ET).

Created 2026-10-28. Writes FILES only (no DB rows), zero Convex calls.

OCC publishes open interest once a night; by ~08:35 the vendor shows the OI as of
yesterday's close. This job pulls that OI per contract from CVForge for every
watchlist name (index roots excluded), subtracts the OI banked by last night's
17:15 ``oi_chain_eod`` run (``source='convex_eod'``, which holds the OI as of the
day BEFORE yesterday), and so gets yesterday's change in OI per contract.

OI alone has no side. The side comes from yesterday's aggressor split, which the
same ``oi_chain_eod`` row carries (``gammas_buy`` / ``gammas_sell``, Convex's
measured buy vs sell). Contracts bought ~= volume x gammas_buy / (buy + sell).
The four-way read per contract:

    OI up,   buyers dominated   -> BTO  buy to open   (new longs)
    OI up,   sellers dominated  -> STO  sell to open  (new shorts / writing)
    OI down, sellers dominated  -> STC  sell to close (longs exiting)
    OI down, buyers dominated   -> BTC  buy to close  (shorts covering)

Cross-check (Mithil's 10/21 rule, side from IV): the strike's IV change over the
same session (mean of call+put IV, prior EOD vs the EOD before it) MINUS its
expiry's ATM IV change; up = demand-led (BTO/BTC), down = supply-led (STO/STC),
under 0.10 vol pt = flat. Each row says agree / disagree / flat; the summary
reports the agreement share. Where no split was banked (before 10/25) the IV read
is used as the side instead (side_src = "iv").

An imbalance under 10% of volume is tagged MIX and not counted. A change larger
than the day's volume (exercise, assignment, adjustment) is tagged ADJ and not
counted. Customer delta added = |dOI| x sign(net bought) x delta x 100 (x spot for $);
the dealer holds the other side.

Freshness: if most contracts that traded yesterday show the SAME OI as the prior
EOD, the vendor has not rolled OI yet. The job waits and retries (default 2 x 15 min,
so 08:50 and 09:05) and labels the result STALE if it never rolls.

Outputs:  reports/oi_am/oi_am_YYYYMMDD.json   (summary per name + top contracts)
          reports/oi_am/oi_am_YYYYMMDD.csv    (every contract with |dOI| >= --csv-min)
          Telegram summary (unless --no-telegram)

Run (repo root):  python scripts/oi_am.py [--symbols A,B] [--limit N] [--no-telegram]
Test on history:  python scripts/oi_am.py --date 2026-10-28 --today-oi-from-eod 2026-10-28
                  (uses the 10/28 EOD OI as "this morning's" OI -> the 10/27 session)
NAS (DSM, Mon-Fri 08:35):
    bash /var/services/homes/drmithil/trading-intel/scripts/nas/run_script.sh oi_am
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")
OI_PARAMS = "expiration_date,strike_price,contract_type,open_interest,day_volume,underlying_price"
MIN_IMB = 0.10          # |net| / volume below this -> MIX
STALE_SHARE = 0.80      # >= this share of traded contracts unchanged -> symbol stale
STALE_MIN_ROWS = 5
TRADED_MIN_VOL = 20
IV_DEAD = 0.001         # 0.10 vol point dead zone on the IV cross-check
FOUR = ("BTO", "STO", "STC", "BTC")

READ = {
    ("C", "BTO"): "bought calls",
    ("C", "STO"): "sold calls",
    ("C", "STC"): "call longs out",
    ("C", "BTC"): "call shorts covered",
    ("P", "BTO"): "bought puts",
    ("P", "STO"): "sold puts",
    ("P", "STC"): "put longs out",
    ("P", "BTC"): "put shorts covered",
}


def log(msg: str) -> None:
    try:
        from trading_intel.timeutils import eastern_now

        stamp = f"{eastern_now():%H:%M:%S} ET"
    except Exception:  # noqa: BLE001
        stamp = f"{datetime.now():%H:%M:%S}"
    print(f"{stamp} {msg}", flush=True)


def fnum(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def k_strike(x) -> float:
    return round(float(x), 3)


# ---------------------------------------------------------------- inputs
def watch_symbols(settings, only: list[str] | None) -> list[str]:
    if only:
        return [s.strip().upper() for s in only if s.strip()]
    from trading_intel.memory.db import make_session_factory
    from trading_intel.watchlist import effective_symbols

    with make_session_factory(settings)() as session:
        syms = effective_symbols(session, settings)
    drop = set(settings.chain_exclude_roots) | set(getattr(settings, "index_roots", []) or [])
    return [s for s in syms if s.upper() not in drop]


def load_eod(settings, syms: list[str], before: date | None = None, on: date | None = None) -> dict:
    """{symbol: {"date": d, "rows": {(exp, strike, cp): {...}}}} from oi_chain_eod (convex_eod).

    ``before``: newest EOD strictly before that date (the normal prior-day read).
    ``on``:     exactly that day's EOD (test mode: its OI stands in for this morning).
    """
    from sqlalchemy import text

    from trading_intel.memory.db import make_session_factory

    if on is not None:
        lo, hi = on, on + timedelta(days=1)
    else:
        lo, hi = before - timedelta(days=10), before
    q = text(
        "select symbol, ts, expiry, strike, cp, oi, volume, delta, gamma, iv, "
        "gammas_buy, gammas_sell, deltas_buy, deltas_sell, bid_price, ask_price "
        "from oi_chain_eod where symbol = any(:syms) and source = 'convex_eod' "
        "and ts >= :lo and ts < :hi"
    )
    out: dict = {}
    with make_session_factory(settings)() as session:
        res = session.execute(q, {"syms": list(syms), "lo": datetime.combine(lo, datetime.min.time()),
                                  "hi": datetime.combine(hi, datetime.min.time())})
        rows = res.mappings().all()
    newest: dict = {}
    for r in rows:
        d = r["ts"].date()
        if d > newest.get(r["symbol"], date.min):
            newest[r["symbol"]] = d
    for r in rows:
        sym = r["symbol"]
        if r["ts"].date() != newest[sym]:
            continue
        slot = out.setdefault(sym, {"date": newest[sym], "rows": {}})
        slot["rows"][(r["expiry"], k_strike(r["strike"]), r["cp"])] = dict(r)
    return out


def fetch_cvforge(client, sym: str) -> tuple[dict, float | None]:
    """{(exp, strike, cp): oi} and spot from one CVForge chain call (OI fields only)."""
    from trading_intel.clients.cvforge import _flatten_chain

    data = client._get(f"/chains/{sym.upper()}", {"params": OI_PARAMS})
    oi: dict = {}
    spot = None
    for r in _flatten_chain(data):
        try:
            exp = date.fromisoformat(str(r["expiration"])[:10])
            key = (exp, k_strike(r["strike"]), "C" if str(r["opt_kind"]).lower().startswith("c") else "P")
        except (TypeError, ValueError):
            continue
        v = fnum(r.get("oi"))
        if v is not None:
            oi[key] = v
        if spot is None:
            spot = fnum(r.get("underlying_price"))
    return oi, spot


# ---------------------------------------------------------------- compute
def split(row: dict) -> tuple[float | None, float | None]:
    """(bought, sold) contracts for the prior session from the aggressor split."""
    vol = fnum(row.get("volume")) or 0.0
    if vol <= 0:
        return 0.0, 0.0
    for b, s in (("gammas_buy", "gammas_sell"), ("deltas_buy", "deltas_sell")):
        gb, gs = fnum(row.get(b)), fnum(row.get(s))
        if gb is None or gs is None:
            continue
        gb, gs = abs(gb), abs(gs)
        if gb + gs > 0:
            bought = vol * gb / (gb + gs)
            return bought, vol - bought
    return None, None


def strike_ivs(eod: dict | None) -> tuple[dict, dict]:
    """({(exp, strike): iv}, {exp: atm_iv}) from one EOD book.

    Strike IV = mean of the call and put IV at that strike (parity says they are one
    number; Convex's per-contract IVs differ by several vol points, so the mean is the
    steadier read). ATM = the strike whose call delta is nearest 0.50.
    """
    if not eod:
        return {}, {}
    acc: dict = {}
    calls: dict = {}
    for (exp, strike, cp), r in eod["rows"].items():
        iv = fnum(r.get("iv"))
        if iv is None or not 0 < iv < 5:
            continue
        acc.setdefault((exp, strike), []).append(iv)
        dl = fnum(r.get("delta"))
        if cp == "C" and dl is not None:
            best = calls.get(exp)
            if best is None or abs(dl - 0.5) < best[0]:
                calls[exp] = (abs(dl - 0.5), strike)
    siv = {k: sum(v) / len(v) for k, v in acc.items()}
    atm = {exp: siv[(exp, k)] for exp, (_, k) in calls.items() if (exp, k) in siv}
    return siv, atm


def iv_tag(d: float, rel: float | None) -> str | None:
    """Side from the strike's IV change net of its expiry's ATM change (10/21 rule)."""
    if rel is None:
        return None
    if abs(rel) < IV_DEAD:
        return "FLAT"
    if d > 0:
        return "BTO" if rel > 0 else "STO"
    return "BTC" if rel > 0 else "STC"


def compare(sym: str, prior: dict, am_oi: dict, spot: float | None, asof: date,
            prior2: dict | None = None) -> dict:
    rows = []
    traded = same = 0
    siv1, atm1 = strike_ivs(prior)
    siv0, atm0 = strike_ivs(prior2)
    for key, p in prior["rows"].items():
        exp, strike, cp = key
        if exp < asof or key not in am_oi or p.get("oi") is None:
            continue
        prev, new = float(p["oi"]), float(am_oi[key])
        vol = fnum(p.get("volume")) or 0.0
        if vol >= TRADED_MIN_VOL:
            traded += 1
            same += prev == new
        d = new - prev
        if d == 0:
            continue
        bought, sold = split(p)
        net = None if bought is None else bought - sold
        imb = (net / vol) if (net is not None and vol > 0) else None
        if abs(d) > vol * 1.05 + 10:
            tag = "ADJ"
        elif imb is None:
            tag = "NA"
        elif abs(imb) < MIN_IMB:
            tag = "MIX"
        elif d > 0:
            tag = "BTO" if net > 0 else "STO"
        else:
            tag = "BTC" if net > 0 else "STC"
        rel = None
        if (exp, strike) in siv1 and (exp, strike) in siv0 and exp in atm1 and exp in atm0:
            rel = (siv1[(exp, strike)] - siv0[(exp, strike)]) - (atm1[exp] - atm0[exp])
        ivt = iv_tag(d, rel)
        side_src = "split"
        if tag == "NA" and ivt in FOUR:      # no split banked (pre-10/25 history): use the IV read
            tag, side_src = ivt, "iv"
            net = 1.0 if ivt in ("BTO", "BTC") else -1.0
        if tag not in FOUR or ivt is None:
            iv_check = "na"
        elif side_src == "iv":
            iv_check = "iv-only"
        elif ivt == "FLAT":
            iv_check = "flat"
        else:
            iv_check = "agree" if ivt == tag else "disagree"
        delta = fnum(p.get("delta"))
        cust_sh = None
        if tag in FOUR and delta is not None:
            cust_sh = abs(d) * (1 if net > 0 else -1) * delta * 100.0
        bid, ask = fnum(p.get("bid_price")), fnum(p.get("ask_price"))
        mid = (bid + ask) / 2 if (bid is not None and ask is not None and ask >= bid >= 0) else None
        rows.append({
            "symbol": sym, "expiry": exp.isoformat(), "strike": strike, "cp": cp,
            "oi_prev": int(prev), "oi_new": int(new), "d_oi": int(d), "volume": int(vol),
            "bought": None if bought is None else round(bought), "sold": None if sold is None else round(sold),
            "imb": None if imb is None else round(imb, 3), "tag": tag, "side_src": side_src,
            "d_iv_rel": None if rel is None else round(rel * 100, 2), "iv_check": iv_check,
            "read": READ.get((cp, tag), {"MIX": "two-way", "ADJ": "exercise/adjust", "NA": "no split"}.get(tag, "")),
            "delta": delta, "mid": mid,
            "prem": None if mid is None else round(abs(d) * mid * 100.0),
            "cust_delta_sh": None if cust_sh is None else round(cust_sh),
            "cust_delta_usd": None if (cust_sh is None or spot is None) else round(cust_sh * spot),
        })
    stale = traded >= STALE_MIN_ROWS and same / traded >= STALE_SHARE
    counted = [r for r in rows if r["cust_delta_sh"] is not None]

    def tot(cp, tags):
        return int(sum(r["d_oi"] for r in rows if r["cp"] == cp and (tags is None or r["tag"] in tags)))

    by_tag = {}
    for r in rows:
        by_tag[f'{r["cp"]}_{r["tag"]}'] = by_tag.get(f'{r["cp"]}_{r["tag"]}', 0) + abs(r["d_oi"])
    top = sorted((r for r in rows if r["tag"] in ("BTO", "STO", "STC", "BTC")),
                 key=lambda r: (-(r["prem"] or 0), -abs(r["d_oi"])))[:5]
    cust_usd = sum(r["cust_delta_usd"] or 0 for r in counted) if spot is not None else None
    return {
        "symbol": sym, "prior_eod": prior["date"].isoformat(), "spot": spot,
        "matched": sum(1 for k in prior["rows"] if k in am_oi and k[0] >= asof),
        "traded": traded, "unchanged_traded": same, "stale": stale,
        "d_oi_calls": tot("C", None), "d_oi_puts": tot("P", None),
        "abs_by_tag": by_tag,
        "cust_delta_sh": round(sum(r["cust_delta_sh"] for r in counted)),
        "cust_delta_usd": None if cust_usd is None else round(cust_usd),
        "prem_opened": round(sum(r["prem"] or 0 for r in rows if r["d_oi"] > 0)),
        "prem_closed": round(sum(r["prem"] or 0 for r in rows if r["d_oi"] < 0)),
        "iv_agree": sum(abs(r["d_oi"]) for r in rows if r["iv_check"] == "agree"),
        "iv_disagree": sum(abs(r["d_oi"]) for r in rows if r["iv_check"] == "disagree"),
        "top": top,
        "_rows": rows,
    }


# ---------------------------------------------------------------- output
def money(v) -> str:
    if v is None:
        return "-"
    a = abs(v)
    s = "-" if v < 0 else "+"
    if a >= 1e9:
        return f"{s}${a / 1e9:.1f}B"
    if a >= 1e6:
        return f"{s}${a / 1e6:.0f}M" if a >= 1e7 else f"{s}${a / 1e6:.1f}M"
    if a >= 1e3:
        return f"{s}${a / 1e3:.0f}K"
    return f"{s}${a:.0f}"


def kfmt(n: int) -> str:
    return f"{n / 1000:+.1f}k" if abs(n) >= 1000 else f"{n:+d}"


def contract_label(r: dict) -> str:
    e = date.fromisoformat(r["expiry"])
    k = f'{r["strike"]:g}'
    return f'{k}{r["cp"]} {e:%m/%d}'


def telegram_text(summary: dict, top_n: int) -> str:
    h = summary
    if h["stale"]:
        return (f"<b>OI change {h['asof']}</b>: vendor OI has NOT rolled yet "
                f"({h['n_stale']}/{h['n_names']} names unchanged after {h['attempts']} tries). "
                "No table today; the 17:15 EOD will still bank it.")
    names = [s for s in h["names"] if not s["stale"] and s["cust_delta_usd"] is not None]
    names.sort(key=lambda s: -abs(s["cust_delta_usd"]))
    src = f"TEST, OI from EOD {h['test_from_eod']}" if h.get("test_from_eod") else f"CVForge {h['asof_time']}"
    lines = [f"<b>OI change: {h['session']} session</b> ({h['n_names']} names, {src})",
             "<i>Customer delta added, biggest first</i>"]
    for s in names[:top_n]:
        t = s["top"][0] if s["top"] else None
        tl = f'{contract_label(t)} {kfmt(t["d_oi"])} {t["tag"]}' if t else ""
        lines.append(f"<b>{s['symbol']}</b> {money(s['cust_delta_usd'])}  "
                     f"C {kfmt(s['d_oi_calls'])} / P {kfmt(s['d_oi_puts'])}  {tl}")
    allrows = [r for s in h["names"] if not s["stale"] for r in s["top"]
               if r["tag"] in ("BTO", "STO") and r["prem"]]
    allrows.sort(key=lambda r: -r["prem"])
    if allrows:
        lines.append("")
        lines.append("<i>Biggest new positions (premium at yesterday's mid)</i>")
        for r in allrows[:10]:
            pct = "" if r["volume"] <= 0 else f' {100 * (r["bought"] or 0) / r["volume"]:.0f}% bought'
            lines.append(f'{r["symbol"]} {contract_label(r)} {kfmt(r["d_oi"])} {r["read"]}'
                         f'{pct}, {money(r["prem"]).lstrip("+")}')
    if h.get("notes"):
        lines.append("")
        lines.extend(f"<i>{n}</i>" for n in h["notes"])
    ag = sum(s.get("iv_agree", 0) for s in h["names"])
    dg = sum(s.get("iv_disagree", 0) for s in h["names"])
    chk = f" Strike IV change agrees on {100 * ag / (ag + dg):.0f}% of tagged contracts (by size)." if ag + dg else ""
    lines.append("<i>Side = yesterday's Convex buy/sell split; under 10% imbalance not counted."
                 f"{chk}</i>")
    return "\n".join(lines)


def send_telegram(text: str) -> bool:
    try:
        from trading_intel.clients.telegram import TelegramClient
        from trading_intel.config import Settings

        tc = TelegramClient(Settings())
        if not tc.enabled:
            log("telegram not configured")
            return False
        return bool(tc.send_message(text))
    except Exception as exc:  # noqa: BLE001
        log(f"telegram failed: {exc}")
        return False


# ---------------------------------------------------------------- main
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--date", help="as-of date YYYY-MM-DD (default: today ET)")
    ap.add_argument("--symbols", default="", help="comma list instead of the watchlist")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--per-min", type=int, default=300, help="CVForge pace cap")
    ap.add_argument("--retries", type=int, default=2, help="re-pulls if OI has not rolled")
    ap.add_argument("--retry-wait", type=int, default=900, help="seconds between re-pulls")
    ap.add_argument("--today-oi-from-eod", help="TEST: use this day's convex_eod OI as this morning's OI")
    ap.add_argument("--csv-min", type=int, default=50, help="CSV keeps contracts with |dOI| >= this")
    ap.add_argument("--top", type=int, default=12)
    ap.add_argument("--out-dir", default="reports/oi_am")
    ap.add_argument("--no-telegram", action="store_true")
    a = ap.parse_args(argv)

    from trading_intel.config import get_settings
    from trading_intel.timeutils import eastern_now

    t_start = time.time()
    settings = get_settings()
    now = eastern_now()
    asof = date.fromisoformat(a.date) if a.date else now.date()
    syms = watch_symbols(settings, a.symbols.split(",") if a.symbols else None)
    if a.limit:
        syms = syms[: a.limit]
    log(f"oi_am {asof}: {len(syms)} names")

    prior = load_eod(settings, syms, before=asof)
    log(f"prior EOD loaded for {len(prior)} names "
        f"(dates: {sorted({p['date'].isoformat() for p in prior.values()})})")
    syms = [s for s in syms if s in prior]
    # The EOD before that one, for the strike-IV cross-check (same session's IV change).
    p_dates = sorted({p["date"] for p in prior.values()})
    prior2 = load_eod(settings, syms, before=p_dates[-1]) if p_dates else {}
    log(f"IV check base EOD for {len(prior2)} names "
        f"(dates: {sorted({p['date'].isoformat() for p in prior2.values()})})")

    client = None
    test_eod = None
    if a.today_oi_from_eod:
        test_eod = load_eod(settings, syms, on=date.fromisoformat(a.today_oi_from_eod))
        log(f"TEST mode: morning OI from convex_eod {a.today_oi_from_eod} ({len(test_eod)} names)")
    from trading_intel.clients.cvforge import CVForgeClient

    client = CVForgeClient(settings)
    client._client.headers["User-Agent"] = UA
    gap = 60.0 / max(1, a.per_min)

    attempts = 0
    while True:
        attempts += 1
        results, failed = [], []
        for sym in syms:
            t0 = time.time()
            try:
                am, spot = fetch_cvforge(client, sym)
                if test_eod is not None:
                    am = {k: float(r["oi"]) for k, r in test_eod.get(sym, {"rows": {}})["rows"].items()
                          if r.get("oi") is not None}
                if not am:
                    failed.append(f"{sym}: empty")
                else:
                    p2 = prior2.get(sym)
                    if p2 is not None and p2["date"] >= prior[sym]["date"]:
                        p2 = None
                    results.append(compare(sym, prior[sym], am, spot, asof, p2))
            except Exception as exc:  # noqa: BLE001 - one bad name must not stop the run
                failed.append(f"{sym}: {type(exc).__name__}: {str(exc)[:120]}")
            wait = gap - (time.time() - t0)
            if wait > 0:
                time.sleep(wait)
        n_stale = sum(1 for r in results if r["stale"])
        stale = bool(results) and n_stale / len(results) >= 0.5
        log(f"attempt {attempts}: {len(results)} names, {n_stale} stale, {len(failed)} failed")
        if not stale or attempts > a.retries:
            break
        log(f"OI not rolled yet; retry in {a.retry_wait}s")
        time.sleep(a.retry_wait)
    try:
        client.close()
    except Exception:  # noqa: BLE001
        pass

    prior_dates = sorted({r["prior_eod"] for r in results})
    session = prior_dates[-1] if prior_dates else "?"
    notes = []
    exp_prev = (asof - timedelta(days=1))
    while exp_prev.weekday() >= 5:
        exp_prev -= timedelta(days=1)
    gaps = [r["symbol"] for r in results if r["prior_eod"] != exp_prev.isoformat()]
    if gaps:
        notes.append(f"{len(gaps)} names' last EOD is not {exp_prev:%m/%d} (holiday or missed 17:15 run); "
                     "their change spans more than one session")
    if failed:
        notes.append(f"{len(failed)} names failed: " + ", ".join(f.split(':')[0] for f in failed[:8]))

    out_dir = Path(a.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"oi_am_{asof:%Y%m%d}" + ("_test" if test_eod is not None else "")
    rows_all = [r for s in results for r in s["_rows"]]
    with (out_dir / f"{stem}.csv").open("w", newline="", encoding="utf-8") as fh:
        cols = ["symbol", "expiry", "strike", "cp", "oi_prev", "oi_new", "d_oi", "volume", "bought",
                "sold", "imb", "tag", "read", "side_src", "d_iv_rel", "iv_check", "delta", "mid", "prem",
                "cust_delta_sh", "cust_delta_usd"]
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in sorted(rows_all, key=lambda r: (r["symbol"], -abs(r["d_oi"]))):
            if abs(r["d_oi"]) >= a.csv_min:
                w.writerow(r)
    summary = {
        "asof": asof.isoformat(), "asof_time": f"{now:%H:%M}", "session": session,
        "test_from_eod": a.today_oi_from_eod, "attempts": attempts,
        "n_names": len(results), "n_stale": n_stale, "stale": stale, "failed": failed, "notes": notes,
        "names": [{k: v for k, v in s.items() if k != "_rows"} for s in results],
        "seconds": round(time.time() - t_start),
    }
    (out_dir / f"{stem}.json").write_text(json.dumps(summary, default=str), encoding="utf-8")
    text_msg = telegram_text(summary, a.top)
    (out_dir / f"{stem}.txt").write_text(text_msg, encoding="utf-8")
    log(f"wrote {out_dir / stem}.json/.csv/.txt ({len(rows_all)} changed contracts)")
    if not a.no_telegram:
        log("telegram " + ("sent" if send_telegram(text_msg) else "NOT sent"))
    log(f"done in {time.time() - t_start:.0f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
