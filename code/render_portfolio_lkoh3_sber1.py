#!/usr/bin/env python3
import sys, json, pathlib, math
sys.path.insert(0, '/root/prop-desk/strategy_combine/code')
sys.path.insert(0, '/root/prop-desk/futures_lab')
from portfolio_equity_analyzer import backtest_curve, align_curves, sum_curves
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import pandas as pd
import numpy as np

DATA_ROOT = pathlib.Path('/root/prop-desk/futures_lab/artifacts/tinkoff_futures_data')
REPORT_DIR = pathlib.Path('/root/prop-desk/strategy_combine/reports/strategy_architect')

strategies = [
    {"ticker": "LKOH", "timeframe": "1h", "strategy": "vwap_bands", "params": {"lookback": 20, "band_z": 1.5}, "color": "#2196F3", "label": "LKOH 1h vwap_bands"},
    {"ticker": "LKOH", "timeframe": "15m", "strategy": "vwap_reversion", "params": {"lookback": 20, "entry_z": 0.8, "exit_z": 0.2}, "color": "#FF9800", "label": "LKOH 15m vwap_reversion"},
    {"ticker": "LKOH", "timeframe": "15m", "strategy": "volatility_squeeze", "params": {"bb_period": 14, "bb_stds": 1.5, "kc_period": 20, "kc_mult": 1.0}, "color": "#4CAF50", "label": "LKOH 15m vol_squeeze"},
    {"ticker": "SBER", "timeframe": "1h", "strategy": "atr_breakout", "params": {"lookback": 10, "atr_mult": 1.0}, "color": "#9C27B0", "label": "SBER 1h atr_breakout"},
]

curves = []
metrics_list = []
for s in strategies:
    result = backtest_curve(s, initial_cash=1_000_000)
    pnl = result.get('pnl', [])
    m = result.get('metrics', {})
    curves.append(pnl)
    metrics_list.append(m)
    print(f"{s['label']}: PnL={pnl[-1]:+.0f}, PF={m.get('profit_factor',0):.2f}, WR={m.get('win_rate',0):.0f}%, DD={m.get('max_drawdown',0):.0f}")

aligned = align_curves(curves, n=500)
portfolio_curve = sum_curves(aligned)

total_pnl = portfolio_curve[-1]
peak = portfolio_curve[0]
max_dd = 0
for v in portfolio_curve:
    peak = max(peak, v)
    dd = peak - v
    max_dd = max(max_dd, dd)

x = np.arange(len(portfolio_curve))
slope, intercept = np.polyfit(x, portfolio_curve, 1)
y_pred = slope * x + intercept
ss_res = np.sum((np.array(portfolio_curve) - y_pred) ** 2)
ss_tot = np.sum((np.array(portfolio_curve) - np.mean(portfolio_curve)) ** 2)
r2 = 1 - (ss_res / ss_tot) if ss_tot > 0 else 0

print(f"\n=== PORTFOLIO (4 strategies, equal weight) ===")
print(f"Total PnL: {total_pnl:+.0f}")
print(f"Max DD: {max_dd:.0f}")
print(f"R²: {r2:.3f}")

fig, axes = plt.subplots(2, 1, figsize=(14, 10), gridspec_kw={'height_ratios': [3, 1]})
fig.suptitle('Portfolio: LKOH×3 + SBER×1 (equal weight, 60d backtest)', fontsize=14, fontweight='bold')

ax1 = axes[0]
for i, s in enumerate(strategies):
    pnl = curves[i]
    df = pd.read_csv(DATA_ROOT / f"{s['ticker']}_60d_{s['timeframe']}_continuous.csv")
    dates = pd.to_datetime(df['time'])
    n = min(len(pnl), len(dates))
    equity = [0] + pnl[:n]
    equity = equity[:len(dates[:len(equity)])]
    ax1.plot(dates[:len(equity)], equity, color=s['color'], linewidth=1.5, alpha=0.6, label=s['label'])

ax1.set_ylabel('PnL (synthetic)')
ax1.legend(loc='upper left', fontsize=9)
ax1.grid(True, alpha=0.3)
ax1.set_title('Individual Strategies')

ax2 = axes[1]
df0 = pd.read_csv(DATA_ROOT / f"{strategies[0]['ticker']}_60d_{strategies[0]['timeframe']}_continuous.csv")
dates0 = pd.to_datetime(df0['time'])
n = min(len(portfolio_curve), len(dates0))
portfolio_dates = dates0[:n]
portfolio_vals = portfolio_curve[:n]

ax2.plot(portfolio_dates, portfolio_vals, color='#E91E63', linewidth=2.5)
ax2.fill_between(portfolio_dates, 0, portfolio_vals, alpha=0.2, color='#E91E63')
ax2.axhline(y=0, color='gray', linestyle='--', alpha=0.5)
ax2.set_ylabel('Portfolio PnL')
ax2.set_title(f'Combined Portfolio | PnL={total_pnl:+.0f} | DD={max_dd:.0f} | R²={r2:.3f}')
ax2.grid(True, alpha=0.3)
ax2.xaxis.set_major_formatter(mdates.DateFormatter('%d %b'))
plt.setp(ax2.xaxis.get_majorticklabels(), rotation=30)

plt.tight_layout()
out = str(REPORT_DIR / 'portfolio_lkoh3_sber1.png')
plt.savefig(out, dpi=150, bbox_inches='tight')
print(f"PNG: {out}")
