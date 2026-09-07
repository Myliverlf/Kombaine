"""Фаза A2: walk-forward out-of-sample.
Параметры отобраны на 60d (хвост 365d-файла). Проверяем их на данных ДО
окна отбора — чистый out-of-sample, который стратегия никогда не видела.
"""
import json, sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, '/root/prop-desk/strategy_combine')
sys.path.insert(0, '/root/prop-desk/strategy_combine/code')
sys.path.insert(0, '/root/prop-desk/futures_lab')
import futures_lab as fl

DATA = Path('/root/prop-desk/futures_lab/artifacts/tinkoff_futures_data')
CASH, COMM, SLIP = 20_000.0, 1.5, 2.0
KW = dict(initial_cash=CASH, contracts=1, commission_per_contract=COMM,
          slippage_bps=SLIP, stop_atr=2.0, take_atr=3.0, max_hold_bars=48,
          risk_rub=0.0, max_contracts=1, debug_only=True)

def load(ticker, horizon):
    csv = DATA / f'{ticker}_{horizon}d_1h_continuous.csv'
    args = SimpleNamespace(ticker=ticker, timeframe='1h', file=str(csv), continuous=True,
                           sandbox=True, days=horizon, start=None, end=None, interval='1h',
                           roll_days=5, initial_cash=CASH, contracts=1,
                           commission_per_contract=COMM, slippage_bps=SLIP,
                           stop_atr=2.0, take_atr=3.0, max_hold_bars=48, risk_rub=0.0,
                           max_contracts=1)
    return fl.load_data(args)

cands = [
    {'ticker': 'LKOH', 'strategy': 'ichimoku_cloud', 'params': {'tenkan': 9, 'kijun': 26, 'senkou': 52}, 'in_sample': 10128},
    {'ticker': 'LKOH', 'strategy': 'vwap_bands', 'params': {'std_mult': 2.0, 'min_trades': 3}, 'in_sample': 9550},
    {'ticker': 'LKOH', 'strategy': 'cci_channel_breakout', 'params': {'period': 20, 'threshold': 100.0}, 'in_sample': 9528},
    {'ticker': 'LKOH', 'strategy': 'ft_multi_rsi', 'params': {'fast_rsi': 7, 'slow_rsi': 14, 'fast_ma': 5, 'slow_ma': 100, 'spread': 10.0}, 'in_sample': 7575},
    {'ticker': 'LKOH', 'strategy': 'donchian_breakout', 'params': {'period': 30}, 'in_sample': 6560},
    {'ticker': 'LKOH', 'strategy': 'volatility_squeeze', 'params': {'bb_period': 20, 'bb_stds': 2.0, 'kc_period': 20, 'kc_mult': 1.5}, 'in_sample': 6224},
]
# параметры ichimoku/vwap/cci — восстановить из результата калибровки, если отличаются
calib = json.loads(Path('/root/prop-desk/strategy_combine/reports/strategy_architect/calibration_net_smooth.json').read_text())
by_strat = {c['strategy']: c['params'] for c in calib}
for c in cands:
    if c['strategy'] in by_strat:
        c['params'] = by_strat[c['strategy']]

report = []
for c in cands:
    tk, st, p = c['ticker'], c['strategy'], c['params']
    try:
        spec365, df365 = load(tk, 365)
        spec60, df60 = load(tk, 60)
        n_pre = len(df365) - len(df60)
        if n_pre < 200:
            report.append({'ticker': tk, 'strategy': st, 'error': 'insufficient pre-window'})
            continue
        df_pre = df365.iloc[:n_pre].reset_index(drop=True)
        df_in = df365.iloc[n_pre:].reset_index(drop=True)

        def run(d):
            m, _, _ = fl.run_backtest(d, spec365, st, p, **KW)
            return float(m['total_pnl']), int(m['trade_count']), float(m.get('profit_factor') or 0), float(m.get('max_drawdown') or 0)

        pre_pnl, pre_tr, pre_pf, pre_dd = run(df_pre)
        in_pnl, in_tr, in_pf, in_dd = run(df_in)
        # устойчивость внутри pre-окна (две половины)
        h = len(df_pre)//2
        p1 = run(df_pre.iloc[:h].reset_index(drop=True))
        p2 = run(df_pre.iloc[h:].reset_index(drop=True))

        passed = pre_pnl > 0 and pre_tr >= 10
        report.append({
            'ticker': tk, 'strategy': st, 'params': p,
            'in_sample_60d': {'pnl': round(c['in_sample'], 0), 'note': 'окно отбора'},
            'repro_in_window': {'pnl': round(in_pnl, 1), 'trades': in_tr, 'pf': round(in_pf, 2)},
            'oos_pre_window': {'pnl': round(pre_pnl, 1), 'trades': pre_tr, 'pf': round(pre_pf, 2),
                               'max_dd': round(pre_dd, 1), 'bars': n_pre},
            'oos_half1': {'pnl': round(p1[0], 1), 'trades': p1[1]},
            'oos_half2': {'pnl': round(p2[0], 1), 'trades': p2[1]},
            'oos_pass': passed,
        })
    except Exception as e:
        report.append({'ticker': tk, 'strategy': st, 'error': str(e)[:120]})

out = Path('/root/prop-desk/strategy_combine/reports/strategy_architect/walk_forward_oos.json')
out.write_text(json.dumps(report, ensure_ascii=False, indent=2))
print(f"{'strategy':24s} {'60d(in)':>8s} {'repro':>8s} {'OOS_pnl':>9s} {'OOS_tr':>6s} {'OOS_pf':>6s} {'h1':>8s} {'h2':>8s}  pass")
for r in report:
    if 'error' in r:
        print(f"{r['strategy']:24s} ERROR: {r['error']}")
        continue
    print(f"{r['strategy']:24s} {r['in_sample_60d']['pnl']:8.0f} {r['repro_in_window']['pnl']:8.0f} "
          f"{r['oos_pre_window']['pnl']:9.0f} {r['oos_pre_window']['trades']:6d} {r['oos_pre_window']['pf']:6.2f} "
          f"{r['oos_half1']['pnl']:8.0f} {r['oos_half2']['pnl']:8.0f}  {'PASS' if r['oos_pass'] else 'FAIL'}")
print('written:', out)
