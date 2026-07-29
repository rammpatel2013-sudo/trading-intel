"""Read the three core Jaguar emails straight from Gmail at brief time.

Rather than depend on the ephemeral letters-dir ingest, the brief job pulls the latest
JaguarLive, First Read and Trade Alert directly (reusing the :mod:`letters.gmail_source`
service + walk helpers, and the same ``includeSpamTrash`` so it works after the owner
deletes them). Returns each type's plaintext body; public wp-content PDF text is pulled
best-effort via :func:`pdf_text`. Read-only; the gated ``mp-files`` links are never
touched. Degrades to ``{}`` when Gmail is unavailable so the job never crashes.
"""

from __future__ import annotations

import io

import structlog

from trading_intel.letters.gmail_source import (
    _download_pdf,
    _header,
    _service,
    _walk_body_and_attachments,
)

log = structlog.get_logger(__name__)

#: subject-prefix → core type. First match wins.
_KINDS: tuple[tuple[str, str], ...] = (
    ("jaguarlive", "jaguarlive"),
    ("first read", "first_read"),
    ("trade alert", "trade_alert"),
)


def _classify(subject: str) -> str | None:
    low = (subject or "").lower()
    for prefix, kind in _KINDS:
        if low.startswith(prefix):
            return kind
    return None


def pdf_text(url: str, *, max_pages: int = 12) -> str:
    """Extract text from a public PDF link (best-effort, capped). '' on any failure."""
    data = _download_pdf(url)
    if not data:
        return ""
    try:
        import pdfplumber

        with pdfplumber.open(io.BytesIO(data)) as pdf:
            pages = pdf.pages[:max_pages]
            return "\n".join((p.extract_text() or "") for p in pages).strip()
    except Exception:
        return ""


def fetch_core(settings: object, *, days: int = 3, max_messages: int = 60) -> dict[str, dict]:
    """Latest JaguarLive / First Read / Trade Alert, each as ``{subject, body, ts}``.

    Newest of each type within the window (Trash included). ``{}`` if Gmail is down.
    """
    svc = _service(settings)
    if svc is None:
        return {}
    q = f"from:jaguaranalytics.com newer_than:{max(1, int(days))}d"
    listing = (
        svc.users()
        .messages()
        .list(userId="me", q=q, maxResults=max_messages, includeSpamTrash=True)
        .execute()
    )
    best: dict[str, dict] = {}
    for ref in listing.get("messages", []):
        msg = svc.users().messages().get(userId="me", id=ref["id"], format="full").execute()
        payload = msg.get("payload", {})
        kind = _classify(_header(payload, "Subject"))
        if kind is None:
            continue
        ts = int(msg.get("internalDate", 0))
        if kind in best and best[kind]["ts"] >= ts:
            continue
        body, _ = _walk_body_and_attachments(payload)
        best[kind] = {"subject": _header(payload, "Subject"), "body": body, "ts": ts}
    log.info("jaguar.fetch_core", kinds=sorted(best))
    return best
