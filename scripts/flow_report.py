"""EOD Flow Report — longitudinal option-tape accumulation / distribution insight.

Renders ``flow/report.py::build_flow_report`` into one self-contained dark-theme
HTML file under ``reports/``. Reads only the DURABLE roll-up tables
(``tas_daily_flow`` + ``tas_daily_contract``), which survive the 30-day raw-print
prune, so it works off months of history.

Sections:
  - header — date, lookback window, universe counts
  - KEY FINDINGS — rule-based plain-language callouts (the "important trade
    findings"): top accumulators + net-buy streaks, biggest single-contract
    builds, heaviest distribution, names newly on / dropping off the board
  - Accumulation leaders / Distribution leaders tables
  - Repeat-contract lifecycle table (which exact strikes/expiries are being built)
  - New / Fading name chips

An optional ``llm`` (``LLMProvider``, local Ollama per CLAUDE.md rule 7) adds a
short narrative; it degrades silently if unavailable. Descriptive only (rule 4) —
nothing here is a trade signal.

CLI:  python scripts/flow_report.py [--lookback 21] [--recent 5] [--min-notional 1000000]
"""

from __future__ import annotations

import argparse
import html
from datetime import date
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from trading_intel.config import get_settings
from trading_intel.flow.report import build_flow_report

_OUT = Path(__file__).resolve().parent.parent / "reports"


# ── formatting helpers ─────────────────────────────────────────────────


def _money(v: float | int | None) -> str:
    if v is None:
        return "—"
    a = abs(float(v))
    s = "-" if v < 0 else ""
    if a >= 1e9:
        return f"{s}${a / 1e9:.2f}B"
    if a >= 1e6:
        return f"{s}${a / 1e6:.1f}M"
    if a >= 1e3:
        return f"{s}${a / 1e3:.0f}K"
    return f"{s}${a:.0f}"


def _streak(n: int | None) -> str:
    n = int(n or 0)
    if n > 0:
        return f"+{n}d buy"
    if n < 0:
        return f"{n}d sell"
    return "—"


def _esc(x: object) -> str:
    return html.escape(str(x))


def _cls(v: float | None) -> str:
    if v is None:
        return "mut"
    return "pos" if v > 0 else ("neg" if v < 0 else "mut")


# ── rule-based "important trade findings" ──────────────────────────────


def key_findings(rep: dict[str, Any]) -> list[str]:
    """Plain-language callouts from the report dict. Pure; no I/O."""
    out: list[str] = []
    trend = rep.get("trend", [])
    contracts = rep.get("contracts", [])
    as_of = rep.get("as_of", "")
    lookback = rep.get("lookback_days", "")

    accum = [t for t in trend if (t.get("recent_score") or 0) >= 20]
    distrib = [t for t in trend if (t.get("recent_score") or 0) <= -20]
    out.append(
        f"Over the last {lookback} sessions (as of {as_of}): "
        f"{len(accum)} names accumulating, {len(distrib)} distributing."
    )

    if accum:
        top = accum[0]
        bits = f"score {top['recent_score']:.0f}"
        if (top.get("streak_days") or 0) >= 2:
            bits += f", {_streak(top['streak_days'])} streak"
        if top.get("net_dollar_delta") is not None:
            bits += f", net Δ {_money(top['net_dollar_delta'])}"
        out.append(f"🟢 Strongest accumulation: {top['root']} ({bits}).")

    persistent = [t for t in accum if (t.get("streak_days") or 0) >= 3][:5]
    if persistent:
        names = ", ".join(f"{t['root']} ({_streak(t['streak_days'])})" for t in persistent)
        out.append(f"🔁 Persistent buyers (3+ day streak): {names}.")

    surging = sorted(
        (t for t in accum if (t.get("score_delta") or 0) >= 25),
        key=lambda t: t.get("score_delta") or 0,
        reverse=True,
    )[:4]
    if surging:
        names = ", ".join(f"{t['root']} (+{t['score_delta']:.0f})" for t in surging)
        out.append(f"📈 Accelerating vs prior window: {names}.")

    if contracts:
        c = contracts[0]
        exp = c.get("expiry") or "—"
        expired = bool(isinstance(exp, str) and as_of and exp < as_of)
        tag = " [expired]" if expired else ""
        out.append(
            f"🎯 Biggest single-contract build: {c['root']} {exp} "
            f"{c.get('strike')}{c.get('cp')} — {_money(c.get('total_notional'))} "
            f"over {c.get('days_seen')}d ({c.get('build_side')}){tag}."
        )

    if distrib:
        d = distrib[-1]
        out.append(
            f"🔴 Heaviest distribution: {d['root']} "
            f"(score {d['recent_score']:.0f}, net Δ {_money(d.get('net_dollar_delta'))})."
        )

    new = rep.get("new", [])
    fading = rep.get("fading", [])
    if new:
        out.append(f"🆕 Newly on the accumulation board: {', '.join(new[:10])}.")
    if fading:
        out.append(f"⚠️ Dropping off (fading): {', '.join(fading[:10])}.")
    return out


