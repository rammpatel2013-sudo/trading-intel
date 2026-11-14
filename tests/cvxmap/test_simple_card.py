"""simple_card (2026-11-13): the plain-language nightly scorecard reads the replay's saved files correctly."""
import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "cvxmap"))
import simple_card as C  # noqa: E402

NAMES = ["1 Fly @ vanna top", "2 Fly @ gamma zero", "3 Fly @ vanna bottom", "4 Fly @ gamma zero +1SD",
         "5 Credit spread", "6 Debit 1:5", "7 Old fly"]
SEED = {"since": "2026-10-26", "basis": "vol", "tests": {n: (2, 1000, 100) for n in NAMES}}   # 14 trades, +700
COLS = ["date", "open", "high", "low", "close", "cone0_p10", "cone0_p90", "prev_close", "am_straddle", "range_pct", "regime"]


def _night(root, day, *, pl_by_test, wins, p1, levels_path=None, trades=()):
    d = root / day.replace("-", "")
    d.mkdir(parents=True, exist_ok=True)
    summary = [{"test": n, "n": 1 if n in pl_by_test else 0, "risk": 100, "pl": pl_by_test.get(n, 0), "wins": wins.get(n, 0)} for n in NAMES]
    tot = {"n": sum(s["n"] for s in summary), "pl": sum(s["pl"] for s in summary)}
    (d / "seven_tests.json").write_text(json.dumps({"vol": {"summary": summary, "total": tot, "trades": list(trades)}}))
    (d / "p1.json").write_text(json.dumps(p1))
    if levels_path:
        with open(d / ("levels_%s.ndjson" % day.replace("-", "")), "w") as fh:
            for t, s in levels_path:
                fh.write(json.dumps({"t": t, "S": s}) + "\n")


