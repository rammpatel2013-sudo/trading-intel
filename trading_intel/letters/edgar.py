"""13F holdings: parse + quarter-over-quarter diff.

Primary source is the **CVForge FMP passthrough** (``cvforge.fmp(...)``, ADR-004) — it
returns a fund's holdings WITH tickers (no CUSIP->ticker step) and no SEC scraping; the
``filings_fetch`` job calls it. ``holdings_from_fmp`` parses those rows;
``parse_infotable`` remains for the raw SEC 13F XML fallback. ``diff_holdings`` is the
pure QoQ engine used by both.

Caveat: FMP institutional endpoints were paywalled on the CVForge tier when the
sentiment collector was built (it's parked for that reason) — confirm 13F access AND the
exact ``/stable`` endpoint/field spellings with ``scripts/probe_fmp_13f.py`` before
trusting output. All descriptive research context only (FlashAlpha rule 4).
"""

from __future__ import annotations

from dataclasses import dataclass
from xml.etree import ElementTree as ET


@dataclass(frozen=True, slots=True)
class Holding:
    """One 13F position (aggregated per ticker/CUSIP). ``ticker`` set when the source
    provides it (FMP does; raw SEC XML does not)."""

    issuer: str
    cusip: str
    value_usd: float
    shares: float
    ticker: str | None = None


@dataclass(frozen=True, slots=True)
class HoldingChange:
    """One position's quarter-over-quarter change."""

    cusip: str
    issuer: str
    kind: str  # new | added | trimmed | exited | unchanged
    prev_value: float
    cur_value: float
    prev_shares: float
    cur_shares: float
    ticker: str | None = None


def _localname(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _num(s: object) -> float:
    if s in (None, ""):
        return 0.0
    try:
        return float(str(s).replace(",", "").strip())
    except (TypeError, ValueError):
        return 0.0


def _pick(d: dict, *keys: str) -> object:
    for k in keys:
        v = d.get(k)
        if v not in (None, ""):
            return v
    return None


def holdings_from_fmp(rows: list[dict]) -> list[Holding]:
    """Parse CVForge/FMP 13F holdings rows into ``Holding``s (ticker included). Pure.

    Field spellings vary across FMP endpoints, so read defensively:
    ticker = symbol|ticker|tickercusip; issuer = securityName|nameOfIssuer|name;
    shares = shares|sharesNumber|sshPrnamt; value = marketValue|value|marketValueUsd.
    Aggregated per (ticker or CUSIP), sorted by value descending.
    """
    agg: dict[str, Holding] = {}
    for r in rows:
        if not isinstance(r, dict):
            continue
        if str(_pick(r, "putCallShare", "putCall") or "").strip().lower() in ("put", "call"):
            continue  # skip option legs — keep equity holdings only
        ticker = str(_pick(r, "symbol", "ticker", "tickercusip") or "").upper().strip() or None
        cusip = str(_pick(r, "cusip", "securityCusip") or "").upper().strip()
        issuer = str(_pick(r, "securityName", "nameOfIssuer", "name") or "")
        value = _num(_pick(r, "marketValue", "value", "marketValueUsd"))
        shares = _num(_pick(r, "shares", "sharesNumber", "sshPrnamt"))
        key = ticker or cusip
        if not key:
            continue
        if key in agg:
            p = agg[key]
            agg[key] = Holding(p.issuer, p.cusip, p.value_usd + value, p.shares + shares, p.ticker)
        else:
            agg[key] = Holding(issuer, cusip, value, shares, ticker)
    return sorted(agg.values(), key=lambda h: h.value_usd, reverse=True)


def parse_infotable(xml: str | bytes) -> list[Holding]:
    """Parse a raw SEC 13F information-table XML into holdings (no ticker). Pure fallback."""
    root = ET.fromstring(xml)  # noqa: S314 — SEC/CVForge 13F XML, a controlled source
    agg: dict[str, Holding] = {}
    for info in root.iter():
        if _localname(info.tag) != "infoTable":
            continue
        fields: dict[str, str] = {}
        for child in info.iter():
            ln = _localname(child.tag)
            if child.text and child.text.strip():
                fields.setdefault(ln, child.text.strip())
        cusip = (fields.get("cusip") or "").upper()
        if not cusip:
            continue
        issuer = fields.get("nameOfIssuer", "")
        value = _num(fields.get("value"))
        shares = _num(fields.get("sshPrnamt"))
        if cusip in agg:
            p = agg[cusip]
            agg[cusip] = Holding(p.issuer, cusip, p.value_usd + value, p.shares + shares)
        else:
            agg[cusip] = Holding(issuer, cusip, value, shares)
    return sorted(agg.values(), key=lambda h: h.value_usd, reverse=True)


def _key(h: Holding) -> str:
    return h.ticker or h.cusip


def diff_holdings(prev: list[Holding], cur: list[Holding]) -> list[HoldingChange]:
    """Quarter-over-quarter change per position (keyed on ticker, else CUSIP). Pure."""
    p = {_key(h): h for h in prev}
    c = {_key(h): h for h in cur}
    out: list[HoldingChange] = []
    for key in sorted(set(p) | set(c)):
        ph = p.get(key)
        ch = c.get(key)
        ps = ph.shares if ph else 0.0
        cs = ch.shares if ch else 0.0
        if ph is None:
            kind = "new"
        elif ch is None:
            kind = "exited"
        elif cs > ps:
            kind = "added"
        elif cs < ps:
            kind = "trimmed"
        else:
            kind = "unchanged"
        ref = ch or ph  # type: ignore[assignment]
        out.append(
            HoldingChange(
                cusip=ref.cusip,
                issuer=ref.issuer,
                kind=kind,
                prev_value=ph.value_usd if ph else 0.0,
                cur_value=ch.value_usd if ch else 0.0,
                prev_shares=ps,
                cur_shares=cs,
                ticker=ref.ticker,
            )
        )
    return out
