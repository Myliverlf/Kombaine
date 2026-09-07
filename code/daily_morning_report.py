#!/usr/bin/env python3
"""Daily morning report: top-10 strategies + combined portfolio equity."""
import sys, json, pathlib, itertools, collections
sys.path.insert(0, '/root/prop-desk/strategy_combine/code')
sys.path.insert(0, '/root/prop-desk/futures_lab')
from portfolio_equity_analyzer import align_curves, sum_curves, equity_profile
from futures_lab import run_backtest, _synthetic_spec_for_file
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

DATA = pathlib.Path('/root/prop-desk/futures_lab/artifacts/tinkoff_futures_data')
REPORT = pathlib.Path('/root/prop-desk/strategy_combine/reports/strategy_architect')
CAPITAL = 19800
COMM = 5.0
SLIP = 1.0

REAL_GO = {
    'LKOH': 7413, 'SBER': 4647, 'GAZP': 1432, 'Si': 12718,
    'IMOEX': 2140, 'BR': 16344, 'USDRUB': 1200, 'EURRUB': 1500,
    'NG': 800, 'CNY': 500,
}

# Load signal pool
sp = json.loads((pathlib.Path('/root/prop-desk/strategy_combine/state/signal_pool.json')).read_text())
strategies = sp.get('strategies', {})

real = []
for k, v in strategies.items():
    m = v.get('metrics', {})
    t = v.get('ticker')
    tf = m.get('timeframe', '?')
    strat = v.get('strategy')
    params = v.get('params')
    go = REAL_GO.get(t, 5000)
    csv_path = DATA / f'{t}_60d_{tf}_continuous.csv'
    if not csv_path.exists():
        continue
    df = pd.read_csv(csv_path)
    spec = _synthetic_spec_for_file(t)
    try:
        mm, tr, eq = run_backtest(df, spec, strat, params or {}, initial_cash=CAPITAL, contracts=1, max_contracts=1, commission_per_contract=COMM, slippage_bps=SLIP)
        pnl = float(mm.get('total_pnl') or 0)
        pf = float(mm.get('profit_factor') or 0)
        n = int(mm.get('trade_count') or 0)
        dd = float(mm.get('max_drawdown') or 0)
        wr = float(mm.get('win_rate_pct') or 0)
        if n >= 5 and pnl > 0 and pf >= 1.05:
            real.append({
                'ticker': t, 'strategy': strat, 'tf': tf, 'params': params,
                'pnl': round(pnl, 2), 'month': round(pnl / 2, 2),
                'month_pct': round(pnl / 2 / CAPITAL * 100, 2),
                'pf': pf, 'wr': wr, 'trades': n, 'dd': round(dd, 2),
                'go': go, 'curve': [float(x) - CAPITAL for x in eq.tolist()],
            })
    except Exception as exc:
        raise RuntimeError(f"daily_morning_report failed to build real-money row for {t}/{strat}") from exc

real.sort(key=lambda r: r['pnl'], reverse=True)
top10 = real[:10]

# Render individual equity charts (2 columns x 5 rows)
colors = ['#2196F3', '#FF9800', '#4CAF50', '#9C27B0', '#F44336', '#00BCD4', '#E91E63', '#795548', '#607D8B', '#FFEB3B']
fig, axes = plt.subplots(5, 2, figsize=(18, 20))
fig.suptitle(f'TOP-10 Strategies — Individual Equity (19800₽, {len(top10)} found)', fontsize=14, fontweight='bold')

