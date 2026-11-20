"""quotes_daily + desk_bars become VIEWS over equity_bars (2026-11-19, DB wave A2 step 7b)

Revision ID: 0065_bars_views
Revises: 0064_etf_board
Create Date: 2026-11-19

Hand-written (never autogenerate). Schema rule 1: equity_bars owns daily bars.
- The two tables are RENAMED to quotes_daily_old / desk_bars_old (rows kept; drop in a later ship after a week).
- Any of their rows equity_bars still lacks is copied first (ON CONFLICT DO NOTHING).
- quotes_daily VIEW = equity_bars rows for watchlist symbols (tickers), same columns as before: id (stable hash
  of symbol + date), open/high/low default to close, volume 0 when missing, and rv20 / rv60 computed live =
  annualised sample stdev of daily log returns over 20 / 60 sessions (sqrt 252), NULL until the window is full -
  the same maths as prices/realized_vol.py (checked 11/19 on the NAS DB: mean |diff| vs the stored values
  0.0001-0.0003 for SPX/SPY/AAPL/NVDA in 2026; stored values were computed from shorter fetch windows).
- desk_bars VIEW = equity_bars rows (symbol, date, OHLCV).
Writers moved to equity_bars in the same ship (quotes_daily job, desk_brief save_bars, backfill_vol_history).
Downgrade: drop the views, rename the tables back, copy back any equity_bars rows they miss.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "0065_bars_views"
down_revision: Union[str, None] = "0064_etf_board"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

QUOTES_VIEW = """
CREATE VIEW quotes_daily AS
SELECT (hashtext(b.symbol)::bigint * 100000 + (b.date - DATE '1970-01-01'))::bigint AS id,
       b.symbol, b.date,
       COALESCE(b.open, b.close) AS open, COALESCE(b.high, b.close) AS high,
       COALESCE(b.low, b.close) AS low, b.close, COALESCE(b.volume, 0)::bigint AS volume,
       CASE WHEN count(b.r) OVER w20 = 20 THEN stddev_samp(b.r) OVER w20 * sqrt(252) END AS rv20,
       CASE WHEN count(b.r) OVER w60 = 60 THEN stddev_samp(b.r) OVER w60 * sqrt(252) END AS rv60
FROM (
    SELECT e.symbol, e.date, e.open, e.high, e.low, e.close, e.volume,
           ln(e.close / NULLIF(lag(e.close) OVER (PARTITION BY e.symbol ORDER BY e.date), 0)) AS r
    FROM equity_bars e
    WHERE e.close > 0 AND e.symbol IN (SELECT symbol FROM tickers)
) b
WINDOW w20 AS (PARTITION BY b.symbol ORDER BY b.date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW),
       w60 AS (PARTITION BY b.symbol ORDER BY b.date ROWS BETWEEN 59 PRECEDING AND CURRENT ROW)
"""
DESK_VIEW = """
CREATE VIEW desk_bars AS
SELECT symbol, date, open, high, low, close, volume FROM equity_bars
"""
GRANT_RO = """
DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'claude_ro') THEN
    GRANT SELECT ON quotes_daily, desk_bars, quotes_daily_old, desk_bars_old TO claude_ro;
  END IF;
END $$
"""


def upgrade() -> None:
    for src in ("quotes_daily", "desk_bars"):
        op.execute(
            f"""INSERT INTO equity_bars (symbol, date, open, high, low, close, volume)
                SELECT symbol, date, open, high, low, close, volume FROM {src}
                WHERE close IS NOT NULL
                ON CONFLICT (symbol, date) DO NOTHING"""
        )
        op.execute(f"ALTER TABLE {src} RENAME TO {src}_old")
    op.execute(QUOTES_VIEW)
    op.execute(DESK_VIEW)
    op.execute(GRANT_RO)


def downgrade() -> None:
    op.execute("DROP VIEW desk_bars")
    op.execute("DROP VIEW quotes_daily")
    op.execute("ALTER TABLE desk_bars_old RENAME TO desk_bars")
    op.execute("ALTER TABLE quotes_daily_old RENAME TO quotes_daily")
    op.execute(
        """INSERT INTO quotes_daily (symbol, date, open, high, low, close, volume)
           SELECT symbol, date, COALESCE(open, close), COALESCE(high, close), COALESCE(low, close), close,
                  COALESCE(volume, 0)
           FROM equity_bars WHERE close IS NOT NULL AND symbol IN (SELECT symbol FROM tickers)
           ON CONFLICT ON CONSTRAINT uq_quotes_daily DO NOTHING"""
    )
    op.execute(
        """INSERT INTO desk_bars (symbol, date, open, high, low, close, volume)
           SELECT e.symbol, e.date, e.open, e.high, e.low, e.close, e.volume FROM equity_bars e
           WHERE e.close IS NOT NULL AND e.symbol IN (SELECT DISTINCT symbol FROM desk_bars)
           ON CONFLICT (symbol, date) DO NOTHING"""
    )
