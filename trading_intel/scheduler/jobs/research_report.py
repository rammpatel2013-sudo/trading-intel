"""Scheduled job: build the fused single-ticker research report for each research name.

For every active ticker on the RESEARCH watchlist (``watchlist_entries`` — surfaced by
the letters + 13F jobs), pull:
  * CVForge OHLC aggs (weekly / daily / 4h) -> Weinstein stage (``research.stage``)
  * FMP fundamentals / institutional / analyst (``research.enrich`` — self-adapting)
  * the latest earnings transcript (``earnings.transcripts``)
  * the investor-letter commentary already banked for the name (``watchlist_entries``)
and write ``reports/<SYM>_research_<date>.html``. Fully unattended — every panel degrades
to blank on missing data, so no field-pinning/probe is required.

Runs on the box that has the CVForge key + DB. Descriptive research only (rule 4);
vendor access via the client (rule 1).

Manual run:
    python -m trading_intel.scheduler.jobs.research_report            # all research names
    python -m trading_intel.scheduler.jobs.research_report TAP ORCL   # specific symbols
"""

from __future__ import annotations

import html as _html
import sys
import uuid
from datetime import date, timedelta
from pathlib import Path

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from trading_intel.clients.cvforge import CVForgeClient
from trading_intel.config import Settings, get_settings
from trading_intel.errors import TradingIntelError
from trading_intel.memory.models import WatchlistEntry
from trading_intel.research import enrich
from trading_intel.research.stage import TIMEFRAMES, classify

log = structlog.get_logger(__name__)
_OUT = Path("reports")
_ACCENT = "#1f3864"


def _research_symbols(session: Session) -> list[str]:
    rows = (
        session.execute(
            select(WatchlistEntry.symbol).where(WatchlistEntry.active.is_(True)).distinct()
        )
        .scalars()
        .all()
    )
    return sorted({(s or "").strip().upper() for s in rows if s})


def _letters_for(session: Session, symbol: str) -> list[dict]:
    rows = session.execute(
        select(WatchlistEntry.rationale, WatchlistEntry.themes, WatchlistEntry.added_at)
        .where(WatchlistEntry.symbol == symbol, WatchlistEntry.active.is_(True))
        .order_by(WatchlistEntry.added_at.desc())
    ).all()
    return [
        {"rationale": r.rationale, "themes": r.themes, "added": r.added_at}
        for r in rows
        if r.rationale
    ]


def _closes(df: object) -> list[float]:
    try:
        return [float(c) for c in df["c"].tolist()] if df is not None and not df.empty else []  # type: ignore[attr-defined]
    except (KeyError, TypeError, ValueError):
        return []


def _svg(closes: list[float]) -> str:
    if len(closes) < 5:
        return '<div style="color:#8a93a0">price chart unavailable</div>'
    W, H, PL, PT, PB = 820, 240, 44, 12, 22
    lo, hi = min(closes) - 1, max(closes) + 1
    n = len(closes)
    ma = [None if i + 1 < 30 else sum(closes[i - 29 : i + 1]) / 30 for i in range(n)]

    def x(i: int) -> float:
        return PL + (W - PL - 8) * i / (n - 1)

    def y(v: float) -> float:
        return PT + (H - PT - PB) * (1 - (v - lo) / (hi - lo))

    def path(vals: list) -> str:
        pts = [(x(i), y(float(v))) for i, v in enumerate(vals) if v is not None]
        return "M" + " L".join(f"{a:.1f},{b:.1f}" for a, b in pts)

    lastx, lasty = x(n - 1), y(closes[-1])
    return (
        f'<svg viewBox="0 0 {W} {H}" width="100%" font-family="inherit">'
        f'<path d="{path(ma)}" fill="none" stroke="#b4690e" stroke-width="1.5" stroke-dasharray="4 3"/>'
        f'<path d="{path(closes)}" fill="none" stroke="{_ACCENT}" stroke-width="1.7"/>'
        f'<circle cx="{lastx:.1f}" cy="{lasty:.1f}" r="3" fill="{_ACCENT}"/>'
        f'<text x="{lastx-5:.1f}" y="{lasty-7:.1f}" text-anchor="end" fill="{_ACCENT}" font-size="11" font-weight="700">{closes[-1]:.2f}</text>'
        f'<text x="{W-8}" y="{H-6}" text-anchor="end" fill="#b4690e" font-size="10">30-period MA</text></svg>'
    )


def _money(v: float) -> str:
    a = abs(v)
    if a >= 1e12:
        return f"${v / 1e12:.2f}T"
    if a >= 1e9:
        return f"${v / 1e9:.2f}B"
    if a >= 1e6:
        return f"${v / 1e6:.1f}M"
    if a >= 1e3:
        return f"${v / 1e3:.0f}K"
    return f"${v:,.0f}"


