"""FINRA short-interest client — the one swing-dossier field FMP doesn't carry.

The only module that talks to FINRA (CLAUDE.md rule 1). Two sources:

* **Reg SHO daily short-volume** (FREE, no auth) — the consolidated
  ``CNMSshvolYYYYMMDD.txt`` file (pipe-delimited: Date|Symbol|ShortVolume|
  ShortExemptVolume|TotalVolume|Market). Market-wide for one session; we filter
  the symbol and compute short-volume ratio + an N-day average. This is a daily
  *off-exchange short-volume* proxy — available immediately, no credentials.

* **Settled bi-monthly Short Interest** (the real SI% / days-to-cover) — via the
  FINRA API (``api.finra.org``), which needs a free OAuth client id/secret. Gated
  behind ``settings.FINRA_CLIENT_ID`` / ``FINRA_CLIENT_SECRET``; returns ``None``
  until those are set, so the collector degrades to the daily proxy alone.

Every method is best-effort and degrades to ``None``/empty on failure.
Descriptive research input only (rule 4).
"""

from __future__ import annotations

from datetime import date, timedelta

import structlog

log = structlog.get_logger(__name__)

_REGSHO_URL = "https://cdn.finra.org/equity/regsho/daily/CNMSshvol{ymd}.txt"
_FINRA_TOKEN_URL = "https://ews.fip.finra.org/fip/rest/ews/oauth2/access_token"
_FINRA_SI_URL = "https://api.finra.org/data/group/otcMarket/name/consolidatedShortInterest"


class FinraClient:
    """Fetch FINRA short data. Inject an httpx-like ``client`` in tests to skip HTTP."""

    def __init__(
        self,
        *,
        client: object | None = None,
        timeout: float = 20.0,
        client_id: str | None = None,
        client_secret: str | None = None,
    ) -> None:
        self._client = client
        self._timeout = timeout
        self._cid = client_id
        self._secret = client_secret

    def _http_get(self, url: str, *, as_json: bool = False):
        try:
            if self._client is not None:
                resp = self._client.get(url)
            else:
                import httpx

                resp = httpx.get(url, timeout=self._timeout)
            resp.raise_for_status()
            return resp.json() if as_json else resp.text
        except Exception as exc:  # network / 404 (no file that day) / shape
            log.warning("finra.fetch_failed", url=url, error=str(exc))
            return None

    # ── Reg SHO daily short volume (free, no auth) ─────────────────────────
    def short_volume_day(self, symbol: str, day: date) -> dict | None:
        """One session's short/total volume for ``symbol`` from the Reg SHO file.

        Returns ``{'date','symbol','short_volume','total_volume','short_ratio'}``
        or ``None`` (weekend/holiday → no file, or symbol absent that day).
        """
        text = self._http_get(_REGSHO_URL.format(ymd=day.strftime("%Y%m%d")))
        if not text:
            return None
        sym = symbol.upper()
        for line in text.splitlines():
            parts = line.split("|")
            if len(parts) < 5 or parts[1].upper() != sym:
                continue
            try:
                short_v = float(parts[2])
                total_v = float(parts[4])
            except (ValueError, IndexError):
                return None
            if total_v <= 0:
                return None
            return {
                "date": day,
                "symbol": sym,
                "short_volume": short_v,
                "total_volume": total_v,
                "short_ratio": round(short_v / total_v, 4),
            }
        return None

    def short_volume_avg(self, symbol: str, *, lookback: int = 10, end: date | None = None) -> dict | None:
        """Average short-volume ratio over the last ``lookback`` sessions with data.

        Walks back calendar days (skipping missing files) until it has ``lookback``
        readings or exhausts a ~3x window. Returns ``{'symbol','n','short_ratio_avg',
        'latest'}`` or ``None``.
        """
        end = end or date.today()
        got: list[dict] = []
        d = end
        for _ in range(lookback * 3 + 5):
            row = self.short_volume_day(symbol, d)
            if row:
                got.append(row)
                if len(got) >= lookback:
                    break
            d -= timedelta(days=1)
        if not got:
            return None
        avg = sum(r["short_ratio"] for r in got) / len(got)
        return {
            "symbol": symbol.upper(),
            "n": len(got),
            "short_ratio_avg": round(avg, 4),
            "latest": got[0],
        }

    # ── settled bi-monthly short interest (needs free FINRA API creds) ─────
    def _token(self) -> str | None:
        if not (self._cid and self._secret):
            return None
        try:
            import base64

            import httpx

            auth = base64.b64encode(f"{self._cid}:{self._secret}".encode()).decode()
            resp = httpx.post(
                _FINRA_TOKEN_URL,
                params={"grant_type": "client_credentials"},
                headers={"Authorization": f"Basic {auth}"},
                timeout=self._timeout,
            )
            resp.raise_for_status()
            return resp.json().get("access_token")
        except Exception as exc:
            log.warning("finra.token_failed", error=str(exc))
            return None

    def settled_short_interest(self, symbol: str) -> dict | None:
        """Latest settled short interest + days-to-cover via the FINRA API.

        Requires ``FINRA_CLIENT_ID``/``FINRA_CLIENT_SECRET`` (free OAuth client);
        returns ``None`` when unset so the caller falls back to the daily proxy.
        Fields: ``short_interest``, ``avg_daily_volume``, ``days_to_cover``,
        ``settlement_date``.
        """
        token = self._token()
        if not token:
            return None
        try:
            import httpx

            body = {
                "limit": 1,
                "compareFilters": [
                    {"fieldName": "symbolCode", "fieldValue": symbol.upper(), "compareType": "EQUAL"}
                ],
                "sortFields": ["-settlementDate"],
            }
            resp = httpx.post(
                _FINRA_SI_URL,
                json=body,
                headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
                timeout=self._timeout,
            )
            resp.raise_for_status()
            rows = resp.json()
            row = rows[0] if isinstance(rows, list) and rows else None
            if not row:
                return None
            si = row.get("currentShortPositionQuantity")
            adv = row.get("averageDailyVolumeQuantity")
            dtc = (float(si) / float(adv)) if si and adv else None
            return {
                "symbol": symbol.upper(),
                "short_interest": si,
                "avg_daily_volume": adv,
                "days_to_cover": round(dtc, 2) if dtc else None,
                "settlement_date": row.get("settlementDate"),
            }
        except Exception as exc:
            log.warning("finra.si_failed", symbol=symbol, error=str(exc))
            return None
