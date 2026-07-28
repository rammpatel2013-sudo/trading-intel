"""One-time: mint a ``gmail.readonly`` ``token.json`` for the letters lane.

Run ONCE on a machine with a browser (the laptop). Reads the Google OAuth *client
secrets* from ``GMAIL_CREDENTIALS_PATH``, opens a consent page, and writes the authorised
user token to ``GMAIL_TOKEN_PATH``. Copy that ``token.json`` to the NAS afterwards — the
scheduled ``letters.gmail_source.fetch_new`` then runs unattended.

Read-only scope; paths come from ``.env`` and no secret value is logged (rule 2).

    pip install google-auth-oauthlib google-api-python-client google-auth
    python scripts/gmail_auth.py
"""

from __future__ import annotations

from pathlib import Path

from trading_intel.config import get_settings

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]


def main() -> None:
    from google_auth_oauthlib.flow import InstalledAppFlow  # noqa: PLC0415 — optional dep, lazy

    settings = get_settings()
    creds_path = str(getattr(settings, "GMAIL_CREDENTIALS_PATH", "") or "")
    token_path = str(getattr(settings, "GMAIL_TOKEN_PATH", "") or "")

    if not creds_path or not Path(creds_path).is_file():
        raise SystemExit(
            f"GMAIL_CREDENTIALS_PATH not set or file not found: {creds_path!r}\n"
            "Create an OAuth client (Desktop) in Google Cloud, download credentials.json, "
            "and point GMAIL_CREDENTIALS_PATH at it in .env."
        )
    if not token_path:
        raise SystemExit("GMAIL_TOKEN_PATH not set in .env (where to write token.json).")

    flow = InstalledAppFlow.from_client_secrets_file(creds_path, SCOPES)
    creds = flow.run_local_server(port=0)  # opens the browser for one-time consent
    Path(token_path).write_text(creds.to_json(), encoding="utf-8")
    print(f"Wrote {token_path} (scope: gmail.readonly). Copy this file to the NAS — it is gitignored.")


if __name__ == "__main__":
    main()
