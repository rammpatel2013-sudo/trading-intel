"""O1 build-side guards (2026-11-14, code audit C1/C2/C4).

- convexlib is pinned to a commit, so a cached image layer is exact.
- the six unused packages stay out of pyproject.toml and out of the code.
- .dockerignore keeps the cockpit data out and never hides a build input.
- deploy.sh builds with cached layers by default; --no-cache only behind --fresh.
"""
from __future__ import annotations

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REMOVED = {
    "polars": "polars",
    "scikit-learn": "sklearn",
    "langchain": "langchain",
    "tiktoken": "tiktoken",
    "sec-api": "sec_api",
    "discord-webhook": "discord_webhook",
}
BUILD_INPUTS = ("trading_intel", "scripts", "alembic", "alembic.ini", "pyproject.toml")


def _deps() -> list[str]:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return data["project"]["dependencies"]


def test_convexlib_pinned_to_a_commit() -> None:
    dep = next(d for d in _deps() if d.startswith("convexlib"))
    assert re.search(r"\.git@[0-9a-f]{40}$", dep), dep


def test_removed_packages_not_declared() -> None:
    names = {re.split(r"[<>=@\[ ;]", d, maxsplit=1)[0].strip().lower() for d in _deps()}
    assert not names & set(REMOVED), names & set(REMOVED)


def test_removed_packages_not_imported() -> None:
    pat = re.compile(
        r"^\s*(?:import|from)\s+(?:" + "|".join(REMOVED.values()) + r")\b", re.MULTILINE
    )
    hits = []
    for base in ("trading_intel", "scripts", "alembic"):
        for p in (ROOT / base).rglob("*.py"):
            if "data" in p.relative_to(ROOT).parts:
                continue
            if pat.search(p.read_text(encoding="utf-8", errors="ignore")):
                hits.append(str(p.relative_to(ROOT)))
    assert not hits, hits


def test_dockerignore_keeps_build_inputs() -> None:
    lines = [
        ln.strip()
        for ln in (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
        if ln.strip() and not ln.strip().startswith("#")
    ]
    assert "scripts/cvxmap/data" in lines
    for keep in BUILD_INPUTS:
        assert keep not in lines and f"/{keep}" not in lines and f"{keep}/" not in lines, keep
    for ln in lines:
        assert not ln.startswith(("scripts/*", "trading_intel/", "alembic/")) or "data" in ln, ln


def test_deploy_cached_by_default() -> None:
    s = (ROOT / "scripts" / "nas" / "deploy.sh").read_bytes().decode("utf-8")
    assert "\r" not in s, "deploy.sh must stay LF"
    assert 'NOCACHE=""' in s
    assert '--fresh) NOCACHE="--no-cache"' in s
    assert '"$DOCKER" build $NOCACHE -t trading-intel .' in s
    assert "build --no-cache -t trading-intel" not in s
