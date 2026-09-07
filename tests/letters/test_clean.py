"""Letter-body cleaning — the email chrome that used to render as commentary."""
from __future__ import annotations

from trading_intel.letters.clean import clean_body, snippet

# A composite of what actually reached the 2026-09-07 brief.
_RAW = """View this post on the web at https://docmcgraw.substack.com/p/s-and-p

Email from Substack   
@media (max-width: 1024px) {
  .typography .pullquote-align-left,
  .typography.editor .pullquote-align-right {
    color: red;
  }
}
.typography .pullquot
📏 Expected Move: 7735 <> 7790. Roughly 27.5 points either side.
Read more (https://www.jaguaranalytics.com/options/cloudflare-net)
https://us10.campaign-archive.com/?e=54bab&u=a96d5&id=02b53
© 2026 JaguarAnalytics. All rights reserved.
Email Marketing Powered by Mailchimp
If you do not want to receive emails, you can unsubscribe (https://x.com/u) .
"""


def test_strips_substack_web_preamble():
    assert "View this post on the web" not in clean_body(_RAW)


def test_strips_inline_css():
    out = clean_body(_RAW)
    assert "@media" not in out
    assert "typography" not in out
    assert "{" not in out


def test_strips_tracking_and_footer():
    out = clean_body(_RAW)
    for junk in ("campaign-archive", "Mailchimp", "unsubscribe", "All rights reserved"):
        assert junk not in out


def test_keeps_the_authors_prose():
    out = clean_body(_RAW)
    assert "Expected Move: 7735 <> 7790" in out
    assert "27.5 points either side" in out


def test_clean_body_is_idempotent():
    once = clean_body(_RAW)
    assert clean_body(once) == once


def test_clean_body_handles_empty():
    assert clean_body(None) == ""
    assert clean_body("") == ""


def test_snippet_cuts_on_a_word_boundary():
    # The tracker used rationale[:90] and shipped "~7x forward ad".
    text = ("Shows strong revenue growth and positive earnings, currently "
            "undervalued at ~7x forward adjusted earnings.")
    out = snippet(text, 90)
    assert len(out) <= 90
    assert not out.rstrip("…").endswith(("ad", "forwar"))
    assert out.endswith("…")


def test_snippet_leaves_short_text_alone():
    assert snippet("Cytokinetics is the focus.", 90) == "Cytokinetics is the focus."
