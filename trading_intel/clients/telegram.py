"""Telegram delivery — push digests / reports to a chat via a bot.

Mirrors ``clients/discord.py``: a bot token + chat_id from ``Settings`` and thin Bot API
calls. ``send_message`` pushes the digest text (HTML), ``send_document`` attaches the
report file (HTML / PDF). Degrades to a no-op (returns ``False``) when unconfigured, so a
missing token never breaks a job.

One-time setup: create a bot with ``@BotFather`` -> ``TELEGRAM_BOT_TOKEN``; message the
bot once and read the chat id from ``https://api.telegram.org/bot<token>/getUpdates`` ->
``TELEGRAM_CHAT_ID``. Both live in ``.env`` (rule 2 — secrets only in .env).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import httpx
import structlog

from trading_intel.config import Settings

log = structlog.get_logger(__name__)

_API = "https://api.telegram.org/bot{token}/{method}"

# Reports whose Telegram push is OFF (Mithil, 2026-11-05: the cockpit tabs show them now).
# The job still runs and still writes its files and DB rows — only the push is skipped.
# Override without a code change: TELEGRAM_MUTE="a,b" adds jobs, TELEGRAM_UNMUTE="a,b" removes.
MUTED_JOBS = frozenset({
    "breadth_report",
    "vol_board_report",
    "sector_report",
    "flow_report",
    "eod_vol_report",
    "skew_vanna_report",
    "cockpit_report",
    "vol_regime_report",
})


def _env_set(name: str) -> set[str]:
    return {x.strip() for x in os.environ.get(name, "").split(",") if x.strip()}


def job_name() -> str:
    """Name of the running job: ``python -m pkg.jobs.<name>`` or ``python scripts/<name>.py``."""
    main = sys.modules.get("__main__")
    spec = getattr(main, "__spec__", None)
    if spec is not None and getattr(spec, "name", None):
        return spec.name.rsplit(".", 1)[-1]
    return Path(sys.argv[0]).stem if sys.argv and sys.argv[0] else ""


def is_muted(job: str | None = None) -> bool:
    j = job if job is not None else job_name()
    muted = (set(MUTED_JOBS) | _env_set("TELEGRAM_MUTE")) - _env_set("TELEGRAM_UNMUTE")
    return bool(j) and j in muted
_MAX_TEXT = 4096  # Telegram sendMessage cap
_MAX_CAPTION = 1024


class TelegramClient:
    """Thin Telegram Bot API sender (message + document)."""

    def __init__(self, settings: Settings) -> None:
        # TELEGRAM_BOT_TOKEN is a SecretStr; unwrap it. Empty -> None -> no-op.
        token = getattr(settings, "TELEGRAM_BOT_TOKEN", None)
        if hasattr(token, "get_secret_value"):
            token = token.get_secret_value()
        self._token: str | None = token or None
        chat = getattr(settings, "TELEGRAM_CHAT_ID", None)
        self._chat_id: str | None = str(chat) if chat else None

    @property
    def enabled(self) -> bool:
        return bool(self._token and self._chat_id)

    def send_message(self, text: str, *, parse_mode: str = "HTML", preview: bool = False) -> bool:
        """Send a text message (HTML by default). No-op + ``False`` if unconfigured."""
        if is_muted():
            log.info("telegram.muted", job=job_name())
            return False
        if not self.enabled:
            log.debug("telegram.disabled")
            return False
        url = _API.format(token=self._token, method="sendMessage")
        payload = {
            "chat_id": self._chat_id,
            "text": text[:_MAX_TEXT],
            "parse_mode": parse_mode,
            "disable_web_page_preview": not preview,
        }
        try:
            with httpx.Client(timeout=20.0) as client:
                resp = client.post(url, json=payload)
            ok = resp.status_code == 200
            if not ok:
                log.warning(
                    "telegram.send_message_failed", status=resp.status_code, body=resp.text[:200]
                )
            return ok
        except httpx.HTTPError as exc:
            log.warning("telegram.send_message_error", err=str(exc))
            return False

    def send_document(self, path: str | Path, *, caption: str = "") -> bool:
        """Attach a file (the report HTML/PDF). No-op + ``False`` if unconfigured/missing."""
        if is_muted():
            log.info("telegram.muted", job=job_name())
            return False
        p = Path(path)
        if not self.enabled or not p.is_file():
            return False
        url = _API.format(token=self._token, method="sendDocument")
        try:
            with httpx.Client(timeout=60.0) as client, p.open("rb") as fh:
                resp = client.post(
                    url,
                    data={"chat_id": self._chat_id, "caption": caption[:_MAX_CAPTION]},
                    files={"document": (p.name, fh)},
                )
            ok = resp.status_code == 200
            if not ok:
                log.warning(
                    "telegram.send_document_failed", status=resp.status_code, body=resp.text[:200]
                )
            return ok
        except (httpx.HTTPError, OSError) as exc:
            log.warning("telegram.send_document_error", err=str(exc))
            return False

    def send_photo(self, path: str | Path, *, caption: str = "") -> bool:
        """Send an image (report PNG preview) so it renders inline in Telegram."""
        if is_muted():
            log.info("telegram.muted", job=job_name())
            return False
        p = Path(path)
        if not self.enabled or not p.is_file():
            return False
        url = _API.format(token=self._token, method="sendPhoto")
        try:
            with httpx.Client(timeout=60.0) as client, p.open("rb") as fh:
                resp = client.post(
                    url,
                    data={"chat_id": self._chat_id, "caption": caption[:_MAX_CAPTION], "parse_mode": "HTML"},
                    files={"photo": (p.name, fh)},
                )
            ok = resp.status_code == 200
            if not ok:
                log.warning("telegram.send_photo_failed", status=resp.status_code, body=resp.text[:200])
            return ok
        except (httpx.HTTPError, OSError) as exc:
            log.warning("telegram.send_photo_error", err=str(exc))
            return False