# ── HTML rendering (pure) ──────────────────────────────────────────────

_CSS = """
:root{--bg:#0e1117;--card:#161b22;--line:#232a34;--txt:#e6edf3;--mut:#8b949e;
--grn:#3fb950;--red:#f85149;--blu:#4c9be8;--amb:#d29922}
*{box-sizing:border-box}body{background:var(--bg);color:var(--txt);margin:0;
font:14px/1.5 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif}
.wrap{max-width:1080px;margin:0 auto;padding:28px}
h1{font-size:22px;margin:0 0 2px}.sub{color:var(--mut);margin:0 0 20px}
h2{font-size:15px;text-transform:uppercase;letter-spacing:.06em;color:var(--mut);
margin:26px 0 10px;border-bottom:1px solid var(--line);padding-bottom:6px}
.find{background:var(--card);border:1px solid var(--line);border-left:3px solid var(--blu);
border-radius:8px;padding:14px 18px;margin:0 0 8px}
.find ul{margin:0;padding-left:18px}.find li{margin:5px 0}
table{width:100%;border-collapse:collapse;font-size:13px;background:var(--card);
border:1px solid var(--line);border-radius:8px;overflow:hidden}
th,td{padding:7px 10px;text-align:right;border-bottom:1px solid var(--line)}
th{color:var(--mut);font-weight:600;text-transform:uppercase;font-size:11px;letter-spacing:.04em}
th:first-child,td:first-child{text-align:left}
tr:last-child td{border-bottom:none}
.pos{color:var(--grn)}.neg{color:var(--red)}.mut{color:var(--mut)}
.chip{display:inline-block;background:var(--card);border:1px solid var(--line);
border-radius:12px;padding:3px 10px;margin:3px 4px 3px 0;font-size:12px}
.chip.new{border-color:var(--grn);color:var(--grn)}
.chip.fade{border-color:var(--amb);color:var(--amb)}
.foot{color:var(--mut);font-size:12px;margin-top:26px;
border-top:1px solid var(--line);padding-top:12px}
.exp{opacity:.55}
"""


def _trend_table(rows: list[dict], *, limit: int) -> str:
    head = (
        "<tr><th>Root</th><th>Score</th><th>Δ vs prior</th><th>Streak</th>"
        "<th>Net $Δ (win)</th><th>Days</th></tr>"
    )
    body = []
    for t in rows[:limit]:
        rs = t.get("recent_score") or 0.0
        sd = t.get("score_delta") or 0.0
        nd = t.get("net_dollar_delta")
        body.append(
            "<tr>"
            f"<td>{_esc(t['root'])}</td>"
            f"<td class='{_cls(rs)}'>{rs:.0f}</td>"
            f"<td class='{_cls(sd)}'>{sd:+.0f}</td>"
            f"<td>{_streak(t.get('streak_days'))}</td>"
            f"<td class='{_cls(nd)}'>{_money(nd)}</td>"
            f"<td class='mut'>{t.get('days_observed')}</td>"
            "</tr>"
        )
    return f"<table>{head}{''.join(body)}</table>"


def _contract_table(rows: list[dict], *, as_of: str) -> str:
    head = (
        "<tr><th>Root</th><th>Contract</th><th>Days</th><th>Notional</th>"
        "<th>Cum $Δ</th><th>Side</th></tr>"
    )
    body = []
    for c in rows:
        exp = c.get("expiry") or "—"
        expired = bool(isinstance(exp, str) and as_of and exp < as_of)
        row_cls = " class='exp'" if expired else ""
        contract = f"{exp} {c.get('strike')}{c.get('cp')}" + (" ⏱" if expired else "")
        side = c.get("build_side") or "—"
        side_cls = _cls(1 if side == "accumulation" else (-1 if side == "distribution" else 0))
        cum = c.get("cum_net_dollar_delta")
        body.append(
            f"<tr{row_cls}>"
            f"<td>{_esc(c['root'])}</td>"
            f"<td>{_esc(contract)}</td>"
            f"<td class='mut'>{c.get('days_seen')}</td>"
            f"<td>{_money(c.get('total_notional'))}</td>"
            f"<td class='{_cls(cum)}'>{_money(cum)}</td>"
            f"<td class='{side_cls}'>{_esc(side)}</td>"
            "</tr>"
        )
    return f"<table>{head}{''.join(body)}</table>"


