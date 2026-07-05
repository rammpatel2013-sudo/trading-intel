"""Pure DataFrame aggregators for the option tape (per-name + per-contract daily).

Input contract — one row per print with these columns (already decoded, as stored
in ``tas_prints``): ``root, expiry (date|None), strike (float), cp ('C'/'P'),
side ('buy'/'sell'/...), notional, size, price, delta, spot``. ``derive`` adds the
signed-delta columns the roll-ups need; ``rollup_by_name`` and
``rollup_by_contract`` are pure ``df -> df`` and feed the daily roll-up tables.

Accumulation vs distribution falls straight out of the buy/sell split:
``dominant_side`` per name/contract, and a signed ``net_dollar_delta`` (buy prints
add, sell prints subtract). Descriptive only (rule 4) — nothing here emits a signal.
"""

from __future__ import annotations

import pandas as pd

_SIDE_SIGN = {"buy": 1.0, "sell": -1.0}


def _dominant_side(buy_n: float, sell_n: float, *, tol: float = 0.15) -> str:
    """Label a buy/sell premium split: buy / sell / mixed (within ``tol``)."""
    total = buy_n + sell_n
    if total <= 0:
        return "mixed"
    buy_share = buy_n / total
    if buy_share >= 0.5 + tol:
        return "buy"
    if buy_share <= 0.5 - tol:
        return "sell"
    return "mixed"


def derive(df: pd.DataFrame) -> pd.DataFrame:
    """Coerce numerics + add ``dollar_delta`` / ``signed_dollar_delta`` / ``otm``.

    Idempotent and tolerant of missing optional columns. Rows with no ``root`` are
    dropped (undecodable prints). Returns a copy.
    """
    if df is None or df.empty:
        return pd.DataFrame(
            columns=[
                "root",
                "expiry",
                "strike",
                "cp",
                "side",
                "notional",
                "size",
                "price",
                "delta",
                "spot",
                "dollar_delta",
                "signed_dollar_delta",
                "otm",
            ]
        )
    out = df.copy()
    out["root"] = out.get("root")
    out = out[out["root"].notna()].copy()
    out["cp"] = out.get("cp", "").astype(str).str.upper().str[0]
    out["side"] = out.get("side", "unknown").astype(str).str.lower()
    for col in ("notional", "size", "price", "delta", "spot", "strike"):
        out[col] = pd.to_numeric(out.get(col), errors="coerce")
    out["notional"] = out["notional"].fillna(0.0)
    out["size"] = out["size"].fillna(0.0)

    side_sign = out["side"].map(_SIDE_SIGN).fillna(0.0)
    out["dollar_delta"] = (out["delta"] * out["size"] * 100.0 * out["spot"]).fillna(0.0)
    out["signed_dollar_delta"] = out["dollar_delta"] * side_sign

    is_call = out["cp"] == "C"
    out["otm"] = (is_call & (out["strike"] > out["spot"])) | (
        ~is_call & (out["strike"] < out["spot"])
    )
    return out


def rollup_by_name(df: pd.DataFrame) -> pd.DataFrame:
    """Per-``root`` daily aggregate. Ranked by total notional.

    Columns: ``root, prints, total_notional, call_notional, put_notional,
    buy_notional, sell_notional, net_dollar_delta, gross_dollar_delta,
    net_premium_call_put, pct_buy, dominant_side``.
    """
    cols = [
        "root",
        "prints",
        "total_notional",
        "call_notional",
        "put_notional",
        "buy_notional",
        "sell_notional",
        "net_dollar_delta",
        "gross_dollar_delta",
        "net_premium_call_put",
        "pct_buy",
        "dominant_side",
    ]
    if df is None or df.empty:
        return pd.DataFrame(columns=cols)

    is_call = df["cp"] == "C"
    is_buy = df["side"] == "buy"
    is_sell = df["side"] == "sell"
    g = df.assign(
        call_notional=df["notional"].where(is_call, 0.0),
        put_notional=df["notional"].where(~is_call, 0.0),
        buy_notional=df["notional"].where(is_buy, 0.0),
        sell_notional=df["notional"].where(is_sell, 0.0),
    ).groupby("root")
    out = g.agg(
        prints=("notional", "size"),
        total_notional=("notional", "sum"),
        call_notional=("call_notional", "sum"),
        put_notional=("put_notional", "sum"),
        buy_notional=("buy_notional", "sum"),
        sell_notional=("sell_notional", "sum"),
        net_dollar_delta=("signed_dollar_delta", "sum"),
        gross_dollar_delta=("dollar_delta", lambda s: s.abs().sum()),
    ).reset_index()
    out["net_premium_call_put"] = out["call_notional"] - out["put_notional"]
    out["pct_buy"] = (out["buy_notional"] / out["total_notional"]).where(
        out["total_notional"] > 0, 0.0
    )
    out["dominant_side"] = [
        _dominant_side(b, s) for b, s in zip(out["buy_notional"], out["sell_notional"], strict=True)
    ]
    return out[cols].sort_values("total_notional", ascending=False).reset_index(drop=True)


def rollup_by_contract(df: pd.DataFrame, *, min_prints: int = 1) -> pd.DataFrame:
    """Per-(``root``,``expiry``,``strike``,``cp``) daily aggregate — the repeat-contract grain.

    Columns: ``root, expiry, strike, cp, n_prints, total_notional, total_size,
    avg_price, buy_prints, sell_prints, buy_notional, sell_notional,
    net_dollar_delta, dominant_side``.
    """
    cols = [
        "root",
        "expiry",
        "strike",
        "cp",
        "n_prints",
        "total_notional",
        "total_size",
        "avg_price",
        "spot",
        "avg_delta",
        "buy_prints",
        "sell_prints",
        "buy_notional",
        "sell_notional",
        "net_dollar_delta",
        "dominant_side",
    ]
    if df is None or df.empty:
        return pd.DataFrame(columns=cols)

    is_buy = df["side"] == "buy"
    is_sell = df["side"] == "sell"
    g = df.assign(
        buy_flag=is_buy.astype(int),
        sell_flag=is_sell.astype(int),
        buy_notional=df["notional"].where(is_buy, 0.0),
        sell_notional=df["notional"].where(is_sell, 0.0),
    ).groupby(["root", "expiry", "strike", "cp"], dropna=False)
    out = g.agg(
        n_prints=("notional", "size"),
        total_notional=("notional", "sum"),
        total_size=("size", "sum"),
        avg_price=("price", "mean"),
        spot=("spot", "mean"),
        avg_delta=("delta", "mean"),
        buy_prints=("buy_flag", "sum"),
        sell_prints=("sell_flag", "sum"),
        buy_notional=("buy_notional", "sum"),
        sell_notional=("sell_notional", "sum"),
        net_dollar_delta=("signed_dollar_delta", "sum"),
    ).reset_index()
    out = out[out["n_prints"] >= min_prints].copy()
    out["dominant_side"] = [
        _dominant_side(b, s) for b, s in zip(out["buy_notional"], out["sell_notional"], strict=True)
    ]
    return out[cols].sort_values("total_notional", ascending=False).reset_index(drop=True)
