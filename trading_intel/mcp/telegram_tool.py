"""MCP tool: push an on-demand, phone-readable message/report to Telegram.

Wraps the existing ``TelegramClient`` (bot token + chat id from ``.env``) so any
Cowork / Claude session can one-step deliver a note to the bot that opens ON THE
PHONE. Short text -> ``sendMessage`` (renders natively in the Telegram app). A
full read -> pass ``html`` and it is written to a temp file and sent as a
DOCUMENT that opens in the phone's browser.

PHONE RULE (see report-deploy-workflow): any ``html`` you pass MUST be small and
SERVER-SIDE static -- inline SVG, NO client-side <script>, NO CDN -- or it
renders blank in Telegram's in-app viewer. Delivery only (FlashAlpha rule 4).

Wire in server.py:
    from trading_intel.mcp import telegram_tool as tgt
    tgt.register(mcp, settings)
"""
from __future__ import annotations

import re
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from fastmcp import FastMCP

    from trading_intel.config import Settings


def _slug(title: str) -> str:
    s = re.sub(r"[^A-Za-z0-9_.-]+", "_", (title or "note")).strip("_")
    return (s or "note")[:60]


def register(mcp: "FastMCP", settings: "Settings") -> None:
    """Register the ``send_telegram`` tool against the shared settings."""

    @mcp.tool()
    def send_telegram(
        text: str,
        html: str | None = None,
        title: str = "note",
    ) -> dict[str, Any]:
        """Push a phone-readable note to the Telegram bot.

        - ``text``: the message (Telegram HTML formatting ok, e.g. <b>..</b>).
          Sent as a chat message when no ``html`` is given -- renders natively in
          the phone app. Also used as the caption when ``html`` is attached.
        - ``html``: optional full HTML document. Written to a temp file and sent
          as a DOCUMENT that opens in the phone browser. MUST be small + static
          (inline SVG, no <script>, no CDN) to render on mobile.
        - ``title``: filename / caption label for the attached doc.

        Returns ``{ok, sent_document, sent_message}``. No-op ``{ok: False}`` if
        the bot token / chat id are not configured in .env. Delivery only (rule 4).
        """
        from trading_intel.clients.telegram import TelegramClient

        tg = TelegramClient(settings)
        if not tg.enabled:
            return {
                "ok": False,
                "reason": "telegram not configured (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID in .env)",
                "sent_document": None,
                "sent_message": None,
            }

        doc_ok: bool | None = None
        msg_ok: bool | None = None
        if html:
            path = Path(tempfile.gettempdir()) / f"tg_{_slug(title)}.html"
            path.write_text(html, encoding="utf-8")
            doc_ok = tg.send_document(path, caption=text[:1024])
        else:
            msg_ok = tg.send_message(text)

        ok = bool(doc_ok) if html else bool(msg_ok)
        return {"ok": ok, "sent_document": doc_ok, "sent_message": msg_ok}
