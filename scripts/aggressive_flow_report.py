"""Aggressive Options Flow report — one standout name per sector group → HTML.

For each configured sector group, picks the name with the most AGGRESSIVE
(buyer-initiated) option premium on the latest session, then builds a card:
the call/put lean, headline aggressive premium, the day's unusual-strike count &
put/call volume, the top prints by V/OI, and the top prints by premium. Layout
lives here (template INLINED like the other ``scripts/*_report.py`` generators),
so tweaks deploy on a NAS tarball pull with no image rebuild.

All data is our own DURABLE roll-up, read-only, NO vendor call:
  - aggression + selection  ← ``tas_daily_flow``  (buy/sell premium per name/day)
  - per-strike premium+side ← ``tas_daily_contract`` (the aggressive tape prints)
  - V/OI + unusual count + PC vol ← ``oi_chain_eod`` (market volume / open interest)
"Aggressive" = OBSERVED aggressor side from the Convex tape, never inferred.
Descriptive only (FlashAlpha rule 4).

Run:
    python scripts/aggressive_flow_report.py            # build + push to Telegram
    python scripts/aggressive_flow_report.py --no-push  # build only
"""
from __future__ import annotations

import html as _html
import sys
from datetime import date
from pathlib import Path

import structlog
from sqlalchemy import func, select

from trading_intel.memory.models import OiChainEod, TasDailyContract, TasDailyFlow

log = structlog.get_logger(__name__)

_DEFAULT_OUT = Path("reports") / "aggressive_flow.html"

# ── Sector groups: {display name: member roots}. Edit / extend freely; the report
# renders one card per group (the grid wraps). A name is used by at most one group
# (first-come), so three cards = three distinct standouts. Overridable via
# settings.AGGRESSIVE_FLOW_GROUPS (same {name: [roots]} shape) if set.
_GROUPS: dict[str, list[str]] = {
    "Tech Mega": ["AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL", "GOOG", "TSLA", "NFLX", "AVGO"],
    "Semi": ["NVDA", "AMD", "MU", "AVGO", "TSM", "SMCI", "INTC", "QCOM", "ARM", "LRCX", "AMAT",
             "TXN", "MRVL", "ON", "ADI"],
    "Banks": ["JPM", "BAC", "WFC", "C", "GS", "MS", "USB", "PNC", "TFC", "SCHW", "COF"],
}
_DTE_CAP = 400        # ignore very-long-dated LEAP noise in the chain metrics
_MIN_VOL = 100        # a strike needs at least this day-volume to count as "unusual"
_UNUSUAL_VOI = 1.0    # unusual = volume / open-interest >= this


# ── scalars / format ────────────────────────────────────────────────────────
def _num(x):
    try:
        return float(x) if x is not None else None
    except (TypeError, ValueError):
        return None


def _esc(x):
    return _html.escape("" if x is None else str(x))


def _money(v):
    v = _num(v)
    if v is None:
        return "—"
    a = abs(v)
    if a >= 1e9:
        return f"${v/1e9:.1f}B"
    if a >= 1e6:
        return f"${v/1e6:.1f}M"
    if a >= 1e3:
        return f"${v/1e3:.1f}k"
    return f"${v:.0f}"


def _voi(v):
    v = _num(v)
    if v is None:
        return "—"
    return f"{v:.0f}&times;" if v >= 10 else f"{v:.1f}&times;"


def _md(d):
    return d.strftime("%m/%d") if hasattr(d, "strftime") else ""


def _stk(s, cp):
    s = _num(s)
    if s is None:
        return "?"
    txt = f"{s:.2f}".rstrip("0").rstrip(".")
    return f"{txt}{(cp or '').upper()[:1]}"


# ── data assembly ───────────────────────────────────────────────────────────
def _groups(settings) -> dict[str, list[str]]:
    g = getattr(settings, "AGGRESSIVE_FLOW_GROUPS", None) if settings else None
    return g if isinstance(g, dict) and g else _GROUPS


def _latest_trade_date(session):
    return session.execute(select(func.max(TasDailyFlow.trade_date))).scalar_one_or_none()


