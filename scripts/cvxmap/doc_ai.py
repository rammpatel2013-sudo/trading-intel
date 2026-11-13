#!/usr/bin/env python
"""doc_ai.py - the morning Doc read by the laptop's local AI (2026-11-13).

Mithil 2026-11-13: "understand meaning of what he is saying. use AI, our knowledge" -
free only (local Ollama, rule 7), and no clash with other AI jobs.

What it does (weekdays, DSM 08:00 via scripts/doc_ai_read.py):
  1. every 3 minutes until 09:30 ET, look in Gmail for today's Doc report (am_feed's reader);
  2. once it is in, ask the laptop AI (OllamaProvider, purpose "doc_read" - it waits its
     turn on the shared lock; 08:00-09:30 nobody else may use the laptop AI) to read it:
     the day in one sentence, bias, main worry, events, every level with what Doc says
     happens there, and his if/then rules;
  3. a checker drops anything the AI wrote that is not in the email (every number must
     appear in the text, near the other levels);
  4. writes scripts/cvxmap/data/doc_ai_YYYYMMDD.json with the AI read AND the rule
     reader's read of the same email (kept for the later AI-vs-rules score).
am_feed puts the file into ti.json next to OUR 08:35 map (where they agree / differ), and
uses the AI levels on the cockpit when the rule reader found none.

Descriptive context, never a signal (rule 4). No model call ever leaves the LAN (rule 7).

    python scripts/doc_ai_read.py                 # the DSM loop (08:00 -> 09:30)
    python scripts/doc_ai_read.py --once          # one try now
    python scripts/doc_ai_read.py --text FILE     # read a pasted report (test; writes no file)
"""
from __future__ import annotations

import json
import math
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = Path(os.environ.get("DASH_DIR") or (HERE / "data"))
STOP = (9, 30)                  # the laptop guard closes Ollama at 09:30
POLL_S = 180
MAX_CHARS = 14000               # ~4-5k tokens of report; the rest is links and footer
NUM_CTX = 12288
MAX_TOKENS = 1600
CALL_TIMEOUT_S = 900.0

_NUM = re.compile(r"\b\d{1,2},\d{3}(?:\.\d+)?\b|\b\d{4,5}(?:\.\d+)?\b")
ROLES = {"pivot": "piv", "support": "pos", "resistance": "pos", "trigger": "trig", "target": "chart", "rail": "rail"}
BIAS = ("up", "down", "two-way", "pin")

PROMPT = """You read a professional SPX 0DTE morning newsletter and turn it into a short, exact summary for a trader.
Rules: use ONLY what the text says. Every number you write must appear in the text. SPX index levels only
(ignore ES futures and SPY prices unless the text calls them SPX levels). Plain words, no hype.

Return JSON with exactly these keys:
{{"day": "one plain sentence: what kind of day the author expects and why",
 "bias": "up" or "down" or "two-way" or "pin",
 "pivot": the main pivot level as a number, or null,
 "worry": "one sentence: the main risk the author is watching",
 "events": ["up to 5 short items: scheduled events and times the author names"],
 "levels": [up to 16 items: {{"lo": number, "hi": number (same as lo for a single price),
            "role": "pivot" or "support" or "resistance" or "trigger" or "target" or "rail",
            "says": "at most 12 words: what the author says happens at this level"}}],
 "rules": [up to 8 items: {{"side": "above" or "below", "level": number, "targets": [numbers],
           "says": "at most 15 words: the author's if/then in plain words"}}]}}

TEXT:
<<<
{text}
>>>"""


# ============================================================ pure helpers (tested)
def clean_text(text: str) -> str:
    """Drop links, image lines and the footer; cap the length."""
    out = []
    for raw in (text or "").replace("\r", "").split("\n"):
        line = re.sub(r"https?://\S+", "", raw).strip()
        if re.match(r"(?i)^(unsubscribe|manage subscription|© |you are receiving|view in browser)", line):
            break
        if not line or re.fullmatch(r"[\W_]+", line):
            if out and out[-1] != "":
                out.append("")
            continue
        out.append(line)
    return "\n".join(out).strip()[:MAX_CHARS]


def numbers_in(text: str) -> set[float]:
    return {float(m.replace(",", "")) for m in _NUM.findall(text or "")}


