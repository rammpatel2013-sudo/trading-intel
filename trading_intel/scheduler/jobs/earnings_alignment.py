"""Scheduled job (weekly): the earnings-week signal-alignment screen -> Telegram.

Fahad/Jaguar's process, ported (see ``earnings.screen`` / ``earnings.alignment``):
this week's reporters ranked where the research ANGLE and options FLOW align,
with an upward EPS-estimate REVISION as the top-quality gate. Reads banked data
only; writes an HTML table to ``reports/`` and pushes it via Telegram.
Descriptive ranking only — never a trade signal (FlashAlpha rule 4).

Manual run:
    python -m trading_intel.scheduler.jobs.earnings_alignment
"""

from __future__ import annotations

import html as _html
import uuid
from datetime import date
from pathlib import Path
from typing import Any

import structlog
from sqlalchemy.orm import Session

from trading_intel.clients.telegram import TelegramClient
from trading_intel.config import Settings, get_settings
from trading_intel.earnings.screen import build_alignment_screen

log = structlog.get_logger(__name__)
_OUT = Path("reports")


def _pct(x: float | None) -> str:
    return "—" if x is None else f"{x * 100:+.0f}%"


def _rev(x: float | None) -> str:
    if x is None:
        return "—"
    return (f"↑ {x * 100:+.0f}%" if x > 0 else f"↓ {x * 100:+.0f}%") if x else "flat"


def _mm(x: float | None) -> str:
    return "—" if x is None else f"${x / 1e6:+.1f}M"


def _tier_cls(rank: int) -> str:
    return {1: "t1", 2: "t2", 3: "t3"}.get(rank, "t4")


def _render(rows: list[dict[str, Any]], *, as_of: str) -> str:
    body = ""
    for r in rows:
        themes = ", ".join(str(t) for t in (r.get("themes") or [])[:3])
        body += (
            f'<tr class="{_tier_cls(r["tier_rank"])}"><td class="s">{_html.escape(r["symbol"])}</td>'
            f'<td>{_html.escape(str(r.get("date") or ""))}</td>'
            f'<td>{_html.escape(r.get("bias") or "")}</td>'
            f'<td>{r.get("angle"):+.2f}</td>'
            f'<td>{_mm(r.get("flow"))}</td>'
            f'<td>{_rev(r.get("revision"))}</td>'
            f'<td>{_html.escape(r.get("tier") or "")}</td>'
            f'<td class="th">{_html.escape(themes)}</td></tr>'
        )
    if not body:
        body = '<tr><td colspan="8" style="color:#8a93a0">No reporters with a research angle this week.</td></tr>'
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Earnings Alignment {as_of}</title>
<style>body{{font:14px/1.5 -apple-system,Segoe UI,Roboto,Arial,sans-serif;color:#1a2027;background:#f4f6f9;margin:0;padding:22px 14px 50px}}
.wrap{{max-width:820px;margin:0 auto}}h1{{font-size:19px;color:#12233d;margin:0 0 2px}}.sub{{color:#5b6673;font-size:12.5px;margin-bottom:14px}}
table{{width:100%;border-collapse:collapse;background:#fff;border:1px solid #e2e7ee;border-radius:10px;overflow:hidden;font-variant-numeric:tabular-nums}}
th,td{{text-align:right;padding:8px 9px;border-bottom:1px solid #eef1f6}}th{{background:#12233d;color:#fff;font-size:10.5px;letter-spacing:.4px;text-transform:uppercase}}
td.s,th.s,td.th,th.th{{text-align:left}}td.s{{font-weight:700}}td.th{{color:#8a93a0;font-size:12px}}
tr.t1{{background:#eaf7ef}}tr.t2{{background:#f2f9f4}}tr.t3{{background:#fbf8ec}}
.note{{color:#5b6673;font-size:12px;margin-top:12px}}</style></head><body><div class="wrap">
<h1>🎯 Earnings-Week Alignment</h1>
<div class="sub">{as_of} · this week's reporters ranked where research angle + option flow + EPS revision agree</div>
<table><thead><tr><th class="s">Name</th><th>Reports</th><th>Bias</th><th>Angle</th><th>Flow Δ$</th><th>EPS rev</th><th>Tier</th><th class="th">Themes</th></tr></thead>
<tbody>{body}</tbody></table>
<div class="note">Tier 1 = angle + flow + upward EPS revision all agree (highest-quality asymmetric setup). Descriptive ranking only — never a trade signal (rule 4).</div>
</div></body></html>"""


def _summary(rows: list[dict[str, Any]], *, as_of: str) -> str:
    top = [r for r in rows if r["tier_rank"] <= 2][:8]
    lines = [f"<b>🎯 Earnings Alignment {as_of}</b>"]
    if not top:
        lines.append("No aligned reporters this week.")
    for r in top:
        arrow = "🟢" if r.get("bias") == "bullish" else "🔴"
        lines.append(
            f"{arrow} <b>{_html.escape(r['symbol'])}</b> {_html.escape(str(r.get('date') or ''))} · "
            f"{_html.escape(r.get('tier') or '')} · EPS {_rev(r.get('revision'))}"
        )
    lines.append("<i>Angle+flow+EPS-revision alignment. Descriptive only (rule 4). Full table attached ⬇️</i>")
    return "\n".join(lines)


def run(
    session: Session,
    *,
    settings: Settings | None = None,
    client: TelegramClient | None = None,
    days: int = 7,
) -> dict:
    """Build the alignment screen, write it, and push via Telegram."""
    settings = settings or get_settings()
    bound = log.bind(correlation_id=uuid.uuid4().hex, job="earnings_alignment")
    as_of = date.today().isoformat()
    rows = build_alignment_screen(session, settings, days=days)

    _OUT.mkdir(parents=True, exist_ok=True)
    dest = _OUT / f"earnings_alignment_{as_of}.html"
    dest.write_text(_render(rows, as_of=as_of), encoding="utf-8")

    tele = client or TelegramClient(settings)
    delivered = tele.send_message(_summary(rows, as_of=as_of)) and tele.send_document(
        dest, caption="Earnings-week alignment screen"
    )
    bound.info("earnings_alignment.done", rows=len(rows), file=str(dest), delivered=delivered)
    return {"rows": len(rows), "file": str(dest), "delivered": delivered}


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
    session_factory = make_session_factory(settings)
    with session_factory() as session:
        result = run(session, settings=settings)
    print(f"Done: {result}")


if __name__ == "__main__":
    main()