for i, s in enumerate(top10):
    ax = axes[i // 2][i % 2]
    csv = DATA / f"{s['ticker']}_60d_{s['tf']}_continuous.csv"
    df = pd.read_csv(csv)
    dates = pd.to_datetime(df['time'])
    n = min(len(s['curve']), len(dates))
    equity = [0] + s['curve'][:n]
    equity = equity[:len(dates[:len(equity)])]
    ax.plot(dates[:len(equity)], equity, color=colors[i], linewidth=2)
    ax.fill_between(dates[:len(equity)], 0, equity, alpha=0.15, color=colors[i])
    ax.axhline(y=0, color='gray', linestyle='--', alpha=0.5)
    ax.set_title(f"#{i+1} {s['ticker']} {s['tf']} {s['strategy']}\nPnL={s['month']:.0f}₽/мес ({s['month_pct']:.1f}%) PF={s['pf']:.2f} DD={s['dd']:.0f}", fontsize=10)
    ax.grid(True, alpha=0.3)
    ax.xaxis.set_major_formatter(mdates.DateFormatter('%d %b'))
    plt.setp(ax.xaxis.get_majorticklabels(), rotation=30)

plt.tight_layout()
out_individual = str(REPORT / 'daily_top10_individual.png')
plt.savefig(out_individual, dpi=150, bbox_inches='tight')
plt.close()

# Find best combos (max 2 per ticker, GO <= 70% of capital)
combos = []
max_go = CAPITAL * 0.7
for k in [4, 5, 3, 2]:
    for comb in itertools.combinations(top10, k):
        if len({(x['ticker'], x['strategy']) for x in comb}) < k:
            continue
        cnt = collections.Counter(x['ticker'] for x in comb)
        if any(v > 2 for v in cnt.values()):
            continue
        go = sum(x['go'] for x in comb)
        if go > max_go:
            continue
        total_pnl = sum(x['pnl'] for x in comb)
        combos.append((total_pnl, go, comb))
combos = sorted(combos, key=lambda x: x[0], reverse=True)

# Render best combo portfolio
if combos:
    _, go, comb = combos[0]
    curves = align_curves([x['curve'] for x in comb], n=500)
    port = sum_curves(curves)
    prof = equity_profile(port)

    fig, ax = plt.subplots(1, 1, figsize=(14, 6))
    fig.suptitle(f'BEST COMBO PORTFOLIO: {len(comb)} strategies (equal weight)', fontsize=14, fontweight='bold')

    # Individual lines
    for i, s in enumerate(comb):
        csv = DATA / f"{s['ticker']}_60d_{s['tf']}_continuous.csv"
        df = pd.read_csv(csv)
        dates = pd.to_datetime(df['time'])
        n = min(len(s['curve']), len(dates))
        equity = [0] + s['curve'][:n]
        equity = equity[:len(dates[:len(equity)])]
        ax.plot(dates[:len(equity)], equity, color=colors[i], linewidth=1.5, alpha=0.5, label=f"{s['ticker']} {s['strategy']}")

    # Portfolio
    df0 = pd.read_csv(DATA / f"{comb[0]['ticker']}_60d_{comb[0]['tf']}_continuous.csv")
    dates0 = pd.to_datetime(df0['time'])
    n = min(len(port), len(dates0))
    ax.plot(dates0[:n], port[:n], color='#E91E63', linewidth=3, label='PORTFOLIO')
    ax.fill_between(dates0[:n], 0, port[:n], alpha=0.2, color='#E91E63')
    ax.axhline(y=0, color='gray', linestyle='--', alpha=0.5)

    total_month = port[-1] / 2
    ax.set_title(f'PnL={total_month:.0f}₽/мес ({total_month / CAPITAL * 100:.1f}%) | GO={go:.0f}₽ | R²={prof.get("r2", 0):.3f} | DD={prof.get("max_drawdown", 0):.0f}')
    ax.set_ylabel('PnL (₽)')
    ax.legend(loc='upper left', fontsize=8)
    ax.grid(True, alpha=0.3)
    ax.xaxis.set_major_formatter(mdates.DateFormatter('%d %b'))
    plt.setp(ax.xaxis.get_majorticklabels(), rotation=30)
    plt.tight_layout()
    out_combo = str(REPORT / 'daily_best_combo.png')
    plt.savefig(out_combo, dpi=150, bbox_inches='tight')
    plt.close()

    # Summary
    summary = {
        'top10_count': len(top10),
        'top10': [{k: v for k, v in s.items() if k != 'curve'} for s in top10],
        'combo_n': len(comb),
        'combo_go': round(go),
        'combo_month': round(total_month),
        'combo_month_pct': round(total_month / CAPITAL * 100, 1),
        'combo_r2': round(prof.get('r2', 0), 3),
        'combo_dd': round(prof.get('max_drawdown', 0)),
        'combo_strategies': [{k: v for k, v in s.items() if k != 'curve'} for s in comb],
        'png_individual': out_individual,
        'png_combo': out_combo,
    }
else:
    summary = {'top10_count': len(top10), 'top10': [{k: v for k, v in s.items() if k != 'curve'} for s in top10], 'combo_n': 0}

# Save summary
out_json = str(REPORT / 'daily_report_summary.json')
pathlib.Path(out_json).write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps(summary, ensure_ascii=False))
