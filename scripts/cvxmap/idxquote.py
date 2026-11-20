"""Index and fund quotes for the VIX tab (vixdash.py) and the vol board (volx.py) - created 2026-11-20.

Cboe's free delayed-quote API (cdn-api.cboe.com/api/global/delayed_quotes/...: the quotes and the fund
history charts) started answering 403 from the NAS on 2026-11-20 (every call since the 09:10 start, so
the VIX tab's regime board and term structure sat on "Waiting for the index feed"). The index history
CSVs (us_indices/daily_prices) still answer 200.

Order now: Cboe first. When a whole round of Cboe calls is refused, Cboe is skipped for CBOE_PAUSE_S and
the same symbols come from FMP through CVForge (`quote`, `historical-price-eod/light` - the key every other
FMP call already uses; probed 11/20 14:21 ET: all 11 VIX-tab indices answer, timestamps seconds old).
Rows keep the Cboe shape the callers already read:
    quotes   {sym: {"v": price, "prev": previous close, "t": "YYYY-MM-DDTHH:MM:SS" (ET), "c": price,
                    "src": "fmp"}}
    history  {sym: [(date, close), ...]}   oldest first, sessions before `today` only
Cockpit display data only (rule 4); no Convex calls (rule 1).
"""
import datetime
import time

CBOE_PAUSE_S = 1800                  # after a refused round, leave Cboe alone this long, then try again
FMP_INDEX = {"SPX": "^GSPC"}         # FMP's S&P 500 spelling; every other index = "^" + symbol

try:
    from zoneinfo import ZoneInfo
    _ET = ZoneInfo("America/New_York")
except Exception:                    # pragma: no cover - zoneinfo ships with Python 3.9+
    _ET = None


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def et_iso(epoch):
    """Unix seconds -> naive Eastern 'YYYY-MM-DDTHH:MM:SS' (the format of Cboe's last_trade_time)."""
    try:
        ts = float(epoch)
    except (TypeError, ValueError):
        return ""
    if _ET is not None:
        return datetime.datetime.fromtimestamp(ts, _ET).strftime("%Y-%m-%dT%H:%M:%S")
    return (datetime.datetime.utcfromtimestamp(ts) - datetime.timedelta(hours=5)).strftime("%Y-%m-%dT%H:%M:%S")


def fmp_symbol(sym, is_index):
    return FMP_INDEX.get(sym, "^" + sym) if is_index else sym


def _default_client():
    from trading_intel.clients.cvforge import CVForgeClient
    from trading_intel.config import get_settings

    return CVForgeClient(get_settings())


class Fallback:
    """Cboe circuit breaker + FMP (via CVForge) quotes / daily closes. One per process (thread-safe
    enough for one reader thread: vixdash's regime thread or volx's loop)."""

    def __init__(self, client_factory=None, now=time.time):
        self._factory = client_factory or _default_client
        self._now = now
        self._cv = None
        self.err = None                   # why FMP is unavailable (no key / import), else None
        self.cboe_off_until = 0.0
        self.last_src = ""

    # ---- Cboe breaker
    def cboe_ok(self):
        return self._now() >= self.cboe_off_until

    def cboe_refused(self):
        self.cboe_off_until = self._now() + CBOE_PAUSE_S

    # ---- FMP through CVForge
    def _client(self):
        if self._cv is None and self.err is None:
            try:
                self._cv = self._factory()
            except Exception as exc:      # no .env / key in this container: Cboe-only, say why
                self.err = "%s: %s" % (type(exc).__name__, str(exc)[:100])
        return self._cv

    def quotes(self, idx=(), etf=()):
        cv = self._client()
        out = {}
        if cv is None:
            return out
        for s, is_index in [(s, True) for s in idx] + [(s, False) for s in etf]:
            try:
                d = cv.fmp("quote", {"symbol": fmp_symbol(s, is_index)})
            except Exception:             # one symbol missing never stops the others
                continue
            r = d[0] if isinstance(d, list) and d else {}
            v = _f(r.get("price"))
            if v <= 0:
                continue
            out[s] = {"v": v, "prev": _f(r.get("previousClose")), "t": et_iso(r.get("timestamp")),
                      "c": v, "src": "fmp"}
        if out:
            self.last_src = "fmp"
        return out

    def history(self, today, idx=(), etf=(), keep=400, years=2):
        cv = self._client()
        out = {}
        if cv is None:
            return out
        frm = (today - datetime.timedelta(days=366 * years)).isoformat()
        for s, is_index in [(s, True) for s in idx] + [(s, False) for s in etf]:
            try:
                d = cv.fmp("historical-price-eod/light",
                           {"symbol": fmp_symbol(s, is_index), "from": frm, "to": today.isoformat()})
            except Exception:
                continue
            rows = []
            for r in d if isinstance(d, list) else []:
                try:
                    dt = datetime.date.fromisoformat(str(r.get("date"))[:10])
                except ValueError:
                    continue
                c = _f(r.get("price") if r.get("price") is not None else r.get("close"))
                if dt < today and c > 0:
                    rows.append((dt, c))
            if rows:
                rows.sort()
                out[s] = rows[-keep:]
        return out


def check(data_dir):
    """One line for the ship / check sessions: does the VIX tab (live.json VIXR) and the vol board (volx.json)
    have index quotes again, and from which feed."""
    import json
    import os

    def load(name):
        try:
            with open(os.path.join(data_dir, name), encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, ValueError):
            return {}

    r = load("live.json").get("VIXR") or {}
    v = load("volx.json")
    line = "VIX tab rows %d sources %s VIX9D %s | vol board quotes %d sources %s" % (
        len(r.get("rows") or []), r.get("qsrc"), (r.get("idx") or {}).get("VIX9D"),
        len(v.get("q") or {}), v.get("qsrc"))
    print(line)
    return 0 if r.get("rows") else 1


if __name__ == "__main__":
    import sys

    sys.exit(check(sys.argv[sys.argv.index("--check") + 1] if "--check" in sys.argv else "data"))
