#!/usr/bin/env python3
import sys, json, pathlib, itertools
sys.path.insert(0, '/root/prop-desk/strategy_combine/code')
sys.path.insert(0, '/root/prop-desk/futures_lab')
from portfolio_equity_analyzer import backtest_curve, align_curves, sum_curves
import pandas as pd
import numpy as np

DATA_ROOT = pathlib.Path('/root/prop-desk/futures_lab/artifacts/tinkoff_futures_data')
REPORT_DIR = pathlib.Path('/root/prop-desk/strategy_combine/reports/strategy_architect')

# Real specs from Tinkoff API
SPECS = {
    'LKOH': {'go': 7413, 'point_value': 1.0, 'price': 41654},
    'SBER': {'go': 4647, 'point_value': 100.0, 'price': 270},
    'GAZP': {'go': 1432, 'point_value': 100.0, 'price': 83},
    'Si': {'go': 12718, 'point_value': 1.0, 'price': 84603},
    'IMOEX': {'go': 2140, 'point_value': 10.0, 'price': 2075},
    'BR': {'go': 16344, 'point_value': 832.88, 'price': 92},
    'USDRUB': {'go': 1200, 'point_value': 1.0, 'price': 80},
    'EURRUB': {'go': 1500, 'point_value': 1.0, 'price': 90},
    'NG': {'go': 800, 'point_value': 1.0, 'price': 3},
    'CNY': {'go': 500, 'point_value': 1.0, 'price': 12},
}

# All strategies from signal pool
sp = json.loads((pathlib.Path('/root/prop-desk/strategy_combine/state/signal_pool.json')).read_text())
strategies = sp.get('strategies', {})

all_strats = []
for k, v in strategies.items():
    m = v.get('metrics', {})
    ticker = v.get('ticker')
    tf = m.get('timeframe', '?')
    strat = v.get('strategy')
    params = v.get('params')
    
    # Get real spec
    spec = SPECS.get(ticker, {'go': 5000, 'point_value': 1.0, 'price': 100})
    
    # Run backtest
    csv = DATA_ROOT / f"{ticker}_60d_{tf}_continuous.csv"
    if not csv.exists():
        continue
    
    try:
        row = {"ticker": ticker, "timeframe": tf, "strategy": strat, "params": params}
        result = backtest_curve(row, initial_cash=1_000_000)
        pnl = result.get('pnl', [])
        metrics = result.get('metrics', {})
        
        if not pnl:
            continue
        
        synthetic_pnl = pnl[-1]
        
        # Convert to real rubles: synthetic PnL * point_value
        # But we need to account for the fact that backtest uses point_value=1
        # Real PnL = synthetic_pnl * (real_point_value / 1.0)
        real_pnl_60d = synthetic_pnl * spec['point_value']
        
        # Subtract commission: 5₽ per side per trade
        trades = metrics.get('trades', 0)
        commission = trades * 2 * 5  # 2 sides * 5₽
        real_pnl_60d -= commission
        
        # Monthly return
        real_month = real_pnl_60d / 2
        real_month_pct = (real_month / 19800) * 100
        
        # Max drawdown in real rubles
        real_dd = metrics.get('max_drawdown', 0) * spec['point_value']
        
        all_strats.append({
            'ticker': ticker,
            'strategy': strat,
            'tf': tf,
            'params': params,
            'go': spec['go'],
            'point_value': spec['point_value'],
            'real_pnl_60d': real_pnl_60d,
            'real_month': real_month,
            'real_month_pct': real_month_pct,
            'real_dd': real_dd,
            'pf': metrics.get('profit_factor', 0),
            'wr': metrics.get('win_rate', 0),
            'trades': trades,
            'r2': metrics.get('r2', 0),
            'pnl_curve': pnl,
        })
    except Exception:
        continue

# Sort by real monthly return
all_strats.sort(key=lambda x: x['real_month'], reverse=True)

print("=== ALL STRATEGIES: REAL MONEY (19800₽ capital) ===")
print()
print(f"{'#':>3} {'Ticker':6s} {'TF':4s} {'Strategy':25s} {'GO':>6s} {'Real PnL/мес':>12s} {'%/мес':>7s} {'PF':>5s} {'WR%':>4s} {'DD':>8s} {'Trades':>6s}")
print("-" * 110)
for i, s in enumerate(all_strats):
    print(f"{i+1:3d} {s['ticker']:6s} {s['tf']:4s} {s['strategy']:25s} {s['go']:6.0f} {s['real_month']:10.0f}₽ {s['real_month_pct']:6.1f}% {s['pf']:5.2f} {s['wr']:4.0f} {s['real_dd']:8.0f} {s['trades']:6d}")

# Find best combination within 19800₽
print()
print("=== BEST COMBINATIONS (GO ≤ 19800₽) ===")
print()

best_combo = None
best_monthly = 0

# Try all combinations of 2-4 strategies
for size in [2, 3, 4]:
    for combo in itertools.combinations(all_strats[:8], size):  # top 8 by monthly
        total_go = sum(s['go'] for s in combo)
        if total_go > 19800:
            continue
        
        # Calculate portfolio monthly (average of individual monthly returns)
        avg_monthly = sum(s['real_month'] for s in combo) / len(combo)
        avg_monthly_pct = (avg_monthly / 19800) * 100
        
        # Calculate portfolio DD (max of individual DDs)
        max_dd = max(s['real_dd'] for s in combo)
        
        # Calculate portfolio PF (weighted average)
        avg_pf = sum(s['pf'] for s in combo) / len(combo)
        
        if avg_monthly > best_monthly:
            best_monthly = avg_monthly
            best_combo = {
                'strategies': combo,
                'total_go': total_go,
                'reserve': 19800 - total_go,
                'avg_monthly': avg_monthly,
                'avg_monthly_pct': avg_monthly_pct,
                'max_dd': max_dd,
                'avg_pf': avg_pf,
            }

if best_combo:
    print(f"Best combo: {len(best_combo['strategies'])} strategies")
    print(f"GO: {best_combo['total_go']:.0f}₽ (reserve: {best_combo['reserve']:.0f}₽)")
    print(f"Monthly: {best_combo['avg_monthly']:.0f}₽ ({best_combo['avg_monthly_pct']:.1f}%)")
    print(f"Avg PF: {best_combo['avg_pf']:.2f}")
    print(f"Max DD: {best_combo['max_dd']:.0f}₽")
    print()
    for s in best_combo['strategies']:
        print(f"  {s['ticker']} {s['tf']} {s['strategy']}: {s['real_month']:.0f}₽/мес ({s['real_month_pct']:.1f}%)")
