"""Compare data freshness across databases.

Usage:
    python db_freshness.py                 # uses DATABASE_URL from .env (Supabase)
    python db_freshness.py "<db_url>"      # any other DB, e.g. the local NAS pg

To check the local NAS Postgres from your laptop (reachable on the LAN):
    python db_freshness.py "postgresql+psycopg://intel:intel@192.168.1.211:5433/trading_intel"

Prints, per collector table, the latest timestamp and row count. Delete when done.
"""

import sys

from sqlalchemy import create_engine, text

# (table, time_column) for the main collector/output tables
TABLES = [
    ("greeks_snapshots", "ts"),
    ("greeks_chain", "ts"),
    ("intraday_flow", "ts"),
    ("flow_snapshots", "ts"),
    ("live_gex", "ts"),
    ("delta_flow", "ts"),
    ("skew_snapshots", "ts"),
    ("index_skew_daily", "date"),
    ("vol_richness", "ts"),
    ("vix_options_chain", "ts"),
    ("quotes_daily", "date"),
    ("vix_data", "date"),
    ("tas_prints", "trade_date"),
    ("am_summaries", "date"),
    ("signals", "ts"),
]

if len(sys.argv) > 1:
    url = sys.argv[1]
else:
    from trading_intel.config import get_settings

    url = get_settings().DATABASE_URL

# Mask credentials when echoing which DB we hit
shown = url
if "@" in shown:
    shown = shown.split("@", 1)[0].rsplit(":", 1)[0] + ":***@" + shown.split("@", 1)[1]
print(f"DB: {shown}\n")
print(f"{'table':<22}{'latest':<28}{'rows':>10}")
print("-" * 60)

engine = create_engine(url, pool_pre_ping=True)
with engine.connect() as c:
    for tbl, col in TABLES:
        try:
            r = c.execute(text(f"select max({col})::text, count(*) from {tbl}")).first()
            latest = r[0] if r and r[0] is not None else "(empty)"
            print(f"{tbl:<22}{str(latest):<28}{r[1]:>10}")
        except Exception as e:
            msg = str(e).splitlines()[0][:32]
            print(f"{tbl:<22}{'ERR: ' + msg:<28}{'':>10}")