def _kv(k: str, v: object, *, pct: bool = False, x: bool = False, big: bool = False) -> str:
    if v is None:
        s = '<span style="color:#9aa4b1">—</span>'
    elif big:
        s = _money(float(v))
    elif pct:
        s = f"{float(v)*100:.1f}%"
    elif x:
        s = f"{float(v):.1f}x"
    elif isinstance(v, float):
        s = f"{v:,.2f}"
    else:
        s = _html.escape(str(v))
    return f'<div class="kv"><span class="k">{k}</span><span class="v">{s}</span></div>'


def _stage_rows(stages: dict) -> str:
    color = {"Stage 1": "#b4690e", "Stage 2": "#0f7b3f", "Stage 3": "#b4690e", "Stage 4": "#c0392b"}
    labels = {"weekly": "vs 30-week MA", "daily": "vs 150-day MA", "4h": "vs ~150-period (4h)"}
    out = ""
    for tf in ("weekly", "daily", "4h"):
        r = stages.get(tf)
        if r is None:
            out += (
                f'<tr><td><b>{tf}</b></td><td colspan="2" style="color:#9aa4b1">no data</td></tr>'
            )
            continue
        c = color.get(r.stage, _ACCENT)
        out += (
            f'<tr><td><b>{tf}</b><div class="lmeta">{labels[tf]}</div></td>'
            f'<td><span style="color:{c};font-weight:700">{r.stage}</span> — {r.label}</td>'
            f"<td>{r.action}</td></tr>"
        )
    return out


