"""Options-flow aggregation (descriptive read-through, NOT a signal generator).

Vendor-agnostic: takes a normalized flow-enriched chain (one row per traded
strike/contract carrying a premium notional and an optional signed buy-sell
value) and summarizes the session's positioning: call vs put notional, the
put/call tilt, net premium, and the largest prints. ``ConvexClient`` maps the
convexlib flow fields onto the column names used here.

This module also detects multi-leg packages from per-trade time & sales
(``detect_structures``). It does NOT write to the ``signals`` table
(CLAUDE.md rule 4) — everything here is a flow/regime descriptor only.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from trading_intel.errors import ComputationError

_REQUIRED = ("opt_kind", "premium")


@dataclass
class FlowSummary:
    """Aggregate options-flow stats for one symbol/session."""

    call_notional: float
    put_notional: float
    net_premium: float | None
    n_prints: int
    top_prints: list[dict] = field(default_factory=list)

    @property
    def put_call_ratio(self) -> float | None:
        return (self.put_notional / self.call_notional) if self.call_notional else None

    @property
    def tilt(self) -> str:
        ratio = self.put_call_ratio
        if ratio is None:
            return "n/a"
        if ratio >= 1.3:
            return "defensive (put-heavy)"
        if ratio <= 0.77:
            return "offensive (call-heavy)"
        return "balanced"


def aggregate_flow(chain: pd.DataFrame, *, top_n: int = 10) -> FlowSummary:
    """Summarize call/put notional, net premium, and the largest prints.

    Needs ``opt_kind`` (call/put) and ``premium`` ($ notional traded). Optional
    ``signed`` ($ buy minus sell) drives ``net_premium``; ``strike``/
    ``expiration``/``iv`` enrich the top-prints list when present.
    """
    if chain is None or chain.empty:
        raise ComputationError("Empty chain: cannot aggregate flow")
    missing = [c for c in _REQUIRED if c not in chain.columns]
    if missing:
        raise ComputationError(f"Flow chain missing columns: {missing}")

    df = chain.copy()
    df["premium"] = pd.to_numeric(df["premium"], errors="coerce").fillna(0.0)
    side = df["opt_kind"].astype(str).str.upper().str[0]

    call_notional = float(df.loc[side == "C", "premium"].sum())
    put_notional = float(df.loc[side == "P", "premium"].sum())

    net_premium: float | None = None
    if "signed" in df.columns:
        net_premium = float(pd.to_numeric(df["signed"], errors="coerce").fillna(0.0).sum())

    cols = [c for c in ("expiration", "strike", "opt_kind", "premium", "iv") if c in df.columns]
    top_prints = df.sort_values("premium", ascending=False).head(top_n)[cols].to_dict("records")

    return FlowSummary(
        call_notional=call_notional,
        put_notional=put_notional,
        net_premium=net_premium,
        n_prints=len(df),
        top_prints=top_prints,
    )


def format_flow_markdown(summary: FlowSummary) -> str:
    """Render a FlowSummary as the report's flow section (markdown)."""
    lines = ["## Flow (today)"]
    if summary.call_notional == 0 and summary.put_notional == 0:
        lines.append("No premium traded yet (market closed / pre-open).")
        return "\n".join(lines)
    pcr = summary.put_call_ratio
    pcr_txt = f"{pcr:.2f}" if pcr is not None else "n/a"
    lines.append(
        f"Put notional ${summary.put_notional / 1e6:.0f}M vs "
        f"call ${summary.call_notional / 1e6:.0f}M (P/C {pcr_txt}) - "
        f"{summary.tilt} across {summary.n_prints} strikes."
    )
    if summary.net_premium is not None:
        lines.append(f"Net premium (buy-sell): ${summary.net_premium / 1e6:+.0f}M.")
    if summary.top_prints:
        lines.append("Largest strikes by premium:")
        for pr in summary.top_prints[:5]:
            lines.append(
                f"- {pr.get('expiration')} {pr.get('strike')} {pr.get('opt_kind')}: "
                f"${float(pr.get('premium', 0)) / 1e6:.1f}M"
            )
    return "\n".join(lines)


# ── Per-trade structure detection (multi-leg UOA packages) ──────────────────
# Legs printed at the SAME millisecond on the SAME root are almost always one
# package (a broker fills every leg of a spread/combo on one ticket). We group
# on (root, time) and classify the package shape. Descriptive only — this never
# writes to the signals table (CLAUDE.md rule 4).

_TAS_REQUIRED = ("time", "root", "expiration", "strike", "opt_kind", "size", "premium")


@dataclass
class Structure:
    """One detected multi-leg (or single) package printed on a single ticket."""

    time: pd.Timestamp
    root: str
    kind: str  # "call spread", "put spread", "straddle", "strangle/combo", ...
    n_legs: int
    total_premium: float
    net_premium: float  # buy premium minus sell premium (aggressor-signed)
    expirations: list
    legs: list[dict] = field(default_factory=list)


def _classify_package(g: pd.DataFrame) -> str:
    n = len(g)
    if n == 1:
        return "single"
    kinds = set(g["opt_kind"].astype(str))
    n_exp = g["expiration"].nunique(dropna=False)
    n_strikes = g["strike"].nunique(dropna=False)
    if n_exp > 1:
        return "calendar/diagonal"
    if kinds <= {"call"}:
        return "call spread" if n == 2 else "call fly/ladder"
    if kinds <= {"put"}:
        return "put spread" if n == 2 else "put fly/ladder"
    # both calls and puts, single expiry
    if n == 2:
        return "straddle" if n_strikes == 1 else "strangle/combo"
    return "multi-leg package"


