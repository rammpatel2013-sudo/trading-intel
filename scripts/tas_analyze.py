"""Analyze a daily TAS capture CSV into an Excel flow-intelligence workbook (Phase 2).

Reads one ``data/tas/YYYY-MM-DD.csv`` produced by ``scripts/tas_capture.py`` and
writes ``data/tas/analysis_YYYY-MM-DD.xlsx`` with one sheet per view:

    Summary          headline stats (premium, prints, tickers, net market delta)
    By Ticker        premium + net signed delta-notional per root, ranked
    Repeat Contracts  same (root/expiry/strike/side) hit repeatedly
    Blocks           the single biggest prints
    Sweeps           same contract+side fired in a tight time cluster
    Combos           multi-leg packages sharing a root+timestamp
    Unusual Rank     composite "unusual flow" score per ticker (the watchlist)

Descriptive only — this ranks unusual flow, it never emits a trade signal
(FlashAlpha rule 4). Optional ``--with-gex`` annotates the By-Ticker sheet with
our own stored gamma regime (read-only DB; rule 1 keeps Convex in clients/).

Run (after a real capture exists):
    python scripts/tas_analyze.py                    # today's CSV
    python scripts/tas_analyze.py --date 2026-06-03  # a specific day
    python scripts/tas_analyze.py --file path.csv --with-gex
"""

from __future__ import annotations

import argparse
import re
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

_ET = ZoneInfo("America/New_York")
_CONTRACT_RE = re.compile(r"^\.?([A-Za-z]+)(\d{6})([CcPp])(\d+(?:\.\d+)?)$")
_BLOCK_TOP = 100
_REPEAT_MIN = 3
_SWEEP_WINDOW_S = 6.0
_SWEEP_MIN_LEGS = 3
_SHORT_DTE = 7

# Composite-score weights (sum = 1.0). Tunable; documented in tas_pipeline.md.
_W_NOTIONAL = 0.30
_W_REPEAT = 0.20
_W_AGGRESSION = 0.15
_W_SHORT_OTM = 0.20
_W_DIRECTIONAL = 0.15


# ── parse + derive ─────────────────────────────────────────────────────


def parse_contract(symbol: str) -> tuple[str | None, date | None, str | None, float | None]:
    """Decode a Convex option symbol (``.CVS260702C92`` -> CVS / 2026-07-02 / C / 92)."""
    m = _CONTRACT_RE.match(str(symbol).strip())
    if not m:
        return None, None, None, None
    root, ymd, cp, strike = m.groups()
    try:
        expiry = datetime.strptime(ymd, "%y%m%d").date()
    except ValueError:
        expiry = None
    return root.upper(), expiry, cp.upper(), float(strike)


def load_csv(path: Path, *, as_of: date) -> pd.DataFrame:
    """Load a capture CSV and add decoded contract + derived analysis columns."""
    df = pd.read_csv(path)
    if df.empty:
        return df
    decoded = df["symbol"].map(parse_contract)
    df["root"] = [d[0] for d in decoded]
    df["expiry"] = [d[1] for d in decoded]
    df["cp"] = [d[2] for d in decoded]
    df["strike"] = [d[3] for d in decoded]
    df = df[df["root"].notna()].copy()

    df["notional"] = pd.to_numeric(df.get("notional"), errors="coerce").fillna(0.0)
    df["size"] = pd.to_numeric(df.get("size"), errors="coerce").fillna(0.0)
    df["price"] = pd.to_numeric(df.get("price"), errors="coerce")
    df["delta"] = pd.to_numeric(df.get("delta"), errors="coerce")
    df["spot"] = pd.to_numeric(df.get("spot"), errors="coerce")
    df["side"] = df.get("side", "unknown").astype(str).str.lower()
    df["ts"] = pd.to_datetime(df.get("time"), errors="coerce")

    df["dte"] = df["expiry"].map(lambda e: (e - as_of).days if isinstance(e, date) else None)
    is_call = df["cp"] == "C"
    df["otm"] = (is_call & (df["strike"] > df["spot"])) | (~is_call & (df["strike"] < df["spot"]))
    side_sign = df["side"].map({"buy": 1.0, "sell": -1.0}).fillna(0.0)
    df["dollar_delta"] = (df["delta"] * df["size"] * 100.0 * df["spot"]).fillna(0.0)
    df["signed_dollar_delta"] = df["dollar_delta"] * side_sign
    return df


