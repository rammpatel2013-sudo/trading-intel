"""Substack RSS fetch + parse for investor letters.

``parse_feed`` is pure (RSS XML -> entries); ``fetch_feed`` is the thin httpx GET.
``save_entry`` writes a letter as markdown into the research letters dir so the
existing ``memory.watchlist_ingest`` / knowledge pipeline picks it up unchanged.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from xml.etree import ElementTree as ET

_CONTENT_ENCODED = "{http://purl.org/rss/1.0/modules/content/}encoded"
_TAG = re.compile(r"(?s)<[^>]+>")
_MIN_LETTER_CHARS = 400


@dataclass(frozen=True, slots=True)
class FeedEntry:
    """One RSS item (a Substack post)."""

    title: str
    link: str
    published: str  # ISO date, best-effort
    content: str  # plain text (HTML stripped)
    guid: str


def _strip_html(s: str) -> str:
    s = re.sub(r"(?is)<(script|style).*?</\1>", " ", s or "")
    s = _TAG.sub(" ", s)
    s = (
        s.replace("&nbsp;", " ")
        .replace("&amp;", "&")
        .replace("&#39;", "'")
        .replace("&quot;", '"')
        .replace("&mdash;", "-")
    )
    s = re.sub(r"[ \t]+", " ", s)
    return re.sub(r"\n\s*\n\s*\n+", "\n\n", s).strip()


def _to_iso(pubdate: str) -> str:
    for fmt in ("%a, %d %b %Y %H:%M:%S %Z", "%a, %d %b %Y %H:%M:%S %z", "%a, %d %b %Y %H:%M:%S"):
        try:
            return datetime.strptime(pubdate.strip(), fmt).date().isoformat()
        except ValueError:
            continue
    return pubdate[:10].strip() if pubdate else ""


def parse_feed(xml: str | bytes) -> list[FeedEntry]:
    """Parse an RSS 2.0 feed (Substack) into entries. Pure — no I/O."""
    root = ET.fromstring(xml)  # noqa: S314 — Substack RSS feed, a controlled source
    out: list[FeedEntry] = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        guid = (item.findtext("guid") or link).strip()
        pub = item.findtext("pubDate") or ""
        body = item.findtext(_CONTENT_ENCODED) or item.findtext("description") or ""
        out.append(
            FeedEntry(
                title=title,
                link=link,
                published=_to_iso(pub),
                content=_strip_html(body),
                guid=guid,
            )
        )
    return out


def is_letter(entry: FeedEntry) -> bool:
    """Keep substantive posts; drop short notes/announcements (heuristic)."""
    return len(entry.content) >= _MIN_LETTER_CHARS


def slug(entry: FeedEntry) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", (entry.title or entry.guid).lower()).strip("-")[:60]
    return f"{entry.published or 'nodate'}-{base or 'letter'}"


def save_entry(entry: FeedEntry, fund_dir: Path) -> Path | None:
    """Write the entry as markdown into ``fund_dir``; skip if it already exists.

    Returns the new path, or ``None`` when the file was already present (so the
    caller only ingests genuinely new letters).
    """
    fund_dir.mkdir(parents=True, exist_ok=True)
    dest = fund_dir / f"{slug(entry)}.md"
    if dest.exists():
        return None
    dest.write_text(
        f"# {entry.title}\n\nSource: {entry.link}\nDate: {entry.published}\n\n{entry.content}\n",
        encoding="utf-8",
    )
    return dest


def fetch_feed(url: str, *, timeout: float = 20.0) -> str:
    """Thin RSS GET (httpx). ``parse_feed`` does the real work."""
    import httpx

    headers = {"User-Agent": "trading-intel-letters/1.0 (research)"}
    with httpx.Client(timeout=timeout, headers=headers, follow_redirects=True) as client:
        resp = client.get(url)
        resp.raise_for_status()
        return resp.text
