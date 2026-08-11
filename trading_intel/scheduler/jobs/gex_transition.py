"""Scheduled job (EOD): bank the SPX GEX-transition state series.

Computes the dealer-gamma "quiet unwind" state each session and upserts it into
``gex_transition_daily``. Reads only banked data through the existing read-only
tool fns — net GEX (EOD) via ``get_gamma_history``, clean ATM IV via
``get_iv_tenor`` — and the pure state machine in ``market.gex_transition``.

Idempotent (``ON CONFLICT (symbol, ts) DO UPDATE``). Running it daily both banks
today's row and *backfills* every session in the window that isn't stored yet,
so the series is as complete as the underlying data allows and never re-gaps.
Forward returns (fwd5/10/21) are filled in as later sessions arrive.

Descriptor / research track only (FlashAlpha rule 4) — writes to
``gex_transition_daily``, never to ``signals``.

Manual run:
    python -m trading_intel.scheduler.jobs.gex_transition            # daily update
    python -m trading_intel.scheduler.jobs.gex_transition --backfill # full window
"""

from __future__ import annotations

import argparse

import structlog
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from trading_intel.config import Settings, get_settings
from trading_intel.market.gex_transition import compute
from trading_intel.memory.models import GexTransitionDaily

log = structlog.get_logger(__name__)

_SYMBOL = "SPX"
_TENOR = 30
_UPDATE_COLS = (
    "net_gex",
    "d_gex",
    "d_gex_z",
    "atm_iv",
    "d_iv_pt",
    "state",
    "spot",
    "flip",
    "fwd5",
    "fwd10",
    "fwd21",
)


def _forward_returns(rows: list) -> dict:
    """{iso_date -> (fwd5, fwd10, fwd21)} from the ordered EOD spot series."""
    spots = [(r.date, r.spot) for r in rows]
    out: dict[str, tuple[float | None, float | None, float | None]] = {}
    for i, (d, sp) in enumerate(spots):
        vals: list[float | None] = []
        for n in (5, 10, 21):
            j = i + n
            if sp and j < len(spots) and spots[j][1]:
                vals.append(spots[j][1] / sp - 1.0)
            else:
                vals.append(None)
        out[d.isoformat()] = (vals[0], vals[1], vals[2])
    return out


def run(
    session: Session,
    settings: Settings | None = None,
    *,
    days: int = 120,
    backfill: bool = False,
) -> int:
    """Compute + upsert the transition series. Returns rows written."""
    from trading_intel.mcp.extra_tools import get_iv_tenor
    from trading_intel.mcp.tools import get_gamma_history

    settings = settings or get_settings()
    win = 365 if backfill else max(30, int(days))

    gamma = get_gamma_history(session, _SYMBOL, days=win).get("rows") or []
    iv = get_iv_tenor(session, symbols=[_SYMBOL], tenor_dte=_TENOR, days=win).get("rows") or []
    if not gamma:
        log.warning("gex_transition.no_gamma", symbol=_SYMBOL)
        return 0

    res = compute(gamma, iv, tenor_dte=_TENOR)
    fwd = _forward_returns(res.rows)

    written = 0
    for r in res.rows:
        f5, f10, f21 = fwd.get(r.date.isoformat(), (None, None, None))
        payload = {
            "symbol": _SYMBOL,
            "ts": r.date,
            "net_gex": r.net_gex,
            "d_gex": r.d_gex,
            "d_gex_z": r.d_gex_z,
            "atm_iv": r.atm_iv,
            "d_iv_pt": r.d_iv_pt,
            "state": r.state,
            "spot": r.spot,
            "flip": r.flip,
            "fwd5": f5,
            "fwd10": f10,
            "fwd21": f21,
            "source": "gamma_history+iv_tenor",
        }
        stmt = pg_insert(GexTransitionDaily).values(**payload)
        stmt = stmt.on_conflict_do_update(
            constraint="uq_gex_transition_daily",
            set_={c: getattr(stmt.excluded, c) for c in _UPDATE_COLS},
        )
        session.execute(stmt)
        written += 1
    session.commit()
    latest = res.latest
    log.info(
        "gex_transition.done",
        rows=written,
        n_changes=res.n_changes,
        latest_state=(latest.state if latest else None),
        latest_date=(latest.date.isoformat() if latest else None),
    )
    return written


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--backfill", action="store_true", help="Recompute the full 365d window.")
    ap.add_argument("--days", type=int, default=120)
    args = ap.parse_args()

    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ]
    )
    from trading_intel.memory.db import make_session_factory

    settings = get_settings()
    with make_session_factory(settings)() as session:
        n = run(session, settings, days=args.days, backfill=args.backfill)
    print(f"gex_transition: {n} rows upserted")


if __name__ == "__main__":
    main()