# ── per-view aggregations ──────────────────────────────────────────────


def by_ticker(df: pd.DataFrame) -> pd.DataFrame:
    """Premium + net signed delta-notional per root, ranked by total notional."""
    is_call = df["cp"] == "C"
    g = df.assign(
        call_notional=df["notional"].where(is_call, 0.0),
        put_notional=df["notional"].where(~is_call, 0.0),
        buy_notional=df["notional"].where(df["side"] == "buy", 0.0),
    ).groupby("root")
    out = g.agg(
        prints=("notional", "size"),
        total_notional=("notional", "sum"),
        call_notional=("call_notional", "sum"),
        put_notional=("put_notional", "sum"),
        buy_notional=("buy_notional", "sum"),
        net_dollar_delta=("signed_dollar_delta", "sum"),
        gross_dollar_delta=("dollar_delta", lambda s: s.abs().sum()),
    ).reset_index()
    out["net_premium_call_put"] = out["call_notional"] - out["put_notional"]
    out["pct_buy"] = (out["buy_notional"] / out["total_notional"]).where(
        out["total_notional"] > 0, 0.0
    )
    out = out.drop(columns=["buy_notional"])
    return out.sort_values("total_notional", ascending=False).reset_index(drop=True)


def repeat_contracts(df: pd.DataFrame, *, min_count: int = _REPEAT_MIN) -> pd.DataFrame:
    """Contracts (root/expiry/strike/cp) printed at least ``min_count`` times."""
    g = df.groupby(["root", "expiry", "strike", "cp"])
    out = g.agg(
        n_prints=("notional", "size"),
        total_notional=("notional", "sum"),
        total_size=("size", "sum"),
        avg_price=("price", "mean"),
        buy_prints=("side", lambda s: (s == "buy").sum()),
        sell_prints=("side", lambda s: (s == "sell").sum()),
    ).reset_index()
    out = out[out["n_prints"] >= min_count]
    out["dominant_side"] = out.apply(
        lambda r: (
            "buy"
            if r["buy_prints"] > r["sell_prints"]
            else "sell" if r["sell_prints"] > r["buy_prints"] else "mixed"
        ),
        axis=1,
    )
    return out.sort_values("total_notional", ascending=False).reset_index(drop=True)


def blocks(df: pd.DataFrame, *, top: int = _BLOCK_TOP) -> pd.DataFrame:
    """The biggest single prints (one row = one trade)."""
    cols = [
        "ts",
        "root",
        "expiry",
        "strike",
        "cp",
        "side",
        "size",
        "price",
        "notional",
        "dte",
        "delta",
        "otm",
    ]
    present = [c for c in cols if c in df.columns]
    return df[present].sort_values("notional", ascending=False).head(top).reset_index(drop=True)


def sweeps(
    df: pd.DataFrame, *, window_s: float = _SWEEP_WINDOW_S, min_legs: int = _SWEEP_MIN_LEGS
) -> pd.DataFrame:
    """Same contract+side fired as a tight time cluster (a sweep)."""
    if df["ts"].isna().all():
        return pd.DataFrame(
            columns=[
                "root",
                "expiry",
                "strike",
                "cp",
                "side",
                "n_prints",
                "total_size",
                "total_notional",
                "window_seconds",
                "first_ts",
            ]
        )
    keys = ["root", "expiry", "strike", "cp", "side"]
    clusters: list[dict[str, object]] = []
    for key, grp in df.dropna(subset=["ts"]).sort_values("ts").groupby(keys):
        times = grp["ts"].tolist()
        start = 0
        for i in range(1, len(times) + 1):
            split = i == len(times) or (times[i] - times[i - 1]).total_seconds() > window_s
            if split:
                chunk = grp.iloc[start:i]
                if len(chunk) >= min_legs:
                    span = (chunk["ts"].iloc[-1] - chunk["ts"].iloc[0]).total_seconds()
                    clusters.append(
                        {
                            "root": key[0],
                            "expiry": key[1],
                            "strike": key[2],
                            "cp": key[3],
                            "side": key[4],
                            "n_prints": len(chunk),
                            "total_size": float(chunk["size"].sum()),
                            "total_notional": float(chunk["notional"].sum()),
                            "window_seconds": round(span, 2),
                            "first_ts": chunk["ts"].iloc[0],
                        }
                    )
                start = i
    out = pd.DataFrame(clusters)
    if out.empty:
        return out
    return out.sort_values("total_notional", ascending=False).reset_index(drop=True)


