"""Job-level tests: fetch degrade path + incremental window (no DB needed)."""

from __future__ import annotations

from trading_intel.scheduler.jobs import squeezemetrics as job


class _Resp:
    def __init__(self, text: str, status: int = 200) -> None:
        self.text = text
        self._status = status

    def raise_for_status(self) -> None:
        if self._status >= 400:
            raise RuntimeError(f"HTTP {self._status}")


class _Client:
    def __init__(self, text: str, status: int = 200) -> None:
        self._resp = _Resp(text, status)
        self.calls: list[str] = []

    def get(self, url: str) -> _Resp:
        self.calls.append(url)
        return self._resp


def test_fetch_dix_csv_uses_injected_client() -> None:
    c = _Client("date,price,dix,gex\n2026-10-23,1,0.4,5\n")
    assert job.fetch_dix_csv(client=c).startswith("date,")
    assert c.calls == [job.DIX_CSV_URL]


def test_run_degrades_on_http_error(monkeypatch) -> None:
    class _S:  # session never touched on the failure path
        def execute(self, *a, **k):  # pragma: no cover
            raise AssertionError("must not hit the DB")

    assert job.run(_S(), client=_Client("", status=503)) == 0
