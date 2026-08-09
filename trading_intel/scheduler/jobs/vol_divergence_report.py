"""DSM job wrapper: build the SPX 1-Day Vol Divergence report and push to Telegram.

Layout + compute live in ``scripts/vol_divergence_report.py`` (loaded at runtime so
a tweak deploys on a NAS tarball pull with no image rebuild). This opens a DB
session, builds the HTML, rasterizes a PNG preview (best-effort), and posts both to
the Telegram bot. Descriptor only (rule 4).

Manual run:
    python -m trading_intel.scheduler.jobs.vol_divergence_report            # build + push
    python -m trading_intel.scheduler.jobs.vol_divergence_report --no-push  # build only
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import structlog

log = structlog.get_logger(__name__)


def _load_report():
    root = Path(__file__).resolve().parents[3]
    path = root / "scripts" / "vol_divergence_report.py"
    spec = importlib.util.spec_from_file_location("vol_divergence_report", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _rasterize(html_path: Path) -> Path | None:
    try:
        from playwright.sync_api import sync_playwright
    except Exception:  # noqa: BLE001
        return None
    png = html_path.with_suffix(".png")
    try:
        with sync_playwright() as p:
            b = p.chromium.launch(args=["--no-sandbox"])
            pg = b.new_page(viewport={"width": 430, "height": 900}, device_scale_factor=2)
            pg.goto(f"file://{html_path}")
            pg.wait_for_timeout(350)
            pg.screenshot(path=str(png), full_page=True)
            b.close()
        return png
    except Exception as exc:  # noqa: BLE001
        log.warning("vol_divergence.rasterize_failed", err=str(exc))
        return None


def run(session=None, settings=None, *, push: bool = True) -> str:
    from trading_intel.config import get_settings
    from trading_intel.memory.db import make_session_factory

    settings = settings or get_settings()
    rpt = _load_report()
    if session is not None:
        path = rpt.build(session, settings=settings)
    else:
        sf = make_session_factory(settings)
        with sf() as s:
            path = rpt.build(s, settings=settings)
    path = Path(path)

    if push:
        from trading_intel.clients.telegram import TelegramClient

        tg = TelegramClient(settings)
        png = _rasterize(path)
        photo_sent = tg.send_photo(png, caption="SPX 1-Day Vol Divergence — EOD") if png else False
        doc_sent = tg.send_document(path, caption="" if photo_sent else "SPX 1-Day Vol Divergence — EOD")
        log.info("vol_divergence.pushed", path=str(path), photo=photo_sent, doc=doc_sent)
    return str(path)


def main() -> None:
    structlog.configure(processors=[structlog.processors.add_log_level,
                                    structlog.processors.TimeStamper(fmt="iso"),
                                    structlog.processors.JSONRenderer()])
    push = "--no-push" not in sys.argv[1:]
    print(f"vol divergence written: {run(push=push)}")


if __name__ == "__main__":
    main()