def combos(df: pd.DataFrame) -> pd.DataFrame:
    """Multi-leg packages: >=2 distinct contracts sharing a root + exact timestamp."""
    if df["ts"].isna().all():
        return pd.DataFrame(columns=["root", "ts", "n_legs", "structure", "total_notional", "legs"])
    rows: list[dict[str, object]] = []
    for (root, ts), grp in df.dropna(subset=["ts"]).groupby(["root", "ts"]):
        legs = grp.drop_duplicates(subset=["strike", "cp", "expiry"])
        if len(legs) < 2:
            continue
        cps = set(legs["cp"])
        strikes = legs["strike"].nunique()
        if cps == {"C", "P"} and strikes == 1:
            structure = "straddle/synthetic"
        elif cps == {"C", "P"}:
            structure = "risk-reversal/combo"
        elif len(cps) == 1 and strikes >= 2:
            structure = "vertical/spread"
        else:
            structure = "multi-leg"
        desc = ", ".join(f"{r.cp}{r.strike:g} x{int(r.size)}" for r in legs.itertuples())
        rows.append(
            {
                "root": root,
                "ts": ts,
                "n_legs": len(legs),
                "structure": structure,
                "total_notional": float(grp["notional"].sum()),
                "legs": desc,
            }
        )
    out = pd.DataFrame(rows)
    if out.empty:
        return out
    return out.sort_values("total_notional", ascending=False).reset_index(drop=True)


def unusual_rank(df: pd.DataFrame, tk: pd.DataFrame) -> pd.DataFrame:
    """Composite 0-100 'unusual flow' score per ticker (the ranked watchlist)."""
    rep = df.groupby(["root", "expiry", "strike", "cp"]).size().groupby("root").max()
    short_otm = (
        df.assign(
            so=df["notional"].where(
                (pd.to_numeric(df["dte"], errors="coerce") <= _SHORT_DTE) & df["otm"], 0.0
            )
        )
        .groupby("root")["so"]
        .sum()
    )

    out = tk.copy()
    out["max_repeat"] = out["root"].map(rep).fillna(1)
    out["short_otm_notional"] = out["root"].map(short_otm).fillna(0.0)
    out["short_otm_share"] = (out["short_otm_notional"] / out["total_notional"]).where(
        out["total_notional"] > 0, 0.0
    )
    out["aggression"] = (out["pct_buy"] - 0.5).abs() * 2.0
    out["directional"] = (out["net_dollar_delta"].abs() / out["gross_dollar_delta"]).where(
        out["gross_dollar_delta"] > 0, 0.0
    )

    def _rank01(s: pd.Series) -> pd.Series:
        if s.max() == s.min():
            return pd.Series(0.0, index=s.index)
        return (s - s.min()) / (s.max() - s.min())

    score = (
        _W_NOTIONAL * _rank01(out["total_notional"])
        + _W_REPEAT * _rank01(out["max_repeat"])
        + _W_AGGRESSION * out["aggression"].clip(0, 1)
        + _W_SHORT_OTM * out["short_otm_share"].clip(0, 1)
        + _W_DIRECTIONAL * out["directional"].clip(0, 1)
    )
    out["unusual_score"] = (score * 100.0).round(1)
    out["catalyst_flag"] = (out["short_otm_share"] > 0.4) & (out["directional"] > 0.5)
    cols = [
        "root",
        "unusual_score",
        "catalyst_flag",
        "total_notional",
        "max_repeat",
        "short_otm_share",
        "aggression",
        "directional",
        "net_dollar_delta",
        "pct_buy",
    ]
    return out[cols].sort_values("unusual_score", ascending=False).reset_index(drop=True)


# ── optional GEX cross-reference (read-only DB; rule 1) ─────────────────


