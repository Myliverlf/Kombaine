"""Полный нетто-скан: все тикеры, ранжирование по гладкости эквити.
Цель: найти прибыльные стратегии с ПЛАВНОЙ кривой на разных инструментах.
Издержки: комиссия 1.5₽ + слиппедж 2bps, капитал 20к, 60d/1h.
"""
import json, sys, time
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, '/root/prop-desk/strategy_combine')
sys.path.insert(0, '/root/prop-desk/strategy_combine/code')
sys.path.insert(0, '/root/prop-desk/futures_lab')
import futures_lab as fl
import strategy_zoo as z
from smooth_equity_metrics import smooth_equity_metrics

DATA = Path('/root/prop-desk/futures_lab/artifacts/tinkoff_futures_data')
CASH, COMM, SLIP = 20_000.0, 1.5, 2.0
KW = dict(initial_cash=CASH, contracts=1, commission_per_contract=COMM,
          slippage_bps=SLIP, stop_atr=2.0, take_atr=3.0, max_hold_bars=48,
          risk_rub=0.0, max_contracts=1, debug_only=True)
TICKERS = ['LKOH', 'BR', 'SBER', 'GAZP', 'IMOEX', 'Si', 'NG', 'CNY', 'RI', 'AAU', 'USDRUB', 'EURRUB']
MAX_PARAM_SETS = 8

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
        print(f'{tk}: нет данных', flush=True)
        continue
    for strat, grid in z.PARAM_GRIDS.items():
        grid = grid[:MAX_PARAM_SETS]
        for params in grid:
            try:
                m, trades, eq = fl.run_backtest(df, spec, strat, params, **KW)
                pnl = float(m.get('total_pnl') or 0)
                tr = int(m.get('trade_count') or 0)
                if pnl <= 0 or tr < 15:
                    continue
                sm = smooth_equity_metrics([float(x) for x in eq])
                rows.append({'ticker': tk, 'strategy': strat, 'params': params,
                             'net_pnl': round(pnl, 1), 'trades': tr,
                             'pf': round(float(m.get('profit_factor') or 0), 2),
                             'smooth_score': sm.get('smooth_score', 0),
                             'ulcer': sm.get('ulcer_index', 99),
                             'dd_ratio': sm.get('dd_ratio', 99),
                             'max_dd': round(sm.get('max_drawdown', 0), 1),
                             'r2': sm.get('equity_r2', 0),
                             'clusters': sm.get('drawdown_cluster_count', 99),
                             'tail_pnl': sm.get('tail_pnl', 0)})
            except Exception:
                pass
    print(f'{tk}: готово, прибыльных пока {len(rows)}, {time.time()-t0:.0f}s', flush=True)

rows.sort(key=lambda r: -r['smooth_score'])
out = Path('/root/prop-desk/strategy_combine/reports/strategy_architect/smooth_full_scan.json')
out.write_text(json.dumps({'rows_total': len(rows), 'top': rows}, ensure_ascii=False, indent=2))
print(f'прибыльных >=15 сделок: {len(rows)}')
print('ТОП-25 по гладкости эквити:')
for r in rows[:25]:
    print(f"  {r['ticker']:7s} {r['strategy']:26s} pnl={r['net_pnl']:+8,.0f} score={r['smooth_score']:7.0f} dd={r['max_dd']:7,.0f} ulcer={r['ulcer']:5.1f} r2={r['r2']:.2f} tr={r['trades']}")
print('written:', out)
