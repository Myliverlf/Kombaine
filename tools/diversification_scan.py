"""Фаза A3: скан диверсификации — нетто-поиск края на тикерах кроме LKOH.
Цель: найти прибыльные связки на других тикерах, чтобы разбавить концентрацию.
Комиссия 1.5₽ + слиппедж 2bps, капитал 20к, 60d/1h.
"""
import json, sys, time
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, '/root/prop-desk/strategy_combine')
sys.path.insert(0, '/root/prop-desk/strategy_combine/code')
sys.path.insert(0, '/root/prop-desk/futures_lab')
import futures_lab as fl
import strategy_zoo as z

DATA = Path('/root/prop-desk/futures_lab/artifacts/tinkoff_futures_data')
CASH, COMM, SLIP = 20_000.0, 1.5, 2.0
KW = dict(initial_cash=CASH, contracts=1, commission_per_contract=COMM,
          slippage_bps=SLIP, stop_atr=2.0, take_atr=3.0, max_hold_bars=48,
          risk_rub=0.0, max_contracts=1, debug_only=True)
TICKERS = ['BR', 'SBER', 'GAZP', 'IMOEX', 'Si', 'NG', 'CNY', 'RI', 'AAU', 'USDRUB', 'EURRUB']
MAX_PARAM_SETS = 6  # лимит на стратегию, экономим ОЗУ

def load(ticker):
    csv = DATA / f'{ticker}_60d_1h_continuous.csv'
    if not csv.exists():
        return None, None
    args = SimpleNamespace(ticker=ticker, timeframe='1h', file=str(csv), continuous=True,
                           sandbox=True, days=60, start=None, end=None, interval='1h',
                           roll_days=5, initial_cash=CASH, contracts=1,
                           commission_per_contract=COMM, slippage_bps=SLIP,
                           stop_atr=2.0, take_atr=3.0, max_hold_bars=48, risk_rub=0.0,
                           max_contracts=1)
    return fl.load_data(args)

rows = []
t0 = time.time()
for tk in TICKERS:
    spec, df = load(tk)
    if df is None or len(df) < 100:
        print(f'{tk}: нет данных', file=sys.stderr)
        continue
    for strat, grid in z.PARAM_GRIDS.items():
        grid = grid[:MAX_PARAM_SETS]
        for params in grid:
            try:
                m, _, _ = fl.run_backtest(df, spec, strat, params, **KW)
                pnl = float(m.get('total_pnl') or 0)
                tr = int(m.get('trade_count') or 0)
                pf = float(m.get('profit_factor') or 0)
                rows.append({'ticker': tk, 'strategy': strat, 'params': params,
                             'net_pnl': round(pnl, 1), 'trades': tr, 'pf': round(pf, 2)})
            except Exception:
                pass
    print(f'{tk}: готово, строк пока {len(rows)}, {time.time()-t0:.0f}s', flush=True)

# фильтры: >=15 сделок, положительный нетто
ok = [r for r in rows if r['net_pnl'] > 0 and r['trades'] >= 15]
ok.sort(key=lambda r: -r['net_pnl'])
best_per_tk = {}
for r in ok:
    best_per_tk.setdefault(r['ticker'], []).append(r)
print(f'всего строк: {len(rows)}, прибыльных >=15 сделок: {len(ok)}')
print(f'тики с плюсом: {sorted(best_per_tk)}')
print('топ-15:')
for r in ok[:15]:
    print(f"  {r['ticker']:6s} {r['strategy']:24s} pnl={r['net_pnl']:+9,.0f} tr={r['trades']:3d} pf={r['pf']:.2f} {json.dumps(r['params'])}")
out = Path('/root/prop-desk/strategy_combine/reports/strategy_architect/diversification_scan.json')
out.write_text(json.dumps({'rows_total': len(rows), 'profitable': len(ok), 'top': ok[:50],
                           'by_ticker': {k: v[:5] for k, v in best_per_tk.items()}}, ensure_ascii=False, indent=2))
print('written:', out)
