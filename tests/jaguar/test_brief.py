"""Orchestrator degradation path — no Gmail, no Ollama, no DB, no breadth feed.

Injects the three emails, a fake completer, and a bare session (so every our-tape tool
raises and degrades). Proves the brief still assembles + renders end-to-end.
"""

from __future__ import annotations

from trading_intel.jaguar import brief

LIVE = """Good Morning Jags!
16 hours ago

Weekend Research - July 27

eBay (EBAY) - A 94% correlation index points to GMV upside, reports August 5th.
14 hours ago

Boston Scientific (BSX) - Bullish earnings preview. Someone bought 12,000 December 50-strike calls at $4.48 on the offer, roughly $5.4 million of premium.
9 hours ago

Herc Holdings (HRI) - Earnings preview: Herc reports Tuesday before the open. $5.77 million of bullish premium against zero bearish.
8 hours ago

Conversations with Jaguars - July 27

Summary: No new trades today. Jay added Blue Bird; Chronicle added United Rentals.
8 hours ago"""

CORE = {
    "jaguarlive": {"subject": "JaguarLive, July 27", "body": LIVE, "ts": 1},
    "first_read": {
        "subject": "First Read - July 28th, 2026",
        "body": "Highlights from today's First Read:\n* Notable Callout: Golar FLNG catalyst.\n* CXMT +500% debut.",
        "ts": 2,
    },
    "trade_alert": {
        "subject": "Trade Alert - Let's Play the Game",
        "body": "Buy Take-Two common for $245 or less.",
        "ts": 3,
    },
}


class _FakeLLM:
    def complete(self, prompt, *, model=None, max_tokens=2048):
        return ""  # empty → condense falls back to grounded excerpt


class _FakeSettings:
    LLM_DAILY_MODEL = "qwen2.5:14b"


class _BareSession:
    """No .execute → every our-tape tool raises → _safe degrades it."""


def test_build_degrades_and_renders():
    html, b = brief.build_jaguar_brief(
        _BareSession(), settings=_FakeSettings(), llm=_FakeLLM(), cvforge=None, core=CORE
    )
    assert html.startswith("<!doctype html>")
    tickers = {t["ticker"] for t in b["trades"]}
    assert {"BSX", "HRI"} <= tickers
    assert "EBAY" not in tickers  # weekend-research name, not a flow callout

    bsx = next(t for t in b["trades"] if t["ticker"] == "BSX")
    assert "isn't in our options tape" in bsx["ours"]  # DB degraded cleanly
    assert bsx["structure"] and "50/60 call spread" in bsx["structure"]["label"]
    assert bsx["structure"]["max_risk"] is None  # no chain marks → live-priced
    assert bsx["him"].startswith("Boston Scientific")  # grounded fallback (no LLM)

    assert b["breadth"]["rows"][0][1] == "computing"  # no cvforge → degrades
    assert any(k == "New Trade Alert" for k, _ in b["changed"])
    assert any("Weekend Research" == k for k, _ in b["changed"])
