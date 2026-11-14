# ruff: noqa: E501
"""vs_card_v2.py - 2026-11-13 overnight, Mithil's backlog cut: VS morning card v2 additions.

- Lock / moved: each key level vs the previous card (locked = moved <= 5 pts).
- Trust bar: high / medium / low from book age, regime strength (percentile) and whether the
  tape-signed flip agrees with the naive one.
- Next-day tells: 4-6 close-based if/then levels from the card (flips, balance, tests), saved; the
  next card grades the previous card's tells against the session close (fired / not / inside) and
  keeps a running hit rate per tell type in reports/vs_card/tells_score.json.
- Close pressure: one plain line from charm at price (dealer hedges drift price up/down into the close).
Pure functions + small JSON state; descriptor, not a signal (rule 4).
"""
from __future__ import annotations

import html
import json
from datetime import datetime
from pathlib import Path
from typing import Any

E = html.escape
LOCK_PTS = 5.0


def levels(P: dict, lv: dict | None) -> dict[str, float]:
    out: dict[str, float] = {}
    for k in ("full", "ex0"):
        v = ((P.get("flips") or {}).get(k) or {}).get("nearest")
        if v:
            out[f"flip_{k}"] = float(v)
    sg = P.get("signed") or {}
    if sg.get("ok") and (sg.get("flips") or {}).get("nearest"):
        out["flip_signed"] = float(sg["flips"]["nearest"])
    for k in ("balance", "up_test", "down_test"):
        if lv and lv.get(k):
            out[k] = float(lv[k])
    return out


def lock_moved(now: dict[str, float], prev: dict[str, float] | None) -> dict[str, str]:
    res = {}
    for k, v in now.items():
        p = (prev or {}).get(k)
        res[k] = "new" if p is None else ("locked" if abs(v - p) <= LOCK_PTS else f"moved {v - p:+.0f}")
    return res


def trust(P: dict, now: datetime) -> dict[str, Any]:
    why, pts = [], 0
    try:
        age_h = (now - datetime.fromisoformat(P["book"]["ts"])).total_seconds() / 3600
    except Exception:  # noqa: BLE001
        age_h = None
    if age_h is not None and age_h <= 20:
        pts += 1; why.append("book fresh")
    else:
        why.append("book old" if age_h is not None else "book age unknown")
    p = (P.get("pct") or {}).get("full")
    if p is not None and (p >= 70 or p <= 30):
        pts += 1; why.append(f"strong regime ({p}th pct)")
    else:
        why.append("weak regime (near middle)")
    sg = P.get("signed") or {}
    nf = ((P.get("flips") or {}).get("full") or {}).get("nearest")
    sf = (sg.get("flips") or {}).get("nearest") if sg.get("ok") else None
    if nf and sf:
        if abs(nf - sf) <= 15:
            pts += 1; why.append("signed flip agrees")
        else:
            why.append(f"signed flip {sf - nf:+.0f} pts away")
    word = "high" if pts >= 3 else ("medium" if pts == 2 else "low")
    return {"word": word, "pts": pts, "why": why}


def tells(P: dict, lvl: dict[str, float]) -> list[dict[str, Any]]:
    S = float(P["spot"])
    out = []
    f = lvl.get("flip_full")
    if f:
        side = "above" if S < f else "below"
        out.append({"type": "flip", "level": f, "if": f"close {side} {f:,.0f}",
                    "then": "dealers flip to " + ("long gamma: moves get damped" if side == "above" else "short gamma: moves can run")})
    for k, lab, dirn in (("up_test", "up test", "above"), ("down_test", "down test", "below")):
        v = lvl.get(k)
        if v:
            out.append({"type": k, "level": v, "if": f"close {dirn} {v:,.0f}",
                        "then": f"the {lab} breaks; next stop is the next big strike {dirn}"})
    b = lvl.get("balance")
    if b:
        out.append({"type": "balance", "level": b, "if": f"close within 0.25% of {b:,.0f}",
                    "then": "pinned at the balance strike"})
    return out[:6]


def grade(prev_tells: list[dict], close: float) -> list[dict]:
    res = []
    for t in prev_tells:
        L, typ = float(t["level"]), t["type"]
        if typ == "balance":
            fired = abs(close / L - 1) <= 0.0025
        elif "above" in t["if"]:
            fired = close > L
        else:
            fired = close < L
        res.append({**t, "close": close, "fired": bool(fired)})
    return res


def update_score(path: Path, graded: list[dict]) -> dict:
    sc = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    for g in graded:
        s = sc.setdefault(g["type"], {"n": 0, "fired": 0})
        s["n"] += 1
        s["fired"] += int(g["fired"])
    path.write_text(json.dumps(sc, indent=1), encoding="utf-8")
    return sc


def close_pressure(ctot: float | None) -> str:
    if ctot is None:
        return "Close pressure: no charm reading."
    d = "buy" if ctot > 0 else "sell"
    drift = "up" if ctot > 0 else "down"
    return f"Close pressure: as time passes, dealer hedges {d} (charm), a gentle drift {drift} into the close if nothing else moves."


def block_html(lm: dict[str, str], lvl: dict[str, float], tr: dict, tl: list[dict], graded: list[dict],
               score: dict, cp: str) -> str:
    col = {"high": "#3ddc84", "medium": "#ffd23f", "low": "#ff5c5c"}[tr["word"]]
    chips = " · ".join(f'{E(k.replace("_", " "))} <b>{v:,.0f}</b> <i>({E(lm.get(k, "new"))})</i>' for k, v in lvl.items())
    tlh = "".join(f'<li>If <b>{E(t["if"])}</b> → {E(t["then"])}</li>' for t in tl)
    gh = "".join(f'<li>{"✔" if g["fired"] else "✘"} {E(g["if"])} (close {g["close"]:,.0f})</li>' for g in graded)
    sch = " · ".join(f'{E(k)} {v["fired"]}/{v["n"]}' for k, v in score.items()) or "no history yet"
    return f"""<h2>Levels vs yesterday</h2><div>{chips or '–'}</div>
<h2>Trust: <span style="color:{col}">{tr['word'].upper()}</span></h2><div style="font-size:15px">{E('; '.join(tr['why']))}</div>
<div style="margin-top:6px">{E(cp)}</div>
<h2>Tomorrow's tells</h2><ul style="margin:2px 0">{tlh or '<li>–</li>'}</ul>
<h2>Yesterday's tells, graded</h2><ul style="margin:2px 0">{gh or '<li>first card - nothing to grade</li>'}</ul>
<div style="font-size:14px;color:#cbd5e1">Running hit rate per tell: {E(sch)}</div>"""