def _pick_winners(session, groups, td):
    """One standout root per group by aggressive (buyer-initiated) premium; deduped."""
    members = sorted({m for ms in groups.values() for m in ms})
    rows = session.execute(
        select(TasDailyFlow.root, TasDailyFlow.buy_notional, TasDailyFlow.total_notional)
        .where(TasDailyFlow.trade_date == td, TasDailyFlow.root.in_(members))
    ).all()
    by_root = {r.root: r for r in rows}
    winners: dict[str, str | None] = {}
    used: set[str] = set()
    for g, ms in groups.items():
        cand = [(m, by_root[m]) for m in ms if m in by_root and m not in used]
        cand.sort(key=lambda x: _num(x[1].buy_notional) or 0.0, reverse=True)
        if cand and (_num(cand[0][1].buy_notional) or 0) > 0:
            winners[g] = cand[0][0]
            used.add(cand[0][0])
        else:
            winners[g] = None
    return winners


def _chain_map(session, sym):
    """{(expiry, strike, cp): (oi, volume, dte)} from the latest EOD chain for sym."""
    ts = session.execute(
        select(func.max(OiChainEod.ts)).where(OiChainEod.symbol == sym)
    ).scalar_one_or_none()
    if ts is None:
        return {}
    rows = session.execute(
        select(OiChainEod.expiry, OiChainEod.strike, OiChainEod.cp, OiChainEod.oi,
               OiChainEod.volume, OiChainEod.dte)
        .where(OiChainEod.symbol == sym, OiChainEod.ts == ts)
    ).all()
    m = {}
    for r in rows:
        s = _num(r.strike)
        if s is None:
            continue
        m[(r.expiry, round(s, 4), (r.cp or "").upper()[:1])] = (r.oi, r.volume, r.dte)
    return m


def _card_data(session, group, sym, td):
    contracts = session.execute(
        select(TasDailyContract).where(
            TasDailyContract.root == sym, TasDailyContract.trade_date == td
        )
    ).scalars().all()
    chain = _chain_map(session, sym)

    call_buy = sum(_num(c.buy_notional) or 0.0 for c in contracts if (c.cp or "").upper().startswith("C"))
    put_buy = sum(_num(c.buy_notional) or 0.0 for c in contracts if (c.cp or "").upper().startswith("P"))
    side = "CALL" if call_buy >= put_buy else "PUT"
    headline = call_buy if side == "CALL" else put_buy

    enr = []
    for c in contracts:
        cp = (c.cp or "").upper()[:1]
        s = _num(c.strike)
        oi, vol, _dte = chain.get((c.expiry, round(s, 4), cp), (None, None, None)) if s is not None else (None, None, None)
        voi = (float(vol) / float(oi)) if (vol and oi and float(oi) > 0) else None
        enr.append({"expiry": c.expiry, "strike": s, "cp": cp,
                    "premium": _num(c.total_notional) or 0.0, "voi": voi})

    top_premium = sorted(enr, key=lambda d: d["premium"], reverse=True)[:3]
    top_unusual = sorted([e for e in enr if e["voi"] is not None],
                         key=lambda d: d["voi"], reverse=True)[:3]

    unusual_ct = 0
    put_vol = call_vol = 0.0
    for (_exp, _stk_, cp), (oi, vol, dte) in chain.items():
        if vol is None or (dte is not None and dte > _DTE_CAP):
            continue
        v = float(vol)
        if cp == "P":
            put_vol += v
        elif cp == "C":
            call_vol += v
        if oi and float(oi) > 0 and v >= _MIN_VOL and (v / float(oi)) >= _UNUSUAL_VOI:
            unusual_ct += 1
    pc = (put_vol / call_vol) if call_vol > 0 else None

    return {"group": group, "sym": sym, "side": side, "premium": headline,
            "unusual_ct": unusual_ct, "pc": pc, "top_premium": top_premium,
            "top_unusual": top_unusual, "has_flow": bool(contracts)}


