"""Shared plumbing for DSM job wrappers around ``scripts/<name>_report.py``.

Report layouts live in ``scripts/`` (bind-mounted on the NAS, so a layout tweak
deploys on a tarball pull). A wrapper only needs to load that module, call its
``build``, and push the file to Telegram -- these two helpers do exactly that so
each wrapper stays a few lines. Descriptor only (rule 4).
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import structlog

log = structlog.get_logger(__name__)


def load_script(name: str) -> ModuleType:
    """Import ``scripts/<name>.py`` by path (scripts/ is not a package)."""
    path = Path(__file__).resolve().parents[3] / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"_script_{name}", path)
    if spec is None or spec.loader is None:  # pragma: no cover - path is fixed
        raise FileNotFoundError(path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def push(path: str | Path, caption: str, settings) -> bool:
    """Send one built report to Telegram; never raises (a failed push is logged)."""
    try:
        from trading_intel.clients.telegram import TelegramClient

        sent = bool(TelegramClient(settings).send_document(str(path), caption=caption))
    except Exception as exc:
        log.warning("script_report.push_failed", path=str(path), err=str(exc))
        return False
    log.info("script_report.pushed", path=str(path), telegram_sent=sent)
    return sent


def configure_logging() -> None:
    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ]
    )
