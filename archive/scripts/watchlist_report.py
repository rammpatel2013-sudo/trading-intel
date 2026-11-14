"""Standalone HTML watchlist overview (re-openable dashboard).

Queries the watchlist regime descriptors from the database and writes a
dark-themed HTML table to ``reports/watchlist.html`` — a glanceable page you can
keep open and refresh (re-run, or let the daily job regenerate it). Mirrors the
pattern of ``scripts/dashboard_vol_surface.py``; the DB-backed Streamlit page
(``pages/3_Watchlist.py``) is the interactive counterpart.

Regime descriptors only — no signals (FlashAlpha rule 4).

Run (Windows; DATABASE_URL pointed at the NAS):
    .venv\\Scripts\\python scripts\\watchlist_report.py
    .venv\\Scripts\\python scripts\\watchlist_report.py --out reports\\watchlist.html
"""
from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

from sqlalchemy.exc import SQLAlchemyError

from trading_intel.config import get_settings
from trading_intel.dashboard.watchlist_metrics import format_display, load_watchlist_metrics
from trading_intel.memory.db import make_session_factory

_STYLE = """
body { background:#0e1117; color:#e6e6e6; font-family:system-ui,Arial,sans-serif;
       margin:0; padding:24px; }
h1 { font-size:20px; margin:0 0 4px; }
.sub { color:#8b97a7; font-size:13px; margin:0 0 18px; }
table { border-collapse:collapse; width:100%; font-size:13px; }
th, td { padding:7px 10px; text-align:right; border-bottom:1px solid #2a3550; }
th { background:#1f2a44; color:#fff; position:sticky; top:0; text-align:right; }
td:first-child, th:first-child { text-align:left; font-weight:600; }
tr:hover td { background:#161d2e; }
.note { color:#8b97a7; font-size:12px; margin-top:16px; line-height:1.5; }
"""


def render_html(table_html: str, *, generated: str) -> str:
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<title>Watchlist overview</title>"
        f"<style>{_STYLE}</style></head><body>"
        "<h1>Watchlist overview</h1>"
        f"<p class='sub'>Regime descriptors &mdash; generated {generated}</p>"
        f"{table_html}"
        "<p class='note'>Descriptors only, not signals (FlashAlpha rule). "
        "Gamma regime: spot below the flip = dealers short gamma (move-amplifying); "
        "above = long gamma (move-damping). gamma-conc +/-3% = share of gamma-OI within "
        "+/-3% of spot. &Delta;GEX (1wk), skew and wall drift fill in as history accrues."
        "</p></body></html>"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Standalone HTML watchlist overview.")
    parser.add_argument("--out", default=None)
    args = parser.parse_args()

    settings = get_settings()
    session_factory = make_session_factory(settings)
    try:
        with session_factory() as session:
            metrics = load_watchlist_metrics(session, settings.watchlist_symbols)
    except SQLAlchemyError as exc:
        raise SystemExit(f"Could not load watchlist metrics: {exc}") from exc

    display = format_display(metrics)
    table_html = display.to_html(index=False, border=0, justify="right", na_rep="n/a")
    generated = datetime.now().strftime("%Y-%m-%d %H:%M")
    html = render_html(table_html, generated=generated)

    out = Path(args.out) if args.out else Path("reports") / "watchlist.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    print(f"Wrote {out} ({len(metrics)} symbols).")


if __name__ == "__main__":
    main()
