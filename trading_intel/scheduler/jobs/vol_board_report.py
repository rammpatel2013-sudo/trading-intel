"""DSM job wrapper: build the per-name VOL BOARD(s) and push to Telegram.

DSM task = ``bash scripts/nas/run_job.sh vol_board_report``. Layout lives in
``scripts/vol_board_report.py``.

Symbols come from ``VOL_BOARD_SYMBOLS`` in ``.env`` (comma list); the board is
CVForge-fed and rebuilds ~1y of constant-maturity IV per name from ``/mas``
contract closes, so keep the list short -- it is a focus list, not the
watchlist. Descriptor only (rule 4).
"""

from __future__ import annotations

import structlog

log = structlog.get_logger(__name__)

_DEFAULT_SYMBOLS = ("SPY", "QQQ")


def _symbols(settings) -> list[str]:  # noqa: ANN001
    raw = getattr(settings, "VOL_BOARD_SYMBOLS", "") or ""
    syms = [s.strip().upper() for s in str(raw).split(",") if s.strip()]
    return syms or list(_DEFAULT_SYMBOLS)


def run(session=None, settings=None, *, push: bool = True, symbols=None) -> str:
    """Build a vol board per symbol and push each to Telegram."""
    from trading_intel.config import get_settings
    from trading_intel.reports import build_vol_board

    settings = settings or get_settings()

    tg = None
    if push:
        from trading_intel.clients.telegram import TelegramClient

        tg = TelegramClient(settings)

    written: list[str] = []
    for sym in (symbols or _symbols(settings)):
        try:
            path = build_vol_board(sym, settings=settings)
        except Exception as exc:  # noqa: BLE001 - one bad name must not kill the run
            log.warning("vol_board_report.failed", symbol=sym, error=str(exc)[:160])
            continue
        written.append(path)
        if tg is not None:
            sent = tg.send_document(
                path, caption=f"{sym} Vol Board — spot/skew30/fixed-strike vol, realized skew, CM term"
            )
            log.info("vol_board_report.pushed", symbol=sym, path=path, telegram_sent=sent)
    return ", ".join(written)


def main() -> None:
    structlog.configure(processors=[structlog.processors.add_log_level,
                                    structlog.processors.TimeStamper(fmt="iso"),
                                    structlog.processors.JSONRenderer()])
    print(f"vol boards written: {run()}")


if __name__ == "__main__":
    main()
