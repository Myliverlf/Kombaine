#!/usr/bin/env python3
from __future__ import annotations

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
from futures_lab import _synthetic_spec_for_file, run_backtest  # type: ignore

CORE = {'BR', 'GAZP', 'LKOH', 'SBER'}
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
        if len(parts) >= 16 and parts[0] in CORE:
            rows.append({'ticker': parts[0], 'tf': parts[1], 'strategy': parts[2], 'decision': parts[15]})

# keep top 6 core strategies only
rows = rows[:6]
colors = ['#00e5ff', '#76ff03', '#ffca28', '#ff4081', '#b388ff', '#ff6e40']
fig, ax = plt.subplots(figsize=(16, 9), dpi=180)
plt.style.use('dark_background')
initial_cash = 1_000_000.0
for i, r in enumerate(rows):
    ticker, tf, strategy = r['ticker'], r['tf'], r['strategy']
    for days in (1095, 365, 60):
        path = DATA / f'{ticker}_{days}d_{tf}_continuous.csv'
        if path.exists():
            break
    else:
        continue
    df = pd.read_csv(path)
    metrics, trades, _ = run_backtest(
        df,
        _synthetic_spec_for_file(ticker),
        strategy,
        {},
        initial_cash=initial_cash,
        contracts=1,
        commission_per_contract=0.0,
        slippage_bps=0.0,
        stop_atr=2.0,
        take_atr=3.0,
        max_hold_bars=192 if tf == '15m' else 48,
        risk_rub=0.0,
        max_contracts=1,
    )
    if not trades:
        continue
    total = 0.0
    x=[]; cum=[]
    for n, tr in enumerate(trades, 1):
        total += float(tr.pnl)
        x.append(n); cum.append(total)
    color = colors[i % len(colors)]
    ax.plot(x, cum, linewidth=2.7, color=color,
            label=f"{ticker} {strategy} {tf} | trades={len(trades)} | PnL={float(metrics.get('total_pnl') or 0):+.0f}₽ | PF={float(metrics.get('profit_factor') or 0):.2f} | WR={float(metrics.get('win_rate_pct') or 0):.0f}% | DD={float(metrics.get('max_drawdown') or 0):.0f}")
    ax.fill_between(x, 0, cum, color=color, alpha=0.08)

ax.axhline(0, color='#888', linewidth=.9)
ax.set_title('CORE long overlay — BR / GAZP / LKOH / SBER', fontsize=16, fontweight='bold')
ax.set_xlabel('closed trade #')
ax.set_ylabel('cumulative PnL, ₽')
ax.grid(alpha=.25)
ax.legend(loc='best', fontsize=7)
REPORT.mkdir(parents=True, exist_ok=True)
out = REPORT / 'core_long_overlay.png'
fig.savefig(out, bbox_inches='tight', facecolor=fig.get_facecolor())
print(out)
