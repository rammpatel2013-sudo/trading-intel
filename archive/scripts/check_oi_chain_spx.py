"""Quick data check: how much SPX history is in oi_chain_eod, and is it usable
for the VIX decomposition (needs >=2 consecutive days with populated iv + delta,
and strikes spanning the belly/shoulders/wings around the ~30-day expiries).

Run:
    .venv\\Scripts\\python scripts\\check_oi_chain_spx.py
"""

from __future__ import annotations

from sqlalchemy import func, select

from trading_intel.config import get_settings
from trading_intel.memory.db import make_session_factory
from trading_intel.memory.models import OiChainEod

SYMBOL = "SPX"


def main() -> None:
    session = make_session_factory(get_settings())()
    try:
        total, have_iv, have_delta = session.execute(
            select(
                func.count(),
                func.count(OiChainEod.iv),
                func.count(OiChainEod.delta),
            ).where(OiChainEod.symbol == SYMBOL)
        ).one()

        days = list(
            session.execute(
                select(OiChainEod.ts).where(OiChainEod.symbol == SYMBOL).distinct().order_by(OiChainEod.ts)
            ).scalars()
        )

        print(f"symbol={SYMBOL}")
        print(f"  rows={total}  have_iv={have_iv}  have_delta={have_delta}")
        print(f"  distinct snapshot days={len(days)}")
        for d in days[-5:]:
            print(f"    {d}")

        if days:
            latest = days[-1]
            # DTE coverage on the latest snapshot: do we bracket 30 days?
            dtes = sorted(
                {
                    int(x)
                    for x in session.execute(
                        select(OiChainEod.dte)
                        .where(OiChainEod.symbol == SYMBOL, OiChainEod.ts == latest, OiChainEod.dte.is_not(None))
                    ).scalars()
                }
            )
            below = [d for d in dtes if d <= 30]
            above = [d for d in dtes if d >= 30]
            print(f"  latest day DTEs (count={len(dtes)}): "
                  f"near30 below={max(below) if below else None} above={min(above) if above else None}")

        if len(days) >= 2 and have_iv and have_delta:
            print("READY: >=2 days with iv+delta -> can wire the decomposition live.")
        else:
            print("NOT READY: need >=2 EOD snapshots with iv+delta. Run oi_chain_eod on more sessions.")
    finally:
        session.close()


if __name__ == "__main__":
    main()
