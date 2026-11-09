"""Plain-English read for the tenor gamma / vega + factor cards job (created 2026-11-09).

Pure text from the ``tenor.json`` object - used by the Telegram caption, the HTML
report and the MCP tools. Descriptor only (rule 4): positioning read, not a trade signal.
"""

from __future__ import annotations

from typing import Any

from trading_intel.greeks.tenor_buckets import BUCKETS, DISCLAIMER


def _n(x: float | None, k: int = 1) -> str:
    return "n/a" if x is None else f"{x:+,.{k}f}"


def _lvl(x: float | None) -> str:
    return "none on the ladder" if x is None else f"{x:,.0f}"


def gamma_lines(obj: dict[str, Any]) -> list[str]:
    g = (obj or {}).get("gamma") or {}
    if not g:
        return ["No stored chain book yet for the tenor view."]
    b = g["buckets"]
    neg = [lab for k, lab in BUCKETS if (b[k]["at_spot"] or 0) < 0]
    pos = [lab for k, lab in BUCKETS if (b[k]["at_spot"] or 0) > 0]
    t = g["total"]
    out = [
        f"Dealer gamma at spot {obj['spot']:,.0f}: total {_n(t['at_spot'])} $bn per 1% move; "
        f"total zero-cross {_lvl(t['zero_cross'])}.",
        ("Negative in: " + ", ".join(neg) + "." if neg else "No tenor bucket is negative.")
        + (" Positive in: " + ", ".join(pos) + "." if pos else ""),
    ]
    for k, lab in BUCKETS:
        x = b[k]
        sh = "" if x.get("share_pct") is None else f", {x['share_pct']:.0f}% of total"
        out.append(f"{lab}: {_n(x['at_spot'])} $bn{sh}; zero-cross {_lvl(x['zero_cross'])}.")
    ch = obj.get("change_1w")
    if ch:
        parts = [f"{lab} {_n(ch.get(k))}" for k, lab in BUCKETS]
        out.append(
            f"Change since {ch['from']} (5 sessions): total {_n(ch.get('total'))} $bn; "
            + ", ".join(parts)
            + "."
        )
    out.append(
        f"Covers expiries out to {g.get('max_expiry')} ({g.get('max_dte')} days); "
        "later expiries are not stored."
    )
    return out


def vega_lines(obj: dict[str, Any]) -> list[str]:
    v = (obj or {}).get("vega") or {}
    if not v:
        return []
    b = v["buckets"]
    parts = [f"{lab} {_n(b[k]['naive'], 0)}" for k, lab in BUCKETS]
    tot = v["total"]
    s = (
        "Dealer vega, $mn per vol point (dealers long calls / short puts): "
        f"total {_n(tot['naive'], 0)}; " + ", ".join(parts) + "."
    )
    if tot.get("short_all") is not None:
        s += (
            " If customers own every option instead, the dealer book is "
            f"{_n(tot['short_all'], 0)}: "
            "the sign assumption moves the answer a lot."
        )
    return [s]


def factor_lines(fac: dict[str, Any] | None) -> list[str]:
    out = []
    for c in (fac or {}).get("cards") or []:
        if not c.get("found"):
            out.append(f"{c.get('name')}: no data ({c.get('reason', 'missing')}).")
            continue
        out.append(
            f"{c['name']} z {c['z']:+.1f} (2 weeks ago {_n(c.get('z_2w'))}) - {c['badge']} - "
            f"{c['record']['grade']} (n={c['record']['n']})."
        )
    return out


def full_read(obj: dict[str, Any]) -> list[str]:
    return (
        gamma_lines(obj)
        + vega_lines(obj)
        + factor_lines(obj.get("factors"))
        + [DISCLAIMER[0].upper() + DISCLAIMER[1:] + "."]
    )


def caption(obj: dict[str, Any], limit: int = 1000) -> str:
    """Compact Telegram caption (HTML-escaped, within the 1024-char cap)."""
    g = (obj or {}).get("gamma") or {}
    lines = [f"<b>{obj.get('symbol', 'SPX')} gamma &amp; vega by tenor · {obj.get('asof', '')}</b>"]
    if g:
        b = g["buckets"]
        lines.append(
            f"Total {_n(g['total']['at_spot'])} $bn/1% · flip {_lvl(g['total']['zero_cross'])}"
        )
        lines.append(" · ".join(f"{lab} {_n(b[k]['at_spot'])}" for k, lab in BUCKETS))
        ch = obj.get("change_1w")
        if ch:
            lines.append(f"vs {ch['from']}: total {_n(ch.get('total'))}")
    v = (obj or {}).get("vega") or {}
    if v:
        lines.append(f"Vega total {_n(v['total']['naive'], 0)} $mn/vol pt (naive sign)")
    for c in (obj.get("factors") or {}).get("cards") or []:
        if c.get("found"):
            lines.append(f"{c['name']}: z {c['z']:+.1f} · {c['badge']} · {c['record']['grade']}")
    lines.append(f"<i>{DISCLAIMER}</i>")
    s = "\n".join(x.replace("&", "&amp;") if not x.startswith("<") else x for x in lines)
    return s[:limit]