def _render(
    sym: str,
    stages: dict,
    chart: str,
    fund: dict,
    inst: dict,
    an: dict,
    letters: list,
    transcript: dict | None,
) -> str:
    quotes = (
        "".join(
            f'<div class="quote">{_html.escape((c["rationale"] or "")[:600])}</div>'
            for c in letters[:4]
        )
        or '<div class="lmeta">No banked letter commentary yet.</div>'
    )
    tx = "—"
    if transcript:
        tx = f'{_html.escape(str(transcript.get("period") or ""))} {transcript.get("year") or ""} — transcript on file'
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{sym} — Research Report</title>
<style>
:root{{--ink:#1a1f26;--muted:#5b6673;--line:#e5e8ec;--bg:#f7f8fa;--card:#fff;--accent:{_ACCENT}}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 -apple-system,Segoe UI,Roboto,Arial,sans-serif}}
.wrap{{max-width:940px;margin:0 auto;padding:24px 20px 60px}}h1{{margin:0;font-size:23px}}.tk{{color:var(--accent)}}
.sub{{color:var(--muted);font-size:13px}}h2{{font-size:16px;margin:22px 0 8px;color:var(--accent)}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:15px 17px;margin:10px 0}}
.grid{{display:grid;grid-template-columns:1fr 1fr;gap:8px}}.kv{{display:flex;justify-content:space-between;padding:5px 0;border-bottom:1px dashed var(--line);font-size:14px}}
.k{{color:var(--muted)}}.v{{font-weight:600}}table{{width:100%;border-collapse:collapse;font-size:14px}}th,td{{text-align:left;padding:7px 9px;border-bottom:1px solid var(--line);vertical-align:top}}
th{{background:#f0f2f6;font-size:11px;text-transform:uppercase;color:var(--muted)}}.lmeta{{color:var(--muted);font-size:12px}}
.quote{{border-left:3px solid #b4690e;background:#fbfaf5;padding:8px 12px;margin:8px 0;font-size:14px}}
@media(max-width:640px){{.grid{{grid-template-columns:1fr}}}}
</style></head><body><div class="wrap">
<h1><span class="tk">{sym}</span> — Research one-pager</h1>
<div class="sub">{_html.escape(str(fund.get("sector") or ""))} &middot; auto-generated {date.today().isoformat()} &middot; letters + 13F + FMP + CVForge</div>

<h2>Technical &amp; stage analysis</h2>
<div class="card">{chart}
<table style="margin-top:8px"><thead><tr><th>Timeframe</th><th>Weinstein stage</th><th>Read</th></tr></thead><tbody>{_stage_rows(stages)}</tbody></table></div>

<h2>Fundamentals</h2><div class="card"><div class="grid"><div>
{_kv("Price", fund.get("price"))}{_kv("Market cap", fund.get("market_cap"), big=True)}
{_kv("P/E", fund.get("pe"), x=True)}{_kv("EV/EBITDA", fund.get("ev_ebitda"), x=True)}
{_kv("P/FCF", fund.get("p_fcf"), x=True)}{_kv("FCF yield", fund.get("fcf_yield"), pct=True)}
{_kv("Dividend yield", fund.get("div_yield"), pct=True)}</div><div>
{_kv("Gross / oper margin", None) if fund.get("gross_margin") is None else _kv("Gross margin", fund.get("gross_margin"), pct=True)}
{_kv("Operating margin", fund.get("oper_margin"), pct=True)}{_kv("FCF margin", fund.get("fcf_margin"), pct=True)}
{_kv("ROIC / ROE", fund.get("roic"), pct=True)}{_kv("Net debt / EBITDA", fund.get("net_debt_ebitda"), x=True)}
{_kv("Interest coverage", fund.get("interest_coverage"), x=True)}{_kv("SBC % of revenue", fund.get("sbc_pct_rev"), pct=True)}
</div></div></div>

<h2>Institutional &amp; analyst</h2><div class="card"><div class="grid"><div>
{_kv("Institutional ownership", inst.get("inst_pct"), pct=True)}{_kv("Insider buys (Form 4)", an.get("insider_buys"))}</div><div>
{_kv("Institutional holders", inst.get("holders"))}{_kv("Insider sells (Form 4)", an.get("insider_sells"))}</div></div>
<div class="lmeta" style="margin-top:6px">Insider = last 40 Form-4 filings (A/D). Analyst price-target &amp; rating consensus are gated on this data tier.</div></div>

<h2>Earnings &amp; transcript</h2><div class="card">{_kv("Latest transcript", tx)}
<div class="lmeta">Tone / QoQ &Delta;tone via the earnings-inflection detector when wired.</div></div>

<h2>Investor-letter commentary</h2><div class="card">{quotes}</div>

<div class="lmeta" style="margin-top:20px;border-top:1px solid var(--line);padding-top:10px">
Auto-generated from the research pipeline (letters_fetch + filings_fetch + research_report). Blank fields = the vendor
did not return that datapoint. Pair with the full options-vol dashboard (<code>ticker_report.py {sym}</code>).
Descriptive research only — never a trade signal.</div>
</div></body></html>"""


def build_one(
    session: Session, client: CVForgeClient, sym: str, *, days: int = 1500
) -> Path | None:
    sym = sym.upper()
    to = date.today().isoformat()
    frm = (date.today() - timedelta(days=days)).isoformat()
    stages, daily_closes = {}, []
    for tf, (mult, span, maw) in TIMEFRAMES.items():
        try:
            df = client.aggs(sym, frm=frm, to=to, multiplier=mult, timespan=span, limit=50000)
        except (TradingIntelError, KeyError, ValueError):
            continue
        closes = _closes(df)
        stages[tf] = classify(closes, ma_window=maw)
        if tf == "daily":
            daily_closes = closes[-260:]
    try:
        fund = enrich.pull_fundamentals(client, sym)
        inst = enrich.pull_institutional(client, sym)
        an = enrich.pull_analyst(client, sym)
    except (TradingIntelError, KeyError, ValueError, TypeError):
        fund, inst, an = {}, {}, {}
    letters = _letters_for(session, sym)
    transcript = None
    try:
        from trading_intel.earnings import transcripts

        two = transcripts.latest_two(client, sym)
        transcript = two[0] if two else None
    except (TradingIntelError, ImportError, KeyError, ValueError, TypeError):
        transcript = None

    html_doc = _render(sym, stages, _svg(daily_closes), fund, inst, an, letters, transcript)
    _OUT.mkdir(parents=True, exist_ok=True)
    dest = _OUT / f"{sym}_research_{date.today().isoformat()}.html"
    dest.write_text(html_doc, encoding="utf-8")
    return dest


def run(
    session: Session,
    client: CVForgeClient,
    *,
    settings: Settings | None = None,
    symbols: list[str] | None = None,
) -> dict:
    settings = settings or get_settings()
    bound = log.bind(correlation_id=uuid.uuid4().hex, job="research_report")
    syms = [s.upper() for s in symbols] if symbols else _research_symbols(session)
    written = []
    for sym in syms:
        try:
            dest = build_one(session, client, sym)
        except (TradingIntelError, OSError, ValueError) as exc:
            bound.warning("research_report.skip", symbol=sym, err=str(exc))
            continue
        if dest is not None:
            written.append(str(dest))
    bound.info("research_report.done", n=len(written))
    return {"written": written}


def main() -> None:
    from trading_intel.memory.db import make_session_factory

    settings = get_settings()
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ]
    )
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    client = CVForgeClient(settings)
    session_factory = make_session_factory(settings)
    try:
        with session_factory() as session:
            result = run(session, client, settings=settings, symbols=args or None)
    finally:
        client.close()
    print(f"Done: {result}")


if __name__ == "__main__":
    main()
