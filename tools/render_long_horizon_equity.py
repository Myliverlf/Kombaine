"""Render long-horizon equity curves for battle-ready strategies.

This uses the longest available history per selected candidate and plots the
actual equity curve across time, not a 60d discovery window.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import List, Dict, Any

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

sys.path.insert(0, '/root/prop-desk/strategy_combine')
sys.path.insert(0, '/root/prop-desk/strategy_combine/code')
sys.path.insert(0, '/root/prop-desk/futures_lab')

import futures_lab as fl

ROOT = Path('/root/prop-desk/strategy_combine')
DATA = Path('/root/prop-desk/futures_lab/artifacts/tinkoff_futures_data')
INP = ROOT / 'reports/strategy_architect/long_horizon_battle_scan.json'
OUT_PNG = ROOT / 'reports/strategy_architect/long_horizon_battle_equity.png'
OUT_JSON = ROOT / 'reports/strategy_architect/long_horizon_battle_equity.json'

CASH = 20_000.0
COMM = 1.5
SLIP = 2.0
KW = dict(
    initial_cash=CASH,
    contracts=1,
    commission_per_contract=COMM,
    slippage_bps=SLIP,
    stop_atr=2.0,
    take_atr=3.0,
    max_hold_bars=48,
    risk_rub=0.0,
    max_contracts=1,
    debug_only=True,
)

# Battle-ready picks: diverse enough to show not only LKOH.
PICKS = [
    {"ticker": "RI", "strategy": "ft_multi_rsi", "params": {"fast_rsi": 7, "slow_rsi": 14, "fast_ma": 5, "slow_ma": 200, "spread": 20.0}, "days": 365},
    {"ticker": "RI", "strategy": "nfi_trend", "params": {"trend_fast": 34, "trend_slow": 144, "rsi_period": 7, "rsi_low": 25.0, "rsi_high": 75.0, "adx_period": 10, "adx_min": 20.0}, "days": 365},
    {"ticker": "LKOH", "strategy": "ichimoku_cloud", "params": {"tenkan": 7, "kijun": 34, "senkou_b": 40}, "days": 1095},
    {"ticker": "LKOH", "strategy": "inside_bar_breakout", "params": {"atr_period": 10, "atr_mult": 0.5}, "days": 1095},
]


def load_data(ticker: str, days: int):
    csv = DATA / f"{ticker}_{days}d_1h_continuous.csv"
    if not csv.exists():
        # fallback to the longest available file
        for alt in (1095, 365, 60):
            p = DATA / f"{ticker}_{alt}d_1h_continuous.csv"
            if p.exists():
                csv = p
                break
    args = SimpleNamespace(
        ticker=ticker,
        timeframe='1h',
        file=str(csv),
        continuous=True,
        sandbox=True,
        days=60,
        start=None,
        end=None,
        interval='1h',
        roll_days=5,
        initial_cash=CASH,
        contracts=1,
        commission_per_contract=COMM,
        slippage_bps=SLIP,
        stop_atr=2.0,
        take_atr=3.0,
        max_hold_bars=48,
        risk_rub=0.0,
        max_contracts=1,
    )
    spec, df = fl.load_data(args)
    return csv, spec, df


def main() -> int:
    series: List[Dict[str, Any]] = []
    summary: List[Dict[str, Any]] = []

    for p in PICKS:
        csv, spec, df = load_data(p['ticker'], p['days'])
        metrics, trades, equity = fl.run_backtest(df, spec, p['strategy'], p['params'], **KW)

        # recover time axis (prefer explicit time column)
        if 'time' in df.columns:
            x = [str(t) for t in df['time'].tolist()]
        else:
            x = [str(i) for i in range(len(df))]

        label = f"{p['ticker']} {p['strategy']} · {p['days']}d · pnl={float(metrics.get('total_pnl', 0.0)):+.0f} · pf={float(metrics.get('profit_factor', 0.0)):.2f} · tr={len(trades)}"
        series.append({
            'label': label,
            'ticker': p['ticker'],
            'strategy': p['strategy'],
            'days': p['days'],
            'csv': str(csv),
            'times': x,
            'equity': [float(v) for v in equity],
            'metrics': {
                'total_pnl': float(metrics.get('total_pnl', 0.0)),
                'profit_factor': float(metrics.get('profit_factor', 0.0)),
                'trade_count': int(len(trades)),
                'csv': str(csv),
            },
        })
        summary.append({
            'ticker': p['ticker'],
            'strategy': p['strategy'],
            'days': p['days'],
            'csv': str(csv),
            'pnl': round(float(metrics.get('total_pnl', 0.0)), 2),
            'pf': round(float(metrics.get('profit_factor', 0.0)), 3),
            'trades': len(trades),
        })

    # plot
    plt.style.use('seaborn-v0_8-whitegrid')
    fig, ax = plt.subplots(figsize=(15, 8), dpi=160)
    colors = ['#1f77b4', '#ff7f0e', '#2ca02c', '#d62728']
    for i, s in enumerate(series):
        y = s['equity']
        x = list(range(len(y)))
        ax.plot(x, y, linewidth=2.1, color=colors[i % len(colors)], label=s['label'])
        # mark the last point
        ax.scatter([x[-1]], [y[-1]], s=28, color=colors[i % len(colors)], zorder=4)

    ax.set_title('Battle-ready equity curves on long history (365d / 1095d)', fontsize=16, weight='bold')
    ax.set_xlabel('bars on the selected long horizon')
    ax.set_ylabel('equity, RUB')
    ax.legend(loc='best', fontsize=9)
    ax.grid(True, alpha=0.25)
    fig.tight_layout()
    fig.savefig(OUT_PNG, bbox_inches='tight')

    OUT_JSON.write_text(json.dumps({'series': summary}, ensure_ascii=False, indent=2))
    print(OUT_PNG)
    print(OUT_JSON)
    for s in summary:
        print(f"{s['ticker']:5s} {s['strategy']:24s} {s['days']:4d}d pnl={s['pnl']:+9,.0f} pf={s['pf']:.2f} tr={s['trades']:3d} csv={Path(s['csv']).name}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
