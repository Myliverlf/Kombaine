#!/usr/bin/env python3
from __future__ import annotations
import json
import pathlib
import sys
ROOT = pathlib.Path('/root/prop-desk/strategy_combine')
sys.path.insert(0, str(ROOT / 'code'))
from portfolio_equity_analyzer import backtest_curve
summary = json.loads((ROOT / 'reports/strategy_architect/portfolio_50pct_candidates_20260824.json').read_text())
for r in summary['selected']:
    c = backtest_curve(r, initial_cash=report_capital())['pnl']
    print(r['ticker'], r['timeframe'], r['strategy'], 'stored', r['total_pnl'], 'curve_final', round(c[-1]-c[0], 2), 'n', len(c), 'firstlast', round(c[0], 2), round(c[-1], 2))
