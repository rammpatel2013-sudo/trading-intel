"""Own IVs for the MORNING (08:35) book, for the MCP readers (2026-11-21).

Why: the vendor IV column on the 08:35 SPX book is broken on a few expiries most mornings (11/13: call vs put IV
18-55 vol pts apart; Nov-18 7,860 call at 0.23% -> a fake 7,860 "call wall"). Prices are fine, and the 0DTE card /
close odds / week map already price from our own IVs (``clean_book``). get_walls / get_straddle / get_profile /
get_exposure_by_strike read the stored ``iv`` / ``gxoi`` / ``dxoi`` and so carried the bad values all day.

``book_fix(session, symbol, ts)`` returns ``None`` for an end-of-day book (vendor IV is fine during and after
regular hours: within +-0.3 pts of our own on every expiry, 11/14 measure) and for a book without quotes. For a
morning book it builds one smile per expiry from the quote mids on the put-call-parity forward
(``clean_book.build_smiles``) and gives, per (expiry, strike):
  * ``iv(expiry, strike, vendor)``      our IV where the smile covers the strike, else the vendor IV
  * ``gxoi(...)`` / ``dxoi(...)``      gamma-OI / delta-OI from our IV, in the VENDOR's units: the unit factor is
                                        calibrated per book on near-forward contracts where vendor and own IV agree
                                        (a ratio of greeks does not work: a 0.23% IV gives ~0 model gamma far from
                                        the money while the vendor's stored gxoi was huge). Vendor value where there
                                        is no own IV or no calibration.
Descriptive (rule 4). Never touches OI.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

import numpy as np
from scipy.special import ndtr

from trading_intel.market import clean_book as cb
from trading_intel.market.book_spot import book_kind

MIN_SMILE = 5          # strikes in a smile before we trust it
BIG_GAP = 0.05         # 5 vol pts: counted as a "fixed" contract in the note


@dataclass
class BookFix:
    book_ts: datetime
    smiles: dict[date, cb.ExpirySmile] = field(default_factory=dict)
    n_used: int = 0
    n_big: int = 0
    unit_g: float | None = None     # vendor gxoi / (model gamma x OI) on clean near-forward contracts
    unit_d: float | None = None     # vendor dxoi / (model delta x OI)

    def _sm(self, expiry: Any) -> cb.ExpirySmile | None:
        if isinstance(expiry, datetime):
            expiry = expiry.date()
        return self.smiles.get(expiry)

    def own_iv(self, expiry: Any, strike: float) -> float | None:
        sm = self._sm(expiry)
        if sm is None or len(sm.strikes) < MIN_SMILE:
            return None
        k = float(strike)
        if k < sm.strikes[0] or k > sm.strikes[-1]:
            return None
        v = float(np.interp(k, sm.strikes, sm.ivs))
        return v if np.isfinite(v) and v > 0 else None

    def iv(self, expiry: Any, strike: float, vendor: float | None) -> float | None:
        own = self.own_iv(expiry, strike)
        if own is None:
            return vendor
        self.n_used += 1
        if vendor is None or abs(own - float(vendor)) > BIG_GAP:
            self.n_big += 1
        return own

    def _model(self, expiry: Any, strike: float, cp: str, sig: float) -> tuple[float, float] | None:
        sm = self._sm(expiry)
        if sm is None or not sm.forward:
            return None
        g = float(cb.b76_gamma(sm.forward, np.array([float(strike)]), np.array([sig]), sm.t)[0])
        return g, _delta(sm.forward, float(strike), sig, sm.t, str(cp).upper().startswith("C"))

    def gxoi(self, expiry: Any, strike: float, cp: str, oi: Any, vendor: float | None) -> float | None:
        own = self.own_iv(expiry, strike)
        m = self._model(expiry, strike, cp, own) if own is not None else None
        if m is None or self.unit_g is None or oi is None:
            return vendor
        return self.unit_g * m[0] * float(oi)

    def dxoi(self, expiry: Any, strike: float, cp: str, oi: Any, vendor: float | None) -> float | None:
        own = self.own_iv(expiry, strike)
        m = self._model(expiry, strike, cp, own) if own is not None else None
        if m is None or self.unit_d is None or oi is None:
            return vendor
        return self.unit_d * m[1] * float(oi)

    def calibrate(self, rows: list[dict[str, Any]]) -> None:
        """Unit factors from contracts within 2% of their forward where vendor and own IV agree within 0.5 pt."""
        ug, ud = [], []
        for r in rows:
            own = self.own_iv(r["expiry"], r["strike"])
            sm = self._sm(r["expiry"])
            ven, oi = r.get("iv"), r.get("oi") or 0
            if own is None or sm is None or not sm.forward or ven is None or oi <= 0:
                continue
            if abs(r["strike"] - sm.forward) > 0.02 * sm.forward or abs(own - float(ven)) > 0.005:
                continue
            m = self._model(r["expiry"], r["strike"], r["cp"], own)
            if m is None:
                continue
            if r.get("gxoi") is not None and m[0] > 0:
                ug.append(float(r["gxoi"]) / (m[0] * oi))
            if r.get("dxoi") is not None and abs(m[1]) > 0.05:
                ud.append(float(r["dxoi"]) / (m[1] * oi))
        self.unit_g = float(np.median(ug)) if len(ug) >= 10 else None
        self.unit_d = float(np.median(ud)) if len(ud) >= 10 else None

    def note(self) -> dict[str, Any]:
        return {"iv_source": "own IVs from the 08:35 quote mids (vendor IV where no smile)",
                "contracts_repriced": self.n_used, "contracts_off_by_5pts_or_more": self.n_big}


def _delta(f: float, k: float, sig: float, t: float, call: bool) -> float:
    sd = max(sig * np.sqrt(max(t, 1e-9)), 1e-12)
    d1 = (np.log(f / k) + 0.5 * sd * sd) / sd
    n = float(ndtr(d1))
    return n if call else n - 1.0


def book_fix(session: Any, symbol: str, ts: datetime, max_days: int = 400) -> BookFix | None:
    """Own-IV fix for the book stamped ``ts`` - None unless it is a morning (08:35) book with quotes."""
    try:
        if book_kind(session, symbol, ts) != "am":
            return None
        rows = cb.book_rows(session, symbol, ts, max_days=max_days)
    except Exception:  # noqa: BLE001 - a reader must never fail because the fix could not be built
        return None
    if not rows:
        return None
    smiles = cb.build_smiles(rows, cb.book_time("am", ts.date()))
    by_exp: dict[date, cb.ExpirySmile] = {}
    for (exp, am), sm in sorted(smiles.items(), key=lambda kv: kv[0][1]):   # PM first, AM only if no PM
        if exp in by_exp or not sm.forward or len(sm.strikes) < MIN_SMILE:
            continue
        by_exp[exp] = sm
    if not by_exp:
        return None
    fx = BookFix(book_ts=ts, smiles=by_exp)
    fx.calibrate(_greek_rows(session, symbol, ts))
    return fx


def _greek_rows(session: Any, symbol: str, ts: datetime) -> list[dict[str, Any]]:
    from sqlalchemy import select

    from trading_intel.memory.models import OiChainEod

    q = select(OiChainEod.expiry, OiChainEod.strike, OiChainEod.cp, OiChainEod.oi, OiChainEod.iv,
               OiChainEod.gxoi, OiChainEod.dxoi).where(
        OiChainEod.symbol == symbol.upper(), OiChainEod.ts == ts, OiChainEod.source.like("convex%"))
    return [{"expiry": r[0], "strike": float(r[1]), "cp": r[2], "oi": float(r[3] or 0), "iv": r[4],
             "gxoi": r[5], "dxoi": r[6]} for r in session.execute(q).all()]
