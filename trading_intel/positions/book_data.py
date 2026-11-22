"""DB loaders for the OI-anchored position book (2026-11-22). Read-only SQL; the math is in book.py.

Alignment (verified 2026-11-21 on the Dec 31 7,250P): the 08:35 SPX book in ``oi_chain_eod`` dated D
(source ``convex_am`` = PM SPXW, ``convex_am_set`` = AM SPX monthlies) carries OI as of D-1's close. So the OI
change made by session T = OI(book of the next session) - OI(book of T), and the tape of T is ``tas_prints``
with trade_date = T.
"""

from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import text
from sqlalchemy.orm import Session

from trading_intel.positions.book import QUOTE_WEIGHT

BOOK_SOURCES = {"convex_am": "SPXW", "convex_am_set": "SPX"}

_BOOK_SQL = """
SELECT source, expiry, strike, cp, max(oi) AS oi, max(iv) AS iv, max(gamma) AS gamma
  FROM oi_chain_eod
 WHERE symbol = 'SPX' AND source IN ('convex_am', 'convex_am_set') AND ts >= :d AND ts < :d1
 GROUP BY source, expiry, strike, cp
"""


def book_dates(session: Session, since: date) -> list[date]:
    q = text(
        "SELECT DISTINCT date(ts) FROM oi_chain_eod WHERE symbol = 'SPX' AND source = 'convex_am' "
        "AND ts >= :s ORDER BY 1"
    )
    out = []
    for (d,) in session.execute(q, {"s": since}):
        out.append(d if isinstance(d, date) else date.fromisoformat(str(d)[:10]))
    return out


def load_book(session: Session, d: date) -> dict[tuple, dict]:
    """{(root, expiry, strike, cp): {"oi", "iv", "gamma"}} for the 08:35 book dated d."""
    out = {}
    for r in session.execute(text(_BOOK_SQL), {"d": d, "d1": d + timedelta(days=1)}):
        m = r._mapping
        if m["cp"] not in ("C", "P") or m["expiry"] is None:
            continue
        exp = (
            m["expiry"]
            if isinstance(m["expiry"], date)
            else date.fromisoformat(str(m["expiry"])[:10])
        )
        out[(BOOK_SOURCES[m["source"]], exp, float(m["strike"]), m["cp"])] = {
            "oi": float(m["oi"] or 0.0),
            "iv": m["iv"],
            "gamma": m["gamma"],
        }
    return out


def spot_on(session: Session, d: date) -> float | None:
    for q in (
        "SELECT spot FROM oi_chain_daily WHERE symbol = 'SPX' AND ts >= :d AND ts < :d1 AND spot IS NOT NULL "
        "ORDER BY ts DESC LIMIT 1",
        "SELECT close FROM equity_bars WHERE symbol IN ('SPX', '^SPX', '^GSPC') AND date <= :d "
        "ORDER BY date DESC LIMIT 1",
    ):
        try:
            v = session.execute(text(q), {"d": d, "d1": d + timedelta(days=1)}).scalar()
        except Exception:
            session.rollback()
            continue
        if v:
            return float(v)
    return None


def atm_iv(book: dict[tuple, dict], spot: float | None) -> dict[tuple, float]:
    """{(root, expiry): ATM IV} = mean C/P IV at the strike nearest spot."""
    if not spot:
        return {}
    best: dict[tuple, tuple[float, list]] = {}
    for (root, exp, k, _cp), v in book.items():
        if v.get("iv") is None:
            continue
        key, dist = (root, exp), abs(k - spot)
        cur = best.get(key)
        if cur is None or dist < cur[0]:
            best[key] = (dist, [v["iv"]])
        elif dist == cur[0]:
            cur[1].append(v["iv"])
    return {k: sum(ivs) / len(ivs) for k, (_d, ivs) in best.items()}


def _case_weight() -> str:
    parts = " ".join(f"WHEN '{k}' THEN {w}" for k, w in QUOTE_WEIGHT.items())
    return f"CASE quote_loc {parts} ELSE 0.5 END"


