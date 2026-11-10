"""surface_feed.py - created 2026-11-10 for Mithil. Feeds the cockpit's Surface tab (3D gamma / vanna / charm
by strike x expiry, the ConvexValue "Net GXOI" style surface).

WHAT IT DOES
  wide_feed.py already reads every collector pull (raw_YYYYMMDD.ndjson.gz, pull "idx", every minute, zero extra
  Convex calls). For SPX it now also hands each new minute to build_surface() here, which keeps every expiry
  APART (wide_feed sums them into 0DTE and 1-7 DTE) and writes data/surface_live_SPX.json:

      {"v": 1, "day", "sym", "t" (HH:MM), "x" (spot), "k": [strikes], "beta", "beta_src",
       "ex": [{"e": expiry, "dte", "fam": "PM"|"AM", "iv"}],
       "g" | "va" | "ch": {"oi": [[per strike] per expiry], "vol": [...], "sig": [...]}}

  Same per-strike maths and units as wide_feed (and so the MAP / Orbs): gamma $bn of delta per 1% move, vanna
  $bn of delta per 1 vol point, charm $bn of delta per calendar HOUR. Three dealer books per greek:
      oi  = call OI - put OI                    (naive: dealers long calls, short puts)
      vol = call volume - put volume
      sig = (calls sold - bought) + (puts sold - bought)   (Convex trade tags; customer buy = dealer short)
  The page adds them the way the MAP does: OI | Vol | OI + vol | OI + signed.

  GAMMA + VANNA. Two ways, the page switches:
    beta  : gamma + beta x vanna = the dealer delta change for a +1% SPX move once implied vol moves with spot.
            beta = vol points per +1% move, fitted here from this session's minute pairs (spot, ATM IV of the
            first expiry after today): least squares on the 1-minute changes over the last 60 minutes, clamped
            to [-4, +1]. Fewer than 20 pairs -> -1.0 (a typical SPX day) and beta_src = "default".
    normalized: each greek scaled to its own largest absolute value, then added - shape only, no $ meaning.

WHAT IT NEVER TOUCHES
  Read-only on the raw file through wide_feed; writes only data/surface_live_SPX.json. No production file
  (cvx_live, the MAP, live.json) changes.
"""
from __future__ import annotations

from collections import deque
from typing import Any

SURFACE_SYMS = ("SPX",)
BETA_WINDOW = 60          # minutes of 1-minute changes used for the spot-vol fit
BETA_MIN_PAIRS = 20
BETA_DEFAULT = -1.0       # vol points per +1% SPX move when the session is too young to fit
BETA_CLAMP = (-4.0, 1.0)


class BetaTracker:
    """Spot-vol beta from the session's own minutes (vol points per +1% spot move)."""

    def __init__(self) -> None:
        self.pts: deque[tuple[str, float, float]] = deque(maxlen=BETA_WINDOW + 1)

    def add(self, t: str, spot: float | None, iv: float | None) -> None:
        if not (spot and iv and 0.01 < iv < 5.0):
            return
        if self.pts and self.pts[-1][0] == t:
            self.pts[-1] = (t, spot, iv)
        else:
            self.pts.append((t, spot, iv))

    def beta(self) -> tuple[float, str]:
        p = list(self.pts)
        xs, ys = [], []
        for (_t0, s0, v0), (_t1, s1, v1) in zip(p, p[1:], strict=False):
            xs.append((s1 / s0 - 1.0) * 100.0)       # % move
            ys.append((v1 - v0) * 100.0)             # vol points
        if len(xs) < BETA_MIN_PAIRS:
            return BETA_DEFAULT, "default"
        sxx = sum(x * x for x in xs)
        if sxx <= 1e-12:
            return BETA_DEFAULT, "default"
        b = sum(x * y for x, y in zip(xs, ys, strict=True)) / sxx
        return max(BETA_CLAMP[0], min(BETA_CLAMP[1], b)), "fit %d min" % len(xs)


def _r(x: float) -> float:
    return 0.0 if x == 0 else float("%.5g" % x)


def build_surface(W: Any, L: Any, sym: str, d: dict, today: Any, now: Any,  # noqa: ANN401
                  beta: BetaTracker | None = None) -> dict:
    """One surface payload. W = the wide_feed module (expiry_books, greeks_u, charm_u, strike_iv, strikes),
    L = cvx_live (number helpers). Pure: no file I/O."""
    spot = d["spot"]
    ks = W.strikes(sym, spot)
    gk = 100.0 * spot * spot * 0.01 / L.B          # gamma: $bn per 1% move
    vk = 100.0 * spot * 0.01 / L.B                 # vanna: $bn of delta per 1 vol point
    hk = 100.0 * spot / L.B / 8760.0               # charm: $bn of delta per calendar HOUR
    books = sorted(W.expiry_books(sym, d, today, now), key=lambda b: (b[1], b[5]))
    ex, out = [], {f: {b: [] for b in ("oi", "vol", "sig")} for f in ("g", "va", "ch")}
    first_iv = None
    for grp, dte, t_g, sigma, bk, ed, fwd, t_cal in books:
        fam = am_family(bk)    # wide_feed tags every non-0DTE book "7"; the contract roots say AM or PM
        ex.append({"e": ed, "dte": dte, "fam": fam, "iv": None if sigma is None else round(sigma, 4)})
        if first_iv is None and dte >= 1 and sigma:
            first_iv = sigma
        rows = {f: {b: [0.0] * len(ks) for b in ("oi", "vol", "sig")} for f in ("g", "va", "ch")}
        for i, k in enumerate(ks):
            cp = bk.get(k)
            if not cp:
                continue
            c, p = cp
            gu, vu = W.greeks_u(spot, k, t_g, sigma)
            hu = W.charm_u(spot, k, t_cal, W.strike_iv(k, fwd, c, p, sigma))
            cOI, pOI = L._n(c.get("oi")), L._n(p.get("oi"))
            cV, pV = L._n(c.get("volm")), L._n(p.get("volm"))
            cb, cs = L._n(c.get("volm_buy")), L._n(c.get("volm_sell"))
            pb, ps = L._n(p.get("volm_buy")), L._n(p.get("volm_sell"))
            n = {"oi": cOI - pOI, "vol": cV - pV, "sig": (cs - cb) + (ps - pb)}
            for f, u, kk in (("g", gu, gk), ("va", vu, vk), ("ch", hu, hk)):
                for b, nb in n.items():
                    rows[f][b][i] = u * nb * kk
        for f in rows:
            for b in rows[f]:
                out[f][b].append([_r(v) for v in rows[f][b]])
    t = now.strftime("%H:%M")
    bval, bsrc = BETA_DEFAULT, "default"
    if beta is not None:
        beta.add(t, spot, first_iv)
        bval, bsrc = beta.beta()
    return {"v": 1, "day": today.isoformat(), "sym": sym, "t": t, "x": round(spot, 2), "k": ks,
            "beta": round(bval, 3), "beta_src": bsrc, "ex": ex, **out}


def am_family(bk: dict) -> str:
    """'AM' when every contract symbol in the book carries an AM-settled root (.SPX monthly / .SPXO weekly)."""
    roots = set()
    for c, p in bk.values():
        for x in (c, p):
            s = str((x or {}).get("_sym") or "")
            j = 1
            while j < len(s) and s[j].isalpha():
                j += 1
            if j > 1:
                roots.add(s[1:j])
    return "AM" if roots and roots <= {"SPX", "SPXO"} else "PM"
