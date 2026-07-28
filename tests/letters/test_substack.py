"""Tests for the Substack RSS parser (pure, no I/O)."""

from __future__ import annotations

from trading_intel.letters.substack import is_letter, parse_feed, slug

_LOREM = (
    "We bought AAPL and MSFT this quarter. Lorem ipsum dolor sit amet, consectetur "
    "adipiscing elit, sed do eiusmod tempor incididunt ut labore et dolore magna aliqua. "
    "Ut enim ad minim veniam, quis nostrud exercitation ullamco laboris nisi ut aliquip ex "
    "ea commodo consequat. Duis aute irure dolor in reprehenderit in voluptate velit esse "
    "cillum dolore eu fugiat nulla pariatur. Excepteur sint occaecat cupidatat non proident, "
    "sunt in culpa qui officia deserunt mollit anim id est laborum sed ut perspiciatis unde."
)

_RSS = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/">
<channel>
<title>Test Fund</title>
<item>
<title>Q2 2026 Letter</title>
<link>https://x.substack.com/p/q2-2026-letter</link>
<guid>https://x.substack.com/p/q2-2026-letter</guid>
<pubDate>Mon, 14 Jul 2026 12:00:00 GMT</pubDate>
<content:encoded><![CDATA[<p>{_LOREM}</p>]]></content:encoded>
</item>
<item>
<title>Quick note</title>
<link>https://x.substack.com/p/quick</link>
<guid>gid-2</guid>
<pubDate>Tue, 15 Jul 2026 12:00:00 GMT</pubDate>
<description>short</description>
</item>
</channel>
</rss>"""


def test_parse_feed_extracts_items():
    entries = parse_feed(_RSS)
    assert len(entries) == 2
    first = entries[0]
    assert first.title == "Q2 2026 Letter"
    assert first.link == "https://x.substack.com/p/q2-2026-letter"
    assert first.published == "2026-07-14"
    assert "<p>" not in first.content and "AAPL" in first.content


def test_is_letter_filters_short_notes():
    entries = parse_feed(_RSS)
    assert is_letter(entries[0]) is True
    assert is_letter(entries[1]) is False  # "short" description


def test_slug_uses_date_and_title():
    entries = parse_feed(_RSS)
    assert slug(entries[0]) == "2026-07-14-q2-2026-letter"
