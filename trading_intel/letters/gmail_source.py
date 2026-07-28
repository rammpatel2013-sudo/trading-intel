"""Gmail letters lane — pull investor letters from the inbox via the Gmail API.

The PRIMARY letters source (docs/investor_letters_pipeline.md): query the sender
allowlist, save each message's body + PDF attachments under the research letters dir,
then the existing ``memory.watchlist_ingest.ingest_folder`` turns them into
``watchlist_entries`` + knowledge. Runs unattended on the NAS via a stored OAuth token
(setup in the deploy doc) — no interactive step.

Degrades to ``[]`` when the google libraries or the token are missing, so the job never
crashes on an un-provisioned box. Auth scope: ``gmail.readonly``. Secrets/paths live in
``.env`` (rule 2). Read-only — never sends or modifies mail.
"""

from __future__ import annotations

import base64
import re
from pathlib import Path

import httpx
import structlog

from trading_intel.letters.sources import gmail_senders

log = structlog.get_logger(__name__)

_SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
_TAG = re.compile(r"(?s)<[^>]+>")
# Public WordPress-upload PDFs linked in the email body (e.g. Jaguar's daily
# First-Read / research notes). The paywalled ``/mp-files/`` links are NOT matched
# here, and the %PDF sniff below drops any that resolve to a login page anyway.
_LINK_PDF = re.compile(
    r"https?://[^\s)>\"']+?/wp-content/uploads/[^\s)>\"']+?\.pdf", re.I
)
_MAX_PDF_BYTES = 12 * 1024 * 1024  # cap per linked PDF
_MAX_LINK_PDFS = 15  # per message, so a link-heavy daily can't run away


def _download_pdf(url: str) -> bytes | None:
    """GET a public PDF link (size-capped). None on error, non-200, or non-PDF.

    The %PDF magic-byte check means a gated link that redirects to an HTML login
    page returns None instead of saving a junk 'PDF'.
    """
    try:
        with httpx.Client(follow_redirects=True, timeout=30.0) as client:
            resp = client.get(url)
    except (httpx.HTTPError, OSError):
        return None
    if resp.status_code != 200:
        return None
    data = resp.content
    ctype = resp.headers.get("content-type", "").lower()
    if not data.startswith(b"%PDF") and "application/pdf" not in ctype:
        return None
    return data[:_MAX_PDF_BYTES]


def _service(settings: object) -> object | None:
    """Build a Gmail API client from a stored OAuth token, or ``None`` if unavailable."""
    try:
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build
    except ImportError:
        log.warning("gmail.libs_missing", hint="pip install google-api-python-client google-auth")
        return None
    token = getattr(settings, "GMAIL_TOKEN_PATH", None)
    if not token or not Path(token).is_file():
        log.warning("gmail.no_token", path=str(token))
        return None
    creds = Credentials.from_authorized_user_file(token, _SCOPES)
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def _query(days: int) -> str:
    senders = " OR ".join(f"from:{s}" for s in gmail_senders())
    return f"({senders}) newer_than:{max(1, int(days))}d"


def _slug(s: str, n: int = 60) -> str:
    return (re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")[:n]) or "letter"


def _header(payload: dict, name: str) -> str:
    for h in payload.get("headers", []):
        if h.get("name", "").lower() == name.lower():
            return h.get("value", "")
    return ""


def _b64(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def _walk_body_and_attachments(payload: dict) -> tuple[str, list[tuple[str, str]]]:
    """Return (plain-text body, [(attachment_id, filename)]) from a message payload."""
    text_parts: list[str] = []
    attachments: list[tuple[str, str]] = []

    def visit(part: dict) -> None:
        mime = part.get("mimeType", "")
        body = part.get("body", {})
        filename = part.get("filename") or ""
        if (
            filename
            and body.get("attachmentId")
            and (mime == "application/pdf" or filename.lower().endswith(".pdf"))
        ):
            attachments.append((body["attachmentId"], filename))
        elif mime == "text/plain" and body.get("data"):
            text_parts.append(_b64(body["data"]).decode("utf-8", "ignore"))
        elif mime == "text/html" and body.get("data") and not text_parts:
            html = _b64(body["data"]).decode("utf-8", "ignore")
            text_parts.append(_TAG.sub(" ", html))
        for sub in part.get("parts", []) or []:
            visit(sub)

    visit(payload)
    return ("\n".join(text_parts).strip(), attachments)


def fetch_new(
    settings: object, out_dir: Path, *, days: int = 8, max_messages: int = 150
) -> list[Path]:
    """Pull allowlisted messages from the last ``days`` and save body + PDF attachments.

    Each message becomes ``<out_dir>/<sender-slug>/<date>-<subject>.md`` (body) plus any
    ``.pdf`` attachments alongside it; both flow through the existing ingest. Returns the
    list of newly-written paths (skips files that already exist — idempotent).
    """
    svc = _service(settings)
    if svc is None:
        return []
    saved: list[Path] = []
    listing = (
        svc.users().messages().list(userId="me", q=_query(days), maxResults=max_messages).execute()
    )
    for ref in listing.get("messages", []):
        msg = svc.users().messages().get(userId="me", id=ref["id"], format="full").execute()
        payload = msg.get("payload", {})
        sender = _header(payload, "From")
        subject = _header(payload, "Subject")
        date_str = _header(payload, "Date")[:16].replace(",", "").strip() or "nodate"
        body, attachments = _walk_body_and_attachments(payload)

        addr = (re.search(r"[\w.\-+]+@[\w.\-]+", sender) or [None])[0] if sender else None
        fund_dir = out_dir / _slug(addr or sender or "gmail")
        fund_dir.mkdir(parents=True, exist_ok=True)
        base = f"{_slug(date_str, 12)}-{_slug(subject)}"

        md = fund_dir / f"{base}.md"
        if not md.exists() and len(body) > 80:
            md.write_text(
                f"# {subject}\n\nFrom: {sender}\nDate: {date_str}\n\n{body}\n", encoding="utf-8"
            )
            saved.append(md)
        for att_id, filename in attachments:
            dest = fund_dir / f"{base}-{_slug(Path(filename).stem)}.pdf"
            if dest.exists():
                continue
            att = (
                svc.users()
                .messages()
                .attachments()
                .get(userId="me", messageId=ref["id"], id=att_id)
                .execute()
            )
            if att.get("data"):
                dest.write_bytes(_b64(att["data"]))
                saved.append(dest)

        # Public wp-content PDFs LINKED in the body (Jaguar First-Read / research
        # notes, etc.) — download so they flow through the same ingest as attachments.
        for url in list(dict.fromkeys(_LINK_PDF.findall(body)))[:_MAX_LINK_PDFS]:
            stem = _slug(Path(url.split("?")[0]).stem)
            dest = fund_dir / f"{base}-link-{stem}.pdf"
            if dest.exists():
                continue
            data = _download_pdf(url)
            if data:
                dest.write_bytes(data)
                saved.append(dest)
    log.info("gmail.fetch_new.done", saved=len(saved), senders=len(gmail_senders()))
    return saved