def _fmt_date(e: object) -> str:
    """Format an expiration (Timestamp or str) as YYYY-MM-DD; fall back to str."""
    try:
        ts = pd.Timestamp(e)
        if pd.notna(ts):
            return ts.strftime("%Y-%m-%d")
    except (ValueError, TypeError):
        pass
    return str(e)


def detect_structures(tas: pd.DataFrame, *, min_premium: float = 0.0) -> list[Structure]:
    """Group simultaneous per-trade prints into multi-leg packages and classify.

    Takes a ``ConvexClient.time_and_sales`` frame (one row per print). Prints
    sharing the same ``root`` and ``time`` (ms) are treated as one ticket;
    repeat fills of the SAME contract (expiration+strike+kind) are collapsed
    into one leg (size/premium summed) so classification reflects the true
    structure rather than the fill count. Net premium is aggressor-signed
    (``buy`` adds, ``sell`` subtracts). Returns packages sorted by total premium,
    descending. Purely descriptive.
    """
    if tas is None or tas.empty:
        return []
    missing = [c for c in _TAS_REQUIRED if c not in tas.columns]
    if missing:
        raise ComputationError(f"tas frame missing columns: {missing}")

    df = tas.copy()
    df["premium"] = pd.to_numeric(df["premium"], errors="coerce").fillna(0.0)
    side = (
        df["aggressor_side"].astype(str).str.lower()
        if "aggressor_side" in df.columns
        else pd.Series("undefined", index=df.index)
    )
    df["_signed"] = df["premium"].where(side == "buy", -df["premium"]).where(
        side != "undefined", 0.0
    )

    out: list[Structure] = []
    for (root, t), g in df.groupby(["root", "time"], sort=False):
        total = float(g["premium"].sum())
        if total < min_premium:
            continue
        # Collapse repeat fills of the same contract into a single leg.
        legs: list[dict] = []
        for (exp, stk, kind), lg in g.groupby(
            ["expiration", "strike", "opt_kind"], dropna=False, sort=False
        ):
            leg = {
                "expiration": exp,
                "strike": stk,
                "opt_kind": kind,
                "premium": float(lg["premium"].sum()),
            }
            if "size" in lg.columns:
                leg["size"] = float(pd.to_numeric(lg["size"], errors="coerce").fillna(0.0).sum())
            for c in ("price", "iv", "aggressor_side"):
                if c in lg.columns:
                    leg[c] = lg[c].iloc[0]
            legs.append(leg)
        legs_df = pd.DataFrame(legs)
        out.append(
            Structure(
                time=t,
                root=str(root),
                kind=_classify_package(legs_df),
                n_legs=len(legs_df),
                total_premium=total,
                net_premium=float(g["_signed"].sum()),
                expirations=sorted({_fmt_date(e) for e in legs_df["expiration"].tolist()}),
                legs=legs,
            )
        )
    out.sort(key=lambda s: s.total_premium, reverse=True)
    return out


def format_structures_markdown(structures: list[Structure], *, top_n: int = 8) -> str:
    """Render detected multi-leg packages as a report sub-section (markdown)."""
    lines = ["## Notable packages (per-trade)"]
    multi = [s for s in structures if s.n_legs > 1]
    if not multi:
        lines.append("No multi-leg packages detected in the sampled prints.")
        return "\n".join(lines)
    for s in multi[:top_n]:
        exps = "/".join(s.expirations)
        strikes = "/".join(
            f"{leg.get('strike'):g}" if isinstance(leg.get("strike"), (int, float))
            else str(leg.get("strike"))
            for leg in s.legs
            if leg.get("strike") is not None
        )
        lines.append(
            f"- {s.root} {exps} {s.kind} ({s.n_legs} legs, {strikes}): "
            f"${s.total_premium / 1e6:.1f}M total, ${s.net_premium / 1e6:+.1f}M net"
        )
    return "\n".join(lines)


# Greek-OI + flow metrics summed per expiry (reproduces ConvexValue `flowsum`).
_FLOWSUM_METRICS = (
    "volm_buy",
    "volm_sell",
    "oi",
    "gxoi",
    "dxoi",
    "vxoi",
    "txoi",
    "vannaxoi",
    "vommaxoi",
    "charmxoi",
)


def flowsum_by_expiry(chain: pd.DataFrame) -> pd.DataFrame:
    """Aggregate a flow-summary chain into calls/puts/total rows per expiry.

    Needs ``expiration``, ``opt_kind``, and any subset of ``_FLOWSUM_METRICS``.
    Adds ``volm_bs`` = volm_buy - volm_sell. Returns a long DataFrame with
    columns ``expiry``, ``side`` (calls/puts/total), then the metric sums.
    """
    if chain is None or chain.empty:
        raise ComputationError("Empty chain: cannot build flow summary")
    for col in ("expiration", "opt_kind"):
        if col not in chain.columns:
            raise ComputationError(f"Flowsum chain missing column: {col!r}")

    df = chain.copy()
    metrics = [m for m in _FLOWSUM_METRICS if m in df.columns]
    for m in metrics:
        df[m] = pd.to_numeric(df[m], errors="coerce").fillna(0.0)
    df["_side"] = df["opt_kind"].astype(str).str.upper().str[0].map({"C": "calls", "P": "puts"})

    out_rows: list[dict] = []
    for exp, g in df.groupby("expiration", sort=True):
        for side in ("calls", "puts"):
            sg = g[g["_side"] == side]
            out_rows.append(
                {"expiry": exp, "side": side, **{m: float(sg[m].sum()) for m in metrics}}
            )
        out_rows.append({"expiry": exp, "side": "total", **{m: float(g[m].sum()) for m in metrics}})

    out = pd.DataFrame(out_rows)
    if "volm_buy" in out.columns and "volm_sell" in out.columns:
        out["volm_bs"] = out["volm_buy"] - out["volm_sell"]
    return out
