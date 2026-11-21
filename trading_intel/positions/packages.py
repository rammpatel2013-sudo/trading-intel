"""SPX spread packages from the tape: fills -> verticals / calendars -> side -> open / close / roll (2026-11-21).

Why (Mithil 2026-11-20/21): spreads are about two-thirds of SPX option volume and their legs net out, so the
ledger must hold them as PACKAGES, not as separate directional legs; and closing / rolling / opening a package
is what moves intermediate-term positioning (VolSignals' 11/19 example: ~7,900 lots of the Nov 27 6975/7000
put spread bought back and the Jan 15 6775/6800 put spread sold, 14:18-14:26 ET).

Method (checked on that 11/19 roll):
1. FILL = prints in the same second with the same size on >= 2 contracts (every leg of a package fill prints
   with the fill's quantity). The vendor/guessed ``leg_group`` is NOT used: on 11/19 the guessed groups lumped
   40+ unrelated legs together.
2. A fill on 2 contracts, same expiry + call/put, 2 strikes = VERTICAL; same strike, 2 expiries = CALENDAR;
   4 contracts = 2 expiries x 2 strikes, same call/put = two verticals traded together = ROLL candidate.
   A BOX (call + put vertical, same strikes and expiry) is financing with no market exposure and is skipped.
3. Fills of the same structure within CLUSTER_MIN minutes are one package EVENT.
4. SIDE once per vertical/calendar event (never per leg - per-leg aggressor tags are unreliable inside
   packages): vote = (quote position of the dear leg - of the cheap leg) + AGG_W x (aggressor of dear - of
   cheap), size-weighted; quote position in_hi = +1, in_lo = -1, mid / outside = 0. vote > 0 = BOUGHT
   (long the dear leg), < 0 = SOLD, 0 = undecided. On 11/19: Nov 27 +404 (bought back), Jan 15 -2,872 (sold).
5. ACTION from the next-session OI change of the legs (dOI / size, clipped to +-1, averaged over the legs):
   >= +OPEN_CUT open, <= -CLOSE_CUT close, else unknown. A roll pair = close on one expiry + open on the
   other in the same event; the open half gets ``roll_key`` = the close half's key (holder carried). Rolls can
   be done for a debit or a credit; the net price is NOT used to decide them.
Pure functions (no DB): the job ``trading_intel.scheduler.jobs.pos_packages`` feeds them.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime

CLUSTER_MIN = 10        # fills of the same structure within 10 minutes = one package event
AGG_W = 0.5             # weight of the aggressor tag vs the quote position
OPEN_CUT = 0.5          # mean dOI/size >= +0.5 -> open
CLOSE_CUT = 0.5         # mean dOI/size <= -0.5 -> close
LOC = {"in_hi": 1, "in_lo": -1}
AGG = {"buy": 1, "sell": -1}

Leg = tuple  # (root, expiry: date, strike: float, cp: 'C'|'P')


@dataclass
class Event:
    structure: str                 # vertical / calendar
    legs: tuple                    # (dear_leg, cheap_leg)
    first_ts: datetime
    last_ts: datetime
    size: int = 0                  # package lots
    vote: float = 0.0
    net_x: float = 0.0             # sum(net price x lots), net = dear - cheap
    fills: int = 0
    roll_with: set = field(default_factory=set)   # keys of verticals traded in the same 4-leg fills

    @property
    def side(self) -> int:
        return 1 if self.vote > 0 else (-1 if self.vote < 0 else 0)

    @property
    def net(self) -> float | None:
        return round(self.net_x / self.size, 4) if self.size else None

    @property
    def key(self) -> str:
        (r1, e1, k1, c1), (r2, e2, k2, c2) = self.legs
        return f"{self.structure}|{r1}|{e1}|{k1:g}{c1}|{r2}|{e2}|{k2:g}{c2}|{self.first_ts:%H%M%S}"


def _fills(prints: list[dict]) -> list[list[dict]]:
    """Group prints by (second, size); keep groups on >= 2 contracts with the same number of prints per contract."""
    by: dict = defaultdict(list)
    for p in prints:
        if not p.get("size") or p.get("price") is None or p.get("strike") is None:
            continue
        by[(p["ts"].replace(microsecond=0), int(p["size"]))].append(p)
    out = []
    for grp in by.values():
        per: dict = defaultdict(list)
        for p in grp:
            per[(p["root"], p["expiry"], float(p["strike"]), p["cp"])].append(p)
        if len(per) >= 2 and len({len(v) for v in per.values()}) == 1:
            out.append(grp)
    return out


def _leg_stats(ps: list[dict]) -> tuple[float, float, float, int]:
    lots = sum(int(p["size"]) for p in ps)
    px = sum(float(p["price"]) * int(p["size"]) for p in ps) / lots
    loc = sum(LOC.get(p.get("quote_loc") or "", 0) * int(p["size"]) for p in ps)
    agg = sum(AGG.get(p.get("side") or "", 0) * int(p["size"]) for p in ps)
    return px, loc, agg, lots


def _pairs(per: dict) -> list[tuple[str, Leg, Leg]]:
    """Split a fill's legs into verticals / calendars. Returns (structure, leg_a, leg_b) - order not yet dear/cheap."""
    legs = list(per)
    if len(legs) == 2:
        a, b = legs
        if a[1] == b[1] and a[3] == b[3] and a[2] != b[2]:
            return [("vertical", a, b)]
        if a[2] == b[2] and a[3] == b[3] and a[1] != b[1]:
            return [("calendar", a, b)]
        return []
    if len(legs) == 4:
        byexp: dict = defaultdict(list)
        for lg in legs:
            byexp[(lg[1], lg[3])].append(lg)
        # box = call + put vertical on the same strikes and expiry: financing, no market exposure -> skipped
        if len(byexp) == 2 and len({k[0] for k in byexp}) == 1:
            return []
        if len(byexp) == 2 and all(len(v) == 2 and v[0][2] != v[1][2] for v in byexp.values()):
            return [("vertical", v[0], v[1]) for v in byexp.values()]
    return []


