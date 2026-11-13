"""doc_ai: the laptop-AI read of Doc's report is checked against the email and set next to our map (synthetic text)."""
import importlib.util
import json
import sys
from pathlib import Path

_DIR = Path(__file__).resolve().parents[2] / "scripts" / "cvxmap"
sys.path.insert(0, str(_DIR))
_S = importlib.util.spec_from_file_location("doc_ai", _DIR / "doc_ai.py")
da = importlib.util.module_from_spec(_S)
_S.loader.exec_module(da)
_A = importlib.util.spec_from_file_location("am_feed_doc_ai_test", _DIR / "am_feed.py")
am = importlib.util.module_from_spec(_A)
_A.loader.exec_module(am)

TEXT = """Price sits near 5,100 with fast zones on both sides.
Above 5,125 the chute opens toward 5,145 then 5,160.
Below 5,075 risk toward 5,050. Pivot 5,100. CPI at 8:30.
https://example.com/track?x=1
Unsubscribe here
5,999 should be cut with the footer"""

AI = {"day": "Two-way day around 5,100.", "bias": "pin", "pivot": 5100, "worry": "CPI could break the pin.",
      "events": ["CPI 8:30"],
      "levels": [{"lo": 5160, "hi": 5160, "role": "resistance", "says": "brake"},
                 {"lo": 5125, "hi": 5125, "role": "trigger", "says": "upside gate"},
                 {"lo": 5100, "hi": 5100, "role": "pivot", "says": "equilibrium"},
                 {"lo": 5133, "hi": 5133, "role": "support", "says": "invented level"},
                 {"lo": 5999, "hi": 5999, "role": "target", "says": "footer number"}],
      "rules": [{"side": "above", "level": 5125, "targets": [5145, 5160, 5050], "says": "chute up"},
                {"side": "below", "level": 5075, "targets": [5050], "says": "risk down"},
                {"side": "sideways", "level": 5100, "targets": [], "says": "bad side"}]}


def test_clean_text_drops_links_and_footer():
    t = da.clean_text(TEXT)
    assert "https://" not in t and "Unsubscribe" not in t and "5,999" not in t and "5,125" in t


def test_validate_keeps_only_what_the_email_says():
    ai, dropped = da.validate(AI, da.clean_text(TEXT))
    assert [x["lo"] for x in ai["levels"]] == [5160, 5125, 5100]          # 5133 invented, 5999 cut with the footer
    assert [x["role"] for x in ai["levels"]] == ["pos", "trig", "piv"]
    assert ai["rules"][0]["targets"] == [5145, 5160]                      # 5050 is not above 5125
    assert len(ai["rules"]) == 2 and dropped == 3
    assert ai["bias"] == "pin" and ai["pivot"] == 5100


class _LLM:
    def __init__(self, out, ctx=True):
        self.out, self.ctx, self.prompts = out, ctx, []

    def complete_json(self, prompt, *, model=None, max_tokens=2048, **kw):
        if kw and not self.ctx:
            raise TypeError("no num_ctx")
        self.prompts.append(prompt)
        return self.out


def test_read_with_parses_json_and_falls_back_without_num_ctx():
    llm = _LLM(json.dumps(AI), ctx=False)
    ai, dropped = da.read_with(llm, TEXT, "SPX REPORT 11/16")
    assert len(ai["levels"]) == 3 and "SPX REPORT 11/16" in llm.prompts[0] and "https://" not in llm.prompts[0]


def test_cockpit_formats_and_comparison_with_our_map():
    ai, _ = da.validate(AI, TEXT)
    lv = da.to_doc_levels(ai)
    sc = da.to_scenarios(ai)
    assert lv[0] == {"v": 5160, "label": "brake", "role": "pos"} and sc[0]["dir"] == "up" and sc[0]["targets"] == [[5145, 5145], [5160, 5160]]
    own = {"pivot": 5120, "levels": {"SPX": [{"v": 5122, "label": "gamma flip (pivot)", "role": "piv"},
                                              {"v": 5160, "label": "call wall (0-7 day)", "role": "pos"}]}}
    cmp_ = da.compare(ai, own, spot=5100)
    assert cmp_["agree"] == 2 and cmp_["n"] == 3
    assert "short gamma" in cmp_["regime"] and "pin" in cmp_["clash"] and "20 pts" in cmp_["clash"]


def test_am_feed_uses_ai_levels_only_when_the_rule_reader_found_none():
    ai, _ = da.validate(AI, TEXT)
    dai = {"date": "2026-11-16", "ai": ai, "model": "qwen2.5:14b"}
    empty = {"date": "2026-11-16", "levels": {}, "scenarios": [], "pivot": None, "warnings": []}
    doc, block = am.merge_doc_ai(empty, dai, None, None)
    assert [x["v"] for x in doc["levels"]["SPX"]] == [5160, 5125, 5100] and len(doc["scenarios"]) == 2
    assert doc["levels_from"] == "ai" and block["ai"]["bias"] == "pin" and empty["levels"] == {}
    ruled = {"date": "2026-11-16", "levels": {"SPX": [{"v": 5100, "label": "Pivot", "role": "piv"}]}, "scenarios": [], "warnings": []}
    doc2, _ = am.merge_doc_ai(ruled, dai, None, None)
    assert doc2 is ruled
    doc3, block3 = am.merge_doc_ai(ruled, {"date": "2026-11-16", "ai": None, "error": "OllamaBusy: x"}, None, None)
    assert doc3 is ruled and block3["error"].startswith("OllamaBusy") and "ai" not in block3


def test_run_once_writes_the_day_file(monkeypatch, tmp_path):
    from datetime import datetime

    import trading_intel.scheduler.jobs.letters_fetch as lf
    import trading_intel.synthesis.llm as llm_mod

    monkeypatch.setattr(da, "DATA", tmp_path)
    monkeypatch.setitem(sys.modules, "am_feed", am)
    now = datetime(2026, 11, 16, 8, 20)
    monkeypatch.setattr(am, "doc_from_gmail", lambda s: ({"text": TEXT, "subject": "SPX 0DTE MARKET REPORT: MON 11/16",
                                                          "received": datetime(2026, 11, 16, 8, 12), "source": "gmail"}, None))
    monkeypatch.setattr(lf, "ollama_up", lambda host, timeout=5.0: True)
    built = {}

    class _P:
        def __init__(self, settings, **kw):
            built.update(kw)

        def complete_json(self, prompt, **kw):
            return json.dumps(AI)

    monkeypatch.setattr(llm_mod, "OllamaProvider", _P)

    class _S:
        OLLAMA_HOST = "http://laptop:11434"
        LLM_DAILY_MODEL = "qwen2.5:14b"

        def model_copy(self, update):
            return self

    assert da.run_once(_S(), now=now) == "done"
    obj = json.loads((tmp_path / "doc_ai_20261116.json").read_text(encoding="utf-8"))
    assert obj["ai"]["bias"] == "pin" and obj["received"] == "2026-11-16T08:12" and "levels" in obj["rules_reader"]
    assert built["purpose"] == "doc_read" and 3000 < built["lock_wait"] < 4200
    assert da.run_once(_S(), now=now) == "have"
