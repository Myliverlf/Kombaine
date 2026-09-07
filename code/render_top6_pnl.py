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
    {"ticker": "LKOH", "timeframe": "1h", "strategy": "vwap_bands", "params": {"lookback": 20, "band_z": 1.5}, "color": "#2196F3"},
    {"ticker": "LKOH", "timeframe": "15m", "strategy": "vwap_reversion", "params": {"lookback": 20, "entry_z": 0.8, "exit_z": 0.2}, "color": "#FF9800"},
    {"ticker": "LKOH", "timeframe": "15m", "strategy": "volatility_squeeze", "params": {"bb_period": 14, "bb_stds": 1.5, "kc_period": 20, "kc_mult": 1.0}, "color": "#4CAF50"},
    {"ticker": "SBER", "timeframe": "1h", "strategy": "atr_breakout", "params": {"lookback": 10, "atr_mult": 1.0}, "color": "#9C27B0"},
    {"ticker": "GAZP", "timeframe": "1h", "strategy": "sma_cross", "params": {"fast": 5, "slow": 20}, "color": "#F44336"},
    {"ticker": "Si", "timeframe": "1h", "strategy": "vwap_bands", "params": {"lookback": 30, "band_z": 1.0}, "color": "#00BCD4"},
]

fig, axes = plt.subplots(3, 2, figsize=(16, 14), sharex=False)
fig.suptitle('TOP-6 by PnL — Equity Curves (60d backtest)', fontsize=14, fontweight='bold')

for i, s in enumerate(strategies):
    ax = axes[i // 2][i % 2]
    csv = DATA_ROOT / f"{s['ticker']}_60d_{s['timeframe']}_continuous.csv"
    if not csv.exists():
        ax.text(0.5, 0.5, f'NO DATA', ha='center', va='center', transform=ax.transAxes)
        continue
    
    try:
        result = backtest_curve(s, initial_cash=1_000_000)
        pnl_curve = result.get('pnl', [])
        metrics = result.get('metrics', {})
        
        df = pd.read_csv(csv)
        dates = pd.to_datetime(df['time'])
        
        n = min(len(pnl_curve), len(dates))
        pnl_curve = pnl_curve[:n]
        dates = dates[:n]
        
        equity = [0] + pnl_curve
        equity = equity[:len(dates)]
        
        final_pnl = pnl_curve[-1] if pnl_curve else 0
        
        ax.plot(dates, equity, color=s['color'], linewidth=2)
        ax.fill_between(dates, 0, equity, alpha=0.15, color=s['color'])
        ax.axhline(y=0, color='gray', linestyle='--', alpha=0.5)
        
        text = f"PnL={final_pnl:+.0f}\nPF={metrics.get('profit_factor',0):.2f}\nWR={metrics.get('win_rate',0):.0f}%\nDD={metrics.get('max_drawdown',0):.0f}"
        ax.text(0.02, 0.98, text, transform=ax.transAxes, fontsize=9, verticalalignment='top',
                bbox=dict(boxstyle='round', facecolor='white', alpha=0.8))
        
        ax.set_title(f"#{i+1} {s['ticker']} {s['timeframe']} {s['strategy']}", fontsize=11, fontweight='bold')
        ax.set_ylabel('PnL (synthetic)')
        ax.grid(True, alpha=0.3)
        ax.xaxis.set_major_formatter(mdates.DateFormatter('%d %b'))
        plt.setp(ax.xaxis.get_majorticklabels(), rotation=30)
    except Exception as e:
        ax.text(0.5, 0.5, f'ERROR: {e}', ha='center', va='center', transform=ax.transAxes)

plt.tight_layout()
out = '/root/prop-desk/strategy_combine/reports/strategy_architect/top6_pnl_equity.png'
plt.savefig(out, dpi=150, bbox_inches='tight')
print(json.dumps({"png": out}))
