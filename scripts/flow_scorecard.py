"""Accumulation / distribution scorecard from the durable daily flow roll-up.

Reads ``tas_daily_flow`` (written by the ``tas_daily_rollup`` job) over a lookback
window, scores each name on persistent net buying (accumulation) vs net selling
(distribution), and prints the leaders at each end. Optionally drills into the
repeat-contract grain for one name, and can promote the strongest accumulation /
distribution names onto the research watchlist.

Descriptive research to guide where to look — NOT trade signals, nothing is written
to the ``signals`` table (FlashAlpha rule 4). The ``--promote`` path applies an
OPTIONABILITY guard (must have a recent ``greeks_snapshots`` row) so it never
re-pollutes the watchlist with non-optionable junk — the failure mode we cleaned up
with ``prune_dead_watchlist.py``.

Run (repo root, venv active):
    python scripts/flow_scorecard.py                      # 20-day scorecard, top 15 each side
    python scripts/flow_scorecard.py --days 30 --min-notional 5e6
    python scripts/flow_scorecard.py --contracts MU       # repeat-contract detail for MU
    python scripts/flow_scorecard.py --promote 5          # add top 5 accumulation to watchlist
    python scripts/flow_scorecard.py --promote 5 --side distribution
    python scripts/flow_scorecard.py --csv                # also write data/tas/scorecard_<date>.csv
"""

from __future__ import annotations

import argparse
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from trading_intel.config import get_settings
from trading_intel.flow.scorecard import build_scorecard
from trading_intel.memory.db import make_session_factory
from trading_intel.memory.models import GreeksSnapshot, TasDailyContract, WatchlistEntry
from trading_intel.timeutils import eastern_now

log = structlog.get_logger(__name__)


def _optionable(session: Session, symbol: str, *, within_days: int = 5) -> bool:
    """True if the name has a greeks_snapshots row recently (i.e. options collect)."""
    cut = eastern_now().replace(tzinfo=None) - timedelta(days=within_days)
    hit = session.execute(
        select(GreeksSnapshot.id)
        .where(GreeksSnapshot.symbol == symbol, GreeksSnapshot.ts >= cut)
        .limit(1)
    ).first()
    return hit is not None


def _promote(session: Session, names: list[str], *, label: str, days: int) -> list[str]:
    """Upsert each optionable name as an active research-watchlist entry."""
    added: list[str] = []
    for sym in names:
        if not _optionable(session, sym):
            log.info("flow_scorecard.skip_non_optionable", symbol=sym)
            continue
        existing = (
            session.execute(
                select(WatchlistEntry).where(
                    WatchlistEntry.symbol == sym,
                    WatchlistEntry.source_doc_id.is_(None),
                )
            )
            .scalars()
            .first()
        )
        direction = "buying" if label == "accumulation" else "selling"
        rationale = f"flow {label} ({days}d tape): persistent net {direction}"
        if existing is not None:
            existing.active = True
            existing.rationale = rationale
        else:
            session.add(
                WatchlistEntry(
                    symbol=sym,
                    source_doc_id=None,
                    rationale=rationale,
                    sentiment=None,
                    confidence=None,
                    themes=["flow", label],
                    active=True,
                )
            )
        added.append(sym)
    session.commit()
    return added


def _fmt(df: pd.DataFrame) -> str:
    show = df[
        [
            "root",
            "accum_score",
            "label",
            "days_observed",
            "days_net_buy",
            "days_net_sell",
            "total_notional",
            "net_dollar_delta",
            "buy_tilt",
        ]
    ].copy()
    show["total_notional"] = show["total_notional"].map(lambda v: f"${v/1e6:,.1f}M")
    show["net_dollar_delta"] = show["net_dollar_delta"].map(lambda v: f"${v/1e6:,.1f}M")
    show["buy_tilt"] = show["buy_tilt"].map(lambda v: f"{v:+.2f}")
    with pd.option_context("display.width", 200, "display.max_columns", 20):
        return show.to_string(index=False)