def events_for_day(prints: list[dict]) -> list[Event]:
    """prints: dicts with ts (naive Eastern), root, expiry (date), strike, cp, side, price, size, quote_loc."""
    events: dict = {}           # (structure, legs) -> list[Event] (time-ordered clusters)
    for grp in sorted(_fills(prints), key=lambda g: g[0]["ts"]):
        per: dict = defaultdict(list)
        for p in grp:
            per[(p["root"], p["expiry"], float(p["strike"]), p["cp"])].append(p)
        pairs = _pairs(per)
        made = []
        for structure, a, b in pairs:
            sa, sb = _leg_stats(per[a]), _leg_stats(per[b])
            dear, cheap, sd, sc = (a, b, sa, sb) if sa[0] >= sb[0] else (b, a, sb, sa)
            lots = min(sd[3], sc[3])
            t = min(p["ts"] for p in grp)
            lst = events.setdefault((structure, (dear, cheap)), [])
            ev = lst[-1] if lst and (t - lst[-1].last_ts).total_seconds() <= CLUSTER_MIN * 60 else None
            if ev is None:
                ev = Event(structure, (dear, cheap), t, t)
                lst.append(ev)
            ev.last_ts = max(ev.last_ts, t)
            ev.size += lots
            ev.fills += 1
            ev.vote += (sd[1] - sc[1]) + AGG_W * (sd[2] - sc[2])
            ev.net_x += (sd[0] - sc[0]) * lots
            made.append(ev)
        if len(made) == 2:                       # 4-leg fill = two verticals traded together (roll candidate)
            made[0].roll_with.add(id(made[1]))
            made[1].roll_with.add(id(made[0]))
    return [e for lst in events.values() for e in lst]


def oi_score(ev: Event, doi: dict) -> float | None:
    """Mean over the legs of clip(dOI / lots, -1, 1); None when a leg's OI change is unknown."""
    if not ev.size:
        return None
    vals = []
    for lg in ev.legs:
        d = doi.get(lg)
        if d is None:
            return None
        vals.append(max(-1.0, min(1.0, d / ev.size)))
    return round(sum(vals) / len(vals), 3)


def classify(events: list[Event], doi: dict) -> list[dict]:
    """Rows for pos_packages: action open / close / roll_close / roll_open / unknown, roll_key links the halves."""
    byid = {id(e): e for e in events}
    rows = []
    for e in events:
        sc = oi_score(e, doi)
        act = "unknown" if sc is None else ("open" if sc >= OPEN_CUT else ("close" if sc <= -CLOSE_CUT else "unknown"))
        rows.append({"ev": e, "action": act, "oi_score": sc, "roll_key": None})
    rid = {id(r["ev"]): r for r in rows}
    for r in rows:
        e = r["ev"]
        for oid in e.roll_with:
            o = rid.get(oid)
            if not o:
                continue
            if r["action"] == "close" and o["action"] in ("open", "unknown") and o["ev"].legs[0][1] != e.legs[0][1]:
                r["action"] = "roll_close"
                o["action"] = "roll_open"
                o["roll_key"] = e.key
                r["roll_key"] = byid[oid].key
    return rows


def to_et(ts: datetime, trade_date: date, utc_until: date = date(2026, 11, 19)) -> datetime:
    """tas_prints.ts was naive UTC up to 2026-11-19 (clock fix shipped that evening) and naive Eastern after."""
    if trade_date > utc_until:
        return ts
    from zoneinfo import ZoneInfo
    return ts.replace(tzinfo=ZoneInfo("UTC")).astimezone(ZoneInfo("America/New_York")).replace(tzinfo=None)
