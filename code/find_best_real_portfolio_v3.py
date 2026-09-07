#!/usr/bin/env python3
import sys, json, pathlib, itertools, collections
sys.path.insert(0, '/root/prop-desk/strategy_combine/code')
sys.path.insert(0, '/root/prop-desk/futures_lab')
from portfolio_equity_analyzer import align_curves, sum_curves, equity_profile
from futures_lab import run_backtest, _synthetic_spec_for_file
import pandas as pd

DATA = pathlib.Path('/root/prop-desk/futures_lab/artifacts/tinkoff_futures_data')
BASE = pathlib.Path('/root/prop-desk/strategy_combine/reports/strategy_architect')
CAPITAL = 19800
COMM = 5.0
SLIP = 1.0
MAX_GO = CAPITAL * 0.7

# Real GO from Tinkoff API (read-only)
REAL_GO = {
    'LKOH': 7413, 'SBER': 4647, 'GAZP': 1432, 'Si': 12718,
    'IMOEX': 2140, 'BR': 16344, 'USDRUB': 1200, 'EURRUB': 1500,
    'NG': 800, 'CNY': 500,
}

# All strategies from signal pool
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
    if not csv_path.exists(): continue
    
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
                'pnl': round(pnl, 2), 'month': round(pnl/2, 2),
                'month_pct': round(pnl/2/CAPITAL*100, 2),
                'pf': pf, 'wr': wr, 'trades': n, 'dd': round(dd, 2),
                'go': go, 'curve': [float(x)-CAPITAL for x in eq.tolist()],
            })
    except Exception:
        continue

real.sort(key=lambda r: r['pnl'], reverse=True)

print("=== REAL MONEY: все стратегии на 19800₽ ===")
print()
print(f"{'#':>3} {'Ticker':6s} {'TF':4s} {'Strategy':25s} {'GO':>6s} {'PnL/мес':>10s} {'%/мес':>7s} {'PF':>5s} {'WR%':>5s} {'DD':>7s} {'Trades':>6s}")
print("-" * 110)
for i, s in enumerate(real):
    print(f"{i+1:3d} {s['ticker']:6s} {s['tf']:4s} {s['strategy']:25s} {s['go']:6.0f} {s['month']:8.0f}₽ {s['month_pct']:6.1f}% {s['pf']:5.2f} {s['wr']:5.0f} {s['dd']:7.0f} {s['trades']:6d}")

# Find best combos: max 2 per ticker, GO <= 13860 (70% of 19800)
combos = []
for k in [4, 5, 3, 2]:
    for comb in itertools.combinations(real[:20], k):
        if len({(x['ticker'],x['strategy']) for x in comb}) < k: continue
        cnt = collections.Counter(x['ticker'] for x in comb)
        if any(v > 2 for v in cnt.values()): continue
        go = sum(x['go'] for x in comb)
        if go > MAX_GO: continue
        total_pnl = sum(x['pnl'] for x in comb)
        combos.append((total_pnl, go, comb))
combos = sorted(combos, key=lambda x: x[0], reverse=True)

if combos:
    for rank, (pnl, go, comb) in enumerate(combos[:3]):
        curves = align_curves([x['curve'] for x in comb], n=500)
        port = sum_curves(curves)
        prof = equity_profile(port)
        
        print()
        print(f"=== COMBO #{rank+1}: {len(comb)} strategies ===")
        print(f"GO: {go:.0f}₽ (reserve: {CAPITAL-go:.0f}₽)")
        print(f"PnL/мес: {pnl/2:.0f}₽ ({pnl/2/CAPITAL*100:.1f}%)")
        print(f"R²: {prof.get('r2',0):.3f}, DD: {prof.get('max_drawdown',0):.0f}")
        for s in comb:
            print(f"  {s['ticker']} {s['tf']} {s['strategy']}: {s['month']:.0f}₽/мес ({s['month_pct']:.1f}%), PF={s['pf']:.2f}")
