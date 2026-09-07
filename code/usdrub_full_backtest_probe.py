#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, '/root/prop-desk/futures_lab')
sys.path.insert(0, '/root/prop-desk/strategy_combine/code')

from futures_lab import run_backtest, _synthetic_spec_for_file  # type: ignore
from equity_shape_filter import equity_shape_metrics  # type: ignore
from strategy_architect_autopilot import archetype_match_score, portfolio_score, trade_bucket  # type: ignore

ROOT = Path('/root/prop-desk/strategy_combine')
DATA = Path('/root/prop-desk/futures_lab/artifacts/tinkoff_futures_data/USDRUB_60d_1h_continuous.csv')
PARAMS = {'lookback': 10, 'atr_mult': 1.0}
INITIAL = 1_000_000.0

def trade_to_dict(t):
    d = getattr(t, '__dict__', {})
    out = {}
    for k, v in d.items():
        if isinstance(v, (str, int, float, bool)) or v is None:
            out[k] = v
    return out

def main() -> int:
    df = pd.read_csv(DATA)
    metrics, trades, eq = run_backtest(
        df,
        _synthetic_spec_for_file('USDRUB'),
        'atr_breakout',
        PARAMS,
        initial_cash=INITIAL,
        contracts=1,
        max_contracts=1,
    )
    shape = equity_shape_metrics(eq, INITIAL)
    vals = [float(x) - INITIAL for x in eq.tolist()]
    peak = vals[0]
    cur_peak_i = 0
    maxdd = 0.0
    maxdd_i = 0
    peak_i = 0
    for i, v in enumerate(vals):
        if v > peak:
            peak = v
            cur_peak_i = i
        dd = peak - v
        if dd > maxdd:
            maxdd = dd
            maxdd_i = i
            peak_i = cur_peak_i
    n = len(vals)
    q = max(1, n // 4)
    quarter_pnls = []
    for a, b in [(0, q), (q, 2*q), (2*q, 3*q), (3*q, n)]:
        b = min(b, n)
        quarter_pnls.append(round(vals[b - 1] - vals[a], 4) if b > a else 0.0)
    row = {**metrics, **shape, 'trades': metrics.get('trade_count')}
    out = {
        'data_file': str(DATA),
        'rows': len(df),
        'first_bar': df.iloc[0].to_dict(),
        'last_bar': df.iloc[-1].to_dict(),
        'ticker': 'USDRUB',
        'timeframe': '1h',
        'strategy': 'atr_breakout',
        'params': PARAMS,
        'metrics': metrics,
        'shape': shape,
        'archetype_match_score': archetype_match_score(row),
        'portfolio_score': portfolio_score(row),
        'trade_bucket': trade_bucket(metrics.get('trade_count')),
        'equity_final': round(vals[-1], 4),
        'equity_min': round(min(vals), 4),
        'equity_max': round(max(vals), 4),
        'maxdd_calc': round(maxdd, 4),
        'maxdd_peak_idx': peak_i,
        'maxdd_trough_idx': maxdd_i,
        'quarter_pnls': quarter_pnls,
        'last_25_pct_pnl': quarter_pnls[-1],
        'trade_rows': [trade_to_dict(t) for t in trades],
        'live_orders': 0,
    }
    rep = ROOT / 'reports/strategy_architect/usdrub_atr_breakout_full_60d.json'
    rep.write_text(json.dumps(out, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
    print(json.dumps({
        'data_file': out['data_file'],
        'rows': out['rows'],
        'first_bar': out['first_bar'],
        'last_bar': out['last_bar'],
        'ticker': out['ticker'],
        'timeframe': out['timeframe'],
        'strategy': out['strategy'],
        'params': out['params'],
        'metrics': out['metrics'],
        'shape': out['shape'],
        'equity_final': out['equity_final'],
        'maxdd_calc': out['maxdd_calc'],
        'quarter_pnls': out['quarter_pnls'],
        'last_25_pct_pnl': out['last_25_pct_pnl'],
        'report': str(rep),
    }, ensure_ascii=False, indent=2, default=str))
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
