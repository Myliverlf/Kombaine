#!/usr/bin/env python3
import sys, json, pathlib
sys.path.insert(0, '/root/prop-desk/strategy_combine/code')
sys.path.insert(0, '/root/prop-desk/futures_lab')
from futures_lab import run_backtest, _synthetic_spec_for_file
import pandas as pd

DATA = pathlib.Path('/root/prop-desk/futures_lab/artifacts/tinkoff_futures_data')

# Test with SBER using synthetic spec
csv_path = DATA / 'SBER_60d_1h_continuous.csv'
df = pd.read_csv(csv_path)
spec = _synthetic_spec_for_file('SBER')
print('Synthetic spec:', spec.__dict__ if hasattr(spec, '__dict__') else spec)

mm, tr, eq = run_backtest(df, spec, 'atr_breakout', {'lookback': 10, 'atr_mult': 1.0}, initial_cash=19800, contracts=1, max_contracts=1, commission_per_contract=5.0, slippage_bps=1.0, debug_only=True)
print('Metrics:', mm)
print('Trades:', len(tr))
print('Final equity:', eq[-1] if len(eq) > 0 else 'N/A')
