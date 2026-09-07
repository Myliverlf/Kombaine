#!/usr/bin/env python3
from __future__ import annotations

import json
from code.capital_context import report_capital
import sys
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

ROOT = Path('/root/prop-desk/strategy_combine')
sys.path.insert(0, str(ROOT / 'code'))

from portfolio_equity_analyzer import load_cycle, candidate_rows, backtest_curve, align_curves, sum_curves  # type: ignore


def main() -> int:
    cycle = load_cycle()
    rows = candidate_rows(cycle, limit=None)
    curves = [backtest_curve(r, initial_cash=report_capital())['pnl'] for r in rows]
    portfolio = sum_curves(align_curves(curves, n=500))
    stamp = cycle.get('ts') or Path(cycle['_cycle_path']).stem.replace('cycle_', '')
    out = ROOT / 'reports' / 'strategy_architect' / f'portfolio_equity_clean_{stamp}.png'
    out.parent.mkdir(parents=True, exist_ok=True)

    plt.figure(figsize=(14, 7), facecolor='white')
    plt.plot(portfolio, color='#111111', linewidth=2.8)
    plt.axhline(0, color='#888888', linewidth=0.8, alpha=0.35)
    plt.grid(True, alpha=0.18)
    plt.title('Portfolio equity — clean equal-weight top', fontsize=15)
    plt.xlabel('normalized time')
    plt.ylabel('PnL, synthetic units')
    plt.tight_layout()
    plt.savefig(out, dpi=180)
    print(json.dumps({'png': str(out), 'cycle': cycle['_cycle_path'], 'top_count': len(rows), 'final_pnl': round(portfolio[-1] - portfolio[0], 4)}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
