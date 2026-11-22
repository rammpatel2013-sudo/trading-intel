"""Named repeat traders ("players") - 2026-11-22. Pure logic, no DB.

Mithil 11/21: "assign some name to this kind of repeated trade as they will come with similar trade again and
again - like JPM in past, the 0DTE whales and now this big guy with puts."

An EVENT is one big thing seen in a session: a spread package (pos_packages), a single-leg build on one contract
(big OI change), or one big 0DTE print. Each event gets a fingerprint (call/put, structure, customer side, days to
expiry, % out of the money, spread width, size). Known players are rule boxes on those features (seeded from what
VolSignals disclosed). A big event no rule matches gets an ``auto-<fingerprint>`` slug; when the same fingerprint
repeats it becomes an "unnamed" player for Mithil to name. Rules are descriptive (who is in the book), NOT signals
(CLAUDE.md rule 4) - nothing here writes to ``signals``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

BIG_LOTS = 5000  # an event this big is worth a fingerprint even if no rule matches
BIG_PREMIUM = 5_000_000  # ... or this much premium ($)
AUTO_MIN_DTE = (
    15  # short-dated weekly flow is routine - an unknown short-dated event needs 2x the size
)
REPEATS_TO_NAME = (
    3  # an auto fingerprint seen this many times becomes "unnamed" (asks Mithil for a name)
)


@dataclass(frozen=True)
class Rule:
    slug: str
    name: str
    description: str
    cp: str | None = None
    structure: tuple[str, ...] = ()
    side: int | None = None  # +1 customer bought / -1 sold
    dte: tuple[int, int] | None = None
    otm: tuple[float, float] | None = None  # % (strike/spot - 1) of the main (dear) leg
    width: tuple[float, float] | None = None  # spread width, index points
    lots: tuple[float, float] | None = None
    premium: tuple[float, float] | None = None
    min_clips: int | None = None  # accumulation in many prints (one contract, one day)
    quarter_end_expiry: bool = False
    extra: dict = field(default_factory=dict)


def _q_end(d: date | None) -> bool:
    return bool(d) and d.month in (3, 6, 9, 12) and d.day >= 28


# Order matters: first rule that fits wins.
RULES: tuple[Rule, ...] = (
    Rule(
        "jpm-collar",
        "JPM Collar",
        "JPM Hedged Equity funds (JHEQX Mar/Jun/Sep/Dec, HE2 Jan/Apr/Jul/Oct, HE3 Feb/May/Aug/Nov): buy ~5% OTM put / "
        "sell ~20% OTM put / sell OTM call on the last business day. Since 2026-03-31 mostly in CME SME options "
        "(off our SPX chain) - kept so a return to SPX is caught.",
        cp="P",
        structure=("vertical",),
        side=1,
        dte=(80, 100),
        otm=(-8.0, -2.0),
        lots=(3000, 1e9),
    ),
    Rule(
        "odte-whale",
        "0DTE Whale",
        "Huge same-day SPX prints or spreads (>= 1,000 lots or >= $1M premium on a 0DTE line).",
        dte=(0, 0),
        lots=(1000, 1e9),
    ),
    Rule("odte-whale", "0DTE Whale", "(premium form)", dte=(0, 0), premium=(1_000_000, 1e12)),
    Rule(
        "year-end-put-whale",
        "Year-End Put Whale",
        "Customer building a big quarter-end SPX put line in many 200-350 lot clips (Oct 2-13 2026: Dec 31 7,250P "
        "~27k, ~$173M; ~21% of dealer short gamma at 6,911 per VolSignals). Watch for the unwind = vol event.",
        cp="P",
        structure=("single",),
        side=1,
        dte=(30, 140),
        otm=(-15.0, 0.0),
        lots=(3000, 1e9),
        quarter_end_expiry=True,
    ),
    Rule(
        "weekly-putspread-seller",
        "Weekly Put-Spread Seller",
        "Sells ~60k x 100-wide SPX put spreads ~18% OTM at ~45 DTE every week (110/110 expired worthless per "
        "VolSignals); rolls e.g. Dec 4 6,250/6,350 -> Dec 31 5,900/6,000.",
        cp="P",
        structure=("vertical",),
        side=-1,
        dte=(25, 65),
        otm=(-26.0, -11.0),
        width=(75, 125),
        lots=(15000, 1e9),
    ),
    Rule(
        "putspread-roller-25w",
        "25-Wide Put-Spread Roller",
        "Sells ~8k 25-wide put spreads ~10% OTM, rolls out (Nov 27 6,975/7,000 -> Jan 15 6,775/6,800).",
        cp="P",
        structure=("vertical", "calendar"),
        side=-1,
        dte=(14, 75),
        otm=(-15.0, -4.0),
        width=(20, 30),
        lots=(3000, 15000),
    ),
    Rule(
        "one-week-put-seller",
        "1-Week Put Seller",
        "Sells ~2,800 ~6% OTM 1-week SPX puts most days for ~$2-7.",
        cp="P",
        structure=("single",),
        side=-1,
        dte=(4, 9),
        otm=(-10.0, -3.5),
        lots=(1000, 6000),
    ),
    Rule(
        "call-spread-lottery",
        "Call-Spread Lottery Buyer",
        "Buys big 1-2 week SPX call spreads ($10M+), never holds to expiry (13 such since mid-2024 per VolSignals).",
        cp="C",
        structure=("vertical",),
        side=1,
        dte=(2, 16),
        otm=(-1.0, 6.0),
        premium=(5_000_000, 1e12),
    ),
)

SEED_PLAYERS = {r.slug: (r.name, r.description) for r in RULES if r.description != "(premium form)"}


def _in(v, rng) -> bool:
    return rng is None or (v is not None and rng[0] <= v <= rng[1])


def fits(rule: Rule, ev: dict) -> bool:
    if rule.cp and ev.get("cp") != rule.cp:
        return False
    if rule.structure and ev.get("structure") not in rule.structure:
        return False
    if rule.side is not None and ev.get("side") != rule.side:
        # a paired spread whose side we could not read (0) may still be claimed by a known spread trader
        if not (
            ev.get("side") == 0
            and ev.get("structure") in ("vertical", "calendar")
            and rule.structure
            and set(rule.structure) <= {"vertical", "calendar"}
        ):
            return False
    if rule.quarter_end_expiry and not _q_end(ev.get("expiry")):
        return False
    if rule.min_clips is not None and (ev.get("clips") or 0) < rule.min_clips:
        return False
    return all(_in(ev.get(k), getattr(rule, k)) for k in ("dte", "otm", "width", "lots", "premium"))


def _bucket(v: float | None, edges: tuple[float, ...]) -> str:
    if v is None:
        return "na"
    for e in edges:
        if v <= e:
            return f"{e:g}"
    return f">{edges[-1]:g}"


def fingerprint(ev: dict) -> str:
    """Coarse shape of an event, stable across repeats: cp-structure-side-dte-otm-width."""
    side = {1: "b", -1: "s"}.get(ev.get("side"), "x")
    return "-".join(
        (
            ev.get("cp") or "x",
            (ev.get("structure") or "x")[:4],
            side,
            "d" + _bucket(ev.get("dte"), (0, 2, 7, 14, 30, 60, 100, 200)),
            "o" + _bucket(ev.get("otm"), (-20, -12, -7, -4, -2, 0, 2, 5, 10)),
            "w" + _bucket(ev.get("width"), (0, 10, 30, 60, 125)),
        )
    )


def classify(ev: dict) -> str | None:
    """Player slug for an event, or None when it is too small to matter and fits no rule."""
    for r in RULES:
        if fits(r, ev):
            return r.slug
    k = 1 if (ev.get("dte") or 0) >= AUTO_MIN_DTE else 2
    if (ev.get("lots") or 0) >= k * BIG_LOTS or (ev.get("premium") or 0) >= k * BIG_PREMIUM:
        return "auto-" + fingerprint(ev)
    return None


def player_status(slug: str, n_sightings: int, named: bool) -> str:
    if named or slug in SEED_PLAYERS:
        return "named"
    return "unnamed" if n_sightings >= REPEATS_TO_NAME else "single"


RULE_BY_SLUG = {r.slug: r for r in RULES}


def leg_leans(slug: str, ev: dict) -> dict:
    """Known side of each leg for a named spread trader: {leg: customer lean}. Lets player knowledge correct the
    book where the tape could not (e.g. the weekly put-spread seller before package data existed).
    """
    r = RULE_BY_SLUG.get(slug)
    if (
        r is None
        or r.side is None
        or ev.get("structure") not in ("vertical", "calendar")
        or len(ev.get("legs", ())) != 2
    ):
        return {}
    return {ev["legs"][0]: float(r.side), ev["legs"][1]: float(-r.side)}
