"""Leveraged-ETF close flow - manual / one-pass runner (2026-11-19).

NAS:    sudo bash /var/services/homes/drmithil/trading-intel/scripts/nas/run_script.sh letf_close_flow --once
Laptop: python scripts/letf_close_flow.py --once
``--once`` = one pass now (no waiting for the last hour). Without it: the same as the DSM task
(``run_job.sh letf_close_live``): wait until 60 min before the close, then every 2 min to close + 6.
"""

from trading_intel.scheduler.jobs.letf_close_live import main

if __name__ == "__main__":
    raise SystemExit(main())
