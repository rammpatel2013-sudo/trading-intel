"""equity foundation (equity / swing side E1-E6)

Revision ID: 0057_equity_foundation
Revises: 0056_finra_dix_daily
Create Date: 2026-11-14

Hand-written (never autogenerate). Creates ONLY the 10 equity_* / external_nhnl tables in
``trading_intel/memory/models_equity.py``. Additive; touches no existing table. ADR-009.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0057_equity_foundation'
down_revision: Union[str, None] = '0056_finra_dix_daily'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

F = sa.Float


def upgrade() -> None:
    op.create_table(
        'equity_members',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('symbol', sa.String(16), nullable=False),
        sa.Column('index_code', sa.String(8), nullable=False),
        sa.Column('start_date', sa.Date(), nullable=True),
        sa.Column('end_date', sa.Date(), nullable=True),
        sa.Column('source', sa.String(24), nullable=False),
    )
    op.create_index('ix_equity_members_symbol', 'equity_members', ['symbol'])
    op.create_table(
        'equity_profiles',
        sa.Column('symbol', sa.String(16), primary_key=True),
        sa.Column('name', sa.String(160)), sa.Column('sector', sa.String(48)),
        sa.Column('industry', sa.String(96)), sa.Column('exchange', sa.String(24)),
        sa.Column('is_etf', sa.Boolean()), sa.Column('is_adr', sa.Boolean()),
        sa.Column('ipo_date', sa.Date()), sa.Column('updated', sa.Date()),
    )
    op.create_table(
        'equity_bars',
        sa.Column('symbol', sa.String(16), primary_key=True),
        sa.Column('date', sa.Date(), primary_key=True),
        sa.Column('open', F()), sa.Column('high', F()), sa.Column('low', F()),
        sa.Column('close', F(), nullable=False), sa.Column('volume', sa.BigInteger()),
        sa.Column('market_cap', F()),
    )
    op.create_index('ix_equity_bars_date', 'equity_bars', ['date'])
    op.create_table(
        'equity_fundamentals',
        sa.Column('symbol', sa.String(16), primary_key=True),
        sa.Column('period_end', sa.Date(), primary_key=True),
        sa.Column('available_date', sa.Date()), sa.Column('fiscal_year', sa.String(8)),
        sa.Column('period', sa.String(4)),
        *[sa.Column(c, F()) for c in (
            'revenue', 'gross_profit', 'operating_income', 'net_income', 'ebitda', 'eps_diluted',
            'interest_expense', 'sbc', 'operating_cash_flow', 'capex', 'free_cash_flow',
            'total_assets', 'total_equity', 'total_debt', 'cash', 'shares_diluted')],
    )
    op.create_table(
        'equity_factor_returns',
        sa.Column('date', sa.Date(), primary_key=True),
        sa.Column('factor', sa.String(48), primary_key=True),
        sa.Column('ret', F()), sa.Column('kind', sa.String(8)),
        sa.Column('n_stocks', sa.Integer()), sa.Column('r2', F()),
    )
    op.create_table(
        'equity_stock_factors',
        sa.Column('date', sa.Date(), primary_key=True),
        sa.Column('symbol', sa.String(16), primary_key=True),
        sa.Column('sector', sa.String(48)),
        *[sa.Column(c, F()) for c in (
            'momentum', 'size', 'value', 'beta', 'volatility', 'quality', 'growth', 'ret', 'resid')],
    )
    op.create_index('ix_equity_stock_factors_symbol', 'equity_stock_factors', ['symbol'])
    op.create_table(
        'external_nhnl',
        sa.Column('date', sa.Date(), primary_key=True),
        *[sa.Column(c, sa.Integer()) for c in (
            'nyse_new_highs', 'nyse_new_lows', 'nyse_issues', 'nasdaq_new_highs', 'nasdaq_new_lows',
            'nasdaq_issues', 'new_highs', 'new_lows', 'total_issues', 'spx_new_highs', 'spx_new_lows',
            'ndx_new_highs', 'ndx_new_lows')],
        sa.Column('status', sa.String(16)),
    )
    op.create_table(
        'equity_gate_daily',
        sa.Column('date', sa.Date(), primary_key=True),
        sa.Column('color', sa.String(8)), sa.Column('score', sa.Integer()),
        sa.Column('trend_note', sa.String(16)),
        *[sa.Column(c, F()) for c in (
            'median_drawdown', 'pct_down_10', 'pct_down_20', 'repair_flow', 'hidden_bear_gap',
            'pct_above_50', 'pct_above_200', 'nhnl_10d')],
        sa.Column('n_stocks', sa.Integer()), sa.Column('votes', sa.JSON()),
    )
    op.create_table(
        'equity_candidates',
        sa.Column('date', sa.Date(), primary_key=True),
        sa.Column('symbol', sa.String(16), primary_key=True),
        sa.Column('list_name', sa.String(16), primary_key=True),
        sa.Column('rank', sa.Integer()), sa.Column('score', F()), sa.Column('sector', sa.String(48)),
        *[sa.Column(c, F()) for c in ('close', 'own_move_6m', 'own_move_12m', 'total_move_12m')],
        sa.Column('trend_ok', sa.Boolean()), sa.Column('box_days', sa.Integer()),
        sa.Column('box_width', F()), sa.Column('box_high', F()), sa.Column('details', sa.JSON()),
    )
    op.create_table(
        'equity_call_iv',
        sa.Column('date', sa.Date(), primary_key=True),
        sa.Column('symbol', sa.String(16), primary_key=True),
        sa.Column('expiry', sa.Date()), sa.Column('dte', sa.Integer()),
        *[sa.Column(c, F()) for c in ('spot', 'iv_atm', 'iv_call_25d', 'call_volume', 'call_oi')],
    )


def downgrade() -> None:
    for t in ('equity_call_iv', 'equity_candidates', 'equity_gate_daily', 'external_nhnl',
              'equity_stock_factors', 'equity_factor_returns', 'equity_fundamentals',
              'equity_bars', 'equity_profiles', 'equity_members'):
        op.drop_table(t)
