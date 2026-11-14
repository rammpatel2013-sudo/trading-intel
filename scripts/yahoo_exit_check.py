"""Yahoo exit check (2026-11-13 overnight). Run on the NAS after the ship:
  sudo bash /var/services/homes/drmithil/trading-intel/scripts/nas/run_script.sh yahoo_exit_check
For every symbol in quotes_daily: FMP's last 30 closes vs the stored (Yahoo) bars -> OK / DIFF / FMP-MISSING.
When every line is OK, set PRICES_YAHOO_FALLBACK=false in the NAS .env (step 2 then removes the
other Yahoo pulls: Cboe index fallbacks, sector correlation, beta reset, vol tab, factor prices,
dashboard pages - list printed at the end)."""
from __future__ import annotations

from sqlalchemy import select

from trading_intel.clients.cvforge import CVForgeClient
from trading_intel.clients.fmp_prices import FmpPriceSource
from trading_intel.config import get_settings
from trading_intel.memory.db import make_session_factory
from trading_intel.memory.models import QuoteDaily

OTHER_YAHOO = ["clients/prices.py fetch_yf_index_close (Cboe fallbacks)", "scheduler/jobs/sector_corr.py",
               "scheduler/jobs/beta_reset.py", "scheduler/jobs/vol_tab.py", "market/factor_prices.py",
               "market/sector_correlation.py", "api/sector.py", "reports.py", "dashboard pages 1/6/14/15/16",
               "dashboard/vix_history_data.py", "scripts/sector_report.py"]


def main() -> None:
    s = get_settings()
    fmp = FmpPriceSource(CVForgeClient(s))
    ok = 0
    with make_session_factory(s)() as session:
        syms = sorted({r[0] for r in session.execute(select(QuoteDaily.symbol).distinct())})
        for sym in syms:
            stored = {q.date: q.close for q in session.execute(select(QuoteDaily).where(
                QuoteDaily.symbol == sym).order_by(QuoteDaily.date.desc()).limit(30)).scalars()}
            try:
                df = fmp.daily_history(sym, period="3mo")
            except Exception as exc:  # noqa: BLE001
                print(f"{sym:8s} FMP-ERROR {exc}")
                continue
            if df.empty:
                print(f"{sym:8s} FMP-MISSING")
                continue
            got = {d.date(): c for d, c in zip(df["date"], df["close"])}
            common = [d for d in stored if d in got]
            worst = max((abs(got[d] / stored[d] - 1) for d in common if stored[d]), default=None)
            flag = "OK" if worst is not None and worst < 0.002 else "DIFF"
            ok += flag == "OK"
            print(f"{sym:8s} {flag:4s} days={len(common):2d} worst={worst if worst is None else round(100 * worst, 3)}%")
        print(f"\n{ok}/{len(syms)} symbols match. Other Yahoo pulls for step 2:")
        for o in OTHER_YAHOO:
            print("  -", o)


if __name__ == "__main__":
    main()