def render_html(rep: dict[str, Any], *, llm_note: str | None = None) -> str:
    as_of = rep.get("as_of", "")
    trend = rep.get("trend", [])
    accum = [t for t in trend if (t.get("recent_score") or 0) >= 20]
    distrib = sorted(
        (t for t in trend if (t.get("recent_score") or 0) <= -20),
        key=lambda t: t.get("recent_score") or 0,
    )
    finds = "".join(f"<li>{_esc(f)}</li>" for f in key_findings(rep))
    note = f"<div class='find'>{_esc(llm_note)}</div>" if llm_note else ""
    new_chips = "".join(f"<span class='chip new'>{_esc(n)}</span>" for n in rep.get("new", [])[:24])
    fade_chips = "".join(
        f"<span class='chip fade'>{_esc(n)}</span>" for n in rep.get("fading", [])[:24]
    )
    n_names = rep.get("count", {}).get("trend", 0)
    sub = (
        f"Option-tape accumulation / distribution · {rep.get('lookback_days')}-session "
        f"lookback · as of {_esc(as_of)} · {n_names} names · durable roll-up"
    )
    foot = (
        "Descriptive ranking only (FlashAlpha rule 4) — not a trade signal. "
        "Net $Δ = signed option-delta notional (buy +, sell -). "
        "Score in [-100,+100]: 0.45·net-Δ + 0.35·persistence + 0.20·buy-tilt. "
        "Source: tas_daily_flow / tas_daily_contract."
    )
    return (
        '<!doctype html><html><head><meta charset="utf-8">'
        f"<title>EOD Flow Report — {_esc(as_of)}</title><style>{_CSS}</style></head>"
        '<body><div class="wrap">'
        "<h1>EOD Flow Report</h1>"
        f'<p class="sub">{sub}</p>'
        "<h2>Key findings</h2>"
        f'<div class="find"><ul>{finds}</ul></div>{note}'
        "<h2>Accumulation leaders</h2>"
        f"{_trend_table(accum, limit=15)}"
        "<h2>Distribution leaders</h2>"
        f"{_trend_table(distrib, limit=15)}"
        "<h2>Notable contract builds "
        '<span class="mut">(⏱ = already expired within window)</span></h2>'
        f"{_contract_table(rep.get('contracts', []), as_of=as_of)}"
        "<h2>Rotation</h2>"
        f'<div><b class="pos">New on board</b><br>{new_chips or "—"}</div>'
        '<div style="margin-top:12px">'
        f'<b style="color:var(--amb)">Fading</b><br>{fade_chips or "—"}</div>'
        f'<p class="foot">{foot}</p>'
        "</div></body></html>"
    )


# ── build (DB → report → file) ─────────────────────────────────────────


def _llm_note(rep: dict[str, Any], llm: object, settings: object) -> str | None:
    """Optional local-LLM narrative; degrades silently (rule 7 — no cloud LLM)."""
    if llm is None:
        return None
    try:
        model = getattr(settings, "LLM_DAILY_MODEL", None)
        findings = "\n".join(f"- {f}" for f in key_findings(rep))
        prompt = (
            "You are a derivatives-flow analyst. In 2-3 sentences, summarize the "
            "most important option-tape accumulation/distribution findings for a "
            "trader. Be specific and neutral; do NOT give trade advice.\n\n"
            f"Findings:\n{findings}"
        )
        return llm.complete(prompt, model=model).strip() or None  # type: ignore[attr-defined]
    except Exception:  # pragma: no cover - narrative is best-effort
        return None


def build(
    *,
    lookback_days: int = 21,
    recent_days: int = 5,
    min_notional: float = 1_000_000.0,
    top: int = 25,
    db_url: str | None = None,
    llm: object = None,
    settings: object = None,
) -> Path:
    """Build the EOD flow report and return the written HTML path."""
    settings = settings or get_settings()
    url = db_url or settings.DATABASE_URL  # type: ignore[attr-defined]
    engine = create_engine(url, pool_pre_ping=True)
    with Session(engine) as session:
        rep = build_flow_report(
            session,
            lookback_days=lookback_days,
            recent_days=recent_days,
            min_notional=min_notional,
            top=top,
        )
    note = _llm_note(rep, llm, settings)
    _OUT.mkdir(parents=True, exist_ok=True)
    path = _OUT / f"flow_{rep.get('as_of', date.today().isoformat())}.html"
    path.write_text(render_html(rep, llm_note=note), encoding="utf-8")
    return path


def main() -> None:
    ap = argparse.ArgumentParser(description="EOD option-tape flow report")
    ap.add_argument("--lookback", type=int, default=21)
    ap.add_argument("--recent", type=int, default=5)
    ap.add_argument("--min-notional", type=float, default=1_000_000.0)
    ap.add_argument("--top", type=int, default=25)
    args = ap.parse_args()
    path = build(
        lookback_days=args.lookback,
        recent_days=args.recent,
        min_notional=args.min_notional,
        top=args.top,
    )
    print(f"wrote {path}")


if __name__ == "__main__":
    main()