# ── render ──────────────────────────────────────────────────────────────────
_CSS = """
:root{--bg:#0a0d13;--card:#111621;--card2:#0f141d;--line:#1d2431;--ink:#e9edf4;
--mut:#7a8494;--dim:#5b6472;--teal:#4d9fd6;--green:#35c46a;--red:#ef6f53;--gold:#e6b34d}
*{box-sizing:border-box}html,body{margin:0}
body{background:radial-gradient(1200px 500px at 50% -10%,#131a26 0%,var(--bg) 60%);color:var(--ink);
font:15px/1.5 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;-webkit-font-smoothing:antialiased}
.wrap{max-width:1080px;margin:0 auto;padding:22px 18px 30px}
.top{position:relative;text-align:center;padding:8px 0 14px}
.sigma{position:absolute;left:2px;top:2px;width:26px;height:26px;border:1px solid var(--line);border-radius:7px;
display:flex;align-items:center;justify-content:center;color:var(--teal);font-weight:700;font-size:15px;background:var(--card2)}
h1{margin:0;font-size:29px;font-weight:800;letter-spacing:.09em;text-shadow:0 0 22px rgba(77,159,214,.18)}
.sub{margin-top:6px;color:var(--mut);font-size:12.5px;letter-spacing:.02em}
.grid{display:grid;grid-template-columns:repeat(3,1fr);gap:15px;margin-top:16px}
@media (max-width:760px){.grid{grid-template-columns:1fr}}
.card{background:linear-gradient(180deg,var(--card) 0%,var(--card2) 100%);border:1px solid var(--line);
border-radius:14px;padding:16px 17px 13px}
.eyebrow{color:var(--teal);font-size:11px;font-weight:700;letter-spacing:.14em;text-transform:uppercase}
.tk{font-size:40px;font-weight:800;line-height:1.05;margin:2px 0 8px}
.agg{font-size:13px;font-weight:800;letter-spacing:.1em;text-transform:uppercase;margin-bottom:9px}
.agg.call{color:var(--green)}.agg.put{color:var(--red)}
.prem{font-size:15px;font-weight:600}.meta{color:var(--mut);font-size:12.5px;margin-top:2px}
.sect{color:var(--teal);font-size:10.5px;font-weight:700;letter-spacing:.13em;text-transform:uppercase;
margin:15px 0 7px;padding-bottom:5px;border-bottom:1px solid var(--line)}
.row{display:flex;align-items:baseline;gap:8px;font:12.5px/1.7 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
.row .dt{color:var(--dim);min-width:42px}.row .st{color:var(--ink);font-weight:600;min-width:60px}
.row .x{color:var(--teal)}.row .pm{color:var(--gold)}.row .sep{color:var(--dim)}
.na{color:var(--dim);font-size:12.5px;font-family:ui-monospace,monospace;padding:4px 0}
.foot{color:var(--dim);font-size:10.5px;letter-spacing:.05em;margin-top:13px;padding-top:9px;border-top:1px solid var(--line)}
.pagefoot{text-align:center;color:var(--dim);font-size:11px;margin-top:18px;line-height:1.7}
"""


def _rows_unusual(items):
    if not items:
        return '<div class="na">no unusual strikes</div>'
    out = []
    for e in items:
        out.append(
            f'<div class="row"><span class="dt">{_md(e["expiry"])}</span>'
            f'<span class="st">{_esc(_stk(e["strike"], e["cp"]))}</span><span class="sep">&middot;</span>'
            f'<span class="x">{_voi(e["voi"])}</span><span class="sep">&middot;</span>'
            f'<span class="pm">{_money(e["premium"])}</span></div>')
    return "".join(out)


def _rows_premium(items):
    if not items:
        return '<div class="na">no aggressive prints</div>'
    out = []
    for e in items:
        voi = f'<span class="sep">&middot;</span><span class="x">V/OI {_voi(e["voi"])}</span>' if e["voi"] is not None else ""
        out.append(
            f'<div class="row"><span class="dt">{_md(e["expiry"])}</span>'
            f'<span class="st">{_esc(_stk(e["strike"], e["cp"]))}</span><span class="sep">&middot;</span>'
            f'<span class="pm">{_money(e["premium"])}</span>{voi}</div>')
    return "".join(out)


