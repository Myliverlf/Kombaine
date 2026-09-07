#!/usr/bin/env python3
"""Render equity curves for SBER strategies."""
import sys, json, pathlib
sys.path.insert(0, '/root/prop-desk/strategy_combine/code')
sys.path.insert(0, '/root/prop-desk/futures_lab')
from portfolio_equity_analyzer import backtest_curve
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

DATA_ROOT = pathlib.Path('/root/prop-desk/futures_lab/artifacts/tinkoff_futures_data')

# SBER strategies from signal pool
strategies = [
    {"ticker": "SBER", "timeframe": "1h", "strategy": "atr_breakout", 
     "params": {"lookback": 10, "atr_mult": 1.0}, "color": "#9C27B0"},
    {"ticker": "SBER", "timeframe": "15m", "strategy": "vwap_reversion", 
     "params": {"lookback": 20, "entry_z": 0.8, "exit_z": 0.2}, "color": "#FF5722"},
]

print(f"SBER strategies: {len(strategies)}")

fig, axes = plt.subplots(len(strategies), 1, figsize=(14, 6*len(strategies)))
if len(strategies) == 1:
    axes = [axes]

results = []
for i, s in enumerate(strategies):
    ticker = s['ticker']
    tf = s['timeframe']
    strategy = s['strategy']
    params = s['params']
    color = s['color']
    
    print(f"\n--- {ticker} {tf} {strategy} ---")
    
    # Find data file
    data_file = DATA_ROOT / f"{ticker}_{tf}.parquet"
    if not data_file.exists():
        print(f"  Data file not found: {data_file}")
        continue
    
    # Run backtest
    try:
        curve = backtest_curve(ticker, tf, strategy, params, data_file)
        if curve is None or len(curve) == 0:
            print(f"  No curve returned")
            continue
        
        equity = curve.get('equity', [])
        trades = curve.get('trades', [])
        pnl = curve.get('total_pnl', 0)
        pf = curve.get('profit_factor', 0)
        wr = curve.get('win_rate', 0)
        dd = curve.get('max_drawdown', 0)
        
        print(f"  Trades: {len(trades)}, PnL: {pnl:.0f}₽, PF: {pf:.2f}, WR: {wr*100:.0f}%, DD: {dd:.0f}₽")
        
        results.append({
            'strategy': f"{ticker} {tf} {strategy}",
            'pnl': pnl,
            'pf': pf,
            'wr': wr,
            'dd': dd,
            'trades': len(trades),
        })
        
        # Plot
        ax = axes[i]
        ax.plot(equity, linewidth=2, color=color)
        ax.set_title(f"{ticker} {tf} {strategy} — PnL={pnl:.0f}₽, PF={pf:.2f}, WR={wr*100:.0f}%, DD={dd:.0f}₽")
        ax.set_xlabel("Trades")
        ax.set_ylabel("Equity (₽)")
        ax.grid(True, alpha=0.3)
        ax.axhline(y=equity[0], color='gray', linestyle='--', alpha=0.5)
        
    except Exception as e:
        print(f"  Error: {e}")

plt.tight_layout()
output_path = '/root/prop-desk/strategy_combine/reports/sber_equity_curves.png'
pathlib.Path(output_path).parent.mkdir(parents=True, exist_ok=True)
plt.savefig(output_path, dpi=150, bbox_inches='tight')
print(f"\nSaved: {output_path}")

# Summary
print("\n=== SUMMARY ===")
for r in results:
    print(f"{r['strategy']}: PnL={r['pnl']:.0f}₽, PF={r['pf']:.2f}, WR={r['wr']*100:.0f}%, DD={r['dd']:.0f}₽, trades={r['trades']}")