def attach_gex(tk: pd.DataFrame) -> pd.DataFrame:
    """Annotate tickers we track with our latest stored gamma regime. Best-effort."""
    try:
        from sqlalchemy import select

        from trading_intel.config import get_settings
        from trading_intel.dashboard.watchlist_metrics import flip_distance, gamma_regime
        from trading_intel.memory.db import make_session_factory
        from trading_intel.memory.models import GreeksSnapshot
    except ImportError as exc:  # package not importable from this CWD
        print(f"  --with-gex skipped (import failed: {exc})")
        return tk

    regimes: dict[str, str] = {}
    flips: dict[str, float] = {}
    factory = make_session_factory(get_settings())
    with factory() as session:
        for root in tk["root"]:
            row = session.execute(
                select(GreeksSnapshot)
                .where(GreeksSnapshot.symbol == root)
                .order_by(GreeksSnapshot.ts.desc())
                .limit(1)
            ).scalar_one_or_none()
            if row is None:
                continue
            regimes[root] = gamma_regime(row.spot, row.gex_flip)
            fd = flip_distance(row.spot, row.gex_flip)
            if fd is not None:
                flips[root] = fd
    out = tk.copy()
    out["our_gamma_regime"] = out["root"].map(regimes)
    out["our_flip_dist"] = out["root"].map(flips)
    return out


# ── workbook ───────────────────────────────────────────────────────────


def _summary(df: pd.DataFrame, tk: pd.DataFrame, as_of: date) -> pd.DataFrame:
    biggest = df.loc[df["notional"].idxmax()] if not df.empty else None
    rows = [
        ("date", as_of.isoformat()),
        ("total_prints", len(df)),
        ("total_premium_$", round(float(df["notional"].sum()), 0)),
        ("unique_tickers", int(df["root"].nunique())),
        ("net_market_$delta", round(float(df["signed_dollar_delta"].sum()), 0)),
        ("most_active_ticker", tk.iloc[0]["root"] if not tk.empty else None),
        (
            "biggest_print",
            (
                None
                if biggest is None
                else f"{biggest['root']} {biggest['cp']}{biggest['strike']:g} "
                f"${biggest['notional']:,.0f}"
            ),
        ),
    ]
    return pd.DataFrame(rows, columns=["metric", "value"])


def write_workbook(path: Path, sheets: dict[str, pd.DataFrame]) -> None:
    """Write each frame to its own sheet with a frozen, autosized header."""
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        for name, frame in sheets.items():
            safe = frame if frame is not None and not frame.empty else pd.DataFrame({"": []})
            safe.to_excel(writer, sheet_name=name[:31], index=False)
            ws = writer.sheets[name[:31]]
            ws.freeze_panes = "A2"
            for col_cells in ws.columns:
                width = max(
                    (len(str(c.value)) for c in col_cells if c.value is not None), default=8
                )
                ws.column_dimensions[col_cells[0].column_letter].width = min(width + 2, 48)


def _resolve_csv(args: argparse.Namespace) -> tuple[Path, date]:
    if args.file:
        path = Path(args.file)
        stem = path.stem.replace("analysis_", "")
        try:
            as_of = date.fromisoformat(stem)
        except ValueError:
            as_of = datetime.now(_ET).date()
        return path, as_of
    day = args.date or datetime.now(_ET).strftime("%Y-%m-%d")
    return Path(args.out_dir) / f"{day}.csv", date.fromisoformat(day)


def main() -> None:
    p = argparse.ArgumentParser(description="Analyze a daily TAS capture into Excel.")
    p.add_argument("--date", help="capture date YYYY-MM-DD (default: today ET)")
    p.add_argument("--file", help="explicit CSV path (overrides --date/--out-dir)")
    p.add_argument("--out-dir", default="data/tas", help="folder holding the capture CSVs")
    p.add_argument("--with-gex", action="store_true", help="annotate with our stored gamma regime")
    args = p.parse_args()

    csv_path, as_of = _resolve_csv(args)
    if not csv_path.exists():
        raise SystemExit(f"no capture CSV at {csv_path} — run scripts/tas_capture.py during RTH")

    df = load_csv(csv_path, as_of=as_of)
    if df.empty:
        raise SystemExit(f"{csv_path} has no decodable prints")

    tk = by_ticker(df)
    ranked = unusual_rank(df, tk)
    if args.with_gex:
        tk = attach_gex(tk)

    sheets = {
        "Summary": _summary(df, tk, as_of),
        "By Ticker": tk,
        "Repeat Contracts": repeat_contracts(df),
        "Blocks": blocks(df),
        "Sweeps": sweeps(df),
        "Combos": combos(df),
        "Unusual Rank": ranked,
    }
    out_path = csv_path.with_name(f"analysis_{as_of.isoformat()}.xlsx")
    write_workbook(out_path, sheets)
    print(f"wrote {out_path}  ({len(df):,} prints, {df['root'].nunique()} tickers)")


if __name__ == "__main__":
    main()
