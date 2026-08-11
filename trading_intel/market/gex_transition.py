"""Pure state-machine for the GEX-transition ("quiet unwind") signal.

Detects the cReserve / "Daily GEX Print" state each session from banked
dealer-gamma + implied-vol reads. The edge itself is taken as GIVEN (validated
externally); this module only *classifies today's state* so a report/alert can
surface it. No forward-return claim is made here.

The trigger is UNIT-FREE. Our ``gex_total`` is a normalised net-gamma number
(~100-600), not the "$bn" convention the source used, so a raw "≥2bn" threshold
is not portable. Instead we z-score the day-over-day change ``ΔGEX`` against its
own trailing distribution and threshold in sigmas:

    quiet_unwind : ΔGEX_z ≤ -K   AND   |ΔIV| ≤ IV_FLAT_PT      (bearish / de-risk)
    confirmed    : ΔGEX_z ≤ -K   AND    ΔIV ≥ IV_CONFIRM_PT    (base rate — fear priced)
    gex_drop     : ΔGEX_z ≤ -K                                 (drop, IV ambiguous)
    rebuild      : ΔGEX_z ≥ +K                                 (hedging support rebuilding)
    base         : otherwise                                    (slow bleed = noise)

ΔIV is sourced from the CLEAN constant-maturity ATM IV (``iv_tenor_snapshots``),
NOT the intraday ``atm_iv`` on ``greeks_snapshots`` (which swings 8-21% as the
last snapshot of the day and makes ΔIV meaningless). Callers pass both.

Descriptor / research track only (FlashAlpha rule 4) — nothing here writes a
signal; it labels a regime state.

Pure stdlib (no pandas / no DB) so it is trivially unit-testable.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import date
from typing import Any

# ── tunable thresholds (defaults; override per call as the sample banks) ──────
DEFAULT_K = 1.5  # |ΔGEX z| sigma threshold for a "fast" move
DEFAULT_IV_FLAT_PT = 0.5  # |ΔIV| ≤ this (vol pts) == "pinned"
DEFAULT_IV_CONFIRM_PT = 1.0  # ΔIV ≥ this (vol pts) == "vol confirms the drop"
_GAP_DAYS = 4  # ΔGEX only across trading days ≤ this many calendar days apart

STATE_QUIET = "quiet_unwind"
STATE_CONFIRMED = "confirmed"
STATE_DROP = "gex_drop"
STATE_REBUILD = "rebuild"
STATE_BASE = "base"


def _as_date(v: Any) -> date | None:
    if isinstance(v, date):
        return v
    if isinstance(v, str):
        try:
            return date.fromisoformat(v[:10])
        except ValueError:
            return None
    return None


def eod_gex_series(gamma_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse ``get_gamma_history`` rows to one EOD row per date (last wins).

    ``get_gamma_history`` returns intraday snapshots (several per day); the EOD
    read is the last row on each date. Returns ``[{date, gex, spot, flip,
    atm_iv_raw}]`` sorted ascending. ``atm_iv_raw`` is the noisy gamma-history IV
    (fallback only — prefer the iv_tenor map).
    """
    by_date: dict[date, dict[str, Any]] = {}
    for r in gamma_rows or []:
        d = _as_date(r.get("date") or r.get("ts"))
        g = r.get("gex_total")
        if d is None or g is None:
            continue
        by_date[d] = {
            "date": d,
            "gex": float(g),
            "spot": r.get("spot"),
            "flip": r.get("gex_flip") or r.get("flip"),
            "atm_iv_raw": r.get("atm_iv"),
        }
    return [by_date[d] for d in sorted(by_date)]


def iv_atm_map(iv_tenor_rows: list[dict[str, Any]], *, tenor_dte: int = 30) -> dict[date, float]:
    """{date -> ATM IV (vol points)} from ``get_iv_tenor`` rows at one tenor.

    ``iv_tenor.iv_atm`` is a clean constant-maturity decimal (~0.12); we return
    it in vol POINTS (×100) so ΔIV is in the same pt units the thresholds use.
    """
    out: dict[date, float] = {}
    for r in iv_tenor_rows or []:
        if tenor_dte is not None and r.get("tenor_dte") not in (tenor_dte, None):
            continue
        d = _as_date(r.get("ts") or r.get("date"))
        iv = r.get("iv_atm")
        if d is None or iv is None:
            continue
        out[d] = float(iv) * 100.0
    return out


