"""Clock rule guard (2026-11-19, DB schema rule 5): code under trading_intel/ never reads the host clock directly.

NAS containers run UTC, so ``date.today()`` / ``datetime.now()`` there are UTC wall-clock: after 19:00 ET
(20:00 in summer) "today" is already tomorrow, and ``utcnow()`` stamps a naive column with UTC hours while
every reader treats stored naive times as Eastern. Use ``trading_intel.timeutils.eastern_now()`` /
``eastern_today()`` instead.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from trading_intel import timeutils

ROOT = Path(__file__).resolve().parents[1] / "trading_intel"
BANNED = re.compile(r"(?<![\w.])(?:datetime\.utcnow\(|datetime\.now\(\)|date\.today\(\)|datetime\.datetime\.utcnow\()")


def test_no_host_clock_reads_in_trading_intel() -> None:
    bad = []
    for p in sorted(ROOT.rglob("*.py")):
        if p.name == "timeutils.py":
            continue
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            code = line.split("#", 1)[0]
            if BANNED.search(code) and '"""' not in line and "``" not in line:
                bad.append(f"{p.relative_to(ROOT.parent)}:{i}: {line.strip()}")
    assert not bad, "use eastern_now() / eastern_today():\n" + "\n".join(bad)


def test_eastern_today_matches_eastern_now(monkeypatch) -> None:
    monkeypatch.setattr(timeutils, "eastern_now", lambda: datetime(2026, 11, 19, 19, 30))
    assert timeutils.eastern_today().isoformat() == "2026-11-19"


def test_expiry_clock_uses_eastern_date(monkeypatch) -> None:
    from trading_intel import expiry_clock

    # 20:30 ET = 01:30 UTC next day on a UTC host: the phase must still be computed for 11/19
    monkeypatch.setattr(expiry_clock, "eastern_now", lambda: datetime(2026, 11, 19, 20, 30))
    ev = expiry_clock.next_events()
    assert ev and all(e["days_to"] >= 0 for e in ev)
    assert min(e["days_to"] for e in ev) == (datetime(2026, 11, 20).date() - datetime(2026, 11, 19).date()).days
