"""Strip email chrome from a fetched letter body.

Letters arrive as Substack / Mailchimp HTML-to-text dumps carrying a lot that is
not the author's prose: the "View this post on the web at ..." preamble, raw CSS
blocks (Substack ships a ``@media (max-width:1024px){ .typography ... }`` sheet
inline), Mailchimp campaign-archive and list-manage tracking URLs, unsubscribe
footers, and every hyperlink duplicated as ``label (https://...)``.

Before this module the daily brief sliced ``note_md[:600]`` raw, so the CSS and
the tracking URLs rendered straight into the report. ``clean_body`` removes them;
``snippet`` truncates on a word boundary instead of mid-word.

Pure stdlib, no network, deterministic — safe to call in the render path.
"""

from __future__ import annotations

import re

__all__ = ["clean_body", "snippet"]

#: Substack's "read it on the web" preamble — always the first body line.
_WEB_PREAMBLE = re.compile(r"^\s*View this post on the web at \S+\s*$", re.I | re.M)

#: A CSS at-rule and its (possibly nested) block. Substack inlines a stylesheet
#: into the text/plain part; brace-matching one level of nesting covers it.
_AT_RULE = re.compile(r"@(?:media|supports|font-face|import|charset)[^{]*\{(?:[^{}]|\{[^{}]*\})*\}?", re.S)

#: A bare CSS ruleset: one or more selectors then a declaration block.
_RULESET = re.compile(r"(?m)^[ \t]*[.#][A-Za-z][\w .,:#>\-\[\]=\"'()]*\{[^{}]*\}?\s*$")

#: A dangling selector list line (``.typography .pullquote-align-left,``) left
#: behind when a truncated body cuts the stylesheet mid-rule.
_SELECTOR_LINE = re.compile(r"(?m)^[ \t]*[.#][\w-]+(?:[ .#>:][\w.#>:\-]+)*\s*,?\s*$")

#: Tracking / boilerplate hosts and footer phrases, matched per line.
_JUNK_LINE = re.compile(
    r"(?i)("
    r"campaign-archive\.com|list-manage\.com|login\.mailchimp\.com|"
    r"email-referral|/unsubscribe|manage your subscription|update your profile|"
    r"email marketing powered by mailchimp|you are receiving this email|"
    r"if you do not want to receive|©\s*\d{4}|all rights reserved|"
    r"substack\.com/(?:redirect|app-link|profile)|"
    r"^\s*read more\s*[.(]?\s*$|^\s*unsubscribe\s*$|"
    r"^\s*email from substack\s*$|^\s*view in browser\s*$|"
    r"^\s*(?:forwarded this|share this post|restack|leave a comment|"
    r"upgrade to paid|subscribe now|refer a friend)\b|"
    r"^\s*[-=_*]{6,}\s*$"
    r")"
)

#: ``label (https://url)`` — Mailchimp duplicates every anchor this way.
_INLINE_LINK = re.compile(r"\s*\((?:https?://|mailto:)[^)\s]*\)")

#: A line that is nothing but a URL.
_BARE_URL = re.compile(r"(?m)^\s*(?:https?://|mailto:)\S+\s*$")

#: 3+ consecutive newlines.
_BLANKS = re.compile(r"\n{3,}")


def clean_body(raw: str | None) -> str:
    """Return ``raw`` with email chrome, inline CSS and tracking URLs removed.

    Idempotent and lossless for real prose: only the patterns above are touched,
    so an author's own sentences (including ones containing a URL mid-line) come
    through unchanged. Returns ``""`` for empty input.
    """
    if not raw:
        return ""
    text = raw.replace("\r\n", "\n").replace("\r", "\n")
    text = _WEB_PREAMBLE.sub("", text)
    text = _AT_RULE.sub("", text)
    text = _RULESET.sub("", text)
    text = _SELECTOR_LINE.sub("", text)
    text = _BARE_URL.sub("", text)
    kept = [ln for ln in text.split("\n") if not _JUNK_LINE.search(ln)]
    text = _INLINE_LINK.sub("", "\n".join(kept))
    kept = [ln for ln in text.split("\n") if not _JUNK_LINE.search(ln)]
    text = _BLANKS.sub("\n\n", "\n".join(kept))
    return text.strip()


def snippet(text: str | None, max_chars: int, *, ellipsis: str = "…") -> str:
    """Truncate ``text`` to at most ``max_chars`` on a word boundary.

    The brief used to slice ``[:90]`` / ``[:320]`` / ``[:600]`` straight through
    words ("~7x forward ad", "supply cha"). This backs up to the last space and
    appends an ellipsis, and never returns more than ``max_chars`` characters.
    """
    if not text:
        return ""
    t = " ".join(text.split()) if "\n" not in text else text.strip()
    if len(t) <= max_chars:
        return t
    cut = t[: max(0, max_chars - len(ellipsis))]
    sp = cut.rfind(" ")
    if sp > max_chars * 0.6:  # only back up if it doesn't gut the snippet
        cut = cut[:sp]
    return cut.rstrip(" ,;:—-") + ellipsis
