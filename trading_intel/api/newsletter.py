"""Newsletter signals reader — latest stated LEVELS + IF-THEN SCENARIOS per source.

Pure DB read of ``newsletter_levels`` / ``newsletter_scenarios`` (banked by the
local-Ollama pass in ``scheduler.jobs.letters_fetch``). Feeds the synthesis engine's
cross-check (author's stated level vs our computed value) and if-then / trigger
layer. Descriptor only (rule 4).
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from trading_intel.memory.models import NewsletterLevel, NewsletterScenario

_SOURCES = ("DOC", "VOLSIGNALS", "KURT", "NORSEMAN")


def build_newsletter_signals(
    session: Session, *, sources: tuple[str, ...] = _SOURCES
) -> dict[str, Any]:
    """Latest levels + scenarios per source, keyed by source label."""
    out: dict[str, Any] = {}
    for label in sources:
        latest_lv = session.execute(
            select(func.max(NewsletterLevel.as_of)).where(NewsletterLevel.source == label)
        ).scalar()
        latest_sc = session.execute(
            select(func.max(NewsletterScenario.as_of)).where(NewsletterScenario.source == label)
        ).scalar()
        dates = [d for d in (latest_lv, latest_sc) if d is not None]
        if not dates:
            continue
        as_of = max(dates)

        levels = session.execute(
            select(NewsletterLevel)
            .where(NewsletterLevel.source == label, NewsletterLevel.as_of == as_of)
            .order_by(NewsletterLevel.name)
        ).scalars().all()
        scenarios = session.execute(
            select(NewsletterScenario)
            .where(NewsletterScenario.source == label, NewsletterScenario.as_of == as_of)
            .order_by(NewsletterScenario.idx)
        ).scalars().all()

        out[label] = {
            "as_of": as_of.isoformat(),
            "levels": [
                {"name": lv.name, "value": lv.value, "unit": lv.unit, "note": lv.note}
                for lv in levels
            ],
            "scenarios": [
                {
                    "trigger": s.trigger,
                    "consequence": s.consequence,
                    "direction": s.direction,
                    "confidence": s.confidence,
                }
                for s in scenarios
            ],
        }
    return {"found": bool(out), "sources": out}
