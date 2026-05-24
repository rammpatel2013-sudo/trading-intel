"""Tests for the SEC EDGAR client (injected HTTP, no network)."""

from __future__ import annotations

from trading_intel.clients.edgar import EdgarClient, _strip_html


class _Resp:
    def __init__(self, json_data=None, text=""):
        self._json = json_data
        self.text = text

    def raise_for_status(self):
        pass

    def json(self):
        return self._json


class _Client:
    """Returns a canned response for the first URL substring that matches."""

    def __init__(self, mapping):
        self._mapping = mapping

    def get(self, url):
        for key, resp in self._mapping.items():
            if key in url:
                return resp
        raise KeyError(url)


_TICKERS = {"0": {"ticker": "AAPL", "cik_str": 320193, "title": "Apple Inc."}}
_SUB = {
    "filings": {
        "recent": {
            "form": ["8-K", "10-K", "10-Q"],
            "accessionNumber": ["0000-1", "0000320193-24-000123", "0000-3"],
            "filingDate": ["2024-08-01", "2024-11-01", "2024-05-01"],
            "primaryDocument": ["a.htm", "aapl-10k.htm", "c.htm"],
        }
    }
}
_DOC_HTML = "<html><body><h1>Item 7. MD&A</h1><p>Net sales rose.</p></body></html>"


def _client() -> EdgarClient:
    mapping = {
        "company_tickers.json": _Resp(json_data=_TICKERS),
        "submissions": _Resp(json_data=_SUB),
        "Archives": _Resp(text=_DOC_HTML),
    }
    return EdgarClient(user_agent="test", client=_Client(mapping))


def test_cik_lookup():
    assert _client().cik_for("aapl") == 320193
    assert _client().cik_for("NOPE") is None


def test_latest_10k():
    out = _client().latest_10k("AAPL")
    assert out is not None
    assert out["accession"] == "0000320193-24-000123"
    assert out["date"] == "2024-11-01"
    assert "aapl-10k.htm" in out["doc_url"]
    assert "MD&A" in out["text"] and "Net sales rose." in out["text"]
    assert "<" not in out["text"]  # html stripped


def test_strip_html_drops_tags_and_scripts():
    assert _strip_html("<style>x{}</style><p>Hello <b>world</b></p>") == "Hello world"