def _root(tmp_path):
    rows = [
        {"date": "2026-11-11", "open": 6800, "high": 6830, "low": 6790, "close": 6820, "cone0_p10": 6790, "cone0_p90": 6850,
         "prev_close": 6795, "am_straddle": 40, "range_pct": 100, "regime": "short"},
        {"date": "2026-11-12", "open": 6820, "high": 6850, "low": 6812, "close": 6833.6, "cone0_p10": 6800, "cone0_p90": 6830,
         "prev_close": 6820, "am_straddle": 50, "range_pct": 76, "regime": "long"},
        {"date": "2026-11-13", "open": 6840, "high": 6868, "low": 6827, "close": 6861.2, "cone0_p10": 6822, "cone0_p90": 6884,
         "prev_close": 6833.6, "am_straddle": 58, "range_pct": 70.7, "regime": "long"},
    ]
    with open(tmp_path / "history.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=COLS)
        w.writeheader()
        for r in rows:
            w.writerow(r)
    b_long = {"regime": "long", "lines": {
        "vanna_upper": {"rule_says_binds": True, "lock": "10:20", "level": 6870.0, "err": 2.0},       # high 6868 -> 2 below: hit
        "gamma_lower": {"rule_says_binds": True, "lock": "10:30", "level": 6820.0, "err": -7.0},      # low 6827 -> 7 above: miss
        "gamma_upper": {"rule_says_binds": False, "lock": None, "level": None},
        "vanna_lower": {"rule_says_binds": False, "lock": None, "level": None}}}
    p1_13 = {"boundary": b_long,
             "levels": [{"src": "Ours", "result": "held"}, {"src": "Ours", "result": "held"}, {"src": "Ours", "result": "broke"},
                        {"src": "Straddle", "result": "held"}, {"src": "Straddle", "result": "broke"}, {"src": "Doc", "result": "held"}],
             "own_grades": [{"text": "Holds above 6,845 -> look for 6,865", "t0": 665, "hit": 820},
                            {"text": "Below 6,820 -> 6,800", "t0": None, "hit": None}, {"kind": "lean", "text": "lean up", "t0": 600}],
             "doc_grades": [{"text": "a", "t0": 600, "hit": None}, {"text": "b", "t0": 700, "hit": 720}],
             "proj": [{"t": "14:00", "plain_gz_err": 12.0, "naive": 6.0}, {"t": "10:30", "plain_gz_err": 30.0, "naive": 20.0}]}
    p1_12 = {"boundary": {"regime": "long", "lines": {"vanna_upper": {"rule_says_binds": True, "lock": "11:00", "level": 6860.0, "err": 10.0},
                                                      "gamma_lower": {"rule_says_binds": True, "lock": "11:00", "level": 6810.0, "err": -2.0}}},
             "levels": [{"src": "Ours", "result": "held"}, {"src": "Straddle", "result": "broke"}],
             "own_grades": [{"text": "x", "t0": 640, "hit": None}],
             "proj": [{"t": "14:00", "plain_gz_err": 8.0, "naive": 4.0}]}
    _night(tmp_path, "2026-11-12", pl_by_test={"1 Fly @ vanna top": 200, "3 Fly @ vanna bottom": -150}, wins={"1 Fly @ vanna top": 1}, p1=p1_12)
    _night(tmp_path, "2026-11-13", pl_by_test={"1 Fly @ vanna top": 180, "5 Credit spread": -40}, wins={"1 Fly @ vanna top": 1}, p1=p1_13,
           levels_path=[("09:32", 6840), ("10:10", 6827), ("12:00", 6850), ("14:30", 6868), ("16:00", 6861.2)],
           trades=[{"test": "1 Fly @ vanna top", "in": "12:50", "pl": 180, "spot": 6852}, {"test": "_trigger", "in": "12:50"},
                   {"test": "5 Credit spread", "in": "09:31", "why": "no vanna rails"}, {"test": "5 Credit spread", "in": "10:00", "pl": -40, "spot": 6835}])
    return tmp_path


def test_status_rules():
    assert C.status("pct", 74, 52, 25) == ("gain", "better")
    assert C.status("pct", 74, 52, 10) == ("wait", "better")          # too few nights -> too early, leaning better
    assert C.status("pct", 55, 52, 25) == ("wait", "same")            # within 5 pct-pts -> too close to call
    assert C.status("pct", 40, 52, 25) == ("loss", "worse")
    assert C.status("pts", 20, 7, 25) == ("loss", "worse")            # lower is better
    assert C.status("pts", 4, 7, 25) == ("gain", "better")
    assert C.status("range80", 81, 80, 25) == ("gain", "better")
    assert C.status("range80", 60, 80, 25) == ("loss", "worse")
    assert C.status("pct", None, 50, 30) == ("wait", None)


def test_money_seed_plus_nights(tmp_path):
    root = _root(tmp_path)
    M = C.money(root, "2026-11-13", SEED, NAMES, 50000.0)
    assert M["total"] == 700 + 50 + 140 and M["n"] == 14 + 2 + 2
    assert M["today_pl"] == 140 and M["today_n"] == 2
    assert M["nights"] == 2 and M["up_nights"] == 2
    assert [d["d"] for d in M["days"]] == ["Oct 26–27", "Nov 12", "Nov 13"]
    top = M["strats"][0]
    assert top["id"] == "1 Fly @ vanna top" and top["pl"] == 100 + 200 + 180 and top["n"] == 4 and top["win"] == 100
    assert M["strats"][-1]["id"] == "3 Fly @ vanna bottom" and M["strats"][-1]["pl"] == -50
    # a later night is not counted when rebuilding an earlier one
    assert C.money(root, "2026-11-12", SEED, NAMES, 50000.0)["total"] == 750


def test_signals(tmp_path):
    root = _root(tmp_path)
    S = {s["key"]: s for s in C.signals(root, "2026-11-13")}
    assert S["levels"]["map"] == 75 and S["levels"]["base"] == 33       # Ours 3 held / 4; Straddle 1 / 3
    assert S["range"]["map"] == 67                                      # 11/12 close 6833.6 > P90 6830 -> outside
    assert S["ceiling"]["map"] == 50                                    # 11/12: 10 miss, 2 hit; 11/13: 2 hit, 7 miss
    assert S["ceiling"]["base"] == 0                                    # prior-day high/low never within 5 pts
    assert S["calm"]["map"] == 100                                      # median 76: short 100>76 right, long 76/70.7 <= 76 right
    assert S["close"]["map"] == 10 and S["close"]["base"] == 5 and S["close"]["lean"] == "worse"
    assert S["close"]["lean_text"] == "So far: worse than the simple guess"
    assert S["range"]["lean_text"].startswith("So far: the range is too narrow")
    assert S["plan"]["map"] == 50 and S["plan"]["base"] == 50           # ours 1 of 2 started; Doc 1 of 2
    assert all(s["st"] == "wait" for s in S.values())                   # only 2-3 nights -> everything too early


def test_today(tmp_path):
    root = _root(tmp_path)
    T = C.today(root, "2026-11-13")
    assert T["close"] == 6861.2 and T["moved"] == 41.0 and T["expected"] == 58.0 and abs(T["chg_pct"] - 0.40) < 0.01
    by = {c["title"].split(" — ")[0]: c for c in T["checks"]}
    assert by["Calm day"]["ok"] is True
    assert by["Close inside the morning range"]["ok"] is True
    assert by["Ceiling"]["ok"] is True and "2 pts below" in by["Ceiling"]["text"]
    assert by["Floor"]["ok"] is False and "7 pts above" in by["Floor"]["text"]
    assert by["Game plan hit"]["ok"] is True and "1:40 pm" in by["Game plan hit"]["text"]
    assert T["ceiling"] == 6870.0 and T["floor"] == 6820.0 and T["band"] == [6822.0, 6884.0]
    assert len(T["path"]) == 5 and T["path"][0] == [572, 6840.0]
    assert [t["name"] for t in T["trades"]] == ["Butterfly at the top line", "Paid to stay in the box"]


def test_page_and_telegram(tmp_path):
    root = _root(tmp_path)
    p, D = C.write(root, "2026-11-13", tmp_path / "out.html", seed=SEED, names=NAMES, book=50000.0)
    html = p.read_text(encoding="utf-8")
    assert html.count("</script>") == 1
    body = html.split("<script>")[0]                 # 2026-11-14: everything shows with scripts off (phone file viewers)
    for want in ("Friday, Nov 13", "Is it making money?", "+$890", "Butterfly at the top line", "Ceiling — right",
                 "Close lands in the morning range", "Game plan hit", '<path d="M'):
        assert want in body, want
    assert body.count('class="hit"') > 10 and "data-tip=" in body
    txt = C.telegram_text(D)
    assert txt.startswith("<b>SPX scorecard · Fri 13 Nov</b>")
    assert "Close 6,861 (+0.4%) · moved 41 pts · options expected ±58" in txt
    assert "today +$140 (2)" in txt and "since Oct 26 +$890 (18)" in txt
    assert "✓ Ceiling" in txt and "✕ Floor" in txt
    assert "Best rule so far: Butterfly at the top line +$480" in txt


def test_missing_files_do_not_crash(tmp_path):
    D = C.build(tmp_path, "2026-11-13", seed=SEED, names=NAMES, book=50000.0)
    assert D["money"]["total"] == 700 and D["today"]["checks"] == [] and D["today"]["path"] == []
    assert C.page(D) and C.telegram_text(D).startswith("<b>SPX scorecard")
    assert "No checks today" in C.page(D) and "No price chart for today" in C.page(D)