@dataclass
class TransitionRow:
    date: date
    net_gex: float
    d_gex: float | None = None
    d_gex_z: float | None = None
    atm_iv: float | None = None  # vol points (clean, iv_tenor)
    d_iv_pt: float | None = None
    state: str = STATE_BASE
    spot: float | None = None
    flip: float | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "date": self.date.isoformat(),
            "net_gex": self.net_gex,
            "d_gex": self.d_gex,
            "d_gex_z": self.d_gex_z,
            "atm_iv": self.atm_iv,
            "d_iv_pt": self.d_iv_pt,
            "state": self.state,
            "spot": self.spot,
            "flip": self.flip,
        }


@dataclass
class TransitionResult:
    rows: list[TransitionRow] = field(default_factory=list)
    mu: float | None = None  # trailing ΔGEX mean
    sigma: float | None = None  # trailing ΔGEX stdev
    n_changes: int = 0  # ΔGEX sample size (contiguous only)

    @property
    def latest(self) -> TransitionRow | None:
        return self.rows[-1] if self.rows else None


def classify(
    d_gex_z: float | None,
    d_iv_pt: float | None,
    *,
    k: float = DEFAULT_K,
    iv_flat: float = DEFAULT_IV_FLAT_PT,
    iv_confirm: float = DEFAULT_IV_CONFIRM_PT,
) -> str:
    """Map (ΔGEX z, ΔIV pt) to a state label. Unknown inputs → base."""
    if d_gex_z is None:
        return STATE_BASE
    if d_gex_z <= -k:
        if d_iv_pt is not None and abs(d_iv_pt) <= iv_flat:
            return STATE_QUIET
        if d_iv_pt is not None and d_iv_pt >= iv_confirm:
            return STATE_CONFIRMED
        return STATE_DROP
    if d_gex_z >= k:
        return STATE_REBUILD
    return STATE_BASE


def compute(
    gamma_rows: list[dict[str, Any]],
    iv_tenor_rows: list[dict[str, Any]] | None = None,
    *,
    tenor_dte: int = 30,
    k: float = DEFAULT_K,
    iv_flat: float = DEFAULT_IV_FLAT_PT,
    iv_confirm: float = DEFAULT_IV_CONFIRM_PT,
) -> TransitionResult:
    """Build the daily transition series from raw tool reads.

    ``gamma_rows`` = ``get_gamma_history(...)['rows']``; ``iv_tenor_rows`` =
    ``get_iv_tenor(...)['rows']`` (clean ATM IV). ΔGEX is computed only across
    CONTIGUOUS trading days (a gap > _GAP_DAYS, e.g. the June outage, breaks the
    difference — it is not a real one-day move). ΔGEX_z uses the mean/stdev of
    all contiguous ΔGEX in the window. ΔIV prefers the iv_tenor map and falls
    back to the noisy gamma-history IV only if the clean value is missing.
    """
    eod = eod_gex_series(gamma_rows)
    ivmap = iv_atm_map(iv_tenor_rows or [], tenor_dte=tenor_dte)

    # First pass: raw ΔGEX / ΔIV across contiguous days.
    raw: list[TransitionRow] = []
    prev: dict[str, Any] | None = None
    for e in eod:
        d = e["date"]
        atm = ivmap.get(d)
        if atm is None and e.get("atm_iv_raw") is not None:
            atm = float(e["atm_iv_raw"]) * 100.0
        row = TransitionRow(
            date=d,
            net_gex=e["gex"],
            atm_iv=atm,
            spot=e.get("spot"),
            flip=e.get("flip"),
        )
        if prev is not None and (d - prev["date"]).days <= _GAP_DAYS:
            row.d_gex = e["gex"] - prev["gex"]
            if atm is not None and prev.get("atm") is not None:
                row.d_iv_pt = round(atm - prev["atm"], 4)
        raw.append(row)
        prev = {"date": d, "gex": e["gex"], "atm": atm}

    changes = [r.d_gex for r in raw if r.d_gex is not None]
    mu = statistics.mean(changes) if changes else None
    sigma = statistics.pstdev(changes) if len(changes) >= 2 else None

    for r in raw:
        if r.d_gex is not None and sigma:
            r.d_gex_z = (r.d_gex - mu) / sigma
        r.state = classify(r.d_gex_z, r.d_iv_pt, k=k, iv_flat=iv_flat, iv_confirm=iv_confirm)

    return TransitionResult(rows=raw, mu=mu, sigma=sigma, n_changes=len(changes))
