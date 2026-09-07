#!/usr/bin/env python3
from __future__ import annotations

import json
from code.capital_context import report_capital
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
            rows.append({'ticker': parts[0], 'tf': parts[1], 'strategy': parts[2], 'decision': parts[15]})

rows = rows[:3]
colors = ['#00e5ff', '#76ff03', '#ffca28']
fig, axes = plt.subplots(len(rows), 1, figsize=(14, 4.3 * len(rows)), sharex=False, dpi=170)
if len(rows) == 1:
    axes = [axes]

summary = []
for ax, r, color in zip(axes, rows, colors):
    ticker, tf, strategy = r['ticker'], r['tf'], r['strategy']
    path = DATA / f'{ticker}_60d_{tf}_continuous.csv'
    if not path.exists():
        ax.text(0.5, 0.5, f'missing {path.name}', ha='center', va='center')
        continue
    df = pd.read_csv(path)
    metrics, trades, eq = run_backtest(
        df,
        _synthetic_spec_for_file(ticker),
        strategy,
        {},
        initial_cash=report_capital(),
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
        ax.text(0.5, 0.5, f'no closed trades for {ticker} {strategy}', ha='center', va='center')
        continue
    pnl = []
    cum = 0.0
    x = []
    for i, tr in enumerate(trades, 1):
        cum += float(tr.pnl)
        pnl.append(cum)
        x.append(i)
    ax.plot(x, pnl, color=color, linewidth=2.6)
    ax.fill_between(x, 0, pnl, color=color, alpha=0.12)
    ax.axhline(0, color='#888', linewidth=.9)
    ax.set_title(f"{ticker} {strategy} {tf} — closed trades={len(trades)}, PnL={float(metrics.get('total_pnl') or 0):+.2f}, PF={float(metrics.get('profit_factor') or 0):.2f}, WR={float(metrics.get('win_rate_pct') or 0):.1f}%, DD={float(metrics.get('max_drawdown') or 0):.2f}")
    ax.set_xlabel('closed trade #')
    ax.set_ylabel('cumulative PnL, ₽')
    ax.grid(alpha=.25)
    summary.append({'ticker': ticker, 'strategy': strategy, 'tf': tf, 'trades': len(trades), 'pnl': float(metrics.get('total_pnl') or 0), 'pf': float(metrics.get('profit_factor') or 0), 'wr': float(metrics.get('win_rate_pct') or 0), 'dd': float(metrics.get('max_drawdown') or 0)})

fig.suptitle('Top strategies — closed-trade equity curves', fontsize=16, fontweight='bold')
fig.tight_layout(rect=[0, 0.03, 1, 0.98])
out = REPORT / 'latest_top_trade_by_trade.png'
fig.savefig(out, bbox_inches='tight', facecolor=fig.get_facecolor())
(REPORT / 'latest_top_trade_by_trade.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
print(out)
