"""Telegram card for the position book (2026-11-22): plain HTML text, readable on the phone with scripts off."""

from __future__ import annotations

from datetime import date

from trading_intel.positions import book as B


def _label(r: dict) -> str:
    return f"{r['expiry']:%b %d} {r['strike']:g}{r['cp']}" + (" AM" if r["root"] == "SPX" else "")


def card(
    t: date,
    rows: list[dict],
    sightings: list[dict],
    names: dict,
    unnamed: list[str],
    coverage: str | None,
) -> str:
    """Plain Telegram text (HTML parse mode): biggest dealer-short lines + who holds them + players seen."""
    shares = B.short_gamma_shares(rows)
    top = sorted((r for r in rows if r["key"] in shares), key=lambda r: r["mm_gamma_usd_1pct"])[:6]
    short = sum(r["mm_gamma_usd_1pct"] for r in rows if (r["mm_gamma_usd_1pct"] or 0) < 0)
    net = sum(r["mm_gamma_usd_1pct"] or 0 for r in rows)
    lines = [
        f"<b>Big lines - SPX book after {t:%a %m/%d}</b>",
        f"Dealer gamma on the lines we could sign: net ${net / 1e9:+.1f}bn per 1% "
        f"(short lines alone ${short / 1e9:.1f}bn). Below: the lines dealers are shortest.",
    ]
    for r in top:
        who = names.get(r["player_slug"]) or ""
        lines.append(
            f"• {_label(r)}: customers "
            f"{'long' if r['cust_pos'] > 0 else 'short'} {abs(r['cust_pos']) / 1000:.1f}k · "
            f"<b>{shares[r['key']] * 100:.0f}%</b> of dealer short gamma · sure {r['confidence'] * 100:.0f}%"
            + (f" · {who}" if who else "")
        )
    held: dict[str, dict] = {}
    for r in rows:
        slug = r.get("player_slug")
        if slug and names.get(slug) and abs(r["cust_pos"] or 0) >= 1000:
            if slug not in held or abs(r["cust_pos"]) > abs(held[slug]["cust_pos"]):
                held[slug] = r
    if held:
        lines.append("<b>Who holds what</b>")
        for slug, r in sorted(held.items(), key=lambda kv: -abs(kv[1]["cust_pos"]))[:6]:
            sh = shares.get(r["key"])
            lines.append(
                f"• {names[slug]}: {_label(r)} "
                f"{'long' if r['cust_pos'] > 0 else 'short'} {abs(r['cust_pos']) / 1000:.1f}k"
                + (f" · {sh * 100:.0f}% of dealer short gamma" if sh else "")
            )
    if sightings:
        seen = {}
        for s in sightings:
            seen.setdefault(s["player_slug"], 0)
            seen[s["player_slug"]] += 1
        lines.append(
            "<b>Traders seen</b>: "
            + ", ".join(
                f"{names.get(k) or k} ×{v}"
                for k, v in sorted(seen.items(), key=lambda kv: -kv[1])[:6]
            )
        )
    if unnamed:
        lines.append(f"Repeat trader(s) with no name yet: {', '.join(unnamed[:3])}")
    if coverage:
        lines.append(coverage)
    lines.append(
        "Sure % = share of the line's OI whose side we measured. Customers long = dealers short."
    )
    return "\n".join(lines)
