#!/usr/bin/env python3
import sys, json, pathlib
sys.path.insert(0, '/root/prop-desk/strategy_combine/code')
sys.path.insert(0, '/root/prop-desk/futures_lab')
from portfolio_equity_analyzer import backtest_curve
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import pandas as pd

DATA_ROOT = pathlib.Path('/root/prop-desk/futures_lab/artifacts/tinkoff_futures_data')

strategies = [
    {"ticker": "SBER", "timeframe": "1h", "strategy": "atr_breakout", "params": {"lookback": 10, "atr_mult": 1.0}},
    {"ticker": "GAZP", "timeframe": "1h", "strategy": "sma_cross", "params": {"fast": 5, "slow": 20}},
    {"ticker": "LKOH", "timeframe": "1h", "strategy": "vwap_bands", "params": {"lookback": 20, "band_z": 1.5}},
]

fig, axes = plt.subplots(3, 1, figsize=(14, 12), sharex=False)
fig.suptitle('Live Portfolio — Individual Strategy Equity (60d backtest)', fontsize=14, fontweight='bold')

colors = ['#2196F3', '#FF9800', '#4CAF50']
total_pnl = 0

for i, s in enumerate(strategies):
    ax = axes[i]
    csv = DATA_ROOT / f"{s['ticker']}_60d_{s['timeframe']}_continuous.csv"
    if not csv.exists():
        ax.text(0.5, 0.5, f'NO DATA: {csv.name}', ha='center', va='center', transform=ax.transAxes)
        continue
    
    result = backtest_curve(s, initial_cash=1_000_000)
    pnl_curve = result.get('pnl', [])
    metrics = result.get('metrics', {})
    
    df = pd.read_csv(csv)
    dates = pd.to_datetime(df['time'])
    
    n = min(len(pnl_curve), len(dates))
    pnl_curve = pnl_curve[:n]
    dates = dates[:n]
    
    # Convert PnL to equity (starting from 0)
    equity = [0] + pnl_curve
    equity = equity[:len(dates)]  # trim to match dates length
    equity_dates = dates[:len(equity)]
    
    final_pnl = pnl_curve[-1] if pnl_curve else 0
    total_pnl += final_pnl
    
    ax.plot(equity_dates, equity, color=colors[i], linewidth=2)
    ax.fill_between(equity_dates, 0, equity, alpha=0.1, color=colors[i])
    ax.axhline(y=0, color='gray', linestyle='--', alpha=0.5)
    ax.set_ylabel('PnL (synthetic)')
    ax.set_title(f"{s['ticker']} {s['timeframe']} {s['strategy']}  |  PnL={final_pnl:+.0f}  PF={metrics.get('profit_factor',0):.2f}  WR={metrics.get('win_rate',0):.0f}%  DD={metrics.get('max_drawdown',0):.0f}  trades={metrics.get('trades',0)}", fontsize=11)
    ax.grid(True, alpha=0.3)
    ax.xaxis.set_major_formatter(mdates.DateFormatter('%d %b'))
    plt.setp(ax.xaxis.get_majorticklabels(), rotation=30)

plt.tight_layout()
out = '/root/prop-desk/strategy_combine/reports/strategy_architect/live_portfolio_equity.png'
plt.savefig(out, dpi=150, bbox_inches='tight')
print(json.dumps({"png": out, "total_pnl": total_pnl}))