def _num(x) -> float | None:
    try:
        v = float(str(x).replace(",", ""))
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _s(x, n: int) -> str:
    return re.sub(r"\s+", " ", str(x or "")).strip()[:n]


def validate(ai: dict, text: str) -> tuple[dict, int]:
    """Keep only what the email supports. Returns (clean read, number of items dropped)."""
    seen = numbers_in(text)
    dropped = 0

    def known(v):
        return v is not None and 1000 <= v <= 30000 and any(abs(v - s) < 0.01 for s in seen)

    piv = _num(ai.get("pivot"))
    piv = piv if known(piv) else None
    raw_lv = []
    for x in (ai.get("levels") or [])[:24] if isinstance(ai.get("levels"), list) else []:
        if not isinstance(x, dict):
            dropped += 1
            continue
        lo, hi = _num(x.get("lo")), _num(x.get("hi"))
        hi = lo if hi is None else hi
        if not (known(lo) and known(hi)) or abs(hi - lo) > 60:
            dropped += 1
            continue
        lo, hi = min(lo, hi), max(lo, hi)
        raw_lv.append({"lo": lo, "hi": hi, "role": ROLES.get(str(x.get("role") or "").lower().strip(), "chart"),
                       "says": _s(x.get("says"), 90)})
    vals = sorted((x["lo"] + x["hi"]) / 2 for x in raw_lv)
    ref = piv or (vals[len(vals) // 2] if vals else None)
    near = (lambda v: abs(v - ref) <= 0.08 * ref) if ref else (lambda v: True)
    levels = []
    for x in raw_lv:
        if near(x["lo"]) and near(x["hi"]) and not any(abs(x["lo"] - y["lo"]) < 0.01 and abs(x["hi"] - y["hi"]) < 0.01 for y in levels):
            levels.append(x)
        else:
            dropped += 1
    levels = sorted(levels, key=lambda x: -x["hi"])[:16]
    rules = []
    for r in (ai.get("rules") or [])[:12] if isinstance(ai.get("rules"), list) else []:
        if not isinstance(r, dict):
            dropped += 1
            continue
        L = _num(r.get("level"))
        side = str(r.get("side") or "").lower().strip()
        if side not in ("above", "below") or not known(L) or not near(L):
            dropped += 1
            continue
        tg = [t for t in (_num(t) for t in (r.get("targets") or [])) if known(t) and near(t) and (t > L if side == "above" else t < L)]
        rules.append({"side": side, "level": L, "targets": tg[:4], "says": _s(r.get("says"), 110)})
    bias = str(ai.get("bias") or "").lower().strip()
    clean = {"day": _s(ai.get("day"), 220), "bias": bias if bias in BIAS else None, "pivot": piv,
             "worry": _s(ai.get("worry"), 200),
             "events": [_s(e, 70) for e in (ai.get("events") or [])[:5] if _s(e, 70)] if isinstance(ai.get("events"), list) else [],
             "levels": levels, "rules": rules[:8]}
    return clean, dropped


def read_with(llm, text: str, subject: str = "", model: str | None = None) -> tuple[dict, int]:
    body = clean_text(text)
    prompt = PROMPT.format(text=(subject + "\n\n" if subject else "") + body)
    kw = {"model": model, "max_tokens": MAX_TOKENS}
    try:
        raw = llm.complete_json(prompt, num_ctx=NUM_CTX, **kw)
    except TypeError:            # a provider without num_ctx (tests, other backends)
        raw = llm.complete_json(prompt, **kw)
    ai = json.loads(raw) if isinstance(raw, str) else raw
    if not isinstance(ai, dict):
        raise ValueError("model did not return a JSON object")
    return validate(ai, (subject or "") + "\n" + body)   # only what the model was shown counts


def to_doc_levels(ai: dict) -> list[dict]:
    """AI levels -> the cockpit's level format (am_feed fallback when the rule reader found none)."""
    out = []
    for x in ai.get("levels") or []:
        lo, hi = x["lo"], x["hi"]
        item = {"v": lo if hi == lo else round((lo + hi) / 2), "label": (x.get("says") or "level")[:40], "role": x["role"]}
        if hi != lo:
            item.update({"lo": lo, "v2": hi})
        out.append(item)
    return sorted(out, key=lambda x: -x["v"])


def to_scenarios(ai: dict) -> list[dict]:
    """AI rules -> the cockpit's IF/THEN format (graded live like the rule reader's)."""
    sc = []
    for r in ai.get("rules") or []:
        up = r["side"] == "above"
        sc.append({"kind": "cross", "dir": "up" if up else "down", "level": r["level"],
                   "targets": [[t, t] for t in r["targets"]], "rule": None, "cond": f"{r['side']} {r['level']:g}",
                   "then": ", ".join(f"{t:g}" for t in r["targets"]), "text": r.get("says") or "",
                   "sect": "up" if up else "down", "id": f"a{len(sc) + 1}"})
    return sc


def compare(ai: dict, own: dict | None, spot: float | None = None) -> dict:
    """Doc's levels next to OUR 08:35 map (own levels: flip, walls, straddle edges, DMAs, OI)."""
    ours = [x for x in ((own or {}).get("levels") or {}).get("SPX", []) if _num(x.get("v")) is not None]
    rows, agree = [], 0
    for x in ai.get("levels") or []:
        mid = (x["lo"] + x["hi"]) / 2
        best = min(ours, key=lambda o: abs(float(o["v"]) - mid), default=None)
        d = abs(float(best["v"]) - mid) if best else None
        hit = best is not None and (x["lo"] - 5 <= float(best["v"]) <= x["hi"] + 5)
        agree += hit
        rows.append({"lo": x["lo"], "hi": x["hi"], "role": x["role"], "says": x.get("says") or "",
                     "ours": (f"{best['label']} {float(best['v']):,.0f}" if hit else
                              (f"nearest: {best['label']} {float(best['v']):,.0f} ({d:.0f} pts away)" if best else "")),
                     "agree": bool(hit)})
    flip = _num((own or {}).get("pivot"))
    regime, clash = None, None
    if flip and spot:
        long_g = spot >= flip
        regime = (f"Our map: price {spot:,.0f} is {'above' if long_g else 'below'} our gamma flip {flip:,.0f} - "
                  + ("dealers long gamma, moves tend to get damped." if long_g else "dealers short gamma, moves can run."))
        b = ai.get("bias")
        if b == "pin" and not long_g:
            clash = "Doc expects a pin, but our map is short gamma - a break can run further than he expects."
        elif b in ("up", "down") and long_g:
            clash = f"Doc leans {b}, but our map is long gamma - moves may stall at the walls."
    piv = ai.get("pivot")
    if piv and flip and abs(piv - flip) > 10:
        clash = ((clash + " ") if clash else "") + f"His pivot {piv:,.0f} is {abs(piv - flip):.0f} pts from our flip {flip:,.0f}."
    return {"rows": rows, "agree": agree, "n": len(rows), "regime": regime, "clash": clash}


# ============================================================ the job
def _settings_for_nas(settings):
    """NAS containers reach the laptop Ollama at NAS_OLLAMA_URL (.env) - run_job.sh does the same."""
    host = os.environ.get("OLLAMA_HOST") or ""
    if not host or "localhost" in host or "127.0.0.1" in host:
        try:
            for line in Path(".env").read_text(encoding="utf-8", errors="ignore").splitlines():
                if line.startswith("NAS_OLLAMA_URL="):
                    host = line.split("=", 1)[1].strip().strip('"').strip("'")
        except OSError:
            pass
    if host and "localhost" not in host:
        try:
            return settings.model_copy(update={"OLLAMA_HOST": host})
        except Exception:  # noqa: BLE001
            pass
    return settings


def out_path(day: str) -> Path:
    return DATA / f"doc_ai_{day.replace('-', '')}.json"


def _write(path: Path, obj: dict) -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def run_once(settings, *, now: datetime) -> str:
    """One try. Returns 'done', 'have', 'wait' (no report / AI not up yet)."""
    import am_feed  # noqa: PLC0415 - same folder; heavy imports only when the job runs

    today = now.date().isoformat()
    got, err = am_feed.doc_from_gmail(settings)
    if not got or not got.get("text"):
        print(f"{now:%H:%M} no report yet ({err or 'none'})", flush=True)
        return "wait"
    rules = am_feed.parse_doc(got["text"], got.get("subject") or "")
    day = am_feed._doc_date(rules, got.get("received"))
    if day != today:
        print(f"{now:%H:%M} newest report is {day}, not today", flush=True)
        return "wait"
    path = out_path(today)
    rec = got["received"].isoformat(timespec="minutes") if got.get("received") else None
    if path.exists():
        try:
            old = json.loads(path.read_text(encoding="utf-8"))
            if old.get("received") == rec and old.get("ai"):
                return "have"
        except (OSError, ValueError):
            pass
    from trading_intel.scheduler.jobs.letters_fetch import ollama_up  # noqa: PLC0415
    from trading_intel.synthesis.llm import OllamaProvider  # noqa: PLC0415

    st = _settings_for_nas(settings)
    if not ollama_up(st.OLLAMA_HOST):
        print(f"{now:%H:%M} laptop AI not answering - retry", flush=True)
        return "wait"
    stop = now.replace(hour=STOP[0], minute=STOP[1], second=0, microsecond=0)
    llm = OllamaProvider(st, timeout=CALL_TIMEOUT_S, purpose="doc_read", lock_wait=max(30.0, (stop - now).total_seconds() - 60))
    t0 = time.monotonic()
    obj = {"date": today, "received": rec, "subject": got.get("subject"), "model": st.LLM_DAILY_MODEL,
           "read_at": None, "secs": None, "ai": None, "dropped": 0, "error": None,
           "rules_reader": {k: rules.get(k) for k in ("levels", "scenarios", "pivot", "chop", "up_path", "down_path", "one_thing", "warnings")}}
    try:
        ai, dropped = read_with(llm, got["text"], got.get("subject") or "", model=st.LLM_DAILY_MODEL)
        obj.update(ai=ai, dropped=dropped)
    except Exception as exc:  # noqa: BLE001 - recorded in the file, the cockpit shows it
        obj["error"] = f"{type(exc).__name__}: {str(exc)[:160]}"
    obj["secs"] = round(time.monotonic() - t0)
    from trading_intel.timeutils import eastern_now  # noqa: PLC0415

    obj["read_at"] = eastern_now().isoformat(timespec="minutes")
    _write(path, obj)
    print(f"{now:%H:%M} AI read {'OK' if obj['ai'] else 'FAILED'} in {obj['secs']}s: "
          + (f"{len(obj['ai']['levels'])} levels, {len(obj['ai']['rules'])} rules, {obj['dropped']} dropped" if obj["ai"] else obj["error"]),
          flush=True)
    return "done" if obj["ai"] else "wait"


def main(argv: list[str]) -> int:
    sys.path.insert(0, str(HERE))
    ti_dir = os.environ.get("TI_DIR") or str(HERE.parents[1])
    sys.path.insert(0, ti_dir)
    os.chdir(ti_dir)
    from trading_intel.config import get_settings  # noqa: PLC0415
    from trading_intel.timeutils import eastern_now  # noqa: PLC0415

    settings = get_settings()
    if "--text" in argv:
        from trading_intel.synthesis.llm import OllamaProvider  # noqa: PLC0415

        text = Path(argv[argv.index("--text") + 1]).read_text(encoding="utf-8", errors="ignore")
        st = _settings_for_nas(settings)
        ai, dropped = read_with(OllamaProvider(st, timeout=CALL_TIMEOUT_S, purpose="doc_read"), text, model=st.LLM_DAILY_MODEL)
        print(json.dumps({"ai": ai, "dropped": dropped}, ensure_ascii=False, indent=1))
        return 0
    while True:
        now = eastern_now()
        if (now.hour, now.minute) >= STOP and "--once" not in argv:
            print(f"{now:%H:%M} 09:30 - stopping (the laptop AI closes; the cockpit keeps the rule reader's levels)", flush=True)
            return 0
        if now.weekday() >= 5 and "--once" not in argv:
            return 0
        try:
            state = run_once(settings, now=now)
        except Exception as exc:  # noqa: BLE001 - keep polling until 09:30
            print(f"{now:%H:%M} error {type(exc).__name__}", flush=True)
            state = "wait"
        if state in ("done", "have") or "--once" in argv:
            return 0
        time.sleep(POLL_S)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
