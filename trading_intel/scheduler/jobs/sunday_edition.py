"""Sunday edition of the brief (2026-11-13 overnight; backlog #42, brief item 5).

A weekly roll-up sent Sunday instead of a daily: how SPX and breadth moved over the week,
where the tape pillar stands (posture + bottom checklist), and what the coming week's
expiry calendar looks like. Banked rows only, no vendor calls. NAS DSM: Sun 17:00 ET
(`run_job.sh sunday_edition`). Idempotent: rewrites the same reports/sunday_<date>.html.
Descriptive regime context only (rule 4).
"""
from __future__ import annotations

import uuid
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from trading_intel.clients.telegram import TelegramClient
from trading_intel.config import Settings, get_settings
from trading_intel.memory.models import BreadthSnapshot, QuoteDaily
from trading_intel.synthesis.brief_tape import tape_context
from trading_intel.synthesis.brief_tape_render import tape_section
from trading_intel.synthesis.render_utils import _esc, _fmt

log = structlog.get_logger(__name__)
_OUT = Path("reports")


def _week_change(session: Session, sym: str, today: date) -> dict[str, Any] | None:
    rows = session.execute(select(QuoteDaily).where(QuoteDaily.symbol == sym,
                                                    QuoteDaily.date <= today)
                           .order_by(QuoteDaily.date.desc()).limit(10)).scalars().all()
    if len(rows) < 6:
        return None
    last = rows[0]
    prev = next((r for r in rows if r.date <= last.date - timedelta(days=6)), rows[-1])
    return {"symbol": sym, "close": last.close, "prev": prev.close,
            "chg": 100 * (last.close / prev.close - 1) if prev.close else None}


def _breadth_week(session: Session, today: date) -> dict[str, Any] | None:
    rows = session.execute(select(BreadthSnapshot).where(BreadthSnapshot.source == "fmp_sp500",
                                                         BreadthSnapshot.ts <= today)
                           .order_by(BreadthSnapshot.ts.desc()).limit(6)).scalars().all()
    if len(rows) < 2:
        return None
    a, b = rows[0], rows[-1]
    return {"from": b.ts.isoformat(), "to": a.ts.isoformat(),
            "pct50": (b.pct_above_50, a.pct_above_50), "pct200": (b.pct_above_200, a.pct_above_200),
            "ad": (a.ad_line or 0) - (b.ad_line or 0),
            "net_highs": sum((r.new_highs or 0) - (r.new_lows or 0) for r in rows[:-1])}


def build_html(session: Session, today: date | None = None) -> tuple[str, dict[str, Any]]:
    today = today or date.today()
    weeks = [w for s in ("SPX", "QQQ", "IWM", "RSP") if (w := _week_change(session, s, today))]
    bw = _breadth_week(session, today)
    tape = tape_context(session)
    try:
        from trading_intel.expiry_clock import phase
        clock = phase(today + timedelta(days=1))
    except Exception:  # noqa: BLE001
        clock = None
    rows = "".join(f'<tr><td><b>{_esc(w["symbol"])}</b></td><td>{_fmt(w["close"], 2)}</td>'
                   f'<td style="color:{"#16a34a" if (w["chg"] or 0) >= 0 else "#dc2626"}">'
                   f'{(w["chg"] or 0):+.2f}%</td></tr>' for w in weeks)
    bl = ""
    if bw:
        bl = (f'<p>Breadth {bw["from"]} → {bw["to"]}: above 50-day {bw["pct50"][0]}% → {bw["pct50"][1]}%, '
              f'above 200-day {bw["pct200"][0]}% → {bw["pct200"][1]}%, A-D line {bw["ad"]:+.0f}, '
              f'net new highs over the week {bw["net_highs"]:+d}.</p>')
    cl = f'<p><b>Coming week:</b> {_esc(str((clock or {}).get("read") or (clock or {}).get("phase") or "–"))}</p>'
    html = f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Sunday edition {today}</title><style>body{{font-family:system-ui;margin:16px;font-size:16px;color:#1a2027}}
td{{padding:4px 10px}} .card{{background:#fff;border:1px solid #e2e7ee;border-radius:11px;padding:14px;margin:10px 0}}
.note{{color:#475569;font-size:14px}} .lvl{{display:flex;flex-wrap:wrap;gap:8px}} .chip{{border:1px solid #e2e7ee;border-radius:8px;padding:6px 10px}}
.chip .lab{{display:block;color:#64748b;font-size:12px}}</style></head><body>
<h1>Sunday edition - week to {today - timedelta(days=2)}</h1>
<div class="card"><b>The week</b><table>{rows}</table>{bl}{cl}</div>
{tape_section({"tape": tape})}
<p class="note">Descriptive regime context only - not trading signals (rule 4).</p></body></html>"""
    return html, {"weeks": weeks, "breadth": bw, "posture": tape.get("posture")}


def run(session: Session, *, settings: Settings | None = None,
        client: TelegramClient | None = None) -> dict:
    settings = settings or get_settings()
    bound = log.bind(correlation_id=uuid.uuid4().hex, job="sunday_edition")
    today = date.today()
    html, ctx = build_html(session, today)
    _OUT.mkdir(parents=True, exist_ok=True)
    dest = _OUT / f"sunday_{today.isoformat()}.html"
    dest.write_text(html, encoding="utf-8")
    p = ctx.get("posture") or {}
    msg = f"Sunday edition {today}: posture {str(p.get('word', '-')).upper()} - " + "; ".join(p.get("why") or [])
    tele = client or TelegramClient(settings)
    delivered = tele.send_message(msg) and tele.send_document(dest, caption="Trading-Intel Sunday edition")
    bound.info("sunday_edition.done", file=str(dest), delivered=delivered)
    return {"file": str(dest), "delivered": delivered}


def main() -> None:
    from trading_intel.memory.db import make_session_factory

    settings = get_settings()
    with make_session_factory(settings)() as session:
        run(session, settings=settings)


if __name__ == "__main__":
    main()
