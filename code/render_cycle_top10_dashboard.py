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
CYCLE = REPORT / 'cycle_20260829_011434.json'

sys.path.insert(0, str(FUT))
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from futures_lab import _synthetic_spec_for_file, run_backtest  # type: ignore

payload = json.loads(CYCLE.read_text(encoding='utf-8'))
rows = []
seen = set()

# Prefer top rows, then supplement from indicator leaderboard and policy rows.
def add_row(r: dict):
    ticker = r.get('ticker')
    tf = r.get('timeframe') or r.get('tf') or '15m'
    strategy = r.get('strategy')
    if not ticker or not strategy:
        return
    key = (ticker, tf, strategy)
    if key in seen:
        return
    seen.add(key)
    rows.append({'ticker': ticker, 'tf': tf, 'strategy': strategy, 'params': r.get('params') or {}, 'score': float(r.get('rank_score') or r.get('indicator_score') or r.get('policy_bias') or 0.0)})

for src in (payload.get('top') or [], payload.get('policy_family_rows') or [], payload.get('indicator_leaderboard') or []):
    for r in src:
        add_row(r)
        if len(rows) >= 10:
            break
    if len(rows) >= 10:
        break

# If still short, just keep what we have — better than inventing.
rows = rows[:10]

fig, axes = plt.subplots(5, 2, figsize=(18, 22), dpi=180)
axes = axes.flatten()
plt.style.use('dark_background')
colors = ['#00e5ff', '#76ff03', '#ffca28', '#ff4081', '#b388ff', '#ff6e40', '#26c6da', '#9ccc65', '#f06292', '#ffd54f']
summary = []

for idx, (ax, r) in enumerate(zip(axes, rows)):
    ticker = r['ticker']
    tf = r['tf']
    strategy = r['strategy']
    params = r['params']
    path = DATA / f'{ticker}_1095d_{tf}_continuous.csv'
    if not path.exists():
        path = DATA / f'{ticker}_365d_{tf}_continuous.csv'
    if not path.exists():
        path = DATA / f'{ticker}_60d_{tf}_continuous.csv'
    if not path.exists():
        ax.text(0.5, 0.5, f'missing data\n{ticker} {tf}', ha='center', va='center')
        ax.set_axis_off()
        continue

    df = pd.read_csv(path)
    metrics, trades, _ = run_backtest(
        df,
        _synthetic_spec_for_file(ticker),
        strategy,
        params,
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
        ax.text(0.5, 0.5, f'no trades\n{ticker} {strategy}', ha='center', va='center')
        ax.set_axis_off()
        continue

    cum = []
    x = []
    total = 0.0
    for n, tr in enumerate(trades, 1):
        total += float(tr.pnl)
        x.append(n)
        cum.append(total)

    color = colors[idx % len(colors)]
    ax.plot(x, cum, linewidth=2.2, color=color)
    ax.fill_between(x, 0, cum, color=color, alpha=0.08)
    ax.axhline(0, color='#666', linewidth=0.8)
    ax.set_title(f"{ticker} {strategy} {tf}\nPnL={float(metrics.get('total_pnl') or 0):+.0f}₽ | PF={float(metrics.get('profit_factor') or 0):.2f} | WR={float(metrics.get('win_rate_pct') or 0):.0f}% | DD={float(metrics.get('max_drawdown') or 0):.0f}", fontsize=10)
    ax.grid(alpha=0.2)
    summary.append({'ticker': ticker, 'tf': tf, 'strategy': strategy, 'trades': len(trades), 'pnl': float(metrics.get('total_pnl') or 0), 'pf': float(metrics.get('profit_factor') or 0), 'wr': float(metrics.get('win_rate_pct') or 0), 'dd': float(metrics.get('max_drawdown') or 0)})

for ax in axes[len(rows):]:
    ax.set_axis_off()

fig.suptitle('Cycle 20260829_011434 — Top strategy trade curves (10 panels)', fontsize=18, fontweight='bold')
fig.tight_layout(rect=[0, 0.02, 1, 0.97])
out = REPORT / 'cycle_20260829_011434_top10_dashboard.png'
fig.savefig(out, bbox_inches='tight', facecolor=fig.get_facecolor())
(REPORT / 'cycle_20260829_011434_top10_dashboard.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
print(out)
