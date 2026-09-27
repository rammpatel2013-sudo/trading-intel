"""Tape & catalysts — per-name volume/money-flow + revenue trend + catalysts.

Data: ``quotes_daily`` (OHLCV) for the tape; the CVForge FMP passthrough for
quarterly income statements + stock news (the direct FMP key 402s on both);
``earnings_events`` for the next report date. News is tagged by keyword (no LLM,
rule 7) into catalyst buckets; 13F-holding / price-target churn is dropped as
noise. ``render_section`` returns the HTML block the ticker report embeds.
Descriptor only (rule 4).
"""
from __future__ import annotations

import html
import re
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from trading_intel.market import tape as t
from trading_intel.memory.models import QuoteDaily

log = structlog.get_logger(__name__)

_TAGS: list[tuple[str, re.Pattern[str]]] = [
    ("regulatory", re.compile(r"\b(FDA|PDUFA|BLA|sBLA|NDA|EMA|CRL)\b|(?i:\bapprov(al|ed|es)\b|"
                              r"complete response|clinical hold|breakthrough therapy|fast track)")),
    ("clinical", re.compile(r"(?i)\b(phase (1|2|3|i{1,3})\b|pivotal|topline|data readout|primary endpoint)")),
    ("legal", re.compile(r"\b(DOJ|FTC)\b|\bSEC (probe|investigation|charges|subpoena)|"
                         r"(?i:\blawsuit|class action|subpoena|settle(s|d|ment)\b|antitrust)")),
    ("capital", re.compile(r"(?i)\b(public offering|registered direct|private placement|dilut\w*|"
                           r"convertible notes?|ATM program|buyback|repurchase program)")),
    ("corporate", re.compile(r"(?i)\b(to acquire|acquisition of|merger|partnership with|license agreement|"
                             r"raises guidance|cuts guidance|guidance|CEO (steps|resign|depart))")),
    ("earnings", re.compile(r"(?i)\b(earnings|quarterly results|reports Q[1-4]|Q[1-4] (results|revenue)|revenue (beat|miss))")),
]
# MarketBeat-style 13F / rating churn: cashtag headlines, holder buys/sells, targets.
_NOISE = re.compile(r"\$[A-Z]{1,5}\b|(?i:\b(stake|holdings|position (in|cut|trimmed|raised)|shares (sold|bought|acquired)|"
                    r"sells [\d,]+ shares|buys [\d,]+ shares|stock (sold|bought)|consensus|price target|"
                    r"rating|upgrade|downgrade)\b)")


def tag_news(items: list[dict]) -> list[dict]:
    out = []
    for it in items:
        title = str(it.get("title") or "")
        if not title or _NOISE.search(title):
            continue
        body = str(it.get("text") or "")[:400]
        # headline decides; the body can only add hard regulatory/legal hits
        tags = [name for name, rx in _TAGS
                if rx.search(title) or (name in ("regulatory", "legal") and rx.search(body))]
        out.append({"date": str(it.get("publishedDate") or "")[:10], "title": title,
                    "site": it.get("site") or it.get("publisher"), "url": it.get("url"),
                    "tags": tags})
    return out


def revenue_trend(stmts: list[dict]) -> list[dict]:
    """Quarterly revenue oldest→newest with QoQ and YoY growth."""
    rows = sorted((s for s in stmts if s.get("revenue") is not None), key=lambda s: s["date"])
    out = []
    for i, s in enumerate(rows):
        rev = float(s["revenue"])
        prev = float(rows[i - 1]["revenue"]) if i else None
        yago = float(rows[i - 4]["revenue"]) if i >= 4 else None
        out.append({"date": s["date"], "revenue": rev,
                    "qoq": (rev / prev - 1) if prev else None,
                    "yoy": (rev / yago - 1) if yago else None,
                    "net_income": s.get("netIncome")})
    return out