def load_tape(session: Session, d: date) -> dict[tuple, dict]:
    """Single-leg customer flow of session d per contract: quote-weighted buy / sell, prints, clips (100-1,000 lots).
    Spread legs (counted via pos_packages) and financing / box prints are left out."""
    q = text(f"""
SELECT root, expiry, strike, cp,
       sum(CASE WHEN side = 'buy' THEN size * {_case_weight()} ELSE 0 END) AS buy,
       sum(CASE WHEN side = 'sell' THEN size * {_case_weight()} ELSE 0 END) AS sell,
       count(*) AS prints,
       sum(CASE WHEN size BETWEEN 100 AND 1000 THEN 1 ELSE 0 END) AS clips,
       sum(size * price * 100) AS premium
  FROM tas_prints
 WHERE trade_date = :d AND root IN ('SPX', 'SPXW') AND cp IN ('C', 'P')
   AND (spread_leg IS NULL OR spread_leg = false) AND (is_financing IS NULL OR is_financing = false)
 GROUP BY root, expiry, strike, cp""")
    out = {}
    for r in session.execute(q, {"d": d}):
        m = r._mapping
        if m["expiry"] is None or m["strike"] is None:
            continue
        exp = (
            m["expiry"]
            if isinstance(m["expiry"], date)
            else date.fromisoformat(str(m["expiry"])[:10])
        )
        out[(m["root"], exp, float(m["strike"]), m["cp"])] = {
            "buy": float(m["buy"] or 0),
            "sell": float(m["sell"] or 0),
            "prints": int(m["prints"] or 0),
            "clips": int(m["clips"] or 0),
            "premium": float(m["premium"] or 0),
        }
    return out


def load_packages(session: Session, d: date) -> list[dict]:
    q = text(
        "SELECT pkg_key, structure, cp, dear_root, dear_expiry, dear_strike, cheap_root, cheap_expiry, "
        "cheap_strike, lots, net, side, action FROM pos_packages WHERE symbol = 'SPX' AND trade_date = :d"
    )
    try:
        rows = [dict(r._mapping) for r in session.execute(q, {"d": d})]
    except Exception:
        session.rollback()
        return []
    for r in rows:
        for k in ("dear_expiry", "cheap_expiry"):
            if r[k] is not None and not isinstance(r[k], date):
                r[k] = date.fromisoformat(str(r[k])[:10])
    return rows


def package_legs(pkgs: list[dict]) -> dict[tuple, list[float]]:
    """{contract: [customer bought, customer sold]} from package legs (dear leg = side, cheap leg = opposite)."""
    out: dict[tuple, list[float]] = {}
    for p in pkgs:
        s, lots = int(p["side"] or 0), float(p["lots"] or 0)
        if not s or not lots:
            continue
        for key, sign in (
            ((p["dear_root"], p["dear_expiry"], float(p["dear_strike"]), p["cp"]), s),
            ((p["cheap_root"], p["cheap_expiry"], float(p["cheap_strike"]), p["cp"]), -s),
        ):
            a = out.setdefault(key, [0.0, 0.0])
            a[0 if sign > 0 else 1] += lots
    return out


def big_0dte_prints(session: Session, d: date, min_size: int = 1000) -> list[dict]:
    q = text(
        "SELECT ts, root, expiry, strike, cp, side, price, size FROM tas_prints WHERE trade_date = :d "
        "AND expiry = :d AND root IN ('SPX', 'SPXW') AND size >= :n "
        "AND (is_financing IS NULL OR is_financing = false)"
    )
    return [dict(r._mapping) for r in session.execute(q, {"d": d, "n": min_size})]


def load_state(session: Session, before: date) -> tuple[date | None, dict[tuple, dict]]:
    """The newest stored book row per contract strictly before ``before`` (the state to continue from)."""
    last = session.execute(
        text("SELECT max(date) FROM pos_book_daily WHERE date < :d"), {"d": before}
    ).scalar()
    if last is None:
        return None, {}
    if not isinstance(last, date):
        last = date.fromisoformat(str(last)[:10])
    q = text(
        "SELECT root, expiry, strike, cp, oi, cust_pos, known, seed_oi, gamma, player_slug FROM pos_book_daily "
        "WHERE date = :d"
    )
    out = {}
    for r in session.execute(q, {"d": last}):
        m = r._mapping
        exp = (
            m["expiry"]
            if isinstance(m["expiry"], date)
            else date.fromisoformat(str(m["expiry"])[:10])
        )
        out[(m["root"], exp, float(m["strike"]), m["cp"])] = dict(m)
    return last, out
