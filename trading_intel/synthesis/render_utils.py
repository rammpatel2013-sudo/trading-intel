"""Tiny formatting primitives shared by the daily-brief renderers.

These live apart from any one renderer so the brief's section modules can
import them without importing each other (and without a cycle).
"""

from __future__ import annotations

import html as _html
from typing import Any


def _esc(v: Any) -> str:
    return _html.escape("" if v is None else str(v))


def _fmt(v: float | None, digits: int = 0) -> str:
    if v is None:
        return "—"
    return f"{v:,.{digits}f}"


def _pct(v: float | None, digits: int = 1) -> str:
    if v is None:
        return "—"
    sign = "+" if v >= 0 else ""
    return f"{sign}{v:.{digits}f}%"
