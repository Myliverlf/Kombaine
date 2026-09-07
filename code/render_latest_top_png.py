#!/usr/bin/env python3
from __future__ import annotations

import json
import pathlib
import sys

ROOT = pathlib.Path('/root/prop-desk/strategy_combine')
FUT = pathlib.Path('/root/prop-desk/futures_lab')
REPORT = ROOT / 'reports' / 'strategy_architect'
DATA = FUT / 'artifacts' / 'tinkoff_futures_data'
LATEST = REPORT / 'latest.md'

sys.path.insert(0, str(FUT))
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

from futures_lab import FuturesSpec, run_backtest  # type: ignore

# Use the canonical current live-ready / top candidates from latest.md by parsing the table.
text = LATEST.read_text(encoding='utf-8')
rows = []
collect = False
for line in text.splitlines():
    if line.startswith('| ticker | tf | strategy | bucket |'):
        collect = True
        continue
    if collect:
        if not line.startswith('|'):
            break
        if line.startswith('|---'):
            continue
        parts = [p.strip() for p in line.strip('|').split('|')]
        if len(parts) >= 16:
            rows.append({
                'ticker': parts[0], 'tf': parts[1], 'strategy': parts[2], 'pnl': parts[7], 'pf': parts[8], 'dd': parts[9], 'decision': parts[15],
            })

SPEC_DEFAULTS = {
    'LKOH': dict(point_value=1.0, active_margin=7412.68),
    'GAZP': dict(point_value=100.0, active_margin=1432.47),
    'SBER': dict(point_value=1.0, active_margin=1500.0),
    'Si': dict(point_value=1.0, active_margin=1500.0),
    'IMOEX': dict(point_value=1.0, active_margin=1500.0),
    'BR': dict(point_value=1.0, active_margin=1500.0),
    'USDRUB': dict(point_value=1.0, active_margin=1500.0),
    'EURRUB': dict(point_value=1.0, active_margin=1500.0),
    'CNY': dict(point_value=1.0, active_margin=1500.0),
    'NG': dict(point_value=1.0, active_margin=1500.0),
}

def make_spec(ticker: str) -> FuturesSpec:
    d = SPEC_DEFAULTS.get(ticker, dict(point_value=1.0, active_margin=1500.0))
    return FuturesSpec(
        ticker=ticker,
        uid='',
        name=ticker,
        class_code='SPBFUT',
        lot=1,
        min_price_increment=1.0,
        min_price_increment_amount=float(d['point_value']),
        initial_margin_on_buy=float(d['active_margin']),
        initial_margin_on_sell=float(d['active_margin']),
    )

# Reconstruct equity curves using 60d files only; long-history is already baked into latest top selection.
# The goal here is a readable visual of the current top candidates.
fig, ax = plt.subplots(figsize=(14, 8), dpi=170)
plt.style.use('dark_background')
colors = ['#00e5ff','#76ff03','#ffca28','#ff4081','#b388ff','#ff6e40']
initial_cash = 1_000_000.0
for i, r in enumerate(rows[:6]):
    ticker, tf, strategy = r['ticker'], r['tf'], r['strategy']
    path = DATA / f'{ticker}_60d_{tf}_continuous.csv'
    if not path.exists():
        continue
    df = pd.read_csv(path)
    eq = None
    try:
        _, _, eq = run_backtest(df, make_spec(ticker), strategy, {}, initial_cash=initial_cash, contracts=1, max_contracts=1)
    except Exception:
        continue
    y = (eq - initial_cash).reset_index(drop=True)
    step = max(1, len(y)//500)
    ax.plot(range(0, len(y), step), y.iloc[::step], linewidth=2.2, color=colors[i%len(colors)], label=f'{ticker} {strategy} {tf}')

ax.axhline(0, color='#888', linewidth=.9)
ax.set_title('Strategy Architect — latest top candidates', fontsize=16, weight='bold')
ax.set_xlabel('bars')
ax.set_ylabel('PnL to deposit, ₽')
ax.grid(alpha=.25)
ax.legend(loc='best', fontsize=8)
REPORT.mkdir(parents=True, exist_ok=True)
out = REPORT / 'latest_top_candidates.png'
fig.savefig(out, bbox_inches='tight', facecolor=fig.get_facecolor())
print(out)
