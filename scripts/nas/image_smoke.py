"""Image smoke test - 2026-11-14 (O1, code audit C1/C4), Claude for Mithil.

Run INSIDE the built image after a rebuild:
    sudo /usr/local/bin/docker run --rm trading-intel python scripts/nas/image_smoke.py

Imports every third-party package the NAS code uses, then every trading_intel module.
FAIL (exit 1) only when a module cannot be found - i.e. a package is missing from the
image. Other import errors (no .env inside a bare container, DB not reachable, ...)
are counted as "needs runtime" and do not fail the check.
"""
from __future__ import annotations

import importlib
import pkgutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # repo root (/app in the image)

PACKAGES = (
    "alembic", "apscheduler", "convexlib", "docx", "dotenv", "fastapi", "fastmcp",
    "fredapi", "google_auth_oauthlib", "googleapiclient", "httpx", "numpy", "ollama",
    "openpyxl", "pandas", "pdfplumber", "pgvector", "playwright", "plotly", "psycopg",
    "pydantic", "pydantic_settings", "pypdf", "pysentiment2", "requests", "scipy",
    "sqlalchemy", "streamlit", "structlog", "ta", "yfinance",
)
SKIP_PREFIXES = ("trading_intel.dashboard.pages", "trading_intel.dashboard.Home")


def main() -> int:
    missing: list[str] = []
    for name in PACKAGES:
        try:
            importlib.import_module(name)
        except ModuleNotFoundError as exc:
            missing.append(f"{name} ({exc})")
    import trading_intel

    ok = runtime = 0
    for info in pkgutil.walk_packages(trading_intel.__path__, "trading_intel."):
        if info.name.startswith(SKIP_PREFIXES):
            continue
        try:
            importlib.import_module(info.name)
            ok += 1
        except ModuleNotFoundError as exc:
            top = (exc.name or "").split(".")[0]
            if top and top != "trading_intel":
                missing.append(f"{info.name} needs {exc.name}")
            else:
                runtime += 1
        except Exception:  # noqa: BLE001 - env/DB-dependent import, not a packaging fault
            runtime += 1
    print(f"image smoke: {len(PACKAGES)} packages, {ok} trading_intel modules OK, {runtime} need runtime")
    if missing:
        print("MISSING:", *missing, sep="\n  ")
        return 1
    print("image smoke OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