def _tape(session: Session, sym: str, days: int) -> dict[str, Any] | None:
    rows = session.execute(
        select(QuoteDaily.date, QuoteDaily.high, QuoteDaily.low, QuoteDaily.close,
               QuoteDaily.volume)
        .where(QuoteDaily.symbol == sym).order_by(QuoteDaily.date.desc()).limit(days)
    ).all()[::-1]
    bars = [t.Bar(float(h or c), float(lo or c), float(c), float(v or 0)) for _, h, lo, c, v in rows if c]
    if len(bars) < 25:
        return None
    ob, vp_ = t.obv(bars), t.vpt(bars)
    prof = t.volume_profile(bars[-60:])
    last = bars[-1]
    return {
        "as_of": rows[-1][0].isoformat(), "close": last.close, "volume": last.volume,
        "avg20": sum(b.volume for b in bars[-21:-1]) / 20, "rel_volume": t.rel_volume(bars),
        "cmf20": t.cmf(bars), "obv_20d": t.slope(ob), "vpt_20d": t.slope(vp_),
        "price_20d": (last.close / bars[-21].close - 1) if bars[-21].close else None,
        "vwap20": t.anchored_vwap(bars[-20:]), "vwap60": t.anchored_vwap(bars[-60:]),
        "profile": prof, "obv": ob[-60:], "closes": [b.close for b in bars[-60:]],
    }


def tape_read(tp: dict[str, Any]) -> list[str]:
    """Plain-language lines: accumulation vs distribution, divergence, location."""
    out = []
    rv, cmf, obv20, p20 = tp.get("rel_volume"), tp.get("cmf20"), tp.get("obv_20d"), tp.get("price_20d")
    if rv is not None:
        out.append(f"Volume {rv:.1f}× its 20-day average" + (" — heavy" if rv >= 2 else ""))
    if cmf is not None:
        out.append("Money flow " + ("positive (accumulation)" if cmf > 0.05 else
                                    "negative (distribution)" if cmf < -0.05 else "neutral")
                   + f", CMF {cmf:+.2f}")
    if obv20 is not None and p20 is not None and (obv20 > 0) != (p20 > 0):
        out.append("OBV diverging from price over 20 sessions — "
                   + ("volume leading up (bullish)" if obv20 > 0 else "volume leaving (bearish)"))
    c, prof = tp.get("close"), tp.get("profile")
    if c and prof:
        loc = "above value area" if c > prof["vah"] else "below value area" if c < prof["val"] else "inside value area"
        out.append(f"Price {loc} (60d POC {prof['poc']:,.2f}, VA {prof['val']:,.2f}–{prof['vah']:,.2f})")
    if c and tp.get("vwap20"):
        out.append(f"{'Above' if c >= tp['vwap20'] else 'Below'} 20-day anchored VWAP {tp['vwap20']:,.2f}")
    return out


def build_tape_catalysts(session: Session, symbol: str, *, cvforge: Any = None,
                         days: int = 120) -> dict[str, Any]:
    sym = symbol.strip().upper()
    out: dict[str, Any] = {"symbol": sym, "tape": _tape(session, sym, days)}
    out["tape_read"] = tape_read(out["tape"]) if out["tape"] else []
    try:
        from trading_intel.mcp.em_tools import get_earnings_calendar

        ev = get_earnings_calendar(session, sym, days=120).get("events") or []
        out["next_earnings"] = ev[0] if ev else None
    except Exception as exc:  # noqa: BLE001 — optional block
        log.warning("tape_catalysts.earnings_failed", error=str(exc))
        out["next_earnings"] = None
    stmts, news = [], []
    if cvforge is None:
        try:
            from trading_intel.clients.cvforge import CVForgeClient
            from trading_intel.config import get_settings

            cvforge = CVForgeClient(get_settings())
        except Exception as exc:  # noqa: BLE001
            log.warning("tape_catalysts.cvforge_unavailable", error=str(exc))
    if cvforge is not None:
        try:
            stmts = cvforge.fmp("income-statement", {"symbol": sym, "period": "quarter", "limit": 8}) or []
        except Exception as exc:  # noqa: BLE001
            log.warning("tape_catalysts.income_failed", error=str(exc)[:120])
        try:
            news = cvforge.fmp("news/stock", {"symbols": sym, "limit": 30}) or []
        except Exception as exc:  # noqa: BLE001
            log.warning("tape_catalysts.news_failed", error=str(exc)[:120])
    out["revenue"] = revenue_trend(stmts if isinstance(stmts, list) else [])
    out["catalysts"] = [n for n in tag_news(news if isinstance(news, list) else []) if n["tags"]][:10]
    out["found"] = bool(out["tape"] or out["revenue"] or out["catalysts"])
    return out