def _card_html(group, c):
    if c is None or not c.get("has_flow"):
        return (f'<div class="card"><div class="eyebrow">{_esc(group)}</div>'
                f'<div class="tk" style="font-size:26px;color:var(--mut)">—</div>'
                f'<div class="na">No aggressive flow on the tape today.</div>'
                f'<div class="foot">trading-intel &middot; observed aggressor side</div></div>')
    sidecls = "call" if c["side"] == "CALL" else "put"
    pc = "—" if c["pc"] is None else f'{c["pc"]:.2f}'
    return (
        f'<div class="card">'
        f'<div class="eyebrow">{_esc(group)}</div>'
        f'<div class="tk">{_esc(c["sym"])}</div>'
        f'<div class="agg {sidecls}">{c["side"]} Aggressive</div>'
        f'<div class="prem">{_money(c["premium"])} premium</div>'
        f'<div class="meta">{c["unusual_ct"]} unusual strikes &middot; PC vol {pc}</div>'
        f'<div class="sect">Top unusual (V/OI)</div>{_rows_unusual(c["top_unusual"])}'
        f'<div class="sect">Top premium</div>{_rows_premium(c["top_premium"])}'
        f'<div class="foot">trading-intel &middot; observed aggressor side</div>'
        f'</div>')


def _render(cards: dict, td) -> str:
    when = td.strftime("%b %-d") if hasattr(td, "strftime") else _esc(td)
    body = "".join(_card_html(g, c) for g, c in cards.items())
    n = sum(1 for c in cards.values() if c and c.get("has_flow"))
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Aggressive Options Flow</title><style>{_CSS}</style></head>
<body><div class="wrap">
<div class="top"><div class="sigma">&#931;</div>
<h1>AGGRESSIVE OPTIONS FLOW</h1>
<div class="sub">{when} &middot; EOD &middot; {n} sectors &middot; trading-intel</div></div>
<div class="grid">{body}</div>
<div class="pagefoot">Standout name per sector by aggressive (buyer-initiated) premium from the option tape.
V/OI = day volume / open interest &middot; PC vol = put/call volume &middot; premium = aggressive-print notional.
Descriptive of positioning, not advice.</div>
</div></body></html>"""


# ── public build ────────────────────────────────────────────────────────────
def build(session, *, settings=None, out: Path | None = None, trade_date=None) -> Path:
    """Assemble the report from the DB and write the HTML. Returns the path."""
    groups = _groups(settings)
    td = trade_date or _latest_trade_date(session)
    cards: dict[str, dict | None] = {}
    if td is not None:
        winners = _pick_winners(session, groups, td)
        for g in groups:
            sym = winners.get(g)
            cards[g] = _card_data(session, g, sym, td) if sym else None
    else:
        cards = {g: None for g in groups}

    dest = Path(out) if out else _DEFAULT_OUT
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(_render(cards, td or date.today()), encoding="utf-8")
    log.info("aggressive_flow.built", path=str(dest), trade_date=str(td),
             sectors=sum(1 for c in cards.values() if c and c.get("has_flow")))
    return dest


def main() -> None:
    from trading_intel.config import get_settings
    from trading_intel.memory.db import make_session_factory

    structlog.configure(processors=[structlog.processors.add_log_level,
                                    structlog.processors.TimeStamper(fmt="iso"),
                                    structlog.processors.JSONRenderer()])
    settings = get_settings()
    push = "--no-push" not in sys.argv[1:]
    sf = make_session_factory(settings)
    with sf() as session:
        path = build(session, settings=settings)
    if push:
        from trading_intel.clients.telegram import TelegramClient

        sent = TelegramClient(settings).send_document(path, caption="Aggressive Options Flow — EOD")
        log.info("aggressive_flow.pushed", path=str(path), telegram_sent=sent)
    print(f"aggressive flow written: {path}")


if __name__ == "__main__":
    main()
