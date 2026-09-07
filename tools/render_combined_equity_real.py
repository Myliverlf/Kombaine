#!/usr/bin/env python3
"""LEGACY — голый matplotlib. НОВЫЕ ГРАФИКИ НЕ РИСОВАТЬ ЗДЕСЬ.
Единственный правильный путь: tools/lieflat_render.py::render_equity_report
(скилл lieflat-charts, Mono). См. START.md раздел ГРАФИКИ.
Файл оставлен только ради старых отчётов по cycle JSON; не расширять.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

# Local import path
sys.path.insert(0, '/root/prop-desk/futures_lab')
import futures_lab as fl

ROOT = Path('/root/prop-desk/strategy_combine')
DATA_DIR = Path('/root/prop-desk/futures_lab/artifacts/tinkoff_futures_data')


def pick_rows(cycle: dict, top_n: int = 6):
    rows = [r for r in cycle.get('top', []) if r.get('params')]
    # de-duplicate exact strategy clones so the chart shows distinct equity paths
    uniq = {}
    for r in rows:
        key = (
            r.get('ticker'),
            r.get('strategy'),
            r.get('timeframe'),
            json.dumps(r.get('params') or {}, sort_keys=True, ensure_ascii=False),
        )
        if key not in uniq:
            uniq[key] = r
    rows = list(uniq.values())
    # prefer frequent/tradable rows, then rank_score order already present
    rows = sorted(rows, key=lambda r: (-int(r.get('trades', 0) or 0), -float(r.get('rank_score', 0) or 0)))
    return rows[:top_n]


def load_real_series(row: dict, capital: float):
    ticker = row['ticker']
    tf = row['timeframe']
    interval = '15m' if tf == '15m' else '1h'
    csv_path = DATA_DIR / f'{ticker}_60d_{tf}_continuous.csv'
    if not csv_path.exists():
        raise FileNotFoundError(f'missing data file: {csv_path}')

    args = SimpleNamespace(
        ticker=ticker,
        timeframe=tf,
        file=str(csv_path),
        continuous=True,
        sandbox=True,
        days=60,
        start=None,
        end=None,
        interval=interval,
        roll_days=5,
        initial_cash=capital,
        contracts=1,
        commission_per_contract=1.5,
        slippage_bps=2.0,
        stop_atr=2.0,
        take_atr=3.0,
        max_hold_bars=48,
        risk_rub=0.0,
        max_contracts=1,
    )
    spec, df = fl.load_data(args)
    metrics, trades, equity = fl.run_backtest(
        df,
        spec,
        row['strategy'],
        row['params'],
        initial_cash=capital,
        contracts=1,
        commission_per_contract=1.5,
        slippage_bps=2.0,
        stop_atr=2.0,
        take_atr=3.0,
        max_hold_bars=48,
        risk_rub=0.0,
        max_contracts=1,
        debug_only=True,
    )
    return equity, metrics, trades, csv_path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--cycle', default=str(ROOT / 'reports/strategy_architect/cycle_20260902_033822.json'))
    ap.add_argument('--output', default=str(ROOT / 'reports/strategy_architect/combined_top6_equity_real.png'))
    ap.add_argument('--top-n', type=int, default=6)
    ap.add_argument('--cash', type=float, default=20000.0, help='Starting capital in RUB for the equity chart')
    ap.add_argument('--risk-pct', type=float, default=2.7, help='Risk per trade as % of capital')
    ap.add_argument('--max-contracts', type=int, default=1)
    args = ap.parse_args()

    cycle = json.loads(Path(args.cycle).read_text())
    rows = pick_rows(cycle, top_n=args.top_n)
    if not rows:
        raise SystemExit('No rows with params found in cycle JSON')

    series = []
    meta = []
    risk_rub = args.cash * (args.risk_pct / 100.0)
    capital_pct_base = 100.0
    for row in rows:
        equity, metrics, trades, csv_path = load_real_series(row, args.cash)
        series.append((equity, row, metrics, trades, csv_path))
        meta.append({
            'ticker': row['ticker'],
            'strategy': row['strategy'],
            'timeframe': row['timeframe'],
            'trades': len(trades),
            'total_pnl_rub_at_1m': float(metrics.get('total_pnl', 0.0)),
            'profit_factor': float(metrics.get('profit_factor', 0.0)),
            'csv': str(csv_path),
        })

    plt.figure(figsize=(16, 9))
    for equity, row, metrics, trades, csv_path in series:
        # Equity series returned by run_backtest is rendered both as RUB and normalized %.
        y_rub = equity.astype(float)
        y_pct = (y_rub / float(y_rub.iloc[0])) * 100.0
        label = f"{row['ticker']} {row['strategy']} {row['timeframe']} | trades={len(trades)} | pnl={metrics.get('total_pnl', 0.0):.2f} RUB | pf={metrics.get('profit_factor', 0.0):.2f}"
        plt.plot(range(len(y_pct)), y_pct.values, linewidth=2, label=label)

    plt.title(f'Per-strategy equity overlay (NOT a shared pool) — independent backtests @ capital={args.cash:.0f} RUB | risk={args.risk_pct:.2f}%/trade')
    plt.xlabel('Bars')
    plt.ylabel('Normalized equity (start=100)')
    plt.grid(True, alpha=0.2)
    plt.legend(fontsize=8, loc='upper left')
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    plt.tight_layout()
    plt.savefig(out, dpi=160)

    meta_path = out.with_suffix('.json')
    meta_path.write_text(json.dumps({'cash': args.cash, 'risk_pct': args.risk_pct, 'risk_rub': risk_rub, 'capital_pct_base': capital_pct_base, 'rows': meta}, ensure_ascii=False, indent=2), encoding='utf-8')
    print(str(out))
    print(str(meta_path))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
