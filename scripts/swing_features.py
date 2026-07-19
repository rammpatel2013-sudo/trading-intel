#!/usr/bin/env python3
"""On-demand CLI for the swing feature-snapshot collector.

Thin wrapper over ``trading_intel.scheduler.jobs.swing_features`` so the same
code runs here (via ``run_swing_features.bat``) and on the NAS
(``python -m trading_intel.scheduler.jobs.swing_features`` from a DSM task).

    run_swing_features.bat
    .venv\\Scripts\\python scripts\\swing_features.py AAPL NVDA
"""

from __future__ import annotations

from trading_intel.scheduler.jobs.swing_features import main

if __name__ == "__main__":
    main()
