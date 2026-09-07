#!/usr/bin/env python3
from __future__ import annotations
import json, sys
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import pandas as pd
sys.path.insert(0,'/root/prop-desk/futures_lab')
from futures_lab import run_backtest, _synthetic_spec_for_file  # type: ignore
ROOT=Path('/root/prop-desk/strategy_combine')
DATA=Path('/root/prop-desk/futures_lab/artifacts/tinkoff_futures_data/USDRUB_60d_1h_continuous.csv')
OUT=ROOT/'reports/strategy_architect/usdrub_atr_breakout_full_60d.png'
df=pd.read_csv(DATA)
metrics,trades,eq=run_backtest(df,_synthetic_spec_for_file('USDRUB'),'atr_breakout',{'lookback':10,'atr_mult':1.0},initial_cash=1_000_000.0,contracts=1,max_contracts=1)
vals=[float(x)-1_000_000 for x in eq.tolist()]
plt.figure(figsize=(12,6))
plt.plot(vals,label='USDRUB 1h atr_breakout PnL equity')
plt.axhline(0,color='black',linewidth=0.8,alpha=0.4)
plt.title('USDRUB 1h atr_breakout lookback=10 atr_mult=1.0 — full local 60d')
plt.xlabel('bar')
plt.ylabel('PnL synthetic units')
plt.grid(True,alpha=0.25)
plt.legend()
plt.tight_layout()
OUT.parent.mkdir(parents=True, exist_ok=True)
plt.savefig(OUT,dpi=150)
print(json.dumps({'png':str(OUT),'trades':metrics.get('trade_count'),'pnl':metrics.get('total_pnl'),'pf':metrics.get('profit_factor'),'wr':metrics.get('win_rate_pct'),'dd':metrics.get('max_drawdown')},ensure_ascii=False))
