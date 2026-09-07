"""Фаза A1: батарея робастности топ-стратегий.
1. Чувствительность параметров ±10/±20% (плато или пик переобучения)
2. Монте-Карло бутстрап по сделкам (доверительный интервал результата)
3. Субпериоды: первая/вторая половина окна
Всё через живой движок futures_lab с нетто-издержками. Детерминированно (seed=42).
"""
import json, sys, random
from pathlib import Path
from types import SimpleNamespace

SC = Path('/root/prop-desk/strategy_combine')
sys.path.insert(0, str(SC))
sys.path.insert(0, str(SC / 'code'))
sys.path.insert(0, '/root/prop-desk/futures_lab')

import futures_lab as fl

DATA = Path('/root/prop-desk/futures_lab/artifacts/tinkoff_futures_data')
CASH, COMM, SLIP = 20_000.0, 1.5, 2.0
KW = dict(initial_cash=CASH, contracts=1, commission_per_contract=COMM,
          slippage_bps=SLIP, stop_atr=2.0, take_atr=3.0, max_hold_bars=48,
          risk_rub=0.0, max_contracts=1, debug_only=True)

def load(ticker, tf='1h', horizon=60):
    csv = DATA / f'{ticker}_{horizon}d_{tf}_continuous.csv'
    args = SimpleNamespace(ticker=ticker, timeframe=tf, file=str(csv), continuous=True,
                           sandbox=True, days=horizon, start=None, end=None, interval=tf,
                           roll_days=5, initial_cash=CASH, contracts=1,
                           commission_per_contract=COMM, slippage_bps=SLIP,
                           stop_atr=2.0, take_atr=3.0, max_hold_bars=48, risk_rub=0.0,
                           max_contracts=1)
    spec, df = fl.load_data(args)
    return spec, df

def bt(spec, df, strategy, params):
    m, trades, eq = fl.run_backtest(df, spec, strategy, params, **KW)
    return m, trades, eq

def perturb(params, scale):
    out = {}
    for k, v in params.items():
        if isinstance(v, bool) or isinstance(v, str):
            out[k] = v
        elif isinstance(v, int):
            nv = round(v * scale)
            out[k] = max(1, nv)
        else:
            out[k] = round(v * scale, 4)
    return out

calib = json.loads((SC/'reports/strategy_architect/calibration_net_smooth.json').read_text())
cands = calib[:6]  # топ-6 по нетто

report = []
for c in cands:
    tk, st, p = c['ticker'], c['strategy'], c['params']
    spec, df = load(tk)
    m0, tr0, eq0 = bt(spec, df, st, p)
    base_pnl = float(m0['total_pnl'])
    base_tr = int(m0['trade_count'])
    trade_pnls = [float(t.pnl) for t in tr0]

    # 1. Параметрическая чувствительность
    sens = {}
    for scale in (0.8, 0.9, 1.1, 1.2):
        pp = perturb(p, scale)
        if pp == p:
            sens[f'x{scale}'] = {'pnl': base_pnl, 'trades': base_tr}
            continue
        try:
            mm, _, _ = bt(spec, df, st, pp)
            sens[f'x{scale}'] = {'pnl': round(float(mm['total_pnl']), 1), 'trades': int(mm['trade_count'])}
        except Exception as e:
            sens[f'x{scale}'] = {'error': str(e)[:80]}
    pnls = [v['pnl'] for v in sens.values() if 'pnl' in v]
    sens_all_pos = all(x > 0 for x in pnls) if pnls else False
    sens_min = min(pnls) if pnls else None
    sens_avg = sum(pnls)/len(pnls) if pnls else None

    # 2. Монте-Карло бутстрап по сделкам
    rng = random.Random(42)
    mc_totals = []
    n = len(trade_pnls)
    if n >= 10:
        for _ in range(2000):
            sample = [trade_pnls[rng.randrange(n)] for _ in range(n)]
            mc_totals.append(sum(sample))
        mc_totals.sort()
        p05 = mc_totals[int(0.05*len(mc_totals))]
        p50 = mc_totals[int(0.50*len(mc_totals))]
        p95 = mc_totals[int(0.95*len(mc_totals))]
        loss_prob = sum(1 for x in mc_totals if x <= 0) / len(mc_totals)
    else:
        p05 = p50 = p95 = loss_prob = None

    # 3. Субпериоды (половины окна)
    half = len(df) // 2
    sub = {}
    for name, d in (('h1', df.iloc[:half].copy()), ('h2', df.iloc[half:].copy())):
        try:
            mm, _, _ = bt(spec, d.reset_index(drop=True), st, p)
            sub[name] = {'pnl': round(float(mm['total_pnl']), 1), 'trades': int(mm['trade_count'])}
        except Exception as e:
            sub[name] = {'error': str(e)[:60]}
    sub_ok = all(s.get('pnl', -1) > 0 for s in sub.values())

    verdict = 'PLATEAU' if sens_all_pos and sub_ok else ('MIXED' if (sens_min or 0) > 0 else 'PEAK_RISK')
    report.append({
        'ticker': tk, 'strategy': st, 'params': p,
        'base_net_pnl': round(base_pnl, 1), 'trades': base_tr,
        'sensitivity': sens, 'sens_all_positive': sens_all_pos,
        'sens_min_pnl': sens_min, 'sens_avg_pnl': round(sens_avg, 1) if sens_avg is not None else None,
        'monte_carlo': {'p05': p05, 'p50': p50, 'p95': p95, 'loss_prob': loss_prob},
        'subperiods': sub, 'subperiods_both_positive': sub_ok,
        'verdict': verdict,
    })

out = SC / 'reports/strategy_architect/robustness_battery.json'
out.write_text(json.dumps(report, ensure_ascii=False, indent=2))
print(f"{'strategy':24s} {'base':>8s} {'sens_min':>9s} {'avg':>8s} {'MC_p05':>8s} {'loss%':>6s} {'h1':>7s} {'h2':>7s}  verdict")
for r in report:
    mc = r['monte_carlo']
    s = r['subperiods']
    print(f"{r['strategy']:24s} {r['base_net_pnl']:8.0f} "
          f"{(r['sens_min_pnl'] or 0):9.0f} {(r['sens_avg_pnl'] or 0):8.0f} "
          f"{(mc['p05'] or 0):8.0f} {(mc['loss_prob'] or 0)*100:5.1f}% "
          f"{s.get('h1',{}).get('pnl',0):7.0f} {s.get('h2',{}).get('pnl',0):7.0f}  {r['verdict']}")
print('written:', out)
