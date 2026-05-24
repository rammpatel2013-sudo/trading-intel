"""SEC EDGAR client - latest 10-K text for a ticker (free, no API key).

The only module that talks to SEC EDGAR (CLAUDE.md rule 1). Maps ticker -> CIK
via the public company_tickers.json, finds the most recent 10-K in the
submissions feed, and fetches its primary document text (HTML stripped). SEC
fair-access requires a descriptive ``User-Agent`` with a contact email. Degrades
to ``None`` on any failure. Descriptive research input only (rule 4); 10-K text
is large, so callers truncate.
"""
from __future__ import annotations

import re

import structlog

log = structlog.get_logger(__name__)

_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
_ARCHIVE_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/{doc}"


def _strip_html(html: str) -> str:
    text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", html)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


class EdgarClient:
    """Fetch SEC filings. Inject an httpx-like ``client`` in tests to skip HTTP."""

    def __init__(self, *, user_agent: str, client: object | None = None, timeout: float = 20.0):
        self._ua = user_agent
        self._client = client
        self._timeout = timeout
        self._cik_map: dict[str, int] | None = None

    def _get(self, url: str, *, as_json: bool = True):
        try:
            if self._client is not None:
                resp = self._client.get(url)
            else:
                import httpx

                resp = httpx.get(url, headers={"User-Agent": self._ua}, timeout=self._timeout)
            resp.raise_for_status()
            return resp.json() if as_json else resp.text
        except Exception as exc:  # network / shape / parse - degrade to None
            log.warning("edgar.fetch_failed", url=url, error=str(exc))
            return None

    def cik_for(self, ticker: str) -> int | None:
        """CIK for ``ticker`` via the public ticker->CIK map (cached)."""
        if self._cik_map is None:
            data = self._get(_TICKERS_URL)
            self._cik_map = {}
            if isinstance(data, dict):
                for row in data.values():
                    sym = str(row.get("ticker", "")).upper()
                    if sym:
                        self._cik_map[sym] = int(row.get("cik_str", 0))
        return self._cik_map.get(ticker.upper())

    def latest_10k(self, ticker: str) -> dict | None:
        """``{'accession','date','doc_url','text'}`` for the latest 10-K, or None."""
        cik = self.cik_for(ticker)
        if not cik:
            return None
        sub = self._get(_SUBMISSIONS_URL.format(cik=cik))
        if not sub:
            return None
        recent = sub.get("filings", {}).get("recent", {})
        forms = recent.get("form", [])
        accns = recent.get("accessionNumber", [])
        dates = recent.get("filingDate", [])
        docs = recent.get("primaryDocument", [])
        for i, form in enumerate(forms):
            if form == "10-K":
                acc = accns[i]
                url = _ARCHIVE_URL.format(cik=cik, acc=acc.replace("-", ""), doc=docs[i])
                text = self._get(url, as_json=False)
                return {
                    "accession": acc,
                    "date": dates[i] if i < len(dates) else None,
                    "doc_url": url,
                    "text": _strip_html(text or ""),
                }
        return None
