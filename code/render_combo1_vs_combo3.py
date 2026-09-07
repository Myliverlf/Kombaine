#!/usr/bin/env python3
import sys, json, pathlib
sys.path.insert(0, '/root/prop-desk/strategy_combine/code')
sys.path.insert(0, '/root/prop-desk/futures_lab')
from portfolio_equity_analyzer import align_curves, sum_curves, equity_profile
from futures_lab import run_backtest, _synthetic_spec_for_file
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import pandas as pd

DATA = pathlib.Path('/root/prop-desk/futures_lab/artifacts/tinkoff_futures_data')
REPORT = pathlib.Path('/root/prop-desk/strategy_combine/reports/strategy_architect')
CAPITAL = 19800

combos = {
    'Combo #1: LKOH vwap + SBER atr + GAZP sma (40.8%/мес)': [
        {"ticker": "LKOH", "timeframe": "1h", "strategy": "vwap_bands", "params": {"lookback": 20, "band_z": 1.5}, "color": "#2196F3"},
        {"ticker": "SBER", "timeframe": "1h", "strategy": "atr_breakout", "params": {"lookback": 10, "atr_mult": 1.0}, "color": "#9C27B0"},
        {"ticker": "GAZP", "timeframe": "1h", "strategy": "sma_cross", "params": {"fast": 5, "slow": 20}, "color": "#FF9800"},
    ],
    'Combo #3: LKOH vol_squeeze + SBER atr + GAZP sma (33.7%/мес)': [
        {"ticker": "LKOH", "timeframe": "15m", "strategy": "volatility_squeeze", "params": {"bb_period": 14, "bb_stds": 1.5, "kc_period": 20, "kc_mult": 1.0}, "color": "#4CAF50"},
        {"ticker": "SBER", "timeframe": "1h", "strategy": "atr_breakout", "params": {"lookback": 10, "atr_mult": 1.0}, "color": "#9C27B0"},
        {"ticker": "GAZP", "timeframe": "1h", "strategy": "sma_cross", "params": {"fast": 5, "slow": 20}, "color": "#FF9800"},
    ],
}

fig, axes = plt.subplots(2, 1, figsize=(16, 12))
fig.suptitle('Real Money Portfolios: Combo #1 vs Combo #3 (19800₽, commission=5₽)', fontsize=14, fontweight='bold')

for idx, (title, strats) in enumerate(combos.items()):
    ax = axes[idx]
    
    curves = []
    for s in strats:
        csv = DATA / f"{s['ticker']}_60d_{s['timeframe']}_continuous.csv"
        df = pd.read_csv(csv)
        spec = _synthetic_spec_for_file(s['ticker'])
        mm, tr, eq = run_backtest(df, spec, s['strategy'], s['params'], initial_cash=CAPITAL, contracts=1, max_contracts=1, commission_per_contract=5.0, slippage_bps=1.0)
        pnl = [float(x) - CAPITAL for x in eq.tolist()]
        curves.append(pnl)
        
        dates = pd.to_datetime(df['time'])
        n = min(len(pnl), len(dates))
        equity = [0] + pnl[:n]
        equity = equity[:len(dates[:len(equity)])]
        ax.plot(dates[:len(equity)], equity, color=s['color'], linewidth=1.5, alpha=0.6, label=f"{s['ticker']} {s['timeframe']} {s['strategy']}")
    
    # Portfolio curve
    aligned = align_curves(curves, n=500)
    port = sum_curves(aligned)
    prof = equity_profile(port)
    
    # Use first strategy's dates for portfolio x-axis
    df0 = pd.read_csv(DATA / f"{strats[0]['ticker']}_60d_{strats[0]['timeframe']}_continuous.csv")
    dates0 = pd.to_datetime(df0['time'])
    n = min(len(port), len(dates0))
    
    ax.plot(dates0[:n], port[:n], color='#E91E63', linewidth=3, label='PORTFOLIO')
    ax.fill_between(dates0[:n], 0, port[:n], alpha=0.15, color='#E91E63')
    ax.axhline(y=0, color='gray', linestyle='--', alpha=0.5)
    
    total = port[-1]
    ax.set_title(f"{title}\nPnL={total:+.0f}₽/мес ({total/CAPITAL*100/2:.1f}%) | R²={prof.get('r2',0):.3f} | DD={prof.get('max_drawdown',0):.0f}", fontsize=11)
    ax.set_ylabel('PnL (₽)')
    ax.legend(loc='upper left', fontsize=9)
    ax.grid(True, alpha=0.3)
    ax.xaxis.set_major_formatter(mdates.DateFormatter('%d %b'))
    plt.setp(ax.xaxis.get_majorticklabels(), rotation=30)

plt.tight_layout()
out = str(REPORT / 'combo1_vs_combo3_real.png')
plt.savefig(out, dpi=150, bbox_inches='tight')
print(json.dumps({"png": out}))