# ── HTML block for the ticker report (dark house style) ─────────────────────
def _m(x: float | None) -> str:
    if x is None:
        return "—"
    a = abs(x)
    return f"{x/1e9:.2f}B" if a >= 1e9 else f"{x/1e6:.1f}M" if a >= 1e6 else f"{x:,.0f}"


def _pct(x: float | None) -> str:
    return "—" if x is None else f"{x * 100:+.0f}%"


def _fx(x: float | None, fmt: str) -> str:
    return "—" if x is None else format(x, fmt)


def render_section(d: dict[str, Any]) -> str:
    tp = d.get("tape") or {}
    reads = "".join(f"<li>{html.escape(r)}</li>" for r in d.get("tape_read") or [])
    reads = reads or "<li>no tape data</li>"
    prof = tp.get("profile")
    bars = ""
    if prof:
        mx = max(prof["volume"]) or 1
        c = tp.get("close")
        rows = []
        for k in range(len(prof["volume"]) - 1, -1, -1):
            lo_, hi_ = prof["edges"][k], prof["edges"][k + 1]
            here = c is not None and lo_ <= c < hi_
            col = "#e0a23a" if here else "#3fb950" if prof["val"] <= lo_ < prof["vah"] else "#39414f"
            width = prof["volume"][k] / mx * 100
            rows.append("<div style='display:flex;align-items:center;gap:6px;font-size:10px'>"
                        f"<span style='width:62px;color:#9aa4b2'>{lo_:,.2f}</span>"
                        f"<div style='height:7px;width:{width:.0f}%;background:{col}'></div></div>")
        bars = "".join(rows)
    rev_rows = "".join(
        f"<tr><td>{r['date']}</td><td>{_m(r['revenue'])}</td><td>{_pct(r['qoq'])}</td>"
        f"<td>{_pct(r['yoy'])}</td></tr>"
        for r in (d.get("revenue") or [])[-6:][::-1]
    ) or "<tr><td colspan=4>no statements</td></tr>"
    cat_rows = "".join(
        f"<tr><td>{c['date']}</td><td>{html.escape(', '.join(c['tags']))}</td>"
        f"<td style='text-align:left'>{html.escape(c['title'][:110])}</td></tr>"
        for c in d.get("catalysts") or []
    ) or "<tr><td colspan=3>no tagged catalysts in recent news</td></tr>"
    ne = d.get("next_earnings")
    ne_s = f"{ne['date']} ({ne.get('session') or 'time n/a'})" if ne else "not on the calendar (≤120d)"
    rv = _fx(tp.get("rel_volume"), ".2f")
    kv = (
        "<table>"
        f"<tr><th>Volume / 20d avg</th><td>{_m(tp.get('volume'))} / {_m(tp.get('avg20'))}</td></tr>"
        f"<tr><th>Rel. volume</th><td>{rv}{'×' if rv != '—' else ''}</td></tr>"
        f"<tr><th>CMF 20</th><td>{_fx(tp.get('cmf20'), '+.2f')}</td></tr>"
        f"<tr><th>OBV Δ20d / VPT Δ20d</th><td>{_m(tp.get('obv_20d'))} / {_m(tp.get('vpt_20d'))}</td></tr>"
        f"<tr><th>VWAP 20d / 60d</th><td>{_fx(tp.get('vwap20'), ',.2f')} / {_fx(tp.get('vwap60'), ',.2f')}</td></tr>"
        f"<tr><th>Next earnings</th><td>{html.escape(ne_s)}</td></tr></table>"
    )
    return (
        "<h2 class='sec'>Tape &amp; catalysts</h2><div class='grid3'>"
        f"<div class='card'><h3>Volume &amp; money flow</h3>{kv}"
        f"<ul style='font-size:12px;padding-left:16px'>{reads}</ul></div>"
        "<div class='card'><h3>Volume profile · 60d (amber = price, green = value area)</h3>"
        f"{bars or '<p class=muted>n/a</p>'}</div>"
        "<div class='card'><h3>Revenue · quarterly</h3><table class='grid'><tr><th>quarter</th>"
        f"<th>revenue</th><th>QoQ</th><th>YoY</th></tr>{rev_rows}</table></div></div>"
        "<h3 style='font-size:13px;color:#cbd5e1;margin:10px 0 6px'>Catalysts · tagged news "
        "(holding/target churn removed)</h3>"
        f"<table class='grid'><tr><th>date</th><th>type</th><th>headline</th></tr>{cat_rows}</table>"
    )