def main() -> None:
    p = argparse.ArgumentParser(description="Accumulation/distribution flow scorecard.")
    p.add_argument("--days", type=int, default=20, help="lookback window (default 20)")
    p.add_argument(
        "--min-notional",
        type=float,
        default=1_000_000.0,
        help="ignore names below this total premium over the window (default $1M)",
    )
    p.add_argument(
        "--min-days",
        type=int,
        default=2,
        help="ignore names seen on fewer sessions — drops 1-day flukes (default 2)",
    )
    p.add_argument("--top", type=int, default=15, help="rows per side (default 15)")
    p.add_argument("--contracts", help="show repeat-contract detail for this ticker and exit")
    p.add_argument("--promote", type=int, default=0, help="add top N names to the watchlist")
    p.add_argument(
        "--side",
        choices=["accumulation", "distribution"],
        default="accumulation",
        help="which end to promote (default accumulation)",
    )
    p.add_argument("--csv", action="store_true", help="also write the full scorecard to CSV")
    p.add_argument("--out-dir", default="data/tas")
    args = p.parse_args()

    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ]
    )

    session_factory = make_session_factory(get_settings())
    with session_factory() as session:
        if args.contracts:
            sym = args.contracts.strip().upper()
            cut = date.today() - timedelta(days=args.days)
            rows = list(
                session.execute(
                    select(TasDailyContract)
                    .where(TasDailyContract.root == sym, TasDailyContract.trade_date > cut)
                    .order_by(TasDailyContract.total_notional.desc())
                    .limit(40)
                ).scalars()
            )
            if not rows:
                raise SystemExit(
                    f"no tas_daily_contract rows for {sym} in {args.days}d "
                    "(has the rollup job run? try --backfill)"
                )

            def _moneyness(cp: str, strike: float | None, spot: float | None) -> str:
                if not spot or strike is None:
                    return "?"
                pct = (spot - strike) / spot if cp == "C" else (strike - spot) / spot
                tag = "ITM" if pct > 0.01 else ("OTM" if pct < -0.01 else "ATM")
                return f"{tag} {pct*100:+.0f}%"

            def _breakeven(cp: str, strike: float | None, px: float | None) -> float | None:
                if strike is None or px is None:
                    return None
                return strike + px if cp == "C" else strike - px

            df = pd.DataFrame(
                [
                    {
                        "date": r.trade_date,
                        "expiry": r.expiry,
                        "strike": r.strike,
                        "cp": r.cp,
                        "spot": r.spot,
                        "moneyness": _moneyness(r.cp, r.strike, r.spot),
                        "delta": r.avg_delta,
                        "avg_px": r.avg_price,
                        "b/e": _breakeven(r.cp, r.strike, r.avg_price),
                        "n": r.n_prints,
                        "buy": r.buy_prints,
                        "sell": r.sell_prints,
                        "dom": r.dominant_side,
                        "notional": r.total_notional,
                    }
                    for r in rows
                ]
            )
            df["delta"] = df["delta"].map(lambda v: f"{v:.2f}" if pd.notna(v) else "-")
            df["avg_px"] = df["avg_px"].map(lambda v: f"${v:,.2f}" if pd.notna(v) else "-")
            df["b/e"] = df["b/e"].map(lambda v: f"${v:,.2f}" if pd.notna(v) else "-")
            df["spot"] = df["spot"].map(lambda v: f"${v:,.2f}" if pd.notna(v) else "-")
            df["notional"] = df["notional"].map(lambda v: f"${v/1e6:,.1f}M")
            print(f"Repeat-contract detail — {sym} (last {args.days}d, top by premium):")
            print("  b/e = call strike+premium, put strike-premium; moneyness at trade time\n")
            with pd.option_context("display.width", 220, "display.max_columns", 25):
                print(df.to_string(index=False))
            return

        board = build_scorecard(
            session, lookback_days=args.days, min_notional=args.min_notional, min_days=args.min_days
        )
        if board.empty:
            raise SystemExit(
                "scorecard is empty — has tas_daily_rollup run? "
                "(python -m trading_intel.scheduler.jobs.tas_daily_rollup --backfill)"
            )

        accum = board[board["label"] == "accumulation"].head(args.top)
        distrib = board[board["label"] == "distribution"].tail(args.top).iloc[::-1]

        print(f"=== ACCUMULATION (top {len(accum)}, {args.days}d) — persistent net buying ===")
        print(_fmt(accum) if not accum.empty else "  (none)")
        print(f"\n=== DISTRIBUTION (top {len(distrib)}, {args.days}d) — persistent net selling ===")
        print(_fmt(distrib) if not distrib.empty else "  (none)")

        if args.csv:
            out = Path(args.out_dir)
            out.mkdir(parents=True, exist_ok=True)
            path = out / f"scorecard_{date.today().isoformat()}.csv"
            board.to_csv(path, index=False)
            print(f"\nwrote {path}  ({len(board)} names)")

        if args.promote:
            side_df = accum if args.side == "accumulation" else distrib
            names = list(side_df["root"].head(args.promote))
            added = _promote(session, names, label=args.side, days=args.days)
            promoted = ", ".join(added) or "(none passed optionability)"
            print(f"\nPromoted to watchlist ({args.side}): {promoted}")


if __name__ == "__main__":
    main()
