"""Фаза A3b: walk-forward для топ-кандидатов диверсификации (RI, IMOEX, Si)."""
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
    if not csv.exists():
        return None, None
    args = SimpleNamespace(ticker=ticker, timeframe='1h', file=str(csv), continuous=True,
                           sandbox=True, days=horizon, start=None, end=None, interval='1h',
                           roll_days=5, initial_cash=CASH, contracts=1,
                           commission_per_contract=COMM, slippage_bps=SLIP,
                           stop_atr=2.0, take_atr=3.0, max_hold_bars=48, risk_rub=0.0,
                           max_contracts=1)
    return fl.load_data(args)

scan = json.loads(Path('/root/prop-desk/strategy_combine/reports/strategy_architect/diversification_scan.json').read_text())
# топ-1 по каждому тикеру + топ-3 RI
top = [scan['top'][0]]  # RI vwap_bands 30/1.5
seen = {(top[0]['ticker'], top[0]['strategy'])}
for r in scan['top'][:50]:
    k = (r['ticker'], r['strategy'])
    if r['ticker'] == 'RI' and len([1 for s in seen if s[0]=='RI']) < 4:
        if k not in seen:
            top.append(r); seen.add(k)
    elif r['ticker'] in ('IMOEX', 'Si') and k not in seen:
        top.append(r); seen.add(k)

report = []
for c in top:
    tk, st, p = c['ticker'], c['strategy'], c['params']
    spec365, df365 = load(tk, 365)
    spec60, df60 = load(tk, 60)
    if df365 is None or df60 is None:
        report.append({'ticker': tk, 'strategy': st, 'error': 'no 365d data'})
        continue
    n_pre = len(df365) - len(df60)
    df_pre = df365.iloc[:n_pre].reset_index(drop=True)
    df_in = df365.iloc[n_pre:].reset_index(drop=True)

    def run(d):
        m, _, _ = fl.run_backtest(d, spec365, st, p, **KW)
        return float(m.get('total_pnl') or 0), int(m.get('trade_count') or 0), float(m.get('profit_factor') or 0)

    pre_pnl, pre_tr, pre_pf = run(df_pre)
    in_pnl, in_tr, _ = run(df_in)
    h = len(df_pre)//2
    p1 = run(df_pre.iloc[:h].reset_index(drop=True))
    p2 = run(df_pre.iloc[h:].reset_index(drop=True))
    passed = pre_pnl > 0 and pre_tr >= 10
    report.append({'ticker': tk, 'strategy': st, 'params': p,
                   'in_sample_60d': round(c['net_pnl'], 0),
                   'repro_in_window': {'pnl': round(in_pnl, 1), 'trades': in_tr},
                   'oos_pre': {'pnl': round(pre_pnl, 1), 'trades': pre_tr, 'pf': round(pre_pf, 2), 'bars': n_pre},
                   'oos_h1': round(p1[0], 1), 'oos_h2': round(p2[0], 1),
                   'oos_pass': passed})

out = Path('/root/prop-desk/strategy_combine/reports/strategy_architect/walk_forward_oos_diversification.json')
out.write_text(json.dumps(report, ensure_ascii=False, indent=2))
print(f"{'ticker':6s} {'strategy':24s} {'60d':>8s} {'repro':>8s} {'OOS':>9s} {'tr':>4s} {'h1':>8s} {'h2':>8s}  pass")
for r in report:
    if 'error' in r:
        print(f"{r['ticker']:6s} {r['strategy']:24s} ERR {r['error']}")
        continue
    o = r['oos_pre']
    print(f"{r['ticker']:6s} {r['strategy']:24s} {r['in_sample_60d']:8.0f} {r['repro_in_window']['pnl']:8.0f} "
          f"{o['pnl']:9.0f} {o['trades']:4d} {r['oos_h1']:8.0f} {r['oos_h2']:8.0f}  {'PASS' if r['oos_pass'] else 'FAIL'}")
